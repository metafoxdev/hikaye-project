import os
import json
import mimetypes
import struct
import time
import threading
import uuid
import shutil
import zipfile
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Any, Generator, Tuple

from dotenv import load_dotenv
from google import genai
from google.genai import types

try:
    from moviepy import ImageClip, concatenate_videoclips, CompositeVideoClip, ColorClip, VideoFileClip
    from moviepy.video.fx import CrossFadeIn, CrossFadeOut
    from moviepy.audio.io.AudioFileClip import AudioFileClip
    from moviepy.audio.AudioClip import CompositeAudioClip
    from moviepy.audio.fx import volumex, audio_fadein, audio_fadeout
    from moviepy.video.VideoClip import TextClip
    import subprocess
    MOVIEPY_AVAILABLE = True
except ImportError:
    MOVIEPY_AVAILABLE = False

from config import Config, Constants, get_config
from utils import (
    setup_logging, 
    sanitize_filename, 
    sanitize_input,
    validate_json_file, 
    save_json_file,
    retry_with_backoff,
    extract_json_from_response,
    get_prompt_from_scene,
    api_stats,
    RateLimiter
)

config = get_config()
logger = setup_logging(log_file=config.LOG_FILE, log_level=config.LOG_LEVEL)
rate_limiter = RateLimiter(max_calls=config.RATE_LIMIT_PER_MINUTE, period=60)


