import os
import json
import mimetypes
import struct
import time
import threading
import uuid
import shutil
import zipfile
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Any, Generator, Tuple

from dotenv import load_dotenv
from google import genai
from google.genai import types

try:
    from moviepy import ImageClip, concatenate_videoclips, CompositeVideoClip, ColorClip, VideoFileClip
    from moviepy.video.fx import CrossFadeIn, CrossFadeOut
    from moviepy.audio.io.AudioFileClip import AudioFileClip
    from moviepy.audio.AudioClip import CompositeAudioClip
    from moviepy.audio.fx import volumex, audio_fadein, audio_fadeout
    from moviepy.video.VideoClip import TextClip
    import subprocess
    MOVIEPY_AVAILABLE = True
except ImportError:
    MOVIEPY_AVAILABLE = False

from config import Config, Constants, get_config
from utils import (
    setup_logging, 
    sanitize_filename, 
    sanitize_input,
    validate_json_file, 
    save_json_file,
    retry_with_backoff,
    extract_json_from_response,
    get_prompt_from_scene,
    api_stats,
    RateLimiter
)

config = get_config()
logger = setup_logging(log_file=config.LOG_FILE, log_level=config.LOG_LEVEL)
rate_limiter = RateLimiter(max_calls=config.RATE_LIMIT_PER_MINUTE, period=60)


