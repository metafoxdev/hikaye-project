import os
import json
from flask import Blueprint, render_template, request, jsonify, stream_with_context, Response, send_from_directory, abort, current_app, send_file
from routes.utils import get_generator, require_generator, validate_request_json
from routes.extensions import csrf
from utils import sanitize_input, api_stats
from config import get_config

config = get_config()


settings_bp = Blueprint('settings_bp', __name__)


@settings_bp.route('/settings', methods=['GET', 'POST'])
@settings_bp.route('/api/v1/settings', methods=['GET', 'POST'])
@csrf.exempt
@require_generator
def handle_settings(gen):
    """Ayarları oku/güncelle"""
    if request.method == 'GET':
        return jsonify(gen.get_settings())
    
    data = request.get_json()
    if not data:
        return jsonify({"error": "Ayar verisi gerekli"}), 400
    
    if gen.update_settings(data):
        return jsonify({"success": True, "message": "Ayarlar kaydedildi"})
    
    return jsonify({
        "success": False,
        "error": "Ayarlar kaydedilemedi"
    }), 500


