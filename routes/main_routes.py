import logging
logger = logging.getLogger("hikaye_resimleyici")
import os
import json
from flask import Blueprint, render_template, request, jsonify, stream_with_context, Response, send_from_directory, abort, current_app, send_file
from routes.utils import get_generator, require_generator, validate_request_json
from routes.extensions import csrf
from utils import sanitize_input, api_stats
from config import get_config

config = get_config()


main_bp = Blueprint('main_bp', __name__)


@main_bp.route('/')
def index():
    """Ana sayfa"""
    return render_template('index.html')


@main_bp.route('/output/<path:filename>')
@csrf.exempt
def serve_output(filename):
    """Output klasöründeki dosyaları serve et (görseller, avatarlar vb.)"""
    # config.OUTPUT_DIR = static/output
    return send_from_directory(config.OUTPUT_DIR, filename)


@main_bp.route('/static/output/<path:filename>')
@csrf.exempt
def serve_static_output(filename):
    """Eski static/output yolunu da destekle (geriye uyumluluk)"""
    return send_from_directory(config.OUTPUT_DIR, filename)


@main_bp.route('/health')
@csrf.exempt
def health_check():
    """Sağlık kontrolü endpoint'i"""
    gen = get_generator()
    return jsonify({
        "status": "healthy" if gen else "degraded",
        "generator": "ready" if gen else "not_initialized"
    })



@main_bp.app_errorhandler(400)
def bad_request(error):
    """400 Bad Request"""
    return jsonify({
        "error": "Geçersiz istek",
        "code": "BAD_REQUEST"
    }), 400


@main_bp.app_errorhandler(404)
def not_found(error):
    """404 Not Found"""
    return jsonify({
        "error": "Kaynak bulunamadı",
        "code": "NOT_FOUND"
    }), 404


@main_bp.app_errorhandler(500)
def internal_error(error):
    """500 Internal Server Error"""
    logger.error(f"Internal error: {error}")
    return jsonify({
        "error": "Sunucu hatası",
        "code": "INTERNAL_ERROR"
    }), 500


@main_bp.app_errorhandler(413)
def request_entity_too_large(error):
    """413 Request Entity Too Large"""
    return jsonify({
        "error": "Dosya boyutu çok büyük",
        "code": "FILE_TOO_LARGE"
    }), 413



@main_bp.app_context_processor
def inject_globals():
    """Template'lere global değişkenler ekle"""
    return {
        "app_name": "AI Hikaye Resimleyici",
        "version": "2.0.0"
    }


