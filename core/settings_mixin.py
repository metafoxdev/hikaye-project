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


class SettingsMixin:

    # ========================
    # AYAR YÖNETİMİ
    # ========================
    
    def _load_settings(self) -> Dict[str, Any]:
        """Ayarları yükle"""
        return validate_json_file(config.SETTINGS_FILE, {})
    
    def get_settings(self) -> Dict[str, Any]:
        """Ayarları döndür"""
        return self._load_settings()
    
    def update_settings(self, new_settings: Dict[str, Any]) -> bool:
        """
        Ayarları güncelle
        
        Args:
            new_settings: Yeni ayarlar
            
        Returns:
            Başarılı mı?
        """
        try:
            current = self._load_settings()
            
            # API Key değiştiyse veya temizlendiyse client'ı güncelle
            if "api_key" in new_settings:
                new_key = new_settings.get("api_key")
                
                # Eğer kullanıcı boş string gönderdiyse, json'dan sil
                if new_key == "":
                    if "api_key" in current:
                        del current["api_key"]
                        del new_settings["api_key"] # current.update'de tekrar eklenmemesi için
                    self.api_key = config.GEMINI_API_KEY
                else:
                    self.api_key = new_key
                    
                if self.api_key:
                    self.client = genai.Client(api_key=self.api_key)
                    logger.info("Gemini API client yeni anahtar ile güncellendi")
                else:
                    self.client = None
                    
            current.update(new_settings)
            success = save_json_file(config.SETTINGS_FILE, current)
            if success:
                logger.info("Ayarlar güncellendi")
            return success
        except Exception as e:
            logger.error(f"Ayar güncelleme hatası: {e}")
            return False
