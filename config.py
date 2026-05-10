"""
Merkezi Konfigürasyon Modülü
Tüm uygulama ayarları burada tanımlanır.
"""

import os
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()


class Config:
    """Ana Konfigürasyon Sınıfı"""
    
    # Flask Ayarları
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    FLASK_ENV = os.environ.get('FLASK_ENV', 'development')
    DEBUG = os.environ.get('FLASK_DEBUG', '0') == '1'
    
    # API Ayarları
    GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
    
    # Model Ayarları
    TEXT_MODEL = "gemini-3-pro-preview"
    IMAGE_MODEL = "gemini-3-pro-image-preview"
    ENHANCE_MODEL = "gemini-3-pro-preview"
    TTS_MODEL = "gemini-2.5-pro-preview-tts"  # Text-to-Speech model
    
    # TTS Ses Ayarları (Karakter Seslendirmesi)
    TTS_VOICES = {
        "female": "Leda",    # Kadın sesi
        "male": "Enceladus"      # Erkek sesi
    }
    TTS_ENABLED = True  # Seslendirme özelliği açık/kapalı
    
    # Uygulama Ayarları
    MAX_SCENES = int(os.environ.get('MAX_SCENES', 50))  # 50'ye kadar sahne
    DEFAULT_RESOLUTION = os.environ.get('DEFAULT_RESOLUTION', '2K')
    DEFAULT_ASPECT_RATIO = os.environ.get('DEFAULT_ASPECT_RATIO', '9:16')
    DEFAULT_FRAME_DURATION = 5.0  # 3, 5, 7, 10, 15 saniye seçenekleri
    
    # Retry Ayarları
    MAX_RETRIES = int(os.environ.get('MAX_RETRIES', 3))
    RETRY_DELAY = int(os.environ.get('RETRY_DELAY', 2))
    
    # Paralel İşlem Ayarları
    PARALLEL_IMAGE_GENERATION = os.environ.get('PARALLEL_IMAGE_GENERATION', '1') == '1'
    MAX_PARALLEL_WORKERS = int(os.environ.get('MAX_PARALLEL_WORKERS', 5))
    
    # Rate Limiting
    RATE_LIMIT_PER_MINUTE = int(os.environ.get('RATE_LIMIT_PER_MINUTE', 10))
    RATE_LIMIT_PER_HOUR = int(os.environ.get('RATE_LIMIT_PER_HOUR', 100))
    
    # Dosya Yolları
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.path.join(BASE_DIR, 'data')
    STATIC_DIR = os.path.join(BASE_DIR, 'static')
    OUTPUT_DIR = os.path.join(STATIC_DIR, 'output')
    LOGS_DIR = os.path.join(BASE_DIR, 'logs')
    
    # Data Dosyaları
    HISTORY_FILE = os.path.join(DATA_DIR, 'history.json')
    SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')
    CHARACTERS_FILE = os.path.join(DATA_DIR, 'characters.json')
    
    # Logging
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
    LOG_FILE = os.path.join(LOGS_DIR, 'app.log')
    LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB
    LOG_BACKUP_COUNT = 5
    
    # Video Ayarları
    VIDEO_FPS = 24
    VIDEO_CODEC = 'libx264'
    FADE_DURATION = 0.5  # saniye
    
    # Güvenlik
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max upload
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    
    @classmethod
    def init_directories(cls):
        """Gerekli dizinleri oluştur"""
        for directory in [cls.DATA_DIR, cls.OUTPUT_DIR, cls.LOGS_DIR]:
            os.makedirs(directory, exist_ok=True)
    
    @classmethod
    def validate(cls):
        """Konfigürasyonu doğrula"""
        errors = []
        
        if not cls.GEMINI_API_KEY:
            errors.append("GEMINI_API_KEY çevre değişkeni bulunamadı!")
        
        if cls.MAX_SCENES < 1 or cls.MAX_SCENES > 50:
            errors.append("MAX_SCENES 1-50 arasında olmalı")
        
        if cls.DEFAULT_RESOLUTION not in ['1K', '2K', '4K']:
            errors.append("DEFAULT_RESOLUTION 1K, 2K veya 4K olmalı")
        
        return errors


class DevelopmentConfig(Config):
    """Geliştirme Ortamı Konfigürasyonu"""
    DEBUG = True
    LOG_LEVEL = 'DEBUG'


