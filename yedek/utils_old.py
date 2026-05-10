"""
Yardımcı Fonksiyonlar ve Utility Modülü
"""

import os
import re
import json
import logging
import time
import functools
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable
from logging.handlers import RotatingFileHandler


def setup_logging(
    log_file: str = 'logs/app.log',
    log_level: str = 'INFO',
    log_format: str = '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5
) -> logging.Logger:
    """
    Logging sistemini yapılandır.
    
    Args:
        log_file: Log dosyasının yolu
        log_level: Log seviyesi (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_format: Log formatı
        max_bytes: Maksimum dosya boyutu
        backup_count: Yedek dosya sayısı
    
    Returns:
        Yapılandırılmış logger nesnesi
    """
    # Log dizinini oluştur
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    
    # Logger oluştur
    logger = logging.getLogger('hikaye_resimleyici')
    logger.setLevel(getattr(logging, log_level.upper()))
    
    # Mevcut handler'ları temizle
    logger.handlers.clear()
    
    # Dosya handler
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    file_handler.setFormatter(logging.Formatter(log_format))
    logger.addHandler(file_handler)
    
    # Konsol handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(log_format))
    logger.addHandler(console_handler)
    
    return logger


def sanitize_filename(filename: str, max_length: int = 20) -> str:
    """
    Dosya adını güvenli hale getir.
    
    Args:
        filename: Orijinal dosya adı
        max_length: Maksimum karakter sayısı
    
    Returns:
        Güvenli dosya adı
    """
    # Sadece alfanumerik, boşluk, alt çizgi ve tire karakterlerini tut
    safe = "".join(c for c in filename if c.isalnum() or c in (' ', '_', '-'))
    # Boşlukları alt çizgiye çevir
    safe = safe.replace(' ', '_')
    # Maksimum uzunluğa kes
    return safe[:max_length]


def sanitize_input(text: str, max_length: int = 5000) -> str:
    """
    Kullanıcı girdisini temizle ve XSS'e karşı koru.
    
    Args:
        text: Orijinal metin
        max_length: Maksimum karakter sayısı
    
    Returns:
        Temizlenmiş metin
    """
    if not text:
        return ""
    
    # HTML etiketlerini kaldır
    text = re.sub(r'<[^>]+>', '', text)
    
    # Script injection'ı temizle
    text = re.sub(r'javascript:', '', text, flags=re.IGNORECASE)
    text = re.sub(r'on\w+\s*=', '', text, flags=re.IGNORECASE)
    
    # Maksimum uzunluğa kes
    return text[:max_length].strip()


def validate_json_file(file_path: str, default_content: Any = None) -> Any:
    """
    JSON dosyasını oku ve doğrula. Hata durumunda varsayılan değeri döndür.
    
    Args:
        file_path: JSON dosya yolu
        default_content: Varsayılan içerik
    
    Returns:
        JSON içeriği veya varsayılan değer
    """
    try:
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger = logging.getLogger('hikaye_resimleyici')
        logger.error(f"JSON okuma hatası ({file_path}): {e}")
    
    return default_content if default_content is not None else {}


