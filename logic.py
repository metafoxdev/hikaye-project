"""
Hikaye Oluşturucu Ana Mantık Modülü
Tüm AI işlemleri ve iş mantığı burada yönetilir.
"""

import os
import json
import mimetypes
import struct
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Any, Generator, Tuple

from dotenv import load_dotenv
from google import genai
from google.genai import types

# Video işleme - lazy import for better startup
try:
    from moviepy import ImageClip, concatenate_videoclips, CompositeVideoClip
    from moviepy.video.fx import CrossFadeIn, CrossFadeOut
    MOVIEPY_AVAILABLE = True
except ImportError:
    MOVIEPY_AVAILABLE = False

# Yerel modüller
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

# .env dosyasını yükle
load_dotenv()

# Konfigürasyonu al
config = get_config()
config.init_directories()

# Logger'ı ayarla
logger = setup_logging(
    log_file=config.LOG_FILE,
    log_level=config.LOG_LEVEL
)

# Rate limiter
rate_limiter = RateLimiter(
    max_calls=config.RATE_LIMIT_PER_MINUTE,
    period=60
)


class StoryGeneratorError(Exception):
    """Hikaye oluşturucu için özel exception"""
    pass


class APIError(StoryGeneratorError):
    """API hatası"""
    pass


class ValidationError(StoryGeneratorError):
    """Doğrulama hatası"""
    pass


from core.settings_mixin import SettingsMixin
from core.character_mixin import CharacterMixin
from core.history_mixin import HistoryMixin
from core.api_mixin import APIMixin
from core.scenario_mixin import ScenarioMixin
from core.image_mixin import ImageMixin
from core.audio_mixin import AudioMixin
from core.video_mixin import VideoMixin
from core.story_flow_mixin import Story_flowMixin
from core.scene_mixin import SceneMixin
from core.story_branch_mixin import Story_branchMixin
from core.misc_mixin import MiscMixin

class StoryGenerator(SettingsMixin, CharacterMixin, HistoryMixin, APIMixin, ScenarioMixin, ImageMixin, AudioMixin, VideoMixin, Story_flowMixin, SceneMixin, Story_branchMixin, MiscMixin):
    """Ana Hikaye Oluşturucu Sınıfı

Bu sınıf tüm hikaye oluşturma işlemlerini yönetir:
- Senaryo oluşturma
- Görsel üretme
- Video montajı
- Karakter yönetimi"""
    def __init__(self):
        """StoryGenerator'ı başlat"""
        settings = self._load_settings()
        self.api_key = settings.get("api_key") or config.GEMINI_API_KEY
        
        if not self.api_key:
            logger.error("GEMINI_API_KEY bulunamadı!")
            # Raise etmiyoruz, çünkü kullanıcı ayarlardan girebilir.
        
        if self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
                logger.info("Gemini API client başarıyla oluşturuldu")
            except Exception as e:
                logger.error(f"Gemini client oluşturulamadı: {e}")
        else:
            self.client = None
        
        self.output_dir = config.OUTPUT_DIR
        os.makedirs(self.output_dir, exist_ok=True)
        
        logger.info("StoryGenerator başlatıldı")

