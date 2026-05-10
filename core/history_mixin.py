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


class HistoryMixin:

    # ========================
    # GEÇMİŞ YÖNETİMİ
    # ========================
    
    def get_history(self) -> List[Dict[str, Any]]:
        """Oluşturma geçmişini döndür"""
        return validate_json_file(config.HISTORY_FILE, [])
    
    def _save_to_history(self, entry: Dict[str, Any]) -> bool:
        """
        Geçmişe yeni kayıt ekle
        
        Args:
            entry: Yeni kayıt
            
        Returns:
            Başarılı mı?
        """
        history = self.get_history()
        
        # En başa ekle
        history.insert(0, entry)
        
        # Maksimum sayıyı aşma
        if len(history) > Constants.MAX_HISTORY_ITEMS:
            history = history[:Constants.MAX_HISTORY_ITEMS]
        
        return save_json_file(config.HISTORY_FILE, history)
    
    def _save_updated_history(self, history: List[Dict[str, Any]]) -> bool:
        """Güncellenmiş geçmişi kaydet"""
        return save_json_file(config.HISTORY_FILE, history)
    
    def delete_history_item(self, timestamp: int) -> bool:
        """
        Geçmiş kaydını sil
        
        Args:
            timestamp: Silinecek kaydın timestamp'ı
            
        Returns:
            Başarılı mı?
        """
        history = self.get_history()
        original_count = len(history)
        
        # İlgili dosyaları da sil
        entry = next((item for item in history if item.get("timestamp") == timestamp), None)
        if entry:
            # Görselleri sil
            for img_path in entry.get("images", []):
                full_path = os.path.join(os.getcwd(), img_path)
                if os.path.exists(full_path):
                    try:
                        os.remove(full_path)
                    except Exception as e:
                        logger.warning(f"Görsel silinemedi: {e}")
            
            # Videoyu sil
            video_path = entry.get("video")
            if video_path:
                full_path = os.path.join(os.getcwd(), video_path)
                if os.path.exists(full_path):
                    try:
                        os.remove(full_path)
                    except Exception as e:
                        logger.warning(f"Video silinemedi: {e}")
        
        history = [h for h in history if h.get("timestamp") != timestamp]
        
        if len(history) < original_count:
            success = self._save_updated_history(history)
            if success:
                logger.info(f"Geçmiş kaydı silindi: {timestamp}")
            return success
        
        return False
    
    def clear_history(self) -> bool:
        """Tüm geçmişi temizle"""
        logger.warning("Tüm geçmiş temizleniyor!")
        return save_json_file(config.HISTORY_FILE, [])
