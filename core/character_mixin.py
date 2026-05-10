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


class CharacterMixin:

    # ========================
    # KARAKTER YÖNETİMİ
    # ========================
    
    def _load_characters(self) -> List[Dict[str, Any]]:
        """Karakterleri yükle"""
        return validate_json_file(config.CHARACTERS_FILE, [])
    
    def _save_characters(self, characters: List[Dict[str, Any]]) -> bool:
        """Karakterleri kaydet"""
        return save_json_file(config.CHARACTERS_FILE, characters)
    
    def get_characters(self) -> List[Dict[str, Any]]:
        """Tüm karakterleri döndür"""
        return self._load_characters()
    
    # ========================
    # DUYGU YÖNETİMİ
    # ========================

    # Desteklenen duygular ve İngilizce görsel tanımları
    EMOTION_MAP = {
        "neutral":    "neutral expression, calm face",
        "happy":      "joyful expression, bright smile, warm eyes",
        "sad":        "sorrowful expression, downcast eyes, slight frown",
        "angry":      "furious expression, clenched jaw, intense glare",
        "scared":     "fearful expression, wide eyes, trembling",
        "surprised":  "shocked expression, raised eyebrows, open mouth",
        "disgusted":  "disgusted expression, wrinkled nose, pursed lips",
        "determined": "focused determined expression, firm jaw, sharp eyes",
        "loving":     "tender loving expression, soft eyes, gentle smile",
        "confused":   "confused expression, furrowed brows, tilted head",
        "tired":      "exhausted expression, heavy eyelids, drooping shoulders",
        "proud":      "proud confident expression, raised chin, slight smirk",
    }

    def set_character_emotion(self, char_id: str, emotion: str) -> bool:
        """
        Karakterin aktif duygu durumunu ayarla

        Args:
            char_id: Karakter ID'si
            emotion: Duygu (EMOTION_MAP anahtarlarından biri)

        Returns:
            Başarılı mı?
        """
        if emotion not in self.EMOTION_MAP:
            logger.warning(f"Geçersiz duygu: {emotion}. Desteklenenler: {list(self.EMOTION_MAP.keys())}")
            return False

        characters = self._load_characters()
        for char in characters:
            if char.get("id") == char_id:
                char["emotion"] = emotion
                char["emotion_updated_at"] = int(time.time())
                success = self._save_characters(characters)
                if success:
                    logger.info(f"Karakter duygusu ayarlandı: {char.get('name')} → {emotion}")
                return success

        logger.warning(f"Duygu ayarlanacak karakter bulunamadı: {char_id}")
        return False

    def get_character_emotions(self) -> Dict[str, str]:
        """
        Tüm karakterlerin aktif duygularını döndür

        Returns:
            {karakter_adı: duygu} sözlüğü
        """
        characters = self._load_characters()
        return {
            c.get("name", "?"): c.get("emotion", "neutral")
            for c in characters
        }

    def add_character(
        self, 
        name: str, 
        description: str, 
        attributes: Optional[Dict[str, Any]] = None,
        emotion: str = "neutral"
    ) -> Tuple[bool, str]:
        """
        Yeni karakter ekle
        
        Args:
            name: Karakter adı
            description: Karakter açıklaması
            attributes: Ek özellikler
            emotion: Başlangıç duygu durumu
            
        Returns:
            (Başarılı mı?, Karakter ID'si)
        """
        if not name or not description:
            logger.warning("Karakter eklenemedi: isim veya açıklama boş")
            return False, ""
        
        characters = self._load_characters()
        
        char_id = str(int(time.time() * 1000))
        new_char = {
            "id": char_id,
            "name": sanitize_input(name, max_length=Constants.MAX_CHARACTER_NAME_LENGTH),
            "description": sanitize_input(description, max_length=Constants.MAX_PROMPT_LENGTH),
            "attributes": attributes or {},
            "emotion": emotion if emotion in self.EMOTION_MAP else "neutral",
            "reference_images": [],   # Referans görseller (dosya yolları)
            "created_at": int(time.time())
        }
        
        characters.append(new_char)
        success = self._save_characters(characters)
        
        if success:
            logger.info(f"Yeni karakter eklendi: {name} (duygu: {emotion})")
        
        return success, char_id


    # ========================
    # REFERANS GÖRSEL YÖNETİMİ
    # ========================

    def add_reference_image(self, char_id: str, file_path: str) -> bool:
        """
        Karaktere referans görsel ekle

        Args:
            char_id: Karakter ID'si
            file_path: Kaydedilen dosyanın yolu (static/... formatında)

        Returns:
            Başarılı mı?
        """
        characters = self._load_characters()
        for char in characters:
            if char.get("id") == char_id:
                refs = char.setdefault("reference_images", [])
                if file_path not in refs:
                    refs.append(file_path)
                success = self._save_characters(characters)
                if success:
                    logger.info(f"Referans görsel eklendi: {char.get('name')} ← {file_path}")
                return success
        logger.warning(f"Referans görsel eklenecek karakter bulunamadı: {char_id}")
        return False

    def remove_reference_image(self, char_id: str, file_path: str) -> bool:
        """
        Karakterden referans görsel kaldır ve fiziksel dosyayı sil

        Args:
            char_id: Karakter ID'si
            file_path: Dosya yolu

        Returns:
            Başarılı mı?
        """
        characters = self._load_characters()
        for char in characters:
            if char.get("id") == char_id:
                refs = char.get("reference_images", [])
                if file_path in refs:
                    refs.remove(file_path)
                    # Fiziksel dosyayı sil
                    abs_path = file_path if os.path.isabs(file_path) else os.path.join(os.getcwd(), file_path)
                    try:
                        if os.path.exists(abs_path):
                            os.remove(abs_path)
                    except Exception as e:
                        logger.warning(f"Referans görsel fiziksel silinemedi: {e}")
                success = self._save_characters(characters)
                if success:
                    logger.info(f"Referans görsel kaldırıldı: {char.get('name')} → {file_path}")
                return success
        return False

    def get_all_reference_images(self) -> List[str]:
        """
        Tüm karakterlerin referans görsellerini düz liste olarak döndür
        (Gemini API'ye toplu gönderim için)

        Returns:
            Mevcut tüm referans görsel yolları
        """
        result = []
        for char in self._load_characters():
            for ref in char.get("reference_images", []):
                # Dosya gerçekten var mı kontrol et
                abs_path = ref if os.path.isabs(ref) else os.path.join(os.getcwd(), ref)
                if os.path.exists(abs_path):
                    result.append(abs_path)
                else:
                    logger.debug(f"Referans görsel bulunamadı (atlandı): {ref}")
        return result


    
    def delete_character(self, char_id: str) -> bool:
        """
        Karakter sil
        
        Args:
            char_id: Karakter ID'si
            
        Returns:
            Başarılı mı?
        """
        characters = self._load_characters()
        original_count = len(characters)
        characters = [c for c in characters if c.get('id') != char_id]
        
        if len(characters) < original_count:
            success = self._save_characters(characters)
            if success:
                logger.info(f"Karakter silindi: {char_id}")
            return success
        
        logger.warning(f"Silinecek karakter bulunamadı: {char_id}")
        return False
    
    def update_character(
        self, 
        char_id: str, 
        name: str, 
        description: str, 
        attributes: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Karakter güncelle
        
        Args:
            char_id: Karakter ID'si
            name: Yeni isim
            description: Yeni açıklama
            attributes: Yeni özellikler
            
        Returns:
            Başarılı mı?
        """
        characters = self._load_characters()
        
        for char in characters:
            if char.get('id') == char_id:
                char['name'] = sanitize_input(name, max_length=Constants.MAX_CHARACTER_NAME_LENGTH)
                char['description'] = sanitize_input(description, max_length=Constants.MAX_PROMPT_LENGTH)
                if attributes:
                    char['attributes'] = attributes
                char['updated_at'] = int(time.time())
                
                success = self._save_characters(characters)
                if success:
                    logger.info(f"Karakter güncellendi: {name}")
                return success
        
        logger.warning(f"Güncellenecek karakter bulunamadı: {char_id}")
        return False
    
    def enhance_character(self, character_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Karakter verilerini AI ile zenginleştir
        
        Args:
            character_data: Mevcut karakter verisi
            
        Returns:
            Zenginleştirilmiş karakter verisi veya None
        """
        logger.info(f"Karakter zenginleştiriliyor: {character_data.get('name', 'Adsız')}")
        
        prompt = f"""
        SEN BİR KARAKTER TASARIM UZMANISIN.
        Aşağıdaki basit karakter taslağını alıp, görsel üretim (AI Image Gen) için çok daha zengin, detaylı ve tutarlı hale getirmelisin.

        MEVCUT VERİ:
        {json.dumps(character_data, indent=2, ensure_ascii=False)}

        GÖREVİN:
        1. Karakterin ismini koru veya daha epik/uygun hale getir (eğer çok basitse).
        2. 'Description' alanını İngilizce olarak, görsel odaklı, detaylı bir prompt haline getir (Yüz hatları, kıyafet dokusu, duruş, atmosfer).
        3. Diğer özellikleri (Hair, Eyes, Outfit, Feature) zenginleştir ama ana fikri koru.
        4. Rolüne uygun bir kişilik kat.

        KISITLAMALAR (SELECT ALANLARI İÇİN SADECE BU DEĞERLERİ KULLAN):
        - Gender: Male, Female, Non-binary, Child Boy, Child Girl, Robot, Creature
        - Eyes: Blue, Brown, Green, Hazel, Black, Red, Grey, Glowing
        - Outfit: Casual, Suit, Detective, Medieval, Sci-Fi, Military, Doctor, Gothic, Summer
        - Role: Protagonist, Antagonist, Sidekick, Mentor, Extra

        Hair ve Feature alanları serbest metindir (İngilizce).

        ÇIKTI FORMATI (SADECE JSON):
        {{
          "name": "...",
          "description": "Detailed visual description in English...",
          "attributes": {{
            "gender": "...",
            "hair": "...",
            "eyes": "...",
            "outfit": "...",
            "feature": "...",
            "role": "..."
          }}
        }}
        """
        
        try:
            response = self._make_text_request(prompt, json_response=True)
            enhanced_data = extract_json_from_response(response)
            
            if enhanced_data:
                logger.info("Karakter başarıyla zenginleştirildi")
                api_stats.record_request(True)
                return enhanced_data
            
            logger.warning("Karakter zenginleştirme yanıtı parse edilemedi")
            api_stats.record_request(False)
            return None
            
        except Exception as e:
            logger.error(f"Karakter zenginleştirme hatası: {e}")
            api_stats.record_request(False)
            return None

    # ========================
    # KARAKTER AVATAR
    # ========================
    
    def generate_character_avatar(
        self,
        char_id: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Karakter için örnek avatar görüntüsü oluştur
        
        Args:
            char_id: Karakter ID'si
            
        Returns:
            (Avatar yolu, Hata mesajı)
        """
        logger.info(f"Karakter avatarı oluşturuluyor: {char_id}")
        
        try:
            characters = self._load_characters()
            char = next((c for c in characters if c.get("id") == char_id), None)
            
            if not char:
                return None, "Karakter bulunamadı."
            
            description = char.get("description", "")
            name = char.get("name", "Character")
            
            avatar_prompt = f"""
            Portrait of {description}.
            Medium close-up shot, looking at camera, neutral professional background.
            Highly detailed, masterpiece quality, vibrant colors, cinematic lighting.
            Style: Digital art, character portrait.
            """
            
            image_data = self._make_image_request(
                avatar_prompt,
                aspect_ratio="1:1",
                image_size="1K"
            )
            
            if image_data:
                timestamp = int(time.time())
                safe_name = sanitize_filename(name, 20)
                file_name = f"avatar_{char_id}_{timestamp}.png"
                file_path = os.path.join(self.output_dir, file_name)
                
                with open(file_path, "wb") as f:
                    f.write(image_data)
                
                # Karaktere avatar ekle (output/ klasöründen serve ediliyor)
                char["avatar"] = f"output/{file_name}"
                self._save_characters(characters)
                
                logger.info(f"Avatar oluşturuldu: {file_name}")
                return char["avatar"], None
            
            return None, "Avatar oluşturulamadı."
            
        except Exception as e:
            logger.error(f"Avatar oluşturma hatası: {e}")
            return None, str(e)
