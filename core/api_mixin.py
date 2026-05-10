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


class APIMixin:

    # ========================
    # API İSTEKLERİ
    # ========================
    
    def _check_rate_limit(self):
        """Rate limit kontrolü"""
        if not rate_limiter.is_allowed():
            wait_time = rate_limiter.wait_time()
            logger.warning(f"Rate limit aşıldı, {wait_time:.1f}s bekleniyor")
            time.sleep(wait_time)
    
    @retry_with_backoff(max_retries=3, base_delay=2.0)
    def _make_text_request(
        self, 
        prompt: str, 
        json_response: bool = False
    ) -> str:
        """
        Gemini text API'ye istek yap
        
        Args:
            prompt: İstek promptu
            json_response: JSON yanıt mı bekleniyor?
            
        Returns:
            API yanıtı
        """
        self._check_rate_limit()
        
        if not self.client:
            raise APIError("Lütfen ayarlardan geçerli bir Gemini API anahtarı girin.")
        
        config_obj = types.GenerateContentConfig()
        if json_response:
            config_obj = types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        
        response = self.client.models.generate_content(
            model=Config.TEXT_MODEL,
            contents=prompt,
            config=config_obj
        )
        
        return response.text.strip()
    
    @retry_with_backoff(max_retries=3, base_delay=2.0)
    def _make_image_request(
        self,
        prompt: str,
        aspect_ratio: str = "9:16",
        image_size: str = "2K",
        reference_images: Optional[List[str]] = None
    ) -> Optional[bytes]:
        """
        Gemini image API'ye istek yap
        
        Args:
            prompt: Görsel promptu
            aspect_ratio: En-boy oranı
            image_size: Çözünürlük
            reference_images: Referans görsel yolları
            
        Returns:
            Görsel byte verisi veya None
        """
        self._check_rate_limit()
        
        if not self.client:
            raise APIError("Lütfen ayarlardan geçerli bir Gemini API anahtarı girin.")
        
        parts = [types.Part.from_text(text=prompt)]
        
        # Referans görselleri ekle
        if reference_images:
            for ref_path in reference_images:
                if ref_path and os.path.exists(ref_path):
                    try:
                        with open(ref_path, "rb") as img_file:
                            img_data = img_file.read()
                            mime_type = mimetypes.guess_type(ref_path)[0] or "image/png"
                            parts.append(types.Part.from_bytes(data=img_data, mime_type=mime_type))
                            logger.debug(f"Referans görsel eklendi: {ref_path}")
                    except Exception as e:
                        logger.warning(f"Referans görsel okunamadı: {e}")
        
        contents = [types.Content(role="user", parts=parts)]
        
        generate_config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio=aspect_ratio,
                image_size=image_size
            )
        )
        
        response = self.client.models.generate_content(
            model=Config.IMAGE_MODEL,
            contents=contents,
            config=generate_config
        )
        
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.data:
                    return part.inline_data.data
        
        return None
