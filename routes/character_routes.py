import logging
logger = logging.getLogger("hikaye_resimleyici")
import os
import json
import uuid
from flask import Blueprint, render_template, request, jsonify, stream_with_context, Response, send_from_directory, abort, current_app, send_file
from werkzeug.utils import secure_filename
from routes.utils import get_generator, require_generator, validate_request_json
from routes.extensions import csrf
from utils import sanitize_input, api_stats
from config import get_config

config = get_config()


character_bp = Blueprint('character_bp', __name__)


@character_bp.route('/characters', methods=['GET'])
@character_bp.route('/api/v1/characters', methods=['GET'])
@csrf.exempt
@require_generator
def get_characters(gen):
    """Karakter listesi"""
    return jsonify(gen.get_characters())


@character_bp.route('/characters/add', methods=['POST'])
@character_bp.route('/api/v1/characters/add', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('name', 'description')
def add_character(gen):
    """Yeni karakter ekle"""
    data = request.get_json()
    
    success, char_id = gen.add_character(
        name=data.get('name'),
        description=data.get('description'),
        attributes=data.get('attributes', {}),
        emotion=data.get('emotion', 'neutral')
    )
    
    if success:
        return jsonify({"success": True, "message": "Karakter eklendi", "id": char_id})

    
    return jsonify({
        "success": False,
        "error": "Karakter eklenemedi"
    }), 400


@character_bp.route('/characters/delete', methods=['POST'])
@character_bp.route('/api/v1/characters/delete', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('id')
def delete_character(gen):
    """Karakter sil"""
    data = request.get_json()
    
    if gen.delete_character(data.get('id')):
        return jsonify({"success": True, "message": "Karakter silindi"})
    
    return jsonify({
        "success": False,
        "error": "Karakter silinemedi"
    }), 400


@character_bp.route('/characters/update', methods=['POST'])
@character_bp.route('/api/v1/characters/update', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('id', 'name', 'description')
def update_character(gen):
    """Karakter güncelle"""
    data = request.get_json()
    
    if gen.update_character(
        char_id=data.get('id'),
        name=data.get('name'),
        description=data.get('description'),
        attributes=data.get('attributes', {})
    ):
        return jsonify({"success": True, "message": "Karakter güncellendi"})
    
    return jsonify({
        "success": False,
        "error": "Karakter güncellenemedi"
    }), 400


@character_bp.route('/characters/enhance', methods=['POST'])
@character_bp.route('/api/v1/characters/enhance', methods=['POST'])
@csrf.exempt
@require_generator
def enhance_character(gen):
    """Karakteri AI ile zenginleştir"""
    data = request.get_json()
    
    if not data:
        return jsonify({
            "error": "Karakter verisi gerekli"
        }), 400
    
    enhanced = gen.enhance_character(data)
    
    if enhanced:
        return jsonify(enhanced)
    
    return jsonify({
        "error": "Karakter zenginleştirilemedi"
    }), 500



@character_bp.route('/characters/avatar', methods=['POST'])
@character_bp.route('/api/v1/characters/avatar', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('id')
def generate_avatar(gen):
    """Karakter avatarı oluştur"""
    data = request.get_json()
    
    avatar_path, error = gen.generate_character_avatar(
        char_id=data.get('id')
    )
    
    if avatar_path:
        return jsonify({
            "success": True,
            "avatar_path": avatar_path
        })
    
    return jsonify({
        "success": False,
        "error": error or "Avatar oluşturulamadı"
    }), 500


@character_bp.route('/characters/emotion', methods=['POST'])
@character_bp.route('/api/v1/characters/emotion', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('id', 'emotion')
def set_character_emotion(gen):
    """Karakterin aktif duygu durumunu ayarla"""
    data = request.get_json()
    char_id = data.get('id')
    emotion  = data.get('emotion')

    if gen.set_character_emotion(char_id, emotion):
        return jsonify({"success": True, "message": f"Duygu ayarlandı: {emotion}"})

    return jsonify({"success": False, "error": "Geçersiz karakter ID veya duygu değeri"}), 400


@character_bp.route('/characters/emotions', methods=['GET'])
@character_bp.route('/api/v1/characters/emotions', methods=['GET'])
@csrf.exempt
@require_generator
def get_character_emotions(gen):
    """Tüm karakterlerin aktif duygularını + desteklenen duygu listesini döndür"""
    return jsonify({
        "emotions": gen.get_character_emotions(),
        "available_emotions": list(gen.EMOTION_MAP.keys())
    })

@character_bp.route('/characters/reference/upload', methods=['POST'])
@csrf.exempt
@require_generator
def upload_reference_image(gen):
    """Karakter için referans görsel yükle"""
    char_id = request.form.get('id')
    if not char_id:
        return jsonify({"error": "Karakter ID gerekli"}), 400
    
    if 'file' not in request.files:
        return jsonify({"error": "Dosya bulunamadı"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "Dosya adı boş"}), 400
    
    if file:
        filename = secure_filename(file.filename)
        # Benzersiz isim ver
        ext = os.path.splitext(filename)[1]
        unique_name = f"ref_{char_id}_{uuid.uuid4().hex}{ext}"
        
        # references klasörünü oluştur
        ref_dir = os.path.join(config.OUTPUT_DIR, 'references')
        os.makedirs(ref_dir, exist_ok=True)
        
        save_path = os.path.join(ref_dir, unique_name)
        file.save(save_path)
        
        # Görece yol (static/output/references/...)
        relative_path = os.path.join('static', 'output', 'references', unique_name).replace('\\', '/')
        
        if gen.add_reference_image(char_id, relative_path):
            return jsonify({
                "success": True, 
                "path": relative_path,
                "message": "Referans görsel eklendi"
            })
        
    return jsonify({"error": "Görsel eklenemedi"}), 500

@character_bp.route('/characters/reference/delete', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('id', 'path')
def delete_reference_image(gen):
    """Referans görseli sil"""
    data = request.get_json()
    char_id = data.get('id')
    path = data.get('path')
    
    if gen.remove_reference_image(char_id, path):
        return jsonify({"success": True, "message": "Referans görsel silindi"})
    
    return jsonify({"error": "Görsel silinemedi"}), 400

@character_bp.route('/api/v1/story/enhance', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('prompt')
def enhance_story_prompt(gen):
    """Hikaye promptunu AI ile zenginleştir"""
    data = request.get_json()
    
    enhanced, error = gen.enhance_story_prompt(
        prompt=data.get('prompt'),
        style=data.get('style', 'cinematic')
    )
    
    if enhanced:
        return jsonify({
            "success": True,
            "enhanced_prompt": enhanced
        })
    
    return jsonify({
        "success": False,
        "error": error or "Prompt zenginleştirilemedi"
    }), 500


