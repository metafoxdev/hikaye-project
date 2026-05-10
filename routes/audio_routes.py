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


audio_bp = Blueprint('audio_bp', __name__)


@audio_bp.route('/regenerate_audio', methods=['POST'])
@audio_bp.route('/api/v1/regenerate_audio', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('timestamp', 'index')
def regenerate_audio(gen):
    """Sahne seslendirmesini yeniden oluştur"""
    data = request.get_json()
    
    new_audio_path, error = gen.regenerate_scene_audio(
        history_timestamp=data.get('timestamp'),
        scene_index=int(data.get('index'))
    )
    
    if new_audio_path:
        return jsonify({
            "success": True,
            "new_audio_path": new_audio_path
        })
    
    return jsonify({
        "success": False,
        "error": error or "Seslendirme oluşturulamadı"
    }), 500



SFX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sfx')
ALLOWED_AUDIO_EXTENSIONS = {'mp3', 'wav', 'ogg', 'm4a', 'aac'}

@audio_bp.route('/sfx', methods=['GET'])
@audio_bp.route('/api/v1/sfx', methods=['GET'])
@csrf.exempt
def get_sfx_list():
    """Ses efektleri listesini al"""
    from config import Constants
    
    sfx_files = []
    
    if os.path.exists(SFX_DIR):
        for f in os.listdir(SFX_DIR):
            ext = f.rsplit('.', 1)[-1].lower() if '.' in f else ''
            if ext in ALLOWED_AUDIO_EXTENSIONS:
                file_path = os.path.join(SFX_DIR, f)
                file_size = os.path.getsize(file_path)
                
                # Kategori dosya adından çıkar (ör: ambient_rain.mp3 -> ambient)
                name_parts = os.path.splitext(f)[0].split('_')
                category = name_parts[0] if name_parts[0] in Constants.SFX_CATEGORIES else "other"
                
                sfx_files.append({
                    "filename": f,
                    "name": os.path.splitext(f)[0],
                    "category": category,
                    "category_name": Constants.SFX_CATEGORIES.get(category, "Diğer"),
                    "extension": ext,
                    "size": file_size,
                    "size_kb": round(file_size / 1024, 2)
                })
    
    # Kategoriye göre grupla
    sfx_files.sort(key=lambda x: (x['category'], x['name'].lower()))
    
    return jsonify({
        "sfx": sfx_files,
        "categories": Constants.SFX_CATEGORIES
    })


@audio_bp.route('/sfx/upload', methods=['POST'])
@audio_bp.route('/api/v1/sfx/upload', methods=['POST'])
@csrf.exempt
def upload_sfx():
    """Yeni ses efekti yükle"""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "Dosya seçilmedi"}), 400
    
    file = request.files['file']
    category = request.form.get('category', 'ambient')
    
    if file.filename == '':
        return jsonify({"success": False, "error": "Dosya seçilmedi"}), 400
    
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        return jsonify({
            "success": False, 
            "error": f"Desteklenmeyen format. İzin verilenler: {', '.join(ALLOWED_AUDIO_EXTENSIONS)}"
        }), 400
    
    # Dosya adını oluştur (kategori_isim.ext)
    original_name = os.path.splitext(file.filename)[0]
    filename = f"{category}_{original_name}.{ext}"
    save_path = os.path.join(SFX_DIR, filename)
    
    # Aynı isimde varsa numara ekle
    counter = 1
    while os.path.exists(save_path):
        filename = f"{category}_{original_name}_{counter}.{ext}"
        save_path = os.path.join(SFX_DIR, filename)
        counter += 1
    
    try:
        os.makedirs(SFX_DIR, exist_ok=True)
        file.save(save_path)
        return jsonify({
            "success": True, 
            "message": "Ses efekti yüklendi",
            "filename": filename
        })
    except Exception as e:
        logger.error(f"SFX yükleme hatası: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@audio_bp.route('/sfx/delete', methods=['POST'])
@audio_bp.route('/api/v1/sfx/delete', methods=['POST'])
@csrf.exempt
@validate_request_json('filename')
def delete_sfx():
    """Ses efektini sil"""
    data = request.get_json()
    filename = data.get('filename')
    
    file_path = os.path.join(SFX_DIR, filename)
    
    if not os.path.exists(file_path):
        return jsonify({"success": False, "error": "Dosya bulunamadı"}), 404
    
    try:
        os.remove(file_path)
        return jsonify({"success": True, "message": "Ses efekti silindi"})
    except Exception as e:
        logger.error(f"SFX silme hatası: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@audio_bp.route('/sfx/play/<path:filename>')
@csrf.exempt
def play_sfx(filename):
    """Ses efektini oynat/stream"""
    return send_from_directory(SFX_DIR, filename)



MUSIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'music')

@audio_bp.route('/music', methods=['GET'])
@audio_bp.route('/api/v1/music', methods=['GET'])
@csrf.exempt
def get_music_list():
    """Müzik listesini al"""
    music_files = []
    
    if os.path.exists(MUSIC_DIR):
        for f in os.listdir(MUSIC_DIR):
            ext = f.rsplit('.', 1)[-1].lower() if '.' in f else ''
            if ext in ALLOWED_AUDIO_EXTENSIONS:
                file_path = os.path.join(MUSIC_DIR, f)
                file_size = os.path.getsize(file_path)
                music_files.append({
                    "filename": f,
                    "name": os.path.splitext(f)[0],
                    "extension": ext,
                    "size": file_size,
                    "size_mb": round(file_size / (1024 * 1024), 2)
                })
    
    # İsme göre sırala
    music_files.sort(key=lambda x: x['name'].lower())
    
    return jsonify(music_files)


@audio_bp.route('/music/rename', methods=['POST'])
@audio_bp.route('/api/v1/music/rename', methods=['POST'])
@csrf.exempt
@validate_request_json('old_name', 'new_name')
def rename_music():
    """Müzik dosyasını yeniden adlandır"""
    data = request.get_json()
    old_name = data.get('old_name')
    new_name = data.get('new_name')
    
    if not old_name or not new_name:
        return jsonify({"success": False, "error": "Dosya adları gerekli"}), 400
    
    # Uzantıyı koru
    old_ext = old_name.rsplit('.', 1)[-1] if '.' in old_name else 'mp3'
    
    # Yeni isme uzantı ekle (eğer yoksa)
    if '.' not in new_name:
        new_name = f"{new_name}.{old_ext}"
    
    old_path = os.path.join(MUSIC_DIR, old_name)
    new_path = os.path.join(MUSIC_DIR, new_name)
    
    if not os.path.exists(old_path):
        return jsonify({"success": False, "error": "Dosya bulunamadı"}), 404
    
    if os.path.exists(new_path):
        return jsonify({"success": False, "error": "Bu isimde bir dosya zaten var"}), 400
    
    try:
        os.rename(old_path, new_path)
        return jsonify({"success": True, "message": "Dosya yeniden adlandırıldı", "new_filename": new_name})
    except Exception as e:
        logger.error(f"Müzik yeniden adlandırma hatası: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@audio_bp.route('/music/upload', methods=['POST'])
@audio_bp.route('/api/v1/music/upload', methods=['POST'])
@csrf.exempt
def upload_music():
    """Yeni müzik dosyası yükle"""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "Dosya seçilmedi"}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({"success": False, "error": "Dosya seçilmedi"}), 400
    
    # Uzantı kontrolü
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        return jsonify({
            "success": False, 
            "error": f"Desteklenmeyen format. İzin verilenler: {', '.join(ALLOWED_AUDIO_EXTENSIONS)}"
        }), 400
    
    # Dosya adını temizle
    filename = file.filename
    save_path = os.path.join(MUSIC_DIR, filename)
    
    # Aynı isimde varsa numara ekle
    counter = 1
    name_without_ext = os.path.splitext(filename)[0]
    while os.path.exists(save_path):
        filename = f"{name_without_ext}_{counter}.{ext}"
        save_path = os.path.join(MUSIC_DIR, filename)
        counter += 1
    
    try:
        file.save(save_path)
        return jsonify({
            "success": True, 
            "message": "Müzik yüklendi",
            "filename": filename
        })
    except Exception as e:
        logger.error(f"Müzik yükleme hatası: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@audio_bp.route('/music/delete', methods=['POST'])
@audio_bp.route('/api/v1/music/delete', methods=['POST'])
@csrf.exempt
@validate_request_json('filename')
def delete_music():
    """Müzik dosyasını sil"""
    data = request.get_json()
    filename = data.get('filename')
    
    file_path = os.path.join(MUSIC_DIR, filename)
    
    if not os.path.exists(file_path):
        return jsonify({"success": False, "error": "Dosya bulunamadı"}), 404
    
    try:
        os.remove(file_path)
        return jsonify({"success": True, "message": "Müzik silindi"})
    except Exception as e:
        logger.error(f"Müzik silme hatası: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@audio_bp.route('/music/play/<path:filename>')
@csrf.exempt
def play_music(filename):
    """Müzik dosyasını oynat/stream"""
    return send_from_directory(MUSIC_DIR, filename)