class Story_flowMixin:

    # ========================
    # TAM HİKAYE AKIŞI
    # ========================
    
    def generate_full_story_stream(
        self,
        user_input: str,
        aspect_ratio: str = "9:16",
        image_size: str = "2K",
        scene_count: int = 7,
        frame_duration: float = 5.0,
        dialogue_style: str = "short",
        art_style: str = "comic",
        mood_style: str = "dynamic",
        camera_style: str = "balanced",
        time_of_day: str = "auto",
        season: str = "auto",
        weather: str = "auto",
        outfit_style: str = "auto",
        character_consistency: str = "strict",
        video_transition: str = "none",
        generate_audio: bool = False,
        dual_format: bool = False          # YENİ: hem 9:16 hem 16:9 üret
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Tam hikaye oluşturma akışı (SSE stream)
        
        Args:
            user_input: Hikaye konusu
            dual_format: True → her sahne için 9:16 + 16:9 görseller üretilir
            ... diğer ayarlar
            
        Yields:
            İlerleme güncellemeleri
        """
        logger.info(f"Tam hikaye akışı başlatıldı: {user_input[:50]}... (dual={dual_format})")
        
        # Input validation
        if not user_input or not user_input.strip():
            yield {"error": "Lütfen bir hikaye konusu girin."}
            return
        
        user_input = sanitize_input(user_input)

        # 0. Karakter referans görsellerini topla (Gemini'ye gönderilecek)
        char_ref_images = self.get_all_reference_images()
        if char_ref_images:
            logger.info(f"Karakter referans görselleri: {len(char_ref_images)} dosya")
            yield {"status": f"🖼️ {len(char_ref_images)} karakter referans görseli yüklendi.", "progress": 5}
        
        # 1. Senaryo oluştur
        yield {"status": f"Senaryo oluşturuluyor ({scene_count} sahne)...", "progress": 10}
        
        prompts = self.senaryo_olustur(
            user_input,
            scene_count=scene_count,
            dialogue_style=dialogue_style,
            art_style=art_style,
            mood_style=mood_style,
            camera_style=camera_style,
            time_of_day=time_of_day,
            season=season,
            weather=weather,
            outfit_style=outfit_style,
            character_consistency=character_consistency
        )

        
        if not prompts:
            yield {"error": "Senaryo oluşturulamadı. Lütfen tekrar deneyin."}
            return
        
        yield {"status": f"Senaryo hazır! {len(prompts)} sahne resmedilecek.", "progress": 20}
        
        # 2. Görseller oluştur
        generated_images = []        # Portrait (ya da tek format) yolları
        generated_images_land = []   # Landscape yolları (dual_format=True ise dolu)
        failed_scenes = []
        total_steps = len(prompts)
        generated_audios = []
        
        use_parallel = config.PARALLEL_IMAGE_GENERATION and total_steps > 1

        # ---------- DUAL FORMAT (sıralı - her sahne 2 paralel istek) ----------
        if dual_format:
            mode_label = "🔄 Çift format (9:16 + 16:9)"
            yield {
                "status": f"{mode_label}: {total_steps} sahne oluşturuluyor...",
                "progress": 25,
                "dual_format": True
            }
            for i, prompt in enumerate(prompts):
                progress = 25 + int((i / total_steps) * 50)
                yield {
                    "status": f"Sahne {i + 1}/{total_steps} çift formatta çiziliyor...",
                    "progress": progress,
                    "current_scene": i + 1
                }

                dual = self.resim_uret_dual(
                    prompt, i, user_input,
                    image_size=image_size,
                    reference_images=char_ref_images or None
                )
                portrait_path  = dual.get("portrait")
                landscape_path = dual.get("landscape")

                generated_images.append(portrait_path)
                generated_images_land.append(landscape_path)

                if portrait_path or landscape_path:
                    yield {
                        "status": f"Sahne {i + 1} tamamlandı! (9:16 + 16:9)",
                        "progress": progress + 5,
                        "scene_completed": {
                            "index": i,
                            "path": portrait_path,          # UI'da gösterilen
                            "path_landscape": landscape_path,
                            "audio": None
                        }
                    }
                else:
                    failed_scenes.append(i + 1)

                generated_audios.append(None)

                # Seslendirme
                if generate_audio and config.TTS_ENABLED and portrait_path:
                    audio_path = self.generate_scene_audio(prompt, i, user_input)
                    generated_audios[-1] = audio_path

        # ---------- PARALEL (tek format) ----------
        elif use_parallel:
            yield {
                "status": f"🚀 {total_steps} sahne paralel olarak oluşturuluyor...",
                "progress": 25,
                "parallel_mode": True
            }
            
            completed_scenes = []
            
            def on_progress(completed, total, index, path):
                completed_scenes.append({
                    "index": index,
                    "path": path,
                    "completed": completed,
                    "total": total
                })
            
            generated_images = self.generate_images_parallel(
                prompts,
                user_input,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                reference_images=char_ref_images or None,
                progress_callback=on_progress
            )
            generated_images_land = [None] * total_steps
            
            for i, img_path in enumerate(generated_images):
                if img_path:
                    yield {
                        "status": f"Sahne {i + 1} tamamlandı!",
                        "progress": 25 + int(((i + 1) / total_steps) * 50),
                        "scene_completed": {
                            "index": i,
                            "path": img_path,
                            "audio": None
                        }
                    }
                else:
                    failed_scenes.append(i + 1)
            
            if generate_audio and config.TTS_ENABLED:
                yield {"status": "Sahneler seslendiriliyor...", "progress": 75}
                for i, prompt in enumerate(prompts):
                    if generated_images[i]:
                        audio_path = self.generate_scene_audio(prompt, i, user_input)
                        generated_audios.append(audio_path)
                    else:
                        generated_audios.append(None)
            else:
                generated_audios = [None] * total_steps
        
        # ---------- SIRALI (tek format) ----------
        else:
            generated_images_land = []
            for i, prompt in enumerate(prompts):
                progress = 20 + int((i / total_steps) * 55)
                yield {
                    "status": f"Sahne {i + 1}/{total_steps} çiziliyor...",
                    "progress": progress,
                    "current_scene": i + 1
                }
                
                img_path = self.resim_uret(
                    prompt,
                    i,
                    user_input,
                    aspect_ratio=aspect_ratio,
                    image_size=image_size,
                    reference_images=char_ref_images or None
                )
                
                if img_path:
                    generated_images.append(img_path)
                    generated_images_land.append(None)
                    
                    audio_path = None
                    if generate_audio and config.TTS_ENABLED:
                        yield {
                            "status": f"Sahne {i + 1} seslendiriliyor...",
                            "progress": progress + 3
                        }
                        audio_path = self.generate_scene_audio(prompt, i, user_input)
                        generated_audios.append(audio_path if audio_path else None)
                    else:
                        generated_audios.append(None)
                    
                    yield {
                        "status": f"Sahne {i + 1} tamamlandı!" + (" (sesli)" if audio_path else ""),
                        "progress": progress + 5,
                        "scene_completed": {
                            "index": i,
                            "path": img_path,
                            "audio": audio_path
                        }
                    }
                else:
                    failed_scenes.append(i + 1)
                    generated_images.append(None)
                    generated_images_land.append(None)
                    generated_audios.append(None)
                    yield {
                        "warning": f"Sahne {i + 1} oluşturulamadı",
                        "progress": progress
                    }
        
        # Başarılı görselleri filtrele
        valid_images = [img for img in generated_images if img]
        
        if not valid_images:
            yield {"error": "Hiçbir görsel oluşturulamadı."}
            return
        
        if failed_scenes:
            yield {"warning": f"Bazı sahneler oluşturulamadı: {failed_scenes}"}
        
        yield {"status": "Sahneler hazır! İnceleyebilirsiniz.", "progress": 85}
        
        # 3. Geçmişe kaydet
        history_entry = {
            "timestamp": int(time.time()),
            "prompt": user_input,
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
            "scene_count": scene_count,
            "art_style": art_style,
            "mood_style": mood_style,
            "camera_style": camera_style,
            "dialogue_style": dialogue_style,
            "environment": f"{time_of_day}/{season}/{weather}",
            "outfit_style": outfit_style,
            "character_consistency": character_consistency,
            "video_transition": video_transition,
            "prompts_generated": prompts,
            "images": generated_images,
            "images_landscape": generated_images_land,  # YENİ: 16:9 versiyonlar
            "dual_format": dual_format,                  # YENİ
            "audios": generated_audios,
            "generate_audio": generate_audio,
            "video": None,
            "frame_duration": frame_duration,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        self._save_to_history(history_entry)
        
        yield {
            "status": "Tamamlandı! Videoyu oluşturmak için butona basın.",
            "progress": 100,
            "result": history_entry
        }
        
        logger.info(f"Hikaye akışı tamamlandı: {len(valid_images)}/{len(prompts)} görsel")