class MiscMixin:

    def export_for_social_media(
        self,
        history_timestamp: int,
        platform: str,
        music_file: Optional[str] = None,
        audio_settings: Optional[Dict[str, Any]] = None,
        enable_subtitles: bool = True,
        add_watermark: bool = False,
        watermark_text: str = ""
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Sosyal medya platformu için optimize edilmiş video oluştur
        
        Args:
            history_timestamp: Geçmiş kaydı timestamp'ı
            platform: Hedef platform (instagram_reels, youtube_shorts, tiktok, youtube, twitter, facebook)
            music_file: Arkaplan müzik dosyası
            audio_settings: Ses ayarları
            enable_subtitles: Altyazı eklensin mi
            add_watermark: Watermark eklensin mi
            watermark_text: Watermark metni
            
        Returns:
            (Video yolu, Hata mesajı)
        """
        from config import Constants
        
        if platform not in Constants.SOCIAL_MEDIA_FORMATS:
            return None, f"Geçersiz platform: {platform}"
        
        format_config = Constants.SOCIAL_MEDIA_FORMATS[platform]
        logger.info(f"Sosyal medya export: {platform} ({format_config['name']})")
        
        try:
            history = self.get_history()
            entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not entry:
                return None, "Geçmiş kaydı bulunamadı."
            
            images = [img for img in entry.get("images", []) if img]
            
            if not images:
                return None, "Görsel bulunamadı."
            
            # Platform ayarlarına göre frame duration hesapla
            max_duration = format_config.get("max_duration")
            total_scenes = len(images)
            
            if max_duration:
                # Maksimum süreye göre frame duration ayarla
                optimal_frame_duration = max_duration / total_scenes
                frame_duration = min(optimal_frame_duration, entry.get("frame_duration", 5.0))
                
                # Minimum 2 saniye
                frame_duration = max(2.0, frame_duration)
            else:
                frame_duration = entry.get("frame_duration", 5.0)
            
            user_input = f"{entry.get('prompt', 'Video')}_{platform}"
            transition = entry.get("video_transition", "fade")
            scene_audios = entry.get("audios", [])
            
            # Altyazıları hazırla
            subtitles = None
            if enable_subtitles:
                prompts = entry.get("prompts_generated", [])
                subtitles = self._extract_subtitles_from_prompts(prompts)
            
            # Altyazı ayarları - platforma göre optimize
            subtitle_settings = {
                "enabled": enable_subtitles,
                "fontSize": 28 if format_config["aspect_ratio"] == "9:16" else 24,
                "position": "bottom",
                "margin": 80 if format_config["aspect_ratio"] == "9:16" else 50
            }
            
            # Video oluştur
            video_path = self.video_olustur(
                images,
                user_input,
                frame_duration=frame_duration,
                transition=transition,
                music_file=music_file,
                scene_audios=scene_audios if scene_audios else None,
                audio_settings=audio_settings,
                ken_burns_effect="random",  # Sosyal medya için dinamik efekt
                subtitles=subtitles,
                subtitle_settings=subtitle_settings
            )
            
            if video_path:
                logger.info(f"Sosyal medya video oluşturuldu: {platform}")
                return video_path, None
            
            return None, "Video oluşturulamadı."
            
        except Exception as e:
            logger.error(f"Sosyal medya export hatası: {e}")
            return None, str(e)

    # ========================
    # İSTATİSTİKLER
    # ========================
    
    def export_storyboard_pdf(
        self,
        history_timestamp: int,
        include_prompts: bool = True,
        include_dialogues: bool = True,
        layout: str = "grid"  # grid, list, single
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Hikayeyi storyboard PDF olarak dışa aktar
        
        Args:
            history_timestamp: Geçmiş kaydı timestamp'ı
            include_prompts: Prompt'ları dahil et
            include_dialogues: Diyalogları dahil et
            layout: Sayfa düzeni (grid: 2x2, list: 1 sütun, single: sayfa başı 1)
            
        Returns:
            (PDF dosya yolu, Hata mesajı)
        """
        logger.info(f"Storyboard PDF oluşturuluyor: {history_timestamp}")
        
        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4, landscape
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import inch, cm
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, Table, TableStyle, PageBreak
            from reportlab.lib.enums import TA_CENTER, TA_LEFT
            from PIL import Image as PILImage
            import io
            
            history = self.get_history()
            entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not entry:
                return None, "Geçmiş kaydı bulunamadı."
            
            images = [img for img in entry.get("images", []) if img]
            prompts = entry.get("prompts_generated", [])
            user_input = entry.get("prompt", "Hikaye")
            
            if not images:
                return None, "Görsel bulunamadı."
            
            # PDF dosya adı
            timestamp = int(time.time())
            safe_name = sanitize_filename(user_input, 20)
            pdf_filename = f"{timestamp}_storyboard_{safe_name}.pdf"
            pdf_path = os.path.join(self.output_dir, pdf_filename)
            
            # Sayfa boyutu
            page_size = landscape(A4) if layout == "grid" else A4
            
            doc = SimpleDocTemplate(
                pdf_path,
                pagesize=page_size,
                rightMargin=1*cm,
                leftMargin=1*cm,
                topMargin=1*cm,
                bottomMargin=1*cm
            )
            
            # Stiller
            styles = getSampleStyleSheet()
            title_style = ParagraphStyle(
                'CustomTitle',
                parent=styles['Heading1'],
                fontSize=24,
                alignment=TA_CENTER,
                spaceAfter=20
            )
            scene_title_style = ParagraphStyle(
                'SceneTitle',
                parent=styles['Heading2'],
                fontSize=14,
                textColor=colors.darkblue
            )
            prompt_style = ParagraphStyle(
                'Prompt',
                parent=styles['Normal'],
                fontSize=9,
                textColor=colors.gray,
                spaceAfter=10
            )
            
            elements = []
            
            # Başlık
            elements.append(Paragraph(f"Storyboard: {user_input[:50]}...", title_style))
            elements.append(Paragraph(f"Oluşturulma: {entry.get('created_at', 'Bilinmiyor')}", styles['Normal']))
            elements.append(Spacer(1, 20))
            
            # Görselleri işle
            if layout == "grid":
                # 2x2 grid düzeni
                rows = []
                current_row = []
                
                for i, img_path in enumerate(images):
                    full_path = img_path if os.path.isabs(img_path) else os.path.join(os.getcwd(), img_path)
                    
                    if os.path.exists(full_path):
                        # Görsel boyutunu ayarla
                        img_width = 3.5 * inch
                        img_height = 2.5 * inch
                        
                        try:
                            rl_img = RLImage(full_path, width=img_width, height=img_height)
                            
                            # Sahne bilgisi
                            scene_info = [rl_img]
                            scene_info.append(Paragraph(f"<b>Sahne {i+1}</b>", scene_title_style))
                            
                            if include_prompts and i < len(prompts):
                                prompt_text = get_prompt_from_scene(prompts[i])[:150] + "..."
                                scene_info.append(Paragraph(prompt_text, prompt_style))
                            
                            current_row.append(scene_info)
                            
                            if len(current_row) == 2:
                                rows.append(current_row)
                                current_row = []
                        except Exception as e:
                            logger.warning(f"Görsel eklenemedi: {e}")
                
                if current_row:
                    while len(current_row) < 2:
                        current_row.append([])
                    rows.append(current_row)
                
                # Tablo oluştur
                for row in rows:
                    table_data = [[]]
                    for cell in row:
                        if cell:
                            table_data[0].append(cell)
                        else:
                            table_data[0].append("")
                    
                    if table_data[0]:
                        table = Table(table_data, colWidths=[4*inch, 4*inch])
                        table.setStyle(TableStyle([
                            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                            ('PADDING', (0, 0), (-1, -1), 10),
                        ]))
                        elements.append(table)
                        elements.append(Spacer(1, 20))
            
            else:
                # Liste veya tek sayfa düzeni
                for i, img_path in enumerate(images):
                    full_path = img_path if os.path.isabs(img_path) else os.path.join(os.getcwd(), img_path)
                    
                    if os.path.exists(full_path):
                        elements.append(Paragraph(f"<b>Sahne {i+1}</b>", scene_title_style))
                        
                        try:
                            img_width = 5 * inch if layout == "list" else 7 * inch
                            img_height = 3.5 * inch if layout == "list" else 5 * inch
                            
                            rl_img = RLImage(full_path, width=img_width, height=img_height)
                            elements.append(rl_img)
                        except Exception as e:
                            logger.warning(f"Görsel eklenemedi: {e}")
                        
                        if include_prompts and i < len(prompts):
                            prompt_text = get_prompt_from_scene(prompts[i])
                            elements.append(Paragraph(f"<i>{prompt_text[:300]}...</i>", prompt_style))
                        
                        elements.append(Spacer(1, 20))
                        
                        if layout == "single":
                            elements.append(PageBreak())
            
            # PDF oluştur
            doc.build(elements)
            
            logger.info(f"Storyboard PDF oluşturuldu: {pdf_filename}")
            return f"static/output/{pdf_filename}", None
            
        except ImportError:
            return None, "PDF oluşturmak için 'reportlab' kütüphanesi gerekli. pip install reportlab"
        except Exception as e:
            logger.error(f"PDF oluşturma hatası: {e}")
            return None, str(e)

    def get_stats(self) -> Dict[str, Any]:
        """Uygulama istatistiklerini döndür"""
        history = self.get_history()
        characters = self._load_characters()
        
        total_images = sum(len([img for img in h.get("images", []) if img]) for h in history)
        total_videos = sum(1 for h in history if h.get("video"))
        
        return {
            **api_stats.get_stats(),
            "total_stories": len(history),
            "total_generated_images": total_images,
            "total_generated_videos": total_videos,
            "total_characters": len(characters)
        }

    # ========================
    # ŞABLON YÖNETİMİ
    # ========================
    
    def get_templates(self) -> List[Dict[str, Any]]:
        """Hikaye şablonlarını döndür"""
        templates_file = os.path.join(config.DATA_DIR, "templates.json")
        return validate_json_file(templates_file, [])
    
    def get_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        """Belirli bir şablonu döndür"""
        templates = self.get_templates()
        return next((t for t in templates if t.get("id") == template_id), None)

    # ========================
    # HİKAYE ÖNERİLERİ
    # ========================
    
    def get_story_suggestions(
        self,
        genre: str = "any",
        count: int = 5
    ) -> List[str]:
        """
        AI ile hikaye önerileri al
        
        Args:
            genre: Tür (romance, action, horror, vb.)
            count: Öneri sayısı
            
        Returns:
            Öneri listesi
        """
        logger.info(f"Hikaye önerileri alınıyor: {genre}")
        
        prompt = f"""
        Kısa video hikaye fikirleri öner.
        Tür: {genre if genre != 'any' else 'Herhangi'}
        
        {count} adet benzersiz, ilgi çekici ve dramatik hikaye konsepti yaz.
        Her biri 1-2 cümle olsun.
        Karakterler ve gerilim unsurları içersin.
        
        JSON array formatında döndür: ["öneri1", "öneri2", ...]
        """
        
        try:
            response = self._make_text_request(prompt, json_response=True)
            suggestions = extract_json_from_response(response)
            
            if suggestions and isinstance(suggestions, list):
                return suggestions[:count]
            return []
            
        except Exception as e:
            logger.error(f"Öneri alma hatası: {e}")
            return []

    # ========================
    # EXPORT FONKSİYONLARI
    # ========================
    
    def export_project(
        self,
        history_timestamp: int,
        format: str = "json"
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Projeyi dışa aktar
        
        Args:
            history_timestamp: Geçmiş kaydı timestamp'ı
            format: Format (json, zip)
            
        Returns:
            (Dosya yolu, Hata mesajı)
        """
        import zipfile
        import shutil
        
        logger.info(f"Proje dışa aktarılıyor: {history_timestamp} ({format})")
        
        try:
            history = self.get_history()
            entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not entry:
                return None, "Geçmiş kaydı bulunamadı."
            
            timestamp = int(time.time())
            safe_name = sanitize_filename(entry.get("prompt", "export"), 30)
            
            if format == "json":
                # Sadece JSON
                filename = f"export_{safe_name}_{timestamp}.json"
                filepath = os.path.join(self.output_dir, filename)
                
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(entry, f, ensure_ascii=False, indent=2)
                
                return f"static/output/{filename}", None
            
            elif format == "zip":
                # Görseller ve JSON ile birlikte ZIP
                filename = f"export_{safe_name}_{timestamp}.zip"
                filepath = os.path.join(self.output_dir, filename)
                
                with zipfile.ZipFile(filepath, 'w', zipfile.ZIP_DEFLATED) as zf:
                    # JSON ekle
                    json_data = json.dumps(entry, ensure_ascii=False, indent=2)
                    zf.writestr("project.json", json_data)
                    
                    # Görselleri ekle
                    for i, img_path in enumerate(entry.get("images", [])):
                        if img_path:
                            full_path = os.path.join(os.getcwd(), img_path)
                            if os.path.exists(full_path):
                                ext = os.path.splitext(img_path)[1]
                                zf.write(full_path, f"images/scene_{i+1}{ext}")
                    
                    # Video ekle
                    video_path = entry.get("video")
                    if video_path:
                        full_path = os.path.join(os.getcwd(), video_path)
                        if os.path.exists(full_path):
                            zf.write(full_path, "video/story.mp4")
                
                return f"static/output/{filename}", None
            
            return None, "Desteklenmeyen format."
            
        except Exception as e:
            logger.error(f"Export hatası: {e}")
            return None, str(e)
    
    def import_project(
        self,
        file_path: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        JSON projesini içe aktar
        
        Args:
            file_path: JSON dosya yolu
            
        Returns:
            (Proje verisi, Hata mesajı)
        """
        logger.info(f"Proje içe aktarılıyor: {file_path}")
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                project_data = json.load(f)
            
            # Yeni timestamp ver
            project_data["timestamp"] = int(time.time())
            project_data["imported"] = True
            
            # Geçmişe ekle
            self._save_to_history(project_data)
            
            return project_data, None
            
        except Exception as e:
            logger.error(f"Import hatası: {e}")
            return None, str(e)

    # ========================
    # AI HİKAYE ÖNERİLERİ
    # ========================
    
    def get_story_suggestions(
        self,
        category: str = "all",
        count: int = 5
    ) -> Tuple[Optional[List[Dict]], Optional[str]]:
        """
        AI tabanlı hikaye önerileri al
        
        Args:
            category: Kategori (all, adventure, romance, horror, scifi, fantasy, comedy, drama)
            count: Öneri sayısı
            
        Returns:
            (Öneriler listesi, Hata mesajı)
        """
        logger.info(f"Hikaye önerileri alınıyor: {category}, {count} adet")
        
        category_prompts = {
            "adventure": "macera, aksiyon, keşif",
            "romance": "romantik, aşk, duygusal",
            "horror": "korku, gerilim, gizem",
            "scifi": "bilim kurgu, uzay, gelecek, teknoloji",
            "fantasy": "fantastik, büyü, ejderhalar, mitoloji",
            "comedy": "komedi, eğlenceli, mizah",
            "drama": "drama, duygusal, gerçekçi",
            "all": "çeşitli türlerde"
        }
        
        category_desc = category_prompts.get(category, category_prompts["all"])
        
        prompt = f"""
        {count} adet özgün ve ilgi çekici hikaye önerisi oluştur.
        Kategori: {category_desc}
        
        Her öneri için şunları ver:
        1. title: Kısa ve çarpıcı bir başlık
        2. description: 1-2 cümlelik özet
        3. prompt: Görsel hikaye oluşturmak için kullanılabilecek detaylı prompt (3-4 cümle)
        4. tags: 3-5 adet etiket
        5. mood: Atmosfer (dark, bright, mysterious, romantic, epic, etc.)
        6. style: Önerilen sanat stili (realistic, anime, comic, noir, watercolor, 3d)
        
        JSON formatında döndür:
        {{
            "suggestions": [
                {{
                    "title": "...",
                    "description": "...",
                    "prompt": "...",
                    "tags": ["...", "..."],
                    "mood": "...",
                    "style": "..."
                }}
            ]
        }}
        """
        
        try:
            response = self._make_text_request(prompt, json_response=True)
            data = extract_json_from_response(response)
            
            if data and "suggestions" in data:
                return data["suggestions"], None
            
            # Fallback: Statik öneriler
            logger.warning("API önerileri alınamadı, statik öneriler kullanılıyor")
            return self._get_fallback_suggestions(category, count), None
            
        except Exception as e:
            logger.error(f"Hikaye önerileri hatası: {e}")
            # Fallback döndür
            return self._get_fallback_suggestions(category, count), None
    
    def _get_fallback_suggestions(self, category: str, count: int) -> List[Dict]:
        """API çalışmazsa statik öneriler döndür"""
        fallback = {
            "adventure": [
                {"title": "Kayıp Şehir", "description": "Antik bir haritanın peşinde Amazon ormanlarında macera", "prompt": "Genç bir arkeolog, dedesinden kalan antik haritayla Amazon ormanlarının derinliklerinde kayıp bir Maya şehrini arıyor. Tehlikeli tuzaklar ve gizemli koruyucularla karşılaşıyor.", "tags": ["macera", "keşif", "antik"], "mood": "epic", "style": "realistic"},
                {"title": "Uzay Korsanları", "description": "Galaksiler arası kaçakçılık ve macera", "prompt": "2350 yılında, asi bir uzay gemisi kaptanı ve ekibi, galaktik imparatorluktan kaçırılan prensesi kurtarmak için tehlikeli bir göreve çıkıyor.", "tags": ["uzay", "aksiyon", "kaçış"], "mood": "dynamic", "style": "scifi"}
            ],
            "romance": [
                {"title": "Yağmurlu İstanbul", "description": "İki yabancının tesadüfi karşılaşması", "prompt": "Yağmurlu bir İstanbul akşamında, aynı kafede sığınan iki yabancı. O bir yazar, o bir ressam. Kahve kokulu sohbetler aşka dönüşüyor.", "tags": ["aşk", "istanbul", "sanat"], "mood": "romantic", "style": "realistic"},
                {"title": "Paralel Kalpler", "description": "Farklı dünyalardan iki ruh", "prompt": "Zengin bir iş kadını ve sokak müzisyeni, her gün aynı metro istasyonunda karşılaşıyor. Sınıflar arasındaki uçurum aşkla kapanabilir mi?", "tags": ["romantik", "drama", "şehir"], "mood": "warm", "style": "realistic"}
            ],
            "horror": [
                {"title": "Ayna Odası", "description": "Yansımalar gerçeği göstermeyebilir", "prompt": "Terk edilmiş bir malikanede, antika aynalar koleksiyonu bulan bir grup arkadaş. Aynalardaki yansımaları onları takip etmeye başlıyor.", "tags": ["korku", "gerilim", "doğaüstü"], "mood": "dark", "style": "noir"},
                {"title": "Son Otobüs", "description": "Gece yarısı otobüsünde tuhaf yolcular", "prompt": "Gece vardiyasından dönen bir hemşire, son otobüste garip yolcularla karşılaşıyor. Hiçbiri inmiyor ve şoförün yüzü görünmüyor.", "tags": ["korku", "gizem", "gerilim"], "mood": "mysterious", "style": "noir"}
            ],
            "all": [
                {"title": "Zaman Kapsülü", "description": "50 yıl sonra açılan mektuplar", "prompt": "Bir kasaba, 50 yıl önce gömülen zaman kapsülünü açıyor. İçindeki mektuplar, kasaba halkının hayatını değiştirecek sırlar barındırıyor.", "tags": ["drama", "gizem", "aile"], "mood": "mysterious", "style": "realistic"},
                {"title": "Robot Kalbi", "description": "Yapay zeka duygular öğreniyor", "prompt": "2080 yılında, yaşlı bir adamın bakıcısı olan robot, sahibinin ölümüyle ilk kez üzüntüyü deneyimliyor ve insan olmayı sorguluyor.", "tags": ["bilimkurgu", "duygusal", "felsefe"], "mood": "warm", "style": "scifi"}
            ]
        }
        
        suggestions = fallback.get(category, fallback["all"])
        return suggestions[:count]