class ProductionConfig(Config):
    """Üretim Ortamı Konfigürasyonu"""
    DEBUG = False
    LOG_LEVEL = 'WARNING'


class TestingConfig(Config):
    """Test Ortamı Konfigürasyonu"""
    TESTING = True
    DEBUG = True


# Ortama göre konfigürasyon seç
config_map = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig
}

def get_config():
    """Mevcut ortama göre konfigürasyonu döndür"""
    env = os.environ.get('FLASK_ENV', 'development')
    return config_map.get(env, DevelopmentConfig)


# Sabit Değerler (Magic Numbers yerine)
class Constants:
    """Uygulama Sabitleri"""
    
    # Stil Haritaları
    ART_STYLES = {
        "comic": "Çizgi Roman Stili (Comic Book Style), renkli, konturlu, detaylı",
        "realistic": "Epik Sinematik Realizm, 8K, aşırı gerçekçi, film karesi gibi",
        "anime": "Anime / Manga Stili (Studio Ghibli veya Modern Anime)",
        "noir": "Film Noir, Siyah Beyaz, Yüksek Kontrast, Dedektif filmi havası",
        "watercolor": "Sulu Boya (Watercolor), yumuşak geçişler, sanatsal",
        "cyberpunk_art": "Cyberpunk İllüstrasyon, neon renkler, fütüristik çizgiler",
        "oil": "Rönesans Yağlı Boya Tablosu stili, fırça darbeleri belirgin",
        "3d": "3D Render, Pixar/Disney Animasyon stili, yumuşak ışık",
        "pixel": "Pixel Art, Retro 8-bit Oyun Grafiği",
        "sketch": "Karakalem Eskiz (Charcoal Sketch), kaba çizgiler, sanatsal"
    }
    
    MOOD_STYLES = {
        "dynamic": "Canlı renkler, enerji dolu, parlak ışıklandırma",
        "dark": "Karanlık, kasvetli, gölgeli, gotik atmosfer",
        "horror": "Korku, gerilim, tekinsiz, sisli, soğuk renkler",
        "dreamy": "Rüya gibi, soft focus, pastel tonlar, mistik",
        "warm": "Sıcak tonlar, gün batımı, romantik, yumuşak",
        "cold": "Soğuk tonlar, mavi filtre, mesafeli atmosfer",
        "cyberpunk": "Neon ışıklar, mor-mavi tonlar, fütüristik şehir ışıkları",
        "natural": "Doğal aydınlatma, gerçekçi gün ışığı, dengeli renkler"
    }
    
    CAMERA_STYLES = {
        "balanced": "Dengeli sinematik açılar (Orta ve Geniş plan karışık)",
        "closeup": "Duygusal yoğunluk için bolca Yakın Plan (Close-up) ve Extreme Close-up",
        "wide": "Mekanı göstermek için Geniş Açılar (Wide Shot) ve Epik Manzaralar",
        "low_angle": "Alt Açı (Low Angle), karakterleri güçlü gösteren perspektif",
        "over_shoulder": "Omuz Üstü Çekim (Over-the-shoulder), diyalog odaklı",
        "drone": "Drone Çekimi (Aerial Shot), kuş bakışı",
        "action": "Hareketli, Dinamik ve 'Dutch Angle' gibi aksiyon açıları"
    }
    
    OUTFIT_STYLES = {
        "casual": "Günlük Modern Kıyafetler (Jean, T-shirt, Spor Ayakkabı, Deri Ceket, Rahat Giyim)",
        "business": "Şık İş Kıyafetleri (Takım Elbise, Kravat, Döpiyes, Gömlek, Ofis Stili)",
        "evening": "Gece Kıyafeti (Smokin, Abiye Elbise, Zarif Mücevherler, Balo Stili)",
        "medieval": "Ortaçağ Fantezi (Zırh, Cübbe, Pelerin, Kürk, Deri Kayışlar, Kılıç Kuşanan)",
        "scifi": "Bilim Kurgu / Fütüristik (Metalik Tulumlar, Techwear, Neon Şeritler, Uzay Giysisi)",
        "superhero": "Süper Kahraman Kostümü (Maske, Pelerin, Tayt, Zırhlı Göğüslük)",
        "military": "Askeri / Taktiksel (Kamuflaj, Çelik Yelek, Postal, Üniforma)",
        "victorian": "Viktorya Dönemi / Steampunk (Korse, Silindir Şapka, Dantel, Dişli Çark Aksesuarlar)",
        "street": "Sokak Modası (Oversize Hoodie, Baggy Pants, Sneaker, Bandana)",
        "uniform": "Mesleki Üniforma (Beyaz Önlük, Polis Üniformaları, İtfaiyeci, Pilot)",
        "gothic": "Gotik Stil (Siyah Dantel, Deri, Koyu Makyaj, Metal Aksesuarlar)",
        "summer": "Yazlık (Şort, Mayo, Çiçekli Gömlek, Güneş Gözlüğü, Hasır Şapka)",
        "sport": "Spor Giyim (Tayt, Atlet, Eşofman Takımı, Sporcu Sütyeni)"
    }
    
    TIME_OF_DAY = {
        "sunrise": "Gün Doğumu (Golden Hour)",
        "day": "Öğle Güneşi (Bright Day)",
        "sunset": "Gün Batımı (Sunset)",
        "night": "Gece (Night)",
        "midnight": "Gece Yarısı (Midnight)"
    }
    
    SEASONS = {
        "spring": "İlkbahar (Çiçekli, Yeşil)",
        "summer": "Yaz (Sıcak, Parlak)",
        "autumn": "Sonbahar (Turuncu Yapraklar)",
        "winter": "Kış (Karlı, Soğuk)",
        "apocalyptic": "Kıyamet Sonrası (Yıkık, Kurak)"
    }
    
    WEATHER = {
        "clear": "Açık Gökyüzü",
        "cloudy": "Bulutlu/Kapalı",
        "rain": "Yağmurlu",
        "storm": "Fırtınalı ve Şimşekli",
        "fog": "Sisli/Puslu",
        "snow": "Karlı"
    }
    
    # Video Geçiş Efektleri
    VIDEO_TRANSITIONS = {
        "none": "cut",
        "fade": "crossfadein",
        "slide": "slide"
    }
    
    # Ken Burns Efekt Tipleri
    KEN_BURNS_EFFECTS = {
        "none": "Efekt yok",
        "zoom_in": "Yavaşça yakınlaş",
        "zoom_out": "Yavaşça uzaklaş",
        "pan_left": "Sola kaydır",
        "pan_right": "Sağa kaydır",
        "pan_up": "Yukarı kaydır",
        "pan_down": "Aşağı kaydır",
        "random": "Rastgele efekt"
    }
    
    # Ses Efektleri Kategorileri
    SFX_CATEGORIES = {
        "ambient": "Ortam Sesleri",
        "action": "Aksiyon Sesleri",
        "nature": "Doğa Sesleri",
        "urban": "Şehir Sesleri",
        "horror": "Korku Sesleri",
        "scifi": "Bilim Kurgu Sesleri",
        "comedy": "Komedi Sesleri",
        "transition": "Geçiş Sesleri"
    }
    
    # Sosyal Medya Formatları
    SOCIAL_MEDIA_FORMATS = {
        "instagram_reels": {
            "name": "Instagram Reels",
            "aspect_ratio": "9:16",
            "max_duration": 90,
            "resolution": (1080, 1920),
            "icon": "fab fa-instagram"
        },
        "youtube_shorts": {
            "name": "YouTube Shorts",
            "aspect_ratio": "9:16",
            "max_duration": 60,
            "resolution": (1080, 1920),
            "icon": "fab fa-youtube"
        },
        "tiktok": {
            "name": "TikTok",
            "aspect_ratio": "9:16",
            "max_duration": 180,
            "resolution": (1080, 1920),
            "icon": "fab fa-tiktok"
        },
        "youtube": {
            "name": "YouTube",
            "aspect_ratio": "16:9",
            "max_duration": None,
            "resolution": (1920, 1080),
            "icon": "fab fa-youtube"
        },
        "twitter": {
            "name": "Twitter/X",
            "aspect_ratio": "1:1",
            "max_duration": 140,
            "resolution": (1080, 1080),
            "icon": "fab fa-twitter"
        },
        "facebook": {
            "name": "Facebook",
            "aspect_ratio": "1:1",
            "max_duration": 240,
            "resolution": (1080, 1080),
            "icon": "fab fa-facebook"
        }
    }
    
    # Maksimum Değerler
    MAX_PROMPT_LENGTH = 5000
    MAX_CHARACTER_NAME_LENGTH = 100
    MAX_HISTORY_ITEMS = 100
    SAFE_FILENAME_LENGTH = 20
