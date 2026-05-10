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


class ScenarioMixin:

    # ========================
    # SENARYO OLUŞTURMA
    # ========================
    
    def _get_system_prompt(self) -> str:
        """Sistem promptunu al"""
        settings = self._load_settings()
        default_prompt = """SEN BİR ÇİZGİ ROMAN SENARİSTİ VE SANAT YÖNETMENİSİN.
Görevin, verilen hikaye bölümüne dayanarak detaylı görsel promptlar yazmaktır."""
        return settings.get("system_prompt", default_prompt)
    
    def _build_character_prompt(self) -> str:
        """Karakter promptu oluştur (duygu yönetimi dahil)"""
        characters = self._load_characters()
        
        if not characters:
            return ""
        
        # EMOTION_MAP referansı: CharacterMixin'den inherit ediliyor
        emotion_map = getattr(self, "EMOTION_MAP", {})

        prompt = "### KARAKTER PROFİLLERİ (BU TANIMLARI KULLAN):\n"
        for char in characters:
            name = char.get("name", "Adsız")
            desc = char.get("description", "")
            emotion_key = char.get("emotion", "neutral")
            emotion_desc = emotion_map.get(emotion_key, "neutral expression, calm face")

            prompt += (
                f"- {name}: {desc}. "
                f"[CURRENT EMOTION: {emotion_desc}]\n"
            )

        prompt += (
            "\n(Eğer hikayede bu karakterlerin adı geçiyorsa, görsel tasvirlerini ve "
            "CURRENT EMOTION'ı birebir yüz ifadesine yansıt.)"
        )

        return prompt
    
    def senaryo_olustur(
        self,
        bolum_istegi: str,
        scene_count: int = 7,
        dialogue_style: str = "short",
        art_style: str = "comic",
        mood_style: str = "dynamic",
        camera_style: str = "balanced",
        time_of_day: str = "auto",
        season: str = "auto",
        weather: str = "auto",
        outfit_style: str = "auto",
        character_consistency: str = "strict"
    ) -> List[str]:
        """
        Senaryo ve görsel promptları oluştur
        
        Args:
            bolum_istegi: Hikaye konusu
            scene_count: Sahne sayısı
            dialogue_style: Diyalog tarzı
            art_style: Sanat tarzı
            mood_style: Atmosfer
            camera_style: Kamera stili
            time_of_day: Zaman dilimi
            season: Mevsim
            weather: Hava durumu
            outfit_style: Kıyafet tarzı
            character_consistency: Karakter tutarlılığı
            
        Returns:
            Görsel promptları listesi
        """
        logger.info(f"Senaryo oluşturuluyor: {bolum_istegi[:50]}... ({scene_count} sahne)")
        
        # Ayarları al
        system_prompt = self._get_system_prompt()
        character_block = self._build_character_prompt()
        
        # Diyalog talimatı
        dialogue_map = {
            "short": "Konuşma balonları kısa, öz ve vurucu olsun.",
            "long": "Konuşma balonları detaylı ve hikayeyi derinleştiren yapıda olsun.",
            "none": "HİÇBİR ŞEKİLDE konuşma balonu veya yazı ekleme. Sadece görsel anlatıma odaklan."
        }
        dialogue_instruction = dialogue_map.get(dialogue_style, dialogue_map["short"])
        
        # Stil haritaları
        selected_art = Constants.ART_STYLES.get(art_style, Constants.ART_STYLES["comic"])
        selected_mood = Constants.MOOD_STYLES.get(mood_style, Constants.MOOD_STYLES["dynamic"])
        selected_camera = Constants.CAMERA_STYLES.get(camera_style, Constants.CAMERA_STYLES["balanced"])
        
        # Ortam detayları
        env_details = []
        if time_of_day != "auto" and time_of_day in Constants.TIME_OF_DAY:
            env_details.append(f"Zaman: {Constants.TIME_OF_DAY[time_of_day]}")
        if season != "auto" and season in Constants.SEASONS:
            env_details.append(f"Mevsim: {Constants.SEASONS[season]}")
        if weather != "auto" and weather in Constants.WEATHER:
            env_details.append(f"Hava: {Constants.WEATHER[weather]}")
        
        env_string = " | ".join(env_details) if env_details else "Hikaye akışına uygun olarak belirle."
        
        # Kıyafet talimatı
        outfit_instruction = ""
        if outfit_style != "auto" and outfit_style in Constants.OUTFIT_STYLES:
            outfit_desc = Constants.OUTFIT_STYLES[outfit_style]
            outfit_instruction = f"- KARAKTER KIYAFETLERİ: Tüm karakterler şu tarzda giyinmeli: {outfit_desc}"
        
        # Tutarlılık talimatı
        consistency_instruction = ""
        if character_consistency == "strict":
            consistency_instruction = """
            TUTARLILIK KURALLARI (ÇOK ÖNEMLİ):
            1. Fiziksel yapı her karede %100 aynı kalmalı
            2. Yüz hatları değişmemeli
            3. Kıyafetler senaryoda özel değişim yoksa aynı kalmalı
            """
        
        full_prompt = f"""
        {system_prompt}
        
        {character_block}
        
        {consistency_instruction}
        
        GÖRSEL STİL TANIMLARI:
        - Stil: {selected_art}
        - Atmosfer: {selected_mood}
        - Kamera: {selected_camera}
        - Ortam: {env_string}
        {outfit_instruction}
        
        DİYALOG TARZI: {dialogue_instruction}
        
        GÖREV:
        Verilen hikaye/konu ("{bolum_istegi}") için {scene_count} görsel sahneden oluşan detaylı bir senaryo yaz.
        Her sahne için İngilizce, "Text-to-Image" modeline uygun, karakterlerin fiziksel özelliklerini TEKRAR EDEN bir prompt oluştur.
        
        ÖNEMLİ: Her sahne promptunda, o sahnede yer alan karakterin fiziksel özelliklerini tekrar yaz.
        
        Lütfen bu bölüm için {scene_count} adet sıralı görsel promptunu JSON array formatında oluştur.
        Sadece string'lerden oluşan bir liste döndür: ["prompt1", "prompt2", ...]
        """
        
        try:
            response_text = self._make_text_request(full_prompt, json_response=True)
            prompts = extract_json_from_response(response_text)
            
            if prompts and isinstance(prompts, list):
                # String olmayan elemanları string'e çevir
                prompts = [get_prompt_from_scene(p) for p in prompts]
                logger.info(f"Senaryo oluşturuldu: {len(prompts)} sahne")
                api_stats.record_request(True)
                return prompts[:scene_count]
            
            logger.error("Senaryo yanıtı parse edilemedi")
            api_stats.record_request(False)
            return []
            
        except Exception as e:
            logger.error(f"Senaryo oluşturma hatası: {e}")
            api_stats.record_request(False)
            return []
    
    def enhance_story_prompt(
        self,
        prompt: str,
        style: str = "cinematic"
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Hikaye promptunu AI ile zenginleştir
        
        Args:
            prompt: Kullanıcının girdiği basit prompt
            style: Hedef stil (cinematic, anime, comic, noir, fantasy)
            
        Returns:
            (Zenginleştirilmiş prompt, Hata mesajı)
        """
        logger.info(f"Prompt zenginleştiriliyor: {prompt[:50]}...")
        
        style_guides = {
            "cinematic": "sinematik, film kalitesinde, dramatik ışıklandırma, profesyonel kamera açıları",
            "anime": "anime tarzı, canlı renkler, ekspresif karakterler, dinamik pozlar",
            "comic": "çizgi roman tarzı, kalın çizgiler, canlı renkler, aksiyon dolu",
            "noir": "film noir, siyah-beyaz tonlar, gölgeli, gizemli atmosfer",
            "fantasy": "fantastik, büyülü, detaylı dünya inşası, epik sahneler"
        }
        
        style_desc = style_guides.get(style, style_guides["cinematic"])
        
        enhance_prompt = f"""
        Aşağıdaki basit hikaye fikrini, görsel hikaye oluşturmak için zengin ve detaylı bir prompt'a dönüştür.
        
        Orijinal fikir: {prompt}
        
        Hedef stil: {style_desc}
        
        Zenginleştirirken:
        1. Karakterlerin fiziksel özelliklerini detaylandır
        2. Mekan ve atmosferi tanımla
        3. Duygusal tonu belirt
        4. Görsel detaylar ekle (ışık, renk paleti, kompozisyon)
        5. Hikaye akışını 5-7 sahneye böl
        
        Sadece zenginleştirilmiş prompt'u döndür, başka açıklama yapma.
        Türkçe yaz.
        """
        
        try:
            response = self._make_text_request(enhance_prompt)
            
            if response and len(response) > len(prompt):
                return response.strip(), None
            
            return None, "Prompt zenginleştirilemedi"
            
        except Exception as e:
            logger.error(f"Prompt zenginleştirme hatası: {e}")
            return None, str(e)
