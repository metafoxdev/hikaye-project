import os
import json
from flask import Blueprint, render_template, request, jsonify, stream_with_context, Response, send_from_directory, abort, current_app, send_file
from routes.utils import get_generator, require_generator, validate_request_json
from routes.extensions import csrf
from utils import sanitize_input, api_stats
from config import get_config

config = get_config()


misc_bp = Blueprint('misc_bp', __name__)


@misc_bp.route('/templates', methods=['GET'])
@misc_bp.route('/api/v1/templates', methods=['GET'])
@csrf.exempt
@require_generator
def get_templates(gen):
    """Hikaye şablonlarını al"""
    return jsonify(gen.get_templates())


@misc_bp.route('/templates/<template_id>', methods=['GET'])
@misc_bp.route('/api/v1/templates/<template_id>', methods=['GET'])
@csrf.exempt
@require_generator
def get_template(gen, template_id):
    """Belirli şablonu al"""
    template = gen.get_template(template_id)
    
    if template:
        return jsonify(template)
    
    return jsonify({"error": "Şablon bulunamadı"}), 404



@misc_bp.route('/locale/<lang>', methods=['GET'])
@misc_bp.route('/api/v1/locale/<lang>', methods=['GET'])
@csrf.exempt
def get_locale(lang):
    """Dil dosyasını al"""
    locale_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'locales',
        f'{lang}.json'
    )
    
    if os.path.exists(locale_file):
        try:
            with open(locale_file, 'r', encoding='utf-8') as f:
                return jsonify(json.load(f))
        except Exception as e:
            logger.error(f"Locale okuma hatası: {e}")
    
    return jsonify({"error": "Dil dosyası bulunamadı"}), 404


@misc_bp.route('/locales', methods=['GET'])
@misc_bp.route('/api/v1/locales', methods=['GET'])
@csrf.exempt
def list_locales():
    """Mevcut dilleri listele"""
    locales_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'locales'
    )
    
    available = []
    if os.path.exists(locales_dir):
        for f in os.listdir(locales_dir):
            if f.endswith('.json'):
                available.append(f.replace('.json', ''))
    
    return jsonify({"available_locales": available})



@misc_bp.route('/stats', methods=['GET'])
@misc_bp.route('/api/v1/stats', methods=['GET'])
@csrf.exempt
@require_generator
def get_stats(gen):
    """Uygulama istatistikleri"""
    return jsonify(gen.get_stats())



@misc_bp.route('/csrf-token', methods=['GET'])
def get_csrf_token():
    """CSRF token al"""
    return jsonify({"csrf_token": generate_csrf()})


