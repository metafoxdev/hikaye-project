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


class VideoMixin:

    # ========================
    # KEN BURNS EFEKTİ
    # ========================
    
    def _apply_ken_burns_effect(
        self,
        clip,
        effect_type: str = "random",
        zoom_ratio: float = 1.2
    ):
        """
        Ken Burns efekti uygula (zoom/pan animasyonu)
        
        Args:
            clip: MoviePy ImageClip
            effect_type: Efekt tipi (zoom_in, zoom_out, pan_left, pan_right, pan_up, pan_down, random)
            zoom_ratio: Zoom oranı (1.2 = %20 zoom)
            
        Returns:
            Efekt uygulanmış clip
        """
        import random
        
        if effect_type == "none":
            return clip
        
        if effect_type == "random":
            effect_type = random.choice(["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"])
        
        duration = clip.duration
        w, h = clip.size
        
        # Zoom için büyütülmüş boyut
        new_w = int(w * zoom_ratio)
        new_h = int(h * zoom_ratio)
        
        def make_frame(get_frame):
            def new_frame(t):
                import numpy as np
                from PIL import Image
                
                frame = get_frame(t)
                img = Image.fromarray(frame)
                
                progress = t / duration  # 0.0 -> 1.0
                
                if effect_type == "zoom_in":
                    # Başta normal, sonda yakın
                    current_zoom = 1.0 + (zoom_ratio - 1.0) * progress
                    crop_w = int(w / current_zoom)
                    crop_h = int(h / current_zoom)
                    x = (w - crop_w) // 2
                    y = (h - crop_h) // 2
                    
                elif effect_type == "zoom_out":
                    # Başta yakın, sonda normal
                    current_zoom = zoom_ratio - (zoom_ratio - 1.0) * progress
                    crop_w = int(w / current_zoom)
                    crop_h = int(h / current_zoom)
                    x = (w - crop_w) // 2
                    y = (h - crop_h) // 2
                    
                elif effect_type == "pan_left":
                    # Sağdan sola kaydır
                    crop_w = int(w / zoom_ratio)
                    crop_h = int(h / zoom_ratio)
                    max_x = w - crop_w
                    x = int(max_x * (1 - progress))
                    y = (h - crop_h) // 2
                    
                elif effect_type == "pan_right":
                    # Soldan sağa kaydır
                    crop_w = int(w / zoom_ratio)
                    crop_h = int(h / zoom_ratio)
                    max_x = w - crop_w
                    x = int(max_x * progress)
                    y = (h - crop_h) // 2
                    
                elif effect_type == "pan_up":
                    # Aşağıdan yukarı kaydır
                    crop_w = int(w / zoom_ratio)
                    crop_h = int(h / zoom_ratio)
                    max_y = h - crop_h
                    x = (w - crop_w) // 2
                    y = int(max_y * (1 - progress))
                    
                elif effect_type == "pan_down":
                    # Yukarıdan aşağı kaydır
                    crop_w = int(w / zoom_ratio)
                    crop_h = int(h / zoom_ratio)
                    max_y = h - crop_h
                    x = (w - crop_w) // 2
                    y = int(max_y * progress)
                else:
                    return frame
                
                # Crop ve resize
                cropped = img.crop((x, y, x + crop_w, y + crop_h))
                resized = cropped.resize((w, h), Image.LANCZOS)
                
                return np.array(resized)
            
            return new_frame
        
        return clip.transform(make_frame)

    def _create_subtitle_clip(
        self,
        text: str,
        duration: float,
        video_size: tuple,
        settings: Dict[str, Any]
    ):
        """
        Altyazı klibi oluştur
        
        Args:
            text: Altyazı metni
            duration: Süre
            video_size: Video boyutu (width, height)
            settings: Altyazı ayarları
        """
        try:
            from moviepy import TextClip, CompositeVideoClip, ColorClip
            
            if not text or not text.strip():
                return None
            
            width, height = video_size
            font_size = settings.get("fontSize", 32)
            font_color = settings.get("fontColor", "white")
            bg_color = settings.get("bgColor", "black")
            bg_opacity = settings.get("bgOpacity", 0.7)
            position = settings.get("position", "bottom")
            margin = settings.get("margin", 50)
            max_chars = settings.get("maxCharsPerLine", 50)
            
            # Metni satırlara böl
            words = text.split()
            lines = []
            current_line = ""
            
            for word in words:
                if len(current_line) + len(word) + 1 <= max_chars:
                    current_line += (" " if current_line else "") + word
                else:
                    if current_line:
                        lines.append(current_line)
                    current_line = word
            if current_line:
                lines.append(current_line)
            
            wrapped_text = "\n".join(lines)
            
            # Text clip oluştur
            txt_clip = TextClip(
                text=wrapped_text,
                font_size=font_size,
                color=font_color,
                font="Arial",
                text_align="center",
                size=(width - 100, None)
            ).with_duration(duration)
            
            # Arkaplan oluştur
            txt_width, txt_height = txt_clip.size
            padding = 20
            
            bg_clip = ColorClip(
                size=(txt_width + padding * 2, txt_height + padding),
                color=self._hex_to_rgb(bg_color) if bg_color.startswith("#") else (0, 0, 0)
            ).with_duration(duration).with_opacity(bg_opacity)
            
            # Pozisyon hesapla
            if position == "top":
                y_pos = margin
            elif position == "center":
                y_pos = (height - txt_height) // 2
            else:  # bottom
                y_pos = height - txt_height - margin - padding
            
            x_pos = (width - txt_width - padding * 2) // 2
            
            # Composite oluştur
            subtitle_composite = CompositeVideoClip([
                bg_clip.with_position((x_pos, y_pos)),
                txt_clip.with_position((x_pos + padding, y_pos + padding // 2))
            ], size=video_size).with_duration(duration)
            
            return subtitle_composite
            
        except Exception as e:
            logger.warning(f"Altyazı oluşturulamadı: {e}")
            return None
    
    def _hex_to_rgb(self, hex_color: str) -> tuple:
        """Hex rengi RGB'ye çevir"""
        hex_color = hex_color.lstrip('#')
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    
    def _create_watermark_clip(
        self,
        text: str,
        duration: float,
        video_size: tuple,
        settings: Dict[str, Any]
    ):
        """
        Watermark klibi oluştur
        
        Args:
            text: Watermark metni
            duration: Video süresi
            video_size: Video boyutu
            settings: Watermark ayarları
        """
        try:
            from moviepy import TextClip
            
            if not text or not text.strip():
                return None
            
            width, height = video_size
            font_size = settings.get("fontSize", 20)
            font_color = settings.get("fontColor", "white")
            opacity = settings.get("opacity", 0.5)
            position = settings.get("position", "bottom_right")  # top_left, top_right, bottom_left, bottom_right
            margin = settings.get("margin", 20)
            
            # Text clip oluştur
            txt_clip = TextClip(
                text=text,
                font_size=font_size,
                color=font_color,
                font="Arial"
            ).with_duration(duration).with_opacity(opacity)
            
            txt_width, txt_height = txt_clip.size
            
            # Pozisyon hesapla
            if position == "top_left":
                pos = (margin, margin)
            elif position == "top_right":
                pos = (width - txt_width - margin, margin)
            elif position == "bottom_left":
                pos = (margin, height - txt_height - margin)
            else:  # bottom_right
                pos = (width - txt_width - margin, height - txt_height - margin)
            
            return txt_clip.with_position(pos)
            
        except Exception as e:
            logger.warning(f"Watermark oluşturulamadı: {e}")
            return None
    
    def _extract_subtitles_from_prompts(self, prompts: List) -> List[str]:
        """
        Prompt'lardan diyalogları/altyazıları çıkar
        
        Args:
            prompts: Sahne prompt'ları listesi
            
        Returns:
            Altyazı metinleri listesi
        """
        import re
        
        subtitles = []
        
        for prompt in prompts:
            prompt_text = get_prompt_from_scene(prompt) if isinstance(prompt, dict) else str(prompt)
            
            # Diyalog kalıplarını ara
            # "..." veya «...» veya -... formatları
            dialogue_patterns = [
                r'"([^"]+)"',  # "diyalog"
                r'«([^»]+)»',  # «diyalog»
                r"'([^']+)'",  # 'diyalog'
                r':\s*["\']?([^"\'\.!?]+[\.!?])',  # Karakter: diyalog
            ]
            
            dialogues = []
            for pattern in dialogue_patterns:
                matches = re.findall(pattern, prompt_text)
                dialogues.extend(matches)
            
            if dialogues:
                # İlk diyaloğu al veya hepsini birleştir
                subtitle = " ".join(dialogues[:2])  # Max 2 diyalog
                # Çok uzunsa kısalt
                if len(subtitle) > 150:
                    subtitle = subtitle[:147] + "..."
                subtitles.append(subtitle)
            else:
                # Diyalog yoksa sahne açıklamasından kısa bir özet
                # İlk cümleyi al
                sentences = prompt_text.split('.')
                if sentences:
                    first_sentence = sentences[0].strip()
                    if len(first_sentence) > 100:
                        first_sentence = first_sentence[:97] + "..."
                    subtitles.append(first_sentence)
                else:
                    subtitles.append("")
        
        return subtitles

    # ========================
    # VIDEO OLUŞTURMA
    # ========================
    
    def video_olustur(
        self,
        image_paths: List[str],
        user_input: str,
        frame_duration: float = 5.0,
        transition: str = "none",
        fade_duration: float = 0.5,
        music_file: Optional[str] = None,
        scene_audios: Optional[List[Optional[str]]] = None,
        audio_settings: Optional[Dict[str, Any]] = None,
        ken_burns_effect: str = "none",
        subtitles: Optional[List[str]] = None,
        subtitle_settings: Optional[Dict[str, Any]] = None,
        watermark_text: Optional[str] = None,
        watermark_settings: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Görsellerden video oluştur
        
        Args:
            image_paths: Görsel yolları
            user_input: Video adı için kullanıcı girdisi
            frame_duration: Kare süresi (saniye)
            transition: Geçiş efekti
            fade_duration: Geçiş süresi
            music_file: Arkaplan müzik dosyası (opsiyonel)
            scene_audios: Sahne ses dosyaları listesi (opsiyonel) - Audio ducking için
            audio_settings: Ses ayarları (opsiyonel)
            ken_burns_effect: Ken Burns efekti tipi
            subtitles: Sahne altyazıları listesi (opsiyonel)
            subtitle_settings: Altyazı ayarları (opsiyonel)
            watermark_text: Watermark metni (opsiyonel)
            watermark_settings: Watermark ayarları (opsiyonel)
            
        Returns:
            Video yolu veya None
        """
        if not MOVIEPY_AVAILABLE:
            logger.error("MoviePy yüklü değil, video oluşturulamıyor")
            return None
        
        if not image_paths:
            logger.warning("Video için görsel bulunamadı")
            return None
        
        # Varsayılan ses ayarları
        default_audio_settings = {
            "musicVolume": 0.7,
            "voiceVolume": 1.0,
            "duckingLevel": -10,
            "duckingFade": 0.5,
            "voiceDelay": 0.3,
            "musicFadeOut": 2.0
        }
        
        # Varsayılan altyazı ayarları
        default_subtitle_settings = {
            "enabled": True,
            "fontSize": 32,
            "fontColor": "white",
            "bgColor": "black",
            "bgOpacity": 0.7,
            "position": "bottom",  # bottom, top, center
            "margin": 50,
            "maxCharsPerLine": 50
        }
        
        # Audio settings'i varsayılanlarla birleştir
        if audio_settings:
            audio_cfg = {**default_audio_settings, **audio_settings}
        else:
            audio_cfg = default_audio_settings
        
        # Subtitle settings'i varsayılanlarla birleştir
        if subtitle_settings:
            sub_cfg = {**default_subtitle_settings, **subtitle_settings}
        else:
            sub_cfg = default_subtitle_settings
        
        has_voice = scene_audios and any(a for a in scene_audios)
        has_subtitles = subtitles and any(s for s in subtitles) and sub_cfg.get("enabled", True)
        music_info = f", müzik: {music_file}" if music_file else ""
        voice_info = ", sesli sahneler var" if has_voice else ""
        subtitle_info = ", altyazılı" if has_subtitles else ""
        logger.info(f"Video oluşturuluyor ({len(image_paths)} görsel, {frame_duration}s/kare, geçiş: {transition}{music_info}{voice_info}{subtitle_info})")
        
        try:
            from moviepy import AudioFileClip, CompositeAudioClip
            
            clips = []
            voice_clips = []  # Karakter sesleri
            voice_timing = []  # Ses zamanlamaları (start, end) - ducking için
            
            current_time = 0.0
            last_voice_end_time = 0.0  # Önceki sesin bitiş zamanı
            
            for i, img_rel_path in enumerate(image_paths):
                img_path = img_rel_path
                if not os.path.isabs(img_path):
                    img_path = os.path.join(os.getcwd(), img_rel_path)
                
                if not os.path.exists(img_path):
                    logger.warning(f"Görsel bulunamadı: {img_path}")
                    current_time += frame_duration
                    continue
                
                clip = ImageClip(img_path).with_duration(frame_duration)
                
                # Ken Burns efekti uygula
                if ken_burns_effect and ken_burns_effect != "none":
                    try:
                        clip = self._apply_ken_burns_effect(clip, ken_burns_effect)
                    except Exception as e:
                        logger.debug(f"Ken Burns efekti uygulanamadı: {e}")
                
                # Geçiş efekti uygula
                if transition == "fade" and i > 0:
                    try:
                        clip = clip.with_effects([CrossFadeIn(fade_duration)])
                    except Exception as e:
                        logger.debug(f"Fade efekti uygulanamadı: {e}")
                
                # Altyazı ekle (varsa)
                if has_subtitles and i < len(subtitles) and subtitles[i]:
                    try:
                        from moviepy import CompositeVideoClip
                        subtitle_text = subtitles[i]
                        video_size = clip.size
                        
                        subtitle_clip = self._create_subtitle_clip(
                            subtitle_text,
                            frame_duration,
                            video_size,
                            sub_cfg
                        )
                        
                        if subtitle_clip:
                            clip = CompositeVideoClip([clip, subtitle_clip])
                            logger.debug(f"Sahne {i+1} altyazı eklendi")
                    except Exception as e:
                        logger.warning(f"Sahne {i+1} altyazı eklenemedi: {e}")
                
                clips.append(clip)
                
                # Sahne sesini ekle (varsa)
                if scene_audios and i < len(scene_audios) and scene_audios[i]:
                    audio_rel_path = scene_audios[i]
                    audio_path = audio_rel_path
                    if not os.path.isabs(audio_path):
                        audio_path = os.path.join(os.getcwd(), audio_rel_path)
                    
                    if os.path.exists(audio_path):
                        try:
                            voice_audio = AudioFileClip(audio_path)
                            voice_delay = float(audio_cfg.get("voiceDelay", 0.3))
                            
                            # Önceki ses bitmeden yeni sesi başlatma!
                            # Sahne başlangıcı + gecikme
                            ideal_start = current_time + voice_delay
                            
                            # Eğer önceki ses hala devam ediyorsa, onun bitmesini bekle
                            # + 0.2 saniye boşluk bırak
                            if last_voice_end_time > ideal_start:
                                voice_start = last_voice_end_time + 0.2
                                logger.info(f"Sahne {i+1}: Ses üst üste binmemesi için geciktirildi ({ideal_start:.1f}s -> {voice_start:.1f}s)")
                            else:
                                voice_start = ideal_start
                            
                            voice_audio = voice_audio.with_start(voice_start)
                            
                            # Karakter ses seviyesini uygula
                            voice_volume = float(audio_cfg.get("voiceVolume", 1.0))
                            if voice_volume != 1.0:
                                voice_audio = voice_audio.with_volume_scaled(voice_volume)
                            
                            voice_clips.append(voice_audio)
                            
                            # Sesin bitiş zamanını kaydet
                            voice_end = voice_start + voice_audio.duration
                            last_voice_end_time = voice_end
                            
                            # Ducking için zamanlama kaydet
                            voice_timing.append({
                                "start": voice_start,
                                "end": voice_end,
                                "duration": voice_audio.duration
                            })
                            logger.debug(f"Sahne {i+1} sesi eklendi: {voice_start:.1f}s - {voice_end:.1f}s")
                        except Exception as e:
                            logger.warning(f"Sahne {i+1} ses dosyası yüklenemedi: {e}")
                
                current_time += frame_duration
            
            if not clips:
                logger.error("Video için geçerli görsel bulunamadı")
                return None
            
            # Video birleştir
            if transition == "fade" and len(clips) > 1:
                video = concatenate_videoclips(clips, method="compose")
            else:
                video = concatenate_videoclips(clips, method="compose")
            
            video_duration = video.duration
            
            # Watermark ekle (varsa)
            if watermark_text:
                try:
                    from moviepy import CompositeVideoClip
                    
                    default_watermark_settings = {
                        "fontSize": 20,
                        "fontColor": "white",
                        "opacity": 0.5,
                        "position": "bottom_right",
                        "margin": 20
                    }
                    
                    wm_cfg = {**default_watermark_settings, **(watermark_settings or {})}
                    
                    watermark_clip = self._create_watermark_clip(
                        watermark_text,
                        video_duration,
                        video.size,
                        wm_cfg
                    )
                    
                    if watermark_clip:
                        video = CompositeVideoClip([video, watermark_clip])
                        logger.info(f"Watermark eklendi: {watermark_text}")
                except Exception as e:
                    logger.warning(f"Watermark eklenemedi: {e}")
            
            final_audio = None
            
            # Müzik ekle (eğer belirtildiyse)
            if music_file:
                try:
                    music_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'music')
                    music_path = os.path.join(music_dir, music_file)
                    
                    if os.path.exists(music_path):
                        music_audio = AudioFileClip(music_path)
                        
                        # Video süresine göre müziği ayarla
                        if music_audio.duration > video_duration:
                            music_audio = music_audio.subclipped(0, video_duration)
                        elif music_audio.duration < video_duration:
                            from moviepy.audio.fx import AudioLoop
                            loops_needed = int(video_duration / music_audio.duration) + 1
                            music_audio = music_audio.with_effects([AudioLoop(nloops=loops_needed)])
                            music_audio = music_audio.subclipped(0, video_duration)
                        
                        # Audio Ducking uygula (karakter konuşurken müziği kıs)
                        if voice_timing:
                            logger.info(f"Audio ducking uygulanıyor: {len(voice_timing)} ses parçası")
                            
                            # audio_cfg'den ses ayarlarını al
                            ducking_level_db = float(audio_cfg.get("duckingLevel", -10))
                            fade_time = float(audio_cfg.get("duckingFade", 0.5))
                            music_volume_multiplier = float(audio_cfg.get("musicVolume", 0.7))
                            
                            # dB değerlerini çarpana çevir
                            # -10 dB = 10^(-10/20) ≈ 0.316
                            # 0 dB = 1.0
                            ducked_volume = 10 ** (ducking_level_db / 20)  # kullanıcı ayarından
                            normal_volume = music_volume_multiplier  # normal ses (kullanıcı ayarından)
                            
                            def volume_filter(get_frame, t):
                                """Zamana bağlı volume ayarlama - Audio Ducking (numpy vectorized)"""
                                import numpy as np
                                
                                frame = get_frame(t)
                                
                                # t scalar veya array olabilir
                                t_arr = np.atleast_1d(t)
                                volume_arr = np.ones_like(t_arr, dtype=float) * normal_volume
                                
                                for timing in voice_timing:
                                    start = timing["start"]
                                    end = timing["end"]
                                    
                                    # Fade in bölgesi (müzik kısılıyor)
                                    fade_in_start = start - fade_time
                                    fade_in_end = start
                                    
                                    # Fade out bölgesi (müzik yükseliyor)
                                    fade_out_start = end
                                    fade_out_end = end + fade_time
                                    
                                    # Ducked volume (normal volume * ducked ratio)
                                    actual_ducked = normal_volume * ducked_volume
                                    
                                    # Fade in mask (kısılıyor)
                                    fade_in_mask = (t_arr >= fade_in_start) & (t_arr < fade_in_end)
                                    if np.any(fade_in_mask):
                                        progress = (t_arr[fade_in_mask] - fade_in_start) / fade_time
                                        new_vol = normal_volume - progress * (normal_volume - actual_ducked)
                                        volume_arr[fade_in_mask] = np.minimum(volume_arr[fade_in_mask], new_vol)
                                    
                                    # Tam kısık bölge
                                    ducked_mask = (t_arr >= fade_in_end) & (t_arr < fade_out_start)
                                    volume_arr[ducked_mask] = np.minimum(volume_arr[ducked_mask], actual_ducked)
                                    
                                    # Fade out mask (yükseliyor)
                                    fade_out_mask = (t_arr >= fade_out_start) & (t_arr < fade_out_end)
                                    if np.any(fade_out_mask):
                                        progress = (t_arr[fade_out_mask] - fade_out_start) / fade_time
                                        new_vol = actual_ducked + progress * (normal_volume - actual_ducked)
                                        volume_arr[fade_out_mask] = np.minimum(volume_arr[fade_out_mask], new_vol)
                                
                                # Scalar ise tekrar scalar'a çevir
                                if np.isscalar(t):
                                    volume = float(volume_arr[0])
                                else:
                                    # frame shape'ine göre volume'u broadcast et
                                    volume = volume_arr.reshape(-1, 1) if len(frame.shape) > 1 else volume_arr
                                
                                return frame * volume
                            
                            # Filter uygula
                            music_audio = music_audio.transform(volume_filter, keep_duration=True)
                            logger.info(f"Audio ducking uygulandı: konuşmalarda müzik {ducking_level_db}dB")
                        else:
                            # Ducking yok, sadece müzik ses seviyesini ayarla
                            music_volume_multiplier = float(audio_cfg.get("musicVolume", 0.7))
                            if music_volume_multiplier != 1.0:
                                music_audio = music_audio.with_volume_scaled(music_volume_multiplier)
                        
                        # Sona doğru fade out ekle
                        from moviepy.audio.fx import AudioFadeOut
                        fade_out_duration = float(audio_cfg.get("musicFadeOut", 2.0))
                        if fade_out_duration > 0:
                            music_audio = music_audio.with_effects([AudioFadeOut(fade_out_duration)])
                        
                        final_audio = music_audio
                        logger.info(f"Müzik eklendi: {music_file}")
                    else:
                        logger.warning(f"Müzik dosyası bulunamadı: {music_path}")
                except Exception as e:
                    logger.warning(f"Müzik eklenirken hata: {e}")
            
            # Tüm sesleri birleştir
            if voice_clips or final_audio:
                all_audio_clips = []
                
                if final_audio:
                    all_audio_clips.append(final_audio)
                
                if voice_clips:
                    all_audio_clips.extend(voice_clips)
                    logger.info(f"{len(voice_clips)} karakter sesi eklendi")
                
                if len(all_audio_clips) == 1:
                    composite_audio = all_audio_clips[0]
                else:
                    composite_audio = CompositeAudioClip(all_audio_clips)
                
                video = video.with_audio(composite_audio)
            
            # Dosya adı
            timestamp = int(time.time())
            safe_name = sanitize_filename(user_input, Constants.SAFE_FILENAME_LENGTH)
            video_filename = f"{timestamp}_{safe_name}.mp4"
            video_path = os.path.join(self.output_dir, video_filename)
            
            # Video kaydet
            has_audio = music_file or voice_clips
            video.write_videofile(
                video_path,
                fps=config.VIDEO_FPS,
                codec=config.VIDEO_CODEC,
                audio_codec='aac' if has_audio else None,
                logger=None  # MoviePy loglarını sustur
            )
            
            # Klipleri temizle
            for clip in clips:
                clip.close()
            for vc in voice_clips:
                try:
                    vc.close()
                except:
                    pass
            video.close()
            
            logger.info(f"Video oluşturuldu: {video_filename}")
            api_stats.record_video()
            
            return f"static/output/{video_filename}"
            
        except Exception as e:
            logger.error(f"Video oluşturma hatası: {e}")
            return None

    # ========================
    # VİDEO (GEÇMİŞTEN)
    # ========================
    
    def create_video_from_history(
        self,
        history_timestamp: int,
        music_file: Optional[str] = None,
        audio_settings: Optional[Dict[str, Any]] = None,
        ken_burns_effect: str = "none",
        enable_subtitles: bool = False,
        subtitle_settings: Optional[Dict[str, Any]] = None,
        watermark_text: Optional[str] = None,
        watermark_settings: Optional[Dict[str, Any]] = None,
        selected_scenes: Optional[List[int]] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Geçmiş kaydından video oluştur
        
        Args:
            history_timestamp: Geçmiş kaydı timestamp'ı
            music_file: Arkaplan müzik dosyası (opsiyonel)
            audio_settings: Ses ayarları (opsiyonel)
                - musicVolume: Müzik ses seviyesi (0-1)
                - voiceVolume: Karakter ses seviyesi (0-1)
                - duckingLevel: Ducking miktarı (dB)
                - duckingFade: Fade süresi (saniye)
                - voiceDelay: Ses başlangıç gecikmesi (saniye)
                - musicFadeOut: Video sonu fade out süresi (saniye)
            enable_subtitles: Altyazı eklensin mi
            subtitle_settings: Altyazı ayarları (opsiyonel)
            selected_scenes: Sadece bu indekslerdeki sahneleri kullan (opsiyonel)
            
        Returns:
            (Video yolu, Hata mesajı)
        """
        music_info = f" (müzik: {music_file})" if music_file else ""
        subtitle_info = " (altyazılı)" if enable_subtitles else ""
        selection_info = f" ({len(selected_scenes)} sahne seçildi)" if selected_scenes else ""
        logger.info(f"Geçmişten video oluşturuluyor: {history_timestamp}{music_info}{subtitle_info}{selection_info}")
        
        try:
            history = self.get_history()
            entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not entry:
                return None, "Geçmiş kaydı bulunamadı."
            
            # Tüm sahneleri al
            all_images = entry.get("images", [])
            all_audios = entry.get("audios", [])
            all_prompts = entry.get("prompts_generated", [])
            
            # Seçili sahneleri filtrele
            images = []
            scene_audios = []
            prompts = []
            
            if selected_scenes and len(selected_scenes) > 0:
                # İndeksleri kontrol et ve geçerli olanları al
                valid_indices = [i for i in selected_scenes if 0 <= i < len(all_images)]
                if not valid_indices:
                    return None, "Geçersiz sahne seçimi."
                
                # Sırayı korumak için selected_scenes sırasına göre değil, orijinal sıraya göre alalım?
                # Kullanıcı sıralama değiştirmiş olabilir ama burada sadece "seçilenler" diyor.
                # Genelde seçilenler orijinal sırasıyla işlenmeli, yoksa kullanıcı custom sıralama yapmış olur.
                # Ancak kullanıcı UI'da sahnelerin yerini değiştirebiliyor. Eğer UI'daki sıraya göre indeks geliyorsa sorun yok.
                # Burada gelen indeksler, o anki history listesindeki indekslerdir.
                
                # İndeksleri küçükten büyüğe sıralayalım ki sahne akışı bozulmasın
                # (Kullanıcı isterse reorder endpoint'i ile zaten sırayı değiştirebiliyor)
                valid_indices.sort()
                
                for i in valid_indices:
                    images.append(all_images[i])
                    
                    # Audio varsa al, yoksa None
                    if i < len(all_audios):
                        scene_audios.append(all_audios[i])
                    else:
                        scene_audios.append(None)
                        
                    # Prompt varsa al
                    if i < len(all_prompts):
                        prompts.append(all_prompts[i])
            else:
                # Filtre yoksa hepsini al (None olmayanları)
                # Ancak orijinal kodda [img for img in images if img] vardı.
                # İndekslerin kaymaması için dikkatli olmalıyız.
                # Eğer audio ve prompt eşleşmesi önemliyse, boş olan image'ları atlamak indeksleri kaydırabilir.
                # `video_olustur` metodu muhtemelen non-None imagelar bekliyor.
                
                # Orijinal mantık: Sadece resmi olan sahneler
                for i, img in enumerate(all_images):
                    if img:
                        images.append(img)
                        if i < len(all_audios):
                            scene_audios.append(all_audios[i])
                        else:
                            scene_audios.append(None)
                        if i < len(all_prompts):
                            prompts.append(all_prompts[i])

            if not images:
                return None, "Görsel bulunamadı."
            
            user_input = entry.get("prompt", "Video")
            frame_duration = entry.get("frame_duration", config.DEFAULT_FRAME_DURATION)
            transition = entry.get("video_transition", "none")
            
            # Altyazıları hazırla (prompt'lardan diyalogları çıkar)
            subtitles = None
            if enable_subtitles:
                subtitles = self._extract_subtitles_from_prompts(prompts)
            
            video_path = self.video_olustur(
                images,
                user_input,
                frame_duration=frame_duration,
                transition=transition,
                music_file=music_file,
                scene_audios=scene_audios if scene_audios else None,
                audio_settings=audio_settings,
                ken_burns_effect=ken_burns_effect,
                subtitles=subtitles,
                subtitle_settings=subtitle_settings,
                watermark_text=watermark_text,
                watermark_settings=watermark_settings
            )
            
            if video_path:
                entry["video"] = video_path
                if music_file:
                    entry["video_music"] = music_file
                self._save_updated_history(history)
                return video_path, None
            
            return None, "Video oluşturulamadı."
            
        except Exception as e:
            logger.error(f"Geçmişten video hatası: {e}")
            return None, str(e)

    def merge_stories_to_video(
        self,
        timestamps: List[int],
        music_file: Optional[str] = None,
        video_name: str = "Birleşik Hikaye",
        audio_settings: Optional[Dict[str, Any]] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Birden fazla hikayeyi birleştirerek tek video oluştur
        
        Args:
            timestamps: Hikaye timestamp'ları (sıralı)
            music_file: Arkaplan müzik dosyası (opsiyonel)
            video_name: Video adı
            audio_settings: Ses ayarları (opsiyonel)
            
        Returns:
            (Video yolu, Hata mesajı)
        """
        logger.info(f"Hikayeler birleştiriliyor: {len(timestamps)} hikaye")
        
        try:
            history = self.get_history()
            
            # Tüm görselleri ve sesleri topla (sıralı)
            all_images = []
            all_audios = []
            
            for ts in timestamps:
                entry = next((item for item in history if item.get("timestamp") == ts), None)
                
                if not entry:
                    logger.warning(f"Hikaye bulunamadı: {ts}")
                    continue
                
                images = entry.get("images", [])
                audios = entry.get("audios", [])
                
                # Her görseli ve karşılık gelen sesi ekle
                for i, img in enumerate(images):
                    if img:  # None değilse
                        all_images.append(img)
                        # Karşılık gelen ses varsa ekle, yoksa None
                        audio = audios[i] if audios and i < len(audios) else None
                        all_audios.append(audio)
            
            if not all_images:
                return None, "Birleştirilecek görsel bulunamadı."
            
            logger.info(f"Toplam {len(all_images)} görsel, {sum(1 for a in all_audios if a)} ses birleştirilecek")
            
            # Video oluştur
            video_path = self.video_olustur(
                image_paths=all_images,
                user_input=video_name,
                frame_duration=config.DEFAULT_FRAME_DURATION,
                transition="fade",
                music_file=music_file,
                scene_audios=all_audios if any(all_audios) else None,
                audio_settings=audio_settings
            )
            
            if video_path:
                logger.info(f"Birleşik video oluşturuldu: {video_path}")
                return video_path, None
            
            return None, "Video oluşturulamadı."
            
        except Exception as e:
            logger.error(f"Hikaye birleştirme hatası: {e}")
            return None, str(e)
