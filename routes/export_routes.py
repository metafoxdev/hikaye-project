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


export_bp = Blueprint('export_bp', __name__)


@export_bp.route('/export/social', methods=['GET'])
@export_bp.route('/api/v1/export/social', methods=['GET'])
@csrf.exempt
def get_social_formats():
    """Sosyal medya formatlarını listele"""
    from config import Constants
    return jsonify(Constants.SOCIAL_MEDIA_FORMATS)


@export_bp.route('/export/social', methods=['POST'])
@export_bp.route('/api/v1/export/social', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('timestamp', 'platform')
def export_for_social(gen):
    """Sosyal medya platformu için video oluştur"""
    from config import Constants
    
    data = request.get_json()
    platform = data.get('platform')
    
    if platform not in Constants.SOCIAL_MEDIA_FORMATS:
        return jsonify({
            "success": False,
            "error": f"Geçersiz platform. Geçerli platformlar: {', '.join(Constants.SOCIAL_MEDIA_FORMATS.keys())}"
        }), 400
    
    format_config = Constants.SOCIAL_MEDIA_FORMATS[platform]
    
    video_path, error = gen.export_for_social_media(
        history_timestamp=data.get('timestamp'),
        platform=platform,
        music_file=data.get('music_file'),
        audio_settings=data.get('audio_settings', {}),
        enable_subtitles=data.get('enable_subtitles', True),
        add_watermark=data.get('add_watermark', False),
        watermark_text=data.get('watermark_text', '')
    )
    
    if video_path:
        return jsonify({
            "success": True,
            "video_path": video_path,
            "platform": platform,
            "format": format_config
        })
    
    return jsonify({
        "success": False,
        "error": error or "Video oluşturulamadı"
    }), 500



@export_bp.route('/export/pdf', methods=['POST'])
@export_bp.route('/api/v1/export/pdf', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('timestamp')
def export_storyboard_pdf(gen):
    """Storyboard PDF olarak dışa aktar"""
    data = request.get_json()
    
    pdf_path, error = gen.export_storyboard_pdf(
        history_timestamp=data.get('timestamp'),
        include_prompts=data.get('include_prompts', True),
        include_dialogues=data.get('include_dialogues', True),
        layout=data.get('layout', 'grid')  # grid, list, single
    )
    
    if pdf_path:
        return jsonify({
            "success": True,
            "pdf_path": pdf_path
        })
    
    return jsonify({
        "success": False,
        "error": error or "PDF oluşturulamadı"
    }), 500



@export_bp.route('/export', methods=['POST'])
@export_bp.route('/api/v1/export', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('timestamp')
def export_project(gen):
    """Projeyi dışa aktar"""
    data = request.get_json()
    
    file_path, error = gen.export_project(
        history_timestamp=data.get('timestamp'),
        format=data.get('format', 'json')
    )
    
    if file_path:
        return jsonify({
            "success": True,
            "file_path": file_path
        })
    
    return jsonify({
        "success": False,
        "error": error or "Export başarısız"
    }), 500



@export_bp.route('/download/<path:filename>')
@export_bp.route('/api/v1/download/<path:filename>')
@csrf.exempt
def download_file(filename):
    """Dosya indirme"""
    try:
        # Güvenlik: sadece output dizininden izin ver
        safe_path = os.path.join(config.OUTPUT_DIR, os.path.basename(filename))
        
        if not os.path.exists(safe_path):
            abort(404)
        
        return send_from_directory(
            config.OUTPUT_DIR,
            os.path.basename(filename),
            as_attachment=True
        )
    except Exception as e:
        logger.error(f"İndirme hatası: {e}")
        abort(500)


