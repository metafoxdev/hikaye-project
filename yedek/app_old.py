"""
Flask Web Sunucusu
AI Hikaye Resimleyici Ana Uygulama
"""

import os
import json
import secrets
from functools import wraps

from flask import (
    Flask, 
    render_template, 
    request, 
    jsonify, 
    stream_with_context, 
    Response,
    send_from_directory,
    abort
)
from flask_cors import CORS
from flask_wtf.csrf import CSRFProtect, generate_csrf

# Yerel modüller
from config import Config, get_config
from utils import setup_logging, sanitize_input, api_stats

# Konfigürasyon
config = get_config()
config.init_directories()

# Logger
logger = setup_logging(
    log_file=config.LOG_FILE,
    log_level=config.LOG_LEVEL
)

# Flask uygulaması
app = Flask(__name__)
app.config['SECRET_KEY'] = config.SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = config.MAX_CONTENT_LENGTH
app.config['WTF_CSRF_TIME_LIMIT'] = None  # CSRF token süresiz

# CORS ve CSRF
CORS(app, resources={r"/api/*": {"origins": "*"}})
csrf = CSRFProtect(app)

# Generator'ı lazy load
generator = None


def get_generator():
    """Generator'ı lazy load ile al"""
    global generator
    if generator is None:
        try:
            from logic import StoryGenerator
            generator = StoryGenerator()
            logger.info("StoryGenerator başarıyla yüklendi")
        except Exception as e:
            logger.error(f"Generator başlatılamadı: {e}")
            return None
    return generator


def require_generator(f):
    """Generator gerektiren endpoint'ler için dekoratör"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        gen = get_generator()
        if gen is None:
            return jsonify({
                "error": "Sistem başlatılamadı. Lütfen API anahtarınızı kontrol edin.",
                "code": "GENERATOR_ERROR"
            }), 500
        return f(gen, *args, **kwargs)
    return decorated_function


def validate_request_json(*required_fields):
    """JSON request doğrulama dekoratörü"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not request.is_json:
                return jsonify({
                    "error": "JSON formatında veri bekleniyor",
                    "code": "INVALID_FORMAT"
                }), 400
            
            data = request.get_json()
            missing = [field for field in required_fields if field not in data]
            
            if missing:
                return jsonify({
                    "error": f"Eksik alanlar: {', '.join(missing)}",
                    "code": "MISSING_FIELDS"
                }), 400
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# ========================
# ANA SAYFALAR
# ========================

@app.route('/')
def index():
    """Ana sayfa"""
    return render_template('index.html')


@app.route('/output/<path:filename>')
@csrf.exempt
def serve_output(filename):
    """Output klasöründeki dosyaları serve et (görseller, avatarlar vb.)"""
    # config.OUTPUT_DIR = static/output
    return send_from_directory(config.OUTPUT_DIR, filename)


@app.route('/static/output/<path:filename>')
@csrf.exempt
def serve_static_output(filename):
    """Eski static/output yolunu da destekle (geriye uyumluluk)"""
    return send_from_directory(config.OUTPUT_DIR, filename)


@app.route('/health')
@csrf.exempt
def health_check():
    """Sağlık kontrolü endpoint'i"""
    gen = get_generator()
    return jsonify({
        "status": "healthy" if gen else "degraded",
        "generator": "ready" if gen else "not_initialized"
    })


# ========================
# HİKAYE OLUŞTURMA
# ========================

@app.route('/generate', methods=['POST'])
@app.route('/api/v1/generate', methods=['POST'])
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
            "generate_audio": data.get('generate_audio', False)  # Seslendirme
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


# ========================
# GEÇMİŞ YÖNETİMİ
# ========================

@app.route('/history', methods=['GET'])
@app.route('/api/v1/history', methods=['GET'])
@csrf.exempt
@require_generator
def get_history(gen):
    """Geçmiş listesi"""
    return jsonify(gen.get_history())


@app.route('/history/delete', methods=['POST'])
@app.route('/api/v1/history/delete', methods=['POST'])
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


@app.route('/history/clear', methods=['POST'])
@app.route('/api/v1/history/clear', methods=['POST'])
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


# ========================
# KARAKTER YÖNETİMİ
# ========================

@app.route('/characters', methods=['GET'])
@app.route('/api/v1/characters', methods=['GET'])
@csrf.exempt
@require_generator
def get_characters(gen):
    """Karakter listesi"""
    return jsonify(gen.get_characters())


@app.route('/characters/add', methods=['POST'])
@app.route('/api/v1/characters/add', methods=['POST'])
@csrf.exempt
@require_generator
@validate_request_json('name', 'description')
def add_character(gen):
    """Yeni karakter ekle"""
    data = request.get_json()
    
    if gen.add_character(
        name=data.get('name'),
        description=data.get('description'),
        attributes=data.get('attributes', {})
    ):
        return jsonify({"success": True, "message": "Karakter eklendi"})
    
    return jsonify({
        "success": False,
        "error": "Karakter eklenemedi"
    }), 400


