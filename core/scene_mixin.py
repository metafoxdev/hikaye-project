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


class SceneMixin:

    # ========================
    # SAHNE YENİLEME
    # ========================
    
    def regenerate_scene(
        self,
        history_timestamp: int,
        scene_index: int
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Belirli bir sahneyi yeniden oluştur
        
        Args:
            history_timestamp: Geçmiş kaydı timestamp'ı
            scene_index: Sahne indeksi
            
        Returns:
            (Yeni görsel yolu, Hata mesajı)
        """
        logger.info(f"Sahne {scene_index + 1} yeniden oluşturuluyor...")
        
        try:
            history = self.get_history()
            entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not entry:
                return None, "Geçmiş kaydı bulunamadı."
            
            prompts = entry.get("prompts_generated", [])
            if scene_index < 0 or scene_index >= len(prompts):
                return None, "Geçersiz sahne indeksi."
            
            prompt = prompts[scene_index]
            bolum_adi = entry.get("prompt", "Bolum")
            aspect_ratio = entry.get("aspect_ratio", "9:16")
            image_size = entry.get("image_size", "2K")
            
            new_img_path = self.resim_uret(prompt, scene_index, bolum_adi, aspect_ratio, image_size)
            
            if new_img_path:
                entry["images"][scene_index] = new_img_path
                self._save_updated_history(history)
                return new_img_path, None
            
            return None, "Görsel oluşturulamadı."
            
        except Exception as e:
            logger.error(f"Sahne yenileme hatası: {e}")
            return None, str(e)

    # ========================
    # SAHNE YÖNETİMİ (YENİ)
    # ========================
    
    def delete_scene(
        self,
        history_timestamp: int,
        scene_index: int
    ) -> Tuple[bool, Optional[str]]:
        """
        Belirli bir sahneyi sil
        
        Args:
            history_timestamp: Geçmiş kaydı timestamp'ı
            scene_index: Sahne indeksi
            
        Returns:
            (Başarılı mı?, Hata mesajı)
        """
        logger.info(f"Sahne {scene_index + 1} siliniyor...")
        
        try:
            history = self.get_history()
            entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not entry:
                return False, "Geçmiş kaydı bulunamadı."
            
            prompts = entry.get("prompts_generated", [])
            images = entry.get("images", [])
            
            if scene_index < 0 or scene_index >= len(prompts):
                return False, "Geçersiz sahne indeksi."
            
            # En az 2 sahne kalmalı
            if len(prompts) <= 1:
                return False, "En az bir sahne kalmalıdır."
            
            # Görseli sil
            if scene_index < len(images) and images[scene_index]:
                img_path = os.path.join(os.getcwd(), images[scene_index])
                if os.path.exists(img_path):
                    try:
                        os.remove(img_path)
                    except Exception as e:
                        logger.warning(f"Görsel silinemedi: {e}")
            
            # Listelerden çıkar
            prompts.pop(scene_index)
            if scene_index < len(images):
                images.pop(scene_index)
            
            entry["prompts_generated"] = prompts
            entry["images"] = images
            entry["scene_count"] = len(prompts)
            
            self._save_updated_history(history)
            logger.info(f"Sahne {scene_index + 1} silindi")
            return True, None
            
        except Exception as e:
            logger.error(f"Sahne silme hatası: {e}")
            return False, str(e)
    
    def add_scene(
        self,
        history_timestamp: int,
        after_index: int,
        prompt: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Belirli bir konumdan sonra yeni sahne ekle
        
        Args:
            history_timestamp: Geçmiş kaydı timestamp'ı
            after_index: Ardından ekleneceği sahne indeksi
            prompt: Yeni sahne promptu
            
        Returns:
            (Yeni görsel yolu, Hata mesajı)
        """
        logger.info(f"Sahne {after_index + 2} ekleniyor...")
        
        try:
            history = self.get_history()
            entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not entry:
                return None, "Geçmiş kaydı bulunamadı."
            
            prompts = entry.get("prompts_generated", [])
            images = entry.get("images", [])
            
            if after_index < -1 or after_index >= len(prompts):
                return None, "Geçersiz indeks."
            
            # Prompt'u ekle
            new_index = after_index + 1
            clean_prompt = sanitize_input(prompt)
            prompts.insert(new_index, clean_prompt)
            
            # Görsel oluştur
            bolum_adi = entry.get("prompt", "Bolum")
            aspect_ratio = entry.get("aspect_ratio", "9:16")
            image_size = entry.get("image_size", "2K")
            
            new_img_path = self.resim_uret(
                clean_prompt,
                new_index,
                bolum_adi,
                aspect_ratio,
                image_size
            )
            
            if new_img_path:
                images.insert(new_index, new_img_path)
                entry["prompts_generated"] = prompts
                entry["images"] = images
                entry["scene_count"] = len(prompts)
                
                self._save_updated_history(history)
                return new_img_path, None
            
            return None, "Görsel oluşturulamadı."
            
        except Exception as e:
            logger.error(f"Sahne ekleme hatası: {e}")
            return None, str(e)
    
    def reorder_scenes(
        self,
        history_timestamp: int,
        new_order: List[int]
    ) -> Tuple[bool, Optional[str]]:
        """
        Sahnelerin sırasını değiştir
        
        Args:
            history_timestamp: Geçmiş kaydı timestamp'ı
            new_order: Yeni sıralama (indeks listesi)
            
        Returns:
            (Başarılı mı?, Hata mesajı)
        """
        logger.info(f"Sahne sırası değiştiriliyor: {new_order}")
        
        try:
            history = self.get_history()
            entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not entry:
                return False, "Geçmiş kaydı bulunamadı."
            
            prompts = entry.get("prompts_generated", [])
            images = entry.get("images", [])
            
            # Validate new_order
            if sorted(new_order) != list(range(len(prompts))):
                return False, "Geçersiz sıralama."
            
            # Yeniden sırala
            entry["prompts_generated"] = [prompts[i] for i in new_order]
            entry["images"] = [images[i] if i < len(images) else None for i in new_order]
            
            self._save_updated_history(history)
            logger.info("Sahne sırası güncellendi")
            return True, None
            
        except Exception as e:
            logger.error(f"Sıralama hatası: {e}")
            return False, str(e)
