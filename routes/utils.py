import json
from functools import wraps
from flask import request, jsonify
from utils import setup_logging
from config import get_config

config = get_config()
logger = setup_logging(log_file=config.LOG_FILE, log_level=config.LOG_LEVEL)

generator = None   # modül seviyesinde başlangıç değeri

def get_generator():
    """Generator'ı lazy load ile al"""
    global generator
    if generator is None:
        try:
            from logic import StoryGenerator
            generator = StoryGenerator()
            logger.info("StoryGenerator başarıyla yüklendi")
        except Exception as e:
            logger.error(f"Generator başlatılamadı: {e}")
            return None
    return generator


def require_generator(f):
    """Generator gerektiren endpoint'ler için dekoratör"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        gen = get_generator()
        if gen is None:
            return jsonify({
                "error": "Sistem başlatılamadı. Lütfen API anahtarınızı kontrol edin.",
                "code": "GENERATOR_ERROR"
            }), 500
        return f(gen, *args, **kwargs)
    return decorated_function


def validate_request_json(*required_fields):
    """JSON request doğrulama dekoratörü"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not request.is_json:
                return jsonify({
                    "error": "JSON formatında veri bekleniyor",
                    "code": "INVALID_FORMAT"
                }), 400
            
            data = request.get_json()
            missing = [field for field in required_fields if field not in data]
            
            if missing:
                return jsonify({
                    "error": f"Eksik alanlar: {', '.join(missing)}",
                    "code": "MISSING_FIELDS"
                }), 400
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