@app.route('/characters/delete', methods=['POST'])
@app.route('/api/v1/characters/delete', methods=['POST'])
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


@app.route('/characters/update', methods=['POST'])
@app.route('/api/v1/characters/update', methods=['POST'])
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


@app.route('/characters/enhance', methods=['POST'])
@app.route('/api/v1/characters/enhance', methods=['POST'])
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


# ========================
# AYAR YÖNETİMİ
# ========================

@app.route('/settings', methods=['GET', 'POST'])
@app.route('/api/v1/settings', methods=['GET', 'POST'])
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


# ========================
# SAHNE YENİLEME
# ========================

@app.route('/regenerate_scene', methods=['POST'])
@app.route('/api/v1/regenerate_scene', methods=['POST'])
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


@app.route('/regenerate_scene_with_prompt', methods=['POST'])
@app.route('/api/v1/regenerate_scene_with_prompt', methods=['POST'])
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


@app.route('/regenerate_match_style', methods=['POST'])
@app.route('/api/v1/regenerate_match_style', methods=['POST'])
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


# ========================
# GÖRSEL VARYASYONLARI
# ========================

@app.route('/scene/variations', methods=['POST'])
@app.route('/api/v1/scene/variations', methods=['POST'])
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


@app.route('/scene/select_variation', methods=['POST'])
@app.route('/api/v1/scene/select_variation', methods=['POST'])
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


# ========================
# SESLENDİRME YENİDEN OLUŞTURMA
# ========================

@app.route('/regenerate_audio', methods=['POST'])
@app.route('/api/v1/regenerate_audio', methods=['POST'])
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


# ========================
# VIDEO OLUŞTURMA
# ========================

@app.route('/create_video', methods=['POST'])
@app.route('/api/v1/create_video', methods=['POST'])
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


@app.route('/merge_stories', methods=['POST'])
@app.route('/api/v1/merge_stories', methods=['POST'])
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


# ========================
# SAHNE YÖNETİMİ (YENİ)
# ========================

@app.route('/scene/delete', methods=['POST'])
@app.route('/api/v1/scene/delete', methods=['POST'])
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


@app.route('/scene/add', methods=['POST'])
@app.route('/api/v1/scene/add', methods=['POST'])
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


@app.route('/scene/reorder', methods=['POST'])
@app.route('/api/v1/scene/reorder', methods=['POST'])
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


# ========================
# HİKAYE DALLANMASI
# ========================

@app.route('/story/branch', methods=['POST'])
@app.route('/api/v1/story/branch', methods=['POST'])
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


@app.route('/story/branches', methods=['GET'])
@app.route('/api/v1/story/branches', methods=['GET'])
@csrf.exempt
@require_generator
def get_story_branches(gen):
    """Hikayenin dallarını getir"""
    timestamp = request.args.get('timestamp', type=int)
    
    if not timestamp:
        return jsonify({"error": "timestamp parametresi gerekli"}), 400
    
    branches = gen.get_story_branches(timestamp)
    return jsonify({"branches": branches})


# ========================
# HİKAYE DEVAM
# ========================

@app.route('/story/continue', methods=['POST'])
@app.route('/api/v1/story/continue', methods=['POST'])
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


@app.route('/story/suggestions', methods=['GET'])
@app.route('/api/v1/story/suggestions', methods=['GET'])
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


# ========================
# KARAKTER AVATAR
# ========================

@app.route('/characters/avatar', methods=['POST'])
@app.route('/api/v1/characters/avatar', methods=['POST'])
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


@app.route('/story/enhance', methods=['POST'])
@app.route('/api/v1/story/enhance', methods=['POST'])
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


# ========================
# SOSYAL MEDYA EXPORT
# ========================

@app.route('/export/social', methods=['GET'])
@app.route('/api/v1/export/social', methods=['GET'])
@csrf.exempt
def get_social_formats():
    """Sosyal medya formatlarını listele"""
    from config import Constants
    return jsonify(Constants.SOCIAL_MEDIA_FORMATS)


@app.route('/export/social', methods=['POST'])
@app.route('/api/v1/export/social', methods=['POST'])
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


# ========================
# STORYBOARD PDF EXPORT
# ========================

@app.route('/export/pdf', methods=['POST'])
@app.route('/api/v1/export/pdf', methods=['POST'])
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


# ========================
# ŞABLONLAR
# ========================

@app.route('/templates', methods=['GET'])
@app.route('/api/v1/templates', methods=['GET'])
@csrf.exempt
@require_generator
def get_templates(gen):
    """Hikaye şablonlarını al"""
    return jsonify(gen.get_templates())


