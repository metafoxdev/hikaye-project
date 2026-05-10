import os
import json
from flask import Blueprint, render_template, request, jsonify, stream_with_context, Response, send_from_directory, abort, current_app, send_file
from routes.utils import get_generator, require_generator, validate_request_json
from routes.extensions import csrf
from utils import sanitize_input, api_stats
from config import get_config

config = get_config()


history_bp = Blueprint('history_bp', __name__)


@history_bp.route('/history', methods=['GET'])
@history_bp.route('/api/v1/history', methods=['GET'])
@csrf.exempt
@require_generator
def get_history(gen):
    """Geçmiş listesi"""
    return jsonify(gen.get_history())


@history_bp.route('/history/delete', methods=['POST'])
@history_bp.route('/api/v1/history/delete', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('timestamp')
def delete_history(gen):
    """Geçmiş kaydı sil"""
    data = request.get_json()
    timestamp = data.get('timestamp')
    
    if gen.delete_history_item(timestamp):
        return jsonify({"success": True, "message": "Kayıt silindi"})
    
    return jsonify({
        "success": False,
        "error": "Kayıt silinemedi"
    }), 400


@history_bp.route('/history/clear', methods=['POST'])
@history_bp.route('/api/v1/history/clear', methods=['POST'])
@csrf.exempt
@require_generator
def clear_history(gen):
    """Tüm geçmişi temizle"""
    if gen.clear_history():
        return jsonify({"success": True, "message": "Geçmiş temizlendi"})
    
    return jsonify({
        "success": False,
        "error": "Geçmiş temizlenemedi"
    }), 500


