"""
Flask Web Sunucusu
AI Hikaye Resimleyici Ana Uygulama
"""

import os
import json
import secrets
from functools import wraps

from flask import (
    Flask, 
    render_template, 
    request, 
    jsonify, 
    stream_with_context, 
    Response,
    send_from_directory,
    abort
)
from flask_cors import CORS
from flask_wtf.csrf import CSRFProtect, generate_csrf

# Yerel modüller
from config import Config, get_config
from utils import setup_logging, sanitize_input, api_stats

# Konfigürasyon
config = get_config()
config.init_directories()

# Logger
logger = setup_logging(
    log_file=config.LOG_FILE,
    log_level=config.LOG_LEVEL
)

# Flask uygulaması
app = Flask(__name__)
app.config['SECRET_KEY'] = config.SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = config.MAX_CONTENT_LENGTH
app.config['WTF_CSRF_TIME_LIMIT'] = None  # CSRF token süresiz

# CORS ve CSRF
CORS(app, resources={r"/api/*": {"origins": "*"}})
from routes.extensions import csrf
csrf.init_app(app)

# Generator'ı lazy load
generator = None




# Blueprints Registration
from routes.main_routes import main_bp
app.register_blueprint(main_bp)
from routes.story_routes import story_bp
app.register_blueprint(story_bp)
from routes.history_routes import history_bp
app.register_blueprint(history_bp)
from routes.character_routes import character_bp
app.register_blueprint(character_bp)
from routes.settings_routes import settings_bp
app.register_blueprint(settings_bp)
from routes.scene_routes import scene_bp
app.register_blueprint(scene_bp)
from routes.audio_routes import audio_bp
app.register_blueprint(audio_bp)
from routes.video_routes import video_bp
app.register_blueprint(video_bp)
from routes.export_routes import export_bp
app.register_blueprint(export_bp)
from routes.misc_routes import misc_bp
app.register_blueprint(misc_bp)


if __name__ == '__main__':
    # Konfigürasyon doğrulama
    errors = config.validate()
    if errors:
        for error in errors:
            logger.warning(f"Konfigürasyon uyarısı: {error}")
    
    # Geliştirme modunda çalıştır
    logger.info("=" * 50)
    logger.info("AI Hikaye Resimleyici başlatılıyor...")
    logger.info(f"Ortam: {config.FLASK_ENV}")
    logger.info(f"Debug: {config.DEBUG}")
    logger.info("=" * 50)
    
    app.run(
        host='0.0.0.0',
        port=9900,
        debug=config.DEBUG,
        threaded=True
    )