@app.route('/templates/<template_id>', methods=['GET'])
@app.route('/api/v1/templates/<template_id>', methods=['GET'])
@csrf.exempt
@require_generator
def get_template(gen, template_id):
    """Belirli şablonu al"""
    template = gen.get_template(template_id)
    
    if template:
        return jsonify(template)
    
    return jsonify({"error": "Şablon bulunamadı"}), 404


# ========================
# EXPORT/IMPORT
# ========================

@app.route('/export', methods=['POST'])
@app.route('/api/v1/export', methods=['POST'])
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


# ========================
# DİL DESTEĞİ
# ========================

@app.route('/locale/<lang>', methods=['GET'])
@app.route('/api/v1/locale/<lang>', methods=['GET'])
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


@app.route('/locales', methods=['GET'])
@app.route('/api/v1/locales', methods=['GET'])
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


# ========================
# SES EFEKTLERİ YÖNETİMİ
# ========================

SFX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sfx')
ALLOWED_AUDIO_EXTENSIONS = {'mp3', 'wav', 'ogg', 'm4a', 'aac'}

@app.route('/sfx', methods=['GET'])
@app.route('/api/v1/sfx', methods=['GET'])
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


@app.route('/sfx/upload', methods=['POST'])
@app.route('/api/v1/sfx/upload', methods=['POST'])
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


@app.route('/sfx/delete', methods=['POST'])
@app.route('/api/v1/sfx/delete', methods=['POST'])
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


@app.route('/sfx/play/<path:filename>')
@csrf.exempt
def play_sfx(filename):
    """Ses efektini oynat/stream"""
    return send_from_directory(SFX_DIR, filename)


# ========================
# MÜZİK YÖNETİMİ
# ========================

MUSIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'music')

@app.route('/music', methods=['GET'])
@app.route('/api/v1/music', methods=['GET'])
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


@app.route('/music/rename', methods=['POST'])
@app.route('/api/v1/music/rename', methods=['POST'])
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


@app.route('/music/upload', methods=['POST'])
@app.route('/api/v1/music/upload', methods=['POST'])
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


@app.route('/music/delete', methods=['POST'])
@app.route('/api/v1/music/delete', methods=['POST'])
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


@app.route('/music/play/<path:filename>')
@csrf.exempt
def play_music(filename):
    """Müzik dosyasını oynat/stream"""
    return send_from_directory(MUSIC_DIR, filename)


# ========================
# DOSYA İNDİRME
# ========================

@app.route('/download/<path:filename>')
@app.route('/api/v1/download/<path:filename>')
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


# ========================
# İSTATİSTİKLER
# ========================

@app.route('/stats', methods=['GET'])
@app.route('/api/v1/stats', methods=['GET'])
@csrf.exempt
@require_generator
def get_stats(gen):
    """Uygulama istatistikleri"""
    return jsonify(gen.get_stats())


# ========================
# CSRF TOKEN
# ========================

@app.route('/csrf-token', methods=['GET'])
def get_csrf_token():
    """CSRF token al"""
    return jsonify({"csrf_token": generate_csrf()})


# ========================
# HATA YÖNETİMİ
# ========================

@app.errorhandler(400)
def bad_request(error):
    """400 Bad Request"""
    return jsonify({
        "error": "Geçersiz istek",
        "code": "BAD_REQUEST"
    }), 400


@app.errorhandler(404)
def not_found(error):
    """404 Not Found"""
    return jsonify({
        "error": "Kaynak bulunamadı",
        "code": "NOT_FOUND"
    }), 404


@app.errorhandler(500)
def internal_error(error):
    """500 Internal Server Error"""
    logger.error(f"Internal error: {error}")
    return jsonify({
        "error": "Sunucu hatası",
        "code": "INTERNAL_ERROR"
    }), 500


@app.errorhandler(413)
def request_entity_too_large(error):
    """413 Request Entity Too Large"""
    return jsonify({
        "error": "Dosya boyutu çok büyük",
        "code": "FILE_TOO_LARGE"
    }), 413


# ========================
# CONTEXT PROCESSORS
# ========================

@app.context_processor
def inject_globals():
    """Template'lere global değişkenler ekle"""
    return {
        "app_name": "AI Hikaye Resimleyici",
        "version": "2.0.0"
    }


# ========================
# UYGULAMA BAŞLATMA
# ========================

if __name__ == '__main__':
    # Konfigürasyon doğrulama
    errors = config.validate()
    if errors:
        for error in errors:
            logger.warning(f"Konfigürasyon uyarısı: {error}")
    
    # Geliştirme modunda çalıştır
    logger.info("=" * 50)
    logger.info("AI Hikaye Resimleyici başlatılıyor...")
    logger.info(f"Ortam: {config.FLASK_ENV}")
    logger.info(f"Debug: {config.DEBUG}")
    logger.info("=" * 50)
    
    app.run(
        host='0.0.0.0',
        port=9900,
        debug=config.DEBUG,
        threaded=True
    )
