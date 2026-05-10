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


video_bp = Blueprint('video_bp', __name__)


@video_bp.route('/create_video', methods=['POST'])
@video_bp.route('/api/v1/create_video', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('timestamp')
def create_video(gen):
    """Geçmişten video oluştur"""
    data = request.get_json()
    
    # Ses ayarlarını al (opsiyonel)
    audio_settings = data.get('audio_settings', {})
    
    video_path, error = gen.create_video_from_history(
        history_timestamp=data.get('timestamp'),
        music_file=data.get('music_file'),  # Müzik dosyası (opsiyonel)
        audio_settings=audio_settings,  # Ses ayarları (opsiyonel)
        ken_burns_effect=data.get('ken_burns_effect', 'none'),  # Ken Burns efekti
        enable_subtitles=data.get('enable_subtitles', False),  # Altyazı
        subtitle_settings=data.get('subtitle_settings', {}),  # Altyazı ayarları
        watermark_text=data.get('watermark_text'),  # Watermark metni
        watermark_settings=data.get('watermark_settings', {}),  # Watermark ayarları
        selected_scenes=data.get('selected_scenes')  # Seçilen sahneler (opsiyonel)
    )
    
    if video_path:
        return jsonify({
            "success": True,
            "video_path": video_path
        })
    
    return jsonify({
        "success": False,
        "error": error or "Video oluşturulamadı"
    }), 500


@video_bp.route('/merge_stories', methods=['POST'])
@video_bp.route('/api/v1/merge_stories', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('timestamps')
def merge_stories(gen):
    """Birden fazla hikayeyi birleştirerek tek video oluştur"""
    data = request.get_json()
    
    timestamps = data.get('timestamps', [])
    if len(timestamps) < 2:
        return jsonify({
            "success": False,
            "error": "En az 2 hikaye seçmelisiniz"
        }), 400
    
    video_path, error = gen.merge_stories_to_video(
        timestamps=timestamps,
        music_file=data.get('music_file'),
        video_name=data.get('video_name', 'Birleşik Hikaye'),
        audio_settings=data.get('audio_settings', {})
    )
    
    if video_path:
        return jsonify({
            "success": True,
            "video_path": video_path
        })
    
    return jsonify({
        "success": False,
        "error": error or "Birleşik video oluşturulamadı"
    }), 500