def save_json_file(file_path: str, content: Any, create_backup: bool = True) -> bool:
    """
    JSON dosyasına kaydet (yedekleme ile).
    
    Args:
        file_path: JSON dosya yolu
        content: Kaydedilecek içerik
        create_backup: Yedekleme oluştur
    
    Returns:
        Başarılı mı?
    """
    logger = logging.getLogger('hikaye_resimleyici')
    
    try:
        # Yedekleme oluştur
        if create_backup and os.path.exists(file_path):
            backup_path = f"{file_path}.backup"
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    backup_content = f.read()
                with open(backup_path, 'w', encoding='utf-8') as f:
                    f.write(backup_content)
            except Exception as e:
                logger.warning(f"Yedekleme oluşturulamadı: {e}")
        
        # Ana dosyaya kaydet
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(content, f, indent=4, ensure_ascii=False)
        
        return True
    except Exception as e:
        logger.error(f"JSON kaydetme hatası ({file_path}): {e}")
        return False


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exceptions: tuple = (Exception,)
) -> Callable:
    """
    Exponential backoff ile retry dekoratörü.
    
    Args:
        max_retries: Maksimum deneme sayısı
        base_delay: Başlangıç bekleme süresi
        max_delay: Maksimum bekleme süresi
        exceptions: Yakalanacak exception türleri
    
    Returns:
        Dekoratör fonksiyonu
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger = logging.getLogger('hikaye_resimleyici')
            last_exception = None
            
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt + 1 < max_retries:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        logger.warning(
                            f"{func.__name__} başarısız (deneme {attempt + 1}/{max_retries}). "
                            f"{delay:.1f}s sonra tekrar denenecek. Hata: {e}"
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            f"{func.__name__} tüm denemeler başarısız. Son hata: {e}"
                        )
            
            raise last_exception
        return wrapper
    return decorator


def format_timestamp(timestamp: int) -> str:
    """
    Unix timestamp'ı okunabilir formata çevir.
    
    Args:
        timestamp: Unix timestamp
    
    Returns:
        Formatlanmış tarih string'i
    """
    try:
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return "Bilinmeyen tarih"


def calculate_video_duration(scene_count: int, frame_duration: float) -> float:
    """
    Toplam video süresini hesapla.
    
    Args:
        scene_count: Sahne sayısı
        frame_duration: Kare başına süre (saniye)
    
    Returns:
        Toplam süre (saniye)
    """
    return scene_count * frame_duration


def get_file_size_mb(file_path: str) -> float:
    """
    Dosya boyutunu MB olarak al.
    
    Args:
        file_path: Dosya yolu
    
    Returns:
        Boyut (MB)
    """
    try:
        size_bytes = os.path.getsize(file_path)
        return size_bytes / (1024 * 1024)
    except Exception:
        return 0.0


def clean_old_files(
    directory: str,
    max_age_days: int = 7,
    file_extensions: Optional[List[str]] = None
) -> int:
    """
    Belirli bir yaştan eski dosyaları temizle.
    
    Args:
        directory: Temizlenecek dizin
        max_age_days: Maksimum dosya yaşı (gün)
        file_extensions: Sadece bu uzantıları temizle (None = hepsi)
    
    Returns:
        Silinen dosya sayısı
    """
    logger = logging.getLogger('hikaye_resimleyici')
    deleted_count = 0
    
    if not os.path.exists(directory):
        return 0
    
    current_time = time.time()
    max_age_seconds = max_age_days * 24 * 60 * 60
    
    try:
        for filename in os.listdir(directory):
            file_path = os.path.join(directory, filename)
            
            # Dizinleri atla
            if os.path.isdir(file_path):
                continue
            
            # Uzantı filtresi
            if file_extensions:
                ext = os.path.splitext(filename)[1].lower().lstrip('.')
                if ext not in file_extensions:
                    continue
            
            # Yaş kontrolü
            file_age = current_time - os.path.getmtime(file_path)
            if file_age > max_age_seconds:
                try:
                    os.remove(file_path)
                    deleted_count += 1
                    logger.info(f"Eski dosya silindi: {filename}")
                except Exception as e:
                    logger.warning(f"Dosya silinemedi ({filename}): {e}")
    
    except Exception as e:
        logger.error(f"Temizlik hatası: {e}")
    
    return deleted_count


def extract_json_from_response(raw_text: str) -> Optional[Any]:
    """
    API yanıtından JSON çıkar (markdown bloklarını temizle).
    
    Args:
        raw_text: Ham API yanıtı
    
    Returns:
        Parse edilmiş JSON veya None
    """
    text = raw_text.strip()
    
    # Markdown kod bloklarını temizle
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    
    if text.endswith("```"):
        text = text[:-3]
    
    text = text.strip()
    
    # JSON array bul
    start_bracket = text.find('[')
    end_bracket = text.rfind(']')
    
    if start_bracket != -1 and end_bracket != -1 and end_bracket > start_bracket:
        json_str = text[start_bracket:end_bracket + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
    
    # JSON object bul
    start_brace = text.find('{')
    end_brace = text.rfind('}')
    
    if start_brace != -1 and end_brace != -1 and end_brace > start_brace:
        json_str = text[start_brace:end_brace + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
    
    # Direkt parse dene
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def get_prompt_from_scene(scene_data) -> str:
    """
    Sahne verisinden prompt string'ini çıkar.
    
    Args:
        scene_data: String veya dict olabilir
    
    Returns:
        Prompt string'i
    """
    if isinstance(scene_data, str):
        return scene_data
    
    if isinstance(scene_data, dict):
        # Olası anahtar isimleri
        keys = ['image_prompts', 'visual_description', 'description', 'prompt', 'text', 'content']
        for key in keys:
            if key in scene_data:
                value = scene_data[key]
                if isinstance(value, str):
                    return value
                elif isinstance(value, list) and value:
                    return str(value[0])
        
        # Hiçbiri yoksa dict'i string'e çevir
        return str(scene_data)
    
    return str(scene_data)


class RateLimiter:
    """Basit rate limiter sınıfı"""
    
    def __init__(self, max_calls: int, period: float):
        """
        Args:
            max_calls: Periyod içinde maksimum çağrı sayısı
            period: Periyod süresi (saniye)
        """
        self.max_calls = max_calls
        self.period = period
        self.calls = []
    
    def is_allowed(self) -> bool:
        """Yeni çağrıya izin var mı?"""
        now = time.time()
        
        # Eski çağrıları temizle
        self.calls = [t for t in self.calls if now - t < self.period]
        
        if len(self.calls) < self.max_calls:
            self.calls.append(now)
            return True
        
        return False
    
    def wait_time(self) -> float:
        """Bir sonraki çağrı için bekleme süresi"""
        if len(self.calls) < self.max_calls:
            return 0
        
        now = time.time()
        oldest_call = min(self.calls)
        wait = self.period - (now - oldest_call)
        return max(0, wait)


class APIStats:
    """API kullanım istatistikleri"""
    
    def __init__(self):
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.total_images_generated = 0
        self.total_videos_created = 0
        self.start_time = time.time()
    
    def record_request(self, success: bool = True):
        """İstek kaydet"""
        self.total_requests += 1
        if success:
            self.successful_requests += 1
        else:
            self.failed_requests += 1
    
    def record_image(self):
        """Görsel üretimi kaydet"""
        self.total_images_generated += 1
    
    def record_video(self):
        """Video oluşturma kaydet"""
        self.total_videos_created += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """İstatistikleri al"""
        uptime = time.time() - self.start_time
        return {
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "success_rate": (self.successful_requests / self.total_requests * 100) if self.total_requests > 0 else 0,
            "total_images_generated": self.total_images_generated,
            "total_videos_created": self.total_videos_created,
            "uptime_seconds": uptime,
            "uptime_formatted": f"{int(uptime // 3600)}h {int((uptime % 3600) // 60)}m"
        }


# Global instances
api_stats = APIStats()
