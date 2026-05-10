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


story_bp = Blueprint('story_bp', __name__)


@story_bp.route('/generate', methods=['POST'])
@story_bp.route('/api/v1/generate', methods=['POST'])
@csrf.exempt
@require_generator
def generate(gen):
    """Hikaye oluşturma endpoint'i (SSE stream)"""
    try:
        data = request.get_json() or {}
        
        # Parametreleri al ve doğrula (user_input veya prompt kabul et)
        prompt = sanitize_input(data.get('prompt') or data.get('user_input', ''))
        
        if not prompt:
            return jsonify({
                "error": "Lütfen bir hikaye konusu girin.",
                "code": "EMPTY_PROMPT"
            }), 400
        
        # Ayarları al
        params = {
            "user_input": prompt,
            "aspect_ratio": data.get('aspect_ratio', '9:16'),
            "image_size": data.get('image_size', '2K'),
            "scene_count": min(int(data.get('scene_count', 7)), config.MAX_SCENES),
            "frame_duration": float(data.get('frame_duration', 5.0)),
            "dialogue_style": data.get('dialogue_style', 'short'),
            "art_style": data.get('art_style', 'comic'),
            "mood_style": data.get('mood_style', 'dynamic'),
            "camera_style": data.get('camera_style', 'balanced'),
            "time_of_day": data.get('time_of_day', 'auto'),
            "season": data.get('season', 'auto'),
            "weather": data.get('weather', 'auto'),
            "outfit_style": data.get('outfit_style', 'auto'),
            "character_consistency": data.get('character_consistency', 'strict'),
            "video_transition": data.get('video_transition', 'none'),
            "generate_audio": data.get('generate_audio', False),
            "dual_format": bool(data.get('dual_format', False))   # YENİ: çift format
        }
        
        logger.info(f"Hikaye oluşturma başlatıldı: {prompt[:50]}...")
        
        def generate_stream():
            try:
                for update in gen.generate_full_story_stream(**params):
                    yield f"data: {json.dumps(update, ensure_ascii=False)}\n\n"
            except Exception as e:
                logger.error(f"Stream hatası: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
        
        return Response(
            stream_with_context(generate_stream()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no'
            }
        )
        
    except Exception as e:
        logger.error(f"Generate hatası: {e}")
        return jsonify({
            "error": "Beklenmeyen bir hata oluştu.",
            "details": str(e),
            "code": "INTERNAL_ERROR"
        }), 500



@story_bp.route('/story/branch', methods=['POST'])
@story_bp.route('/api/v1/story/branch', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('timestamp', 'branch_point', 'branch_prompt')
def create_story_branch(gen):
    """Hikayeden alternatif dal oluştur"""
    data = request.get_json()
    
    branch_entry, error = gen.create_story_branch(
        history_timestamp=data.get('timestamp'),
        branch_point=int(data.get('branch_point')),
        branch_prompt=data.get('branch_prompt'),
        branch_name=data.get('branch_name', 'Alternatif Son'),
        scene_count=int(data.get('scene_count', 5))
    )
    
    if branch_entry:
        return jsonify({
            "success": True,
            "branch": branch_entry
        })
    
    return jsonify({
        "success": False,
        "error": error or "Hikaye dalı oluşturulamadı"
    }), 500


@story_bp.route('/story/branches', methods=['GET'])
@story_bp.route('/api/v1/story/branches', methods=['GET'])
@csrf.exempt
@require_generator
def get_story_branches(gen):
    """Hikayenin dallarını getir"""
    timestamp = request.args.get('timestamp', type=int)
    
    if not timestamp:
        return jsonify({"error": "timestamp parametresi gerekli"}), 400
    
    branches = gen.get_story_branches(timestamp)
    return jsonify({"branches": branches})



@story_bp.route('/story/continue', methods=['POST'])
@story_bp.route('/api/v1/story/continue', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('timestamp')
def continue_story(gen):
    """Hikayeye devam et - karakter tutarlılığını koruyarak"""
    data = request.get_json()
    
    new_images, error = gen.continue_story(
        history_timestamp=data.get('timestamp'),
        continuation_prompt=data.get('continuation_prompt', ''),  # Opsiyonel
        scene_count=int(data.get('scene_count', 5))
    )
    
    if new_images:
        return jsonify({
            "success": True,
            "new_images": new_images
        })
    
    return jsonify({
        "success": False,
        "error": error or "Hikaye devam ettirilemedi"
    }), 500


@story_bp.route('/story/suggestions', methods=['GET'])
@story_bp.route('/api/v1/story/suggestions', methods=['GET'])
@csrf.exempt
@require_generator
def get_story_suggestions(gen):
    """AI tabanlı hikaye önerileri al"""
    try:
        category = request.args.get('category', 'all')
        count = int(request.args.get('count', 5))
        
        suggestions, error = gen.get_story_suggestions(category=category, count=count)
        
        if suggestions:
            return jsonify({
                "success": True,
                "suggestions": suggestions
            })
        
        return jsonify({
            "success": False,
            "error": error or "Öneriler alınamadı"
        }), 500
    except Exception as e:
        logger.error(f"Suggestions endpoint hatası: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


