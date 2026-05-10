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


class Story_branchMixin:

    # ========================
    # HİKAYE DALLANMASI
    # ========================
    
    def create_story_branch(
        self,
        history_timestamp: int,
        branch_point: int,
        branch_prompt: str,
        branch_name: str = "Alternatif Son",
        scene_count: int = 5
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Mevcut hikayeden alternatif bir dal oluştur
        
        Args:
            history_timestamp: Ana hikaye timestamp'ı
            branch_point: Dallanma noktası (sahne indeksi, bu sahneden sonra dallanır)
            branch_prompt: Alternatif hikaye yönü
            branch_name: Dal adı
            scene_count: Yeni dal için sahne sayısı
            
        Returns:
            (Yeni hikaye entry, Hata mesajı)
        """
        logger.info(f"Hikaye dallanması oluşturuluyor: sahne {branch_point + 1}'den sonra")
        
        try:
            history = self.get_history()
            parent_entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not parent_entry:
                return None, "Ana hikaye bulunamadı."
            
            parent_images = parent_entry.get("images", [])
            parent_prompts = parent_entry.get("prompts_generated", [])
            
            if branch_point < 0 or branch_point >= len(parent_images):
                return None, "Geçersiz dallanma noktası."
            
            # Dallanma noktasına kadar olan görselleri ve promptları kopyala
            branch_images = parent_images[:branch_point + 1].copy()
            branch_prompts = parent_prompts[:branch_point + 1].copy()
            
            # Yeni sahneler için prompt oluştur
            original_prompt = parent_entry.get("prompt", "")
            branch_context = f"""
            ÖNCEKİ HİKAYE: {original_prompt}
            
            MEVCUT DURUM (Sahne {branch_point + 1}'e kadar):
            {' '.join([get_prompt_from_scene(p)[:100] for p in branch_prompts[-3:]])}
            
            YENİ YÖN: {branch_prompt}
            
            Bu noktadan itibaren hikayeyi yeni yöne çevir.
            """
            
            # Yeni sahneleri oluştur
            new_prompts = self.senaryo_olustur(
                branch_context,
                scene_count=scene_count,
                dialogue_style=parent_entry.get("dialogue_style", "short"),
                art_style=parent_entry.get("art_style", "comic"),
                mood_style=parent_entry.get("mood_style", "dynamic"),
                camera_style=parent_entry.get("camera_style", "balanced")
            )
            
            if not new_prompts:
                return None, "Alternatif senaryo oluşturulamadı."
            
            # Yeni görselleri oluştur
            aspect_ratio = parent_entry.get("aspect_ratio", "9:16")
            image_size = parent_entry.get("image_size", "2K")
            
            if config.PARALLEL_IMAGE_GENERATION:
                new_images = self.generate_images_parallel(
                    new_prompts,
                    f"{branch_name}",
                    aspect_ratio=aspect_ratio,
                    image_size=image_size
                )
            else:
                new_images = []
                for i, prompt in enumerate(new_prompts):
                    img_path = self.resim_uret(
                        prompt,
                        branch_point + 1 + i,
                        branch_name,
                        aspect_ratio=aspect_ratio,
                        image_size=image_size
                    )
                    new_images.append(img_path)
            
            # Tüm görselleri ve promptları birleştir
            branch_images.extend(new_images)
            branch_prompts.extend(new_prompts)
            
            # Yeni hikaye kaydı oluştur
            branch_entry = {
                "timestamp": int(time.time()),
                "prompt": f"{branch_name}: {branch_prompt}",
                "parent_timestamp": history_timestamp,
                "branch_point": branch_point,
                "branch_name": branch_name,
                "is_branch": True,
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
                "scene_count": len(branch_images),
                "art_style": parent_entry.get("art_style", "comic"),
                "mood_style": parent_entry.get("mood_style", "dynamic"),
                "camera_style": parent_entry.get("camera_style", "balanced"),
                "dialogue_style": parent_entry.get("dialogue_style", "short"),
                "prompts_generated": branch_prompts,
                "images": branch_images,
                "audios": [None] * len(branch_images),
                "video": None,
                "frame_duration": parent_entry.get("frame_duration", 5.0),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            
            # Ana hikayeye dal referansı ekle
            if "branches" not in parent_entry:
                parent_entry["branches"] = []
            parent_entry["branches"].append({
                "timestamp": branch_entry["timestamp"],
                "name": branch_name,
                "branch_point": branch_point
            })
            
            # Kaydet
            self._save_to_history(branch_entry)
            self._save_updated_history(history)
            
            logger.info(f"Hikaye dalı oluşturuldu: {branch_name} ({len(new_images)} yeni sahne)")
            return branch_entry, None
            
        except Exception as e:
            logger.error(f"Hikaye dallanması hatası: {e}")
            return None, str(e)
    
    def get_story_branches(self, history_timestamp: int) -> List[Dict[str, Any]]:
        """
        Bir hikayenin tüm dallarını getir
        
        Args:
            history_timestamp: Ana hikaye timestamp'ı
            
        Returns:
            Dal listesi
        """
        history = self.get_history()
        
        # Ana hikayeyi bul
        parent_entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
        
        if not parent_entry:
            return []
        
        branches = []
        
        # Kayıtlı dalları getir
        branch_refs = parent_entry.get("branches", [])
        for ref in branch_refs:
            branch = next((item for item in history if item.get("timestamp") == ref.get("timestamp")), None)
            if branch:
                branches.append({
                    "timestamp": branch["timestamp"],
                    "name": branch.get("branch_name", "Alternatif"),
                    "branch_point": branch.get("branch_point", 0),
                    "scene_count": len(branch.get("images", [])),
                    "created_at": branch.get("created_at", ""),
                    "first_image": next((img for img in branch.get("images", []) if img), None)
                })
        
        return branches

    # ========================
    # HİKAYE DEVAMI
    # ========================
    
    def continue_story(
        self,
        history_timestamp: int,
        continuation_prompt: str = "",
        scene_count: int = 5
    ) -> Tuple[Optional[List[str]], Optional[str]]:
        """
        Mevcut hikayeye devam et - karakter tutarlılığını koruyarak
        
        Args:
            history_timestamp: Geçmiş kaydı timestamp'ı
            continuation_prompt: Devam için konu (boş bırakılırsa otomatik devam)
            scene_count: Eklenecek sahne sayısı
            
        Returns:
            (Yeni görsel yolları, Hata mesajı)
        """
        logger.info(f"Hikaye devam ettiriliyor...")
        
        try:
            history = self.get_history()
            entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not entry:
                return None, "Geçmiş kaydı bulunamadı."
            
            original_prompt = entry.get("prompt", "")
            existing_prompts = entry.get("prompts_generated", [])
            art_style = entry.get("art_style", "comic")
            mood_style = entry.get("mood_style", "dynamic")
            camera_style = entry.get("camera_style", "balanced")
            dialogue_style = entry.get("dialogue_style", "short")
            
            if not existing_prompts:
                return None, "Devam edilecek sahne bulunamadı."
            
            # ========================
            # KARAKTER ANALİZİ
            # ========================
            
            # Son sahnelerden karakter bilgilerini çıkar
            last_scenes = existing_prompts[-5:] if len(existing_prompts) >= 5 else existing_prompts
            last_scene_texts = [get_prompt_from_scene(p) for p in last_scenes]
            
            # Karakterleri analiz et
            character_analysis_prompt = f"""
            Aşağıdaki sahne açıklamalarından TÜM karakterlerin detaylı fiziksel özelliklerini çıkar.
            
            SAHNELER:
            {chr(10).join([f"Sahne {i+1}: {text}" for i, text in enumerate(last_scene_texts)])}
            
            Her karakter için şunları belirle:
            - İsim veya tanımlayıcı (örn: "ana karakter", "genç kız", "yaşlı adam")
            - Saç rengi ve stili
            - Göz rengi
            - Ten rengi
            - Yaş aralığı
            - Kıyafet detayları
            - Ayırt edici özellikler (yara izi, dövme, aksesuar vb.)
            
            JSON formatında döndür:
            {{
                "characters": [
                    {{
                        "identifier": "karakter adı/tanımı",
                        "hair": "saç detayı",
                        "eyes": "göz rengi",
                        "skin": "ten rengi",
                        "age": "yaş aralığı",
                        "clothing": "kıyafet",
                        "distinctive_features": "ayırt edici özellikler"
                    }}
                ],
                "setting": "mevcut mekan/ortam",
                "time_of_day": "gündüz/gece/akşam",
                "mood": "sahne atmosferi"
            }}
            """
            
            character_response = self._make_text_request(character_analysis_prompt, json_response=True)
            character_data = extract_json_from_response(character_response)
            
            # character_data bir liste olabilir, dict olmalı
            if not character_data or not isinstance(character_data, dict):
                character_data = {"characters": [], "setting": "", "time_of_day": "", "mood": ""}
            
            # Karakter profili oluştur
            character_profiles = ""
            if character_data.get("characters"):
                character_profiles = "KARAKTERLERİN FİZİKSEL ÖZELLİKLERİ (MUTLAKA AYNI KALMALI):\n"
                for char in character_data["characters"]:
                    character_profiles += f"""
                    - {char.get('identifier', 'Karakter')}:
                      Saç: {char.get('hair', 'belirtilmemiş')}
                      Gözler: {char.get('eyes', 'belirtilmemiş')}
                      Ten: {char.get('skin', 'belirtilmemiş')}
                      Yaş: {char.get('age', 'belirtilmemiş')}
                      Kıyafet: {char.get('clothing', 'belirtilmemiş')}
                      Özellikler: {char.get('distinctive_features', 'yok')}
                    """
            
            # ========================
            # DEVAM SENARYOSU OLUŞTUR
            # ========================
            
            # Son sahnenin tam metnini al
            last_scene_full = get_prompt_from_scene(existing_prompts[-1])
            second_last_scene = get_prompt_from_scene(existing_prompts[-2]) if len(existing_prompts) > 1 else ""
            
            # Devam promptu
            if continuation_prompt.strip():
                continuation_direction = f"YENİ YÖN/OLAY: {continuation_prompt}"
            else:
                continuation_direction = "Hikayeyi doğal akışında devam ettir. Son sahnedeki olayların mantıksal sonuçlarını göster."
            
            summary_prompt = f"""
            SEN BİR HİKAYE YAZARISIN. Mevcut hikayenin DEVAMINI yazacaksın.
            
            ========================
            ANA HİKAYE KONUSU:
            {original_prompt}
            
            ========================
            {character_profiles}
            
            ========================
            MEVCUT ORTAM:
            Mekan: {character_data.get('setting', 'belirtilmemiş')}
            Zaman: {character_data.get('time_of_day', 'belirtilmemiş')}
            Atmosfer: {character_data.get('mood', 'belirtilmemiş')}
            
            ========================
            SON İKİ SAHNE (BURADAN DEVAM EDECEK):
            
            Sahne {len(existing_prompts) - 1}: {second_last_scene}
            
            Sahne {len(existing_prompts)} (EN SON): {last_scene_full}
            
            ========================
            {continuation_direction}
            
            ========================
            ÖNEMLİ KURALLAR:
            1. KARAKTERLERİN FİZİKSEL ÖZELLİKLERİ KESİNLİKLE DEĞİŞMEMELİ!
               - Aynı saç rengi ve stili
               - Aynı göz rengi
               - Aynı ten rengi
               - Aynı yaş görünümü
               - Kıyafetler mantıklı şekilde değişebilir ama karakter tanınabilir olmalı
            
            2. HİKAYE SÜREKLİLİĞİ:
               - Son sahnedeki olayların doğrudan devamı olmalı
               - Mekan değişikliği varsa geçiş mantıklı olmalı
               - Karakterlerin duygu durumu tutarlı olmalı
            
            3. GÖRSEL PROMPT FORMATI:
               Her sahne için detaylı görsel açıklama yaz:
               - Karakterlerin TAM fiziksel tanımı (her sahnede tekrarla!)
               - Mekan detayları
               - Işık ve atmosfer
               - Aksiyon/poz
               - Kamera açısı
            
            ========================
            ÇIKTI: {scene_count} yeni sahne için JSON array döndür.
            Her prompt en az 100 kelime olmalı ve karakter özelliklerini içermeli.
            
            Örnek format:
            [
                "Sahne açıklaması... [karakter adı]: [tam fiziksel özellikler tekrar edilmeli]...",
                "..."
            ]
            """
            
            response_text = self._make_text_request(summary_prompt, json_response=True)
            new_prompts = extract_json_from_response(response_text)
            
            if not new_prompts or not isinstance(new_prompts, list):
                return None, "Devam senaryosu oluşturulamadı."
            
            # ========================
            # KARAKTER TUTARLILIĞI İÇİN PROMPT ZENGİNLEŞTİRME
            # ========================
            
            enriched_prompts = []
            for prompt in new_prompts[:scene_count]:
                # Her prompt'a karakter özelliklerini ekle
                if character_profiles and character_data.get("characters"):
                    char_reminder = "\n\n[KARAKTER TUTARLILIĞI - "
                    for char in character_data["characters"][:3]:  # İlk 3 karakter
                        char_reminder += f"{char.get('identifier', '')}: {char.get('hair', '')} saç, {char.get('eyes', '')} gözler, {char.get('skin', '')} ten, {char.get('age', '')} yaş. "
                    char_reminder += "]"
                    
                    # Prompt'un sonuna karakter hatırlatması ekle
                    enriched_prompt = f"{prompt}{char_reminder}"
                else:
                    enriched_prompt = prompt
                
                enriched_prompts.append(enriched_prompt)
            
            # ========================
            # GÖRSELLER OLUŞTUR
            # ========================
            
            bolum_adi = entry.get("prompt", "Bolum")
            aspect_ratio = entry.get("aspect_ratio", "9:16")
            image_size = entry.get("image_size", "2K")
            
            # Son görseli referans olarak kullan (varsa)
            last_image_path = None
            for img in reversed(entry.get("images", [])):
                if img and os.path.exists(img if os.path.isabs(img) else os.path.join(os.getcwd(), img)):
                    last_image_path = img if os.path.isabs(img) else os.path.join(os.getcwd(), img)
                    break
            
            reference_images = [last_image_path] if last_image_path else None
            
            if config.PARALLEL_IMAGE_GENERATION:
                new_images = self.generate_images_parallel(
                    enriched_prompts,
                    bolum_adi,
                    aspect_ratio=aspect_ratio,
                    image_size=image_size
                )
            else:
                new_images = []
                for i, prompt in enumerate(enriched_prompts):
                    img_path = self.resim_uret(
                        prompt,
                        len(existing_prompts) + i,
                        bolum_adi,
                        aspect_ratio,
                        image_size
                    )
                    new_images.append(img_path)
            
            # Audios listesini de genişlet
            if "audios" not in entry:
                entry["audios"] = [None] * len(existing_prompts)
            
            # ========================
            # SESLENDİRME OLUŞTUR
            # ========================
            new_audios = []
            
            for i, prompt in enumerate(enriched_prompts):
                scene_index = len(existing_prompts) + i
                try:
                    # Ses oluştur (fonksiyon içinde diyalog çıkarılıyor)
                    audio_path = self.generate_scene_audio(
                        prompt,
                        scene_index,
                        bolum_adi
                    )
                    new_audios.append(audio_path)
                    if audio_path:
                        logger.info(f"Devam sahnesi {scene_index + 1} seslendirmesi oluşturuldu")
                except Exception as e:
                    logger.warning(f"Devam sahnesi {scene_index + 1} seslendirme hatası: {e}")
                    new_audios.append(None)
            
            # Mevcut kayda ekle
            entry["prompts_generated"].extend(enriched_prompts)
            entry["images"].extend(new_images)
            entry["audios"].extend(new_audios)
            entry["scene_count"] = len(entry["prompts_generated"])
            entry["video"] = None  # Video yeniden oluşturulmalı
            
            # Karakter verilerini kaydet (gelecek devamlar için)
            if character_data.get("characters"):
                entry["character_profiles"] = character_data
            
            self._save_updated_history(history)
            
            valid_images = [img for img in new_images if img]
            valid_audios = [a for a in new_audios if a]
            logger.info(f"Hikaye devamı eklendi: {len(valid_images)} yeni sahne, {len(valid_audios)} seslendirme")
            return valid_images, None
            
        except Exception as e:
            logger.error(f"Hikaye devam hatası: {e}")
            return None, str(e)
