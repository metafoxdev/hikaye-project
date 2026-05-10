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


scene_bp = Blueprint('scene_bp', __name__)


@scene_bp.route('/regenerate_scene', methods=['POST'])
@scene_bp.route('/api/v1/regenerate_scene', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('timestamp', 'index')
def regenerate_scene(gen):
    """Sahneyi yeniden oluştur"""
    data = request.get_json()
    
    new_path, error = gen.regenerate_scene(
        history_timestamp=data.get('timestamp'),
        scene_index=int(data.get('index'))
    )
    
    if new_path:
        return jsonify({
            "success": True,
            "new_path": new_path
        })
    
    return jsonify({
        "success": False,
        "error": error or "Bilinmeyen hata"
    }), 500


@scene_bp.route('/regenerate_scene_with_prompt', methods=['POST'])
@scene_bp.route('/api/v1/regenerate_scene_with_prompt', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('timestamp', 'index', 'new_prompt')
def regenerate_scene_with_prompt(gen):
    """Yeni prompt ile sahneyi yeniden oluştur"""
    data = request.get_json()
    
    new_path, error = gen.regenerate_scene_with_prompt(
        history_timestamp=data.get('timestamp'),
        scene_index=int(data.get('index')),
        new_prompt=data.get('new_prompt')
    )
    
    if new_path:
        return jsonify({
            "success": True,
            "new_path": new_path
        })
    
    return jsonify({
        "success": False,
        "error": error or "Bilinmeyen hata"
    }), 500


@scene_bp.route('/regenerate_match_style', methods=['POST'])
@scene_bp.route('/api/v1/regenerate_match_style', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('timestamp', 'index')
def regenerate_match_style(gen):
    """Stil eşleştirmeli yeniden oluştur"""
    data = request.get_json()
    
    new_path, error = gen.regenerate_scene_match_style(
        history_timestamp=data.get('timestamp'),
        scene_index=int(data.get('index'))
    )
    
    if new_path:
        return jsonify({
            "success": True,
            "new_path": new_path
        })
    
    return jsonify({
        "success": False,
        "error": error or "Bilinmeyen hata"
    }), 500



@scene_bp.route('/scene/variations', methods=['POST'])
@scene_bp.route('/api/v1/scene/variations', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('timestamp', 'index')
def generate_variations(gen):
    """Sahne için varyasyonlar oluştur"""
    data = request.get_json()
    
    variations, error = gen.generate_scene_variations(
        history_timestamp=data.get('timestamp'),
        scene_index=int(data.get('index')),
        variation_count=int(data.get('count', 3))
    )
    
    if variations:
        return jsonify({
            "success": True,
            "variations": variations
        })
    
    return jsonify({
        "success": False,
        "error": error or "Varyasyonlar oluşturulamadı"
    }), 500


@scene_bp.route('/scene/select_variation', methods=['POST'])
@scene_bp.route('/api/v1/scene/select_variation', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('timestamp', 'index', 'variation_path')
def select_variation(gen):
    """Varyasyonlardan birini seç"""
    data = request.get_json()
    
    success, error = gen.select_variation(
        history_timestamp=data.get('timestamp'),
        scene_index=int(data.get('index')),
        variation_path=data.get('variation_path')
    )
    
    if success:
        return jsonify({"success": True, "message": "Varyasyon seçildi"})
    
    return jsonify({
        "success": False,
        "error": error or "Varyasyon seçilemedi"
    }), 400



@scene_bp.route('/scene/delete', methods=['POST'])
@scene_bp.route('/api/v1/scene/delete', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('timestamp', 'index')
def delete_scene(gen):
    """Sahneyi sil"""
    data = request.get_json()
    
    success, error = gen.delete_scene(
        history_timestamp=data.get('timestamp'),
        scene_index=int(data.get('index'))
    )
    
    if success:
        return jsonify({"success": True, "message": "Sahne silindi"})
    
    return jsonify({
        "success": False,
        "error": error or "Sahne silinemedi"
    }), 400


@scene_bp.route('/scene/add', methods=['POST'])
@scene_bp.route('/api/v1/scene/add', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('timestamp', 'after_index', 'prompt')
def add_scene(gen):
    """Yeni sahne ekle"""
    data = request.get_json()
    
    new_path, error = gen.add_scene(
        history_timestamp=data.get('timestamp'),
        after_index=int(data.get('after_index')),
        prompt=data.get('prompt')
    )
    
    if new_path:
        return jsonify({
            "success": True,
            "new_path": new_path
        })
    
    return jsonify({
        "success": False,
        "error": error or "Sahne eklenemedi"
    }), 500


@scene_bp.route('/scene/reorder', methods=['POST'])
@scene_bp.route('/api/v1/scene/reorder', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('timestamp', 'new_order')
def reorder_scenes(gen):
    """Sahne sıralamasını değiştir"""
    data = request.get_json()
    
    success, error = gen.reorder_scenes(
        history_timestamp=data.get('timestamp'),
        new_order=data.get('new_order')
    )
    
    if success:
        return jsonify({"success": True, "message": "Sıralama güncellendi"})
    
    return jsonify({
        "success": False,
        "error": error or "Sıralama güncellenemedi"
    }), 400


