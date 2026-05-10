"""
Hikaye Oluşturucu Ana Mantık Modülü
Tüm AI işlemleri ve iş mantığı burada yönetilir.
"""

import os
import json
import mimetypes
import struct
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Any, Generator, Tuple

from dotenv import load_dotenv
from google import genai
from google.genai import types

# Video işleme - lazy import for better startup
try:
    from moviepy import ImageClip, concatenate_videoclips, CompositeVideoClip
    from moviepy.video.fx import CrossFadeIn, CrossFadeOut
    MOVIEPY_AVAILABLE = True
except ImportError:
    MOVIEPY_AVAILABLE = False

# Yerel modüller
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

# .env dosyasını yükle
load_dotenv()

# Konfigürasyonu al
config = get_config()
config.init_directories()

# Logger'ı ayarla
logger = setup_logging(
    log_file=config.LOG_FILE,
    log_level=config.LOG_LEVEL
)

# Rate limiter
rate_limiter = RateLimiter(
    max_calls=config.RATE_LIMIT_PER_MINUTE,
    period=60
)


class StoryGeneratorError(Exception):
    """Hikaye oluşturucu için özel exception"""
    pass


class APIError(StoryGeneratorError):
    """API hatası"""
    pass


class ValidationError(StoryGeneratorError):
    """Doğrulama hatası"""
    pass


class StoryGenerator:
    """
    Ana Hikaye Oluşturucu Sınıfı
    
    Bu sınıf tüm hikaye oluşturma işlemlerini yönetir:
    - Senaryo oluşturma
    - Görsel üretme
    - Video montajı
    - Karakter yönetimi
    """
    
    def __init__(self):
        """StoryGenerator'ı başlat"""
        settings = self._load_settings()
        self.api_key = settings.get("api_key") or config.GEMINI_API_KEY
        
        if not self.api_key:
            logger.error("GEMINI_API_KEY bulunamadı!")
            # Raise etmiyoruz, çünkü kullanıcı ayarlardan girebilir.
        
        if self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
                logger.info("Gemini API client başarıyla oluşturuldu")
            except Exception as e:
                logger.error(f"Gemini client oluşturulamadı: {e}")
        else:
            self.client = None
        
        self.output_dir = config.OUTPUT_DIR
        os.makedirs(self.output_dir, exist_ok=True)
        
        logger.info("StoryGenerator başlatıldı")

    # ========================
    # AYAR YÖNETİMİ
    # ========================
    
    def _load_settings(self) -> Dict[str, Any]:
        """Ayarları yükle"""
        return validate_json_file(config.SETTINGS_FILE, {})
    
    def get_settings(self) -> Dict[str, Any]:
        """Ayarları döndür"""
        return self._load_settings()
    
    def update_settings(self, new_settings: Dict[str, Any]) -> bool:
        """
        Ayarları güncelle
        
        Args:
            new_settings: Yeni ayarlar
            
        Returns:
            Başarılı mı?
        """
        try:
            current = self._load_settings()
            
            # API Key değiştiyse veya temizlendiyse client'ı güncelle
            if "api_key" in new_settings:
                new_key = new_settings.get("api_key")
                
                # Eğer kullanıcı boş string gönderdiyse, json'dan sil
                if new_key == "":
                    if "api_key" in current:
                        del current["api_key"]
                        del new_settings["api_key"] # current.update'de tekrar eklenmemesi için
                    self.api_key = config.GEMINI_API_KEY
                else:
                    self.api_key = new_key
                    
                if self.api_key:
                    self.client = genai.Client(api_key=self.api_key)
                    logger.info("Gemini API client yeni anahtar ile güncellendi")
                else:
                    self.client = None
                    
            current.update(new_settings)
            success = save_json_file(config.SETTINGS_FILE, current)
            if success:
                logger.info("Ayarlar güncellendi")
            return success
        except Exception as e:
            logger.error(f"Ayar güncelleme hatası: {e}")
            return False

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
    
    def add_character(
        self, 
        name: str, 
        description: str, 
        attributes: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Yeni karakter ekle
        
        Args:
            name: Karakter adı
            description: Karakter açıklaması
            attributes: Ek özellikler
            
        Returns:
            Başarılı mı?
        """
        if not name or not description:
            logger.warning("Karakter eklenemedi: isim veya açıklama boş")
            return False
        
        characters = self._load_characters()
        
        new_char = {
            "id": str(int(time.time() * 1000)),  # Unique ID
            "name": sanitize_input(name, max_length=Constants.MAX_CHARACTER_NAME_LENGTH),
            "description": sanitize_input(description, max_length=Constants.MAX_PROMPT_LENGTH),
            "attributes": attributes or {},
            "created_at": int(time.time())
        }
        
        characters.append(new_char)
        success = self._save_characters(characters)
        
        if success:
            logger.info(f"Yeni karakter eklendi: {name}")
        
        return success
    
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
    # GEÇMİŞ YÖNETİMİ
    # ========================
    
    def get_history(self) -> List[Dict[str, Any]]:
        """Oluşturma geçmişini döndür"""
        return validate_json_file(config.HISTORY_FILE, [])
    
    def _save_to_history(self, entry: Dict[str, Any]) -> bool:
        """
        Geçmişe yeni kayıt ekle
        
        Args:
            entry: Yeni kayıt
            
        Returns:
            Başarılı mı?
        """
        history = self.get_history()
        
        # En başa ekle
        history.insert(0, entry)
        
        # Maksimum sayıyı aşma
        if len(history) > Constants.MAX_HISTORY_ITEMS:
            history = history[:Constants.MAX_HISTORY_ITEMS]
        
        return save_json_file(config.HISTORY_FILE, history)
    
    def _save_updated_history(self, history: List[Dict[str, Any]]) -> bool:
        """Güncellenmiş geçmişi kaydet"""
        return save_json_file(config.HISTORY_FILE, history)
    
    def delete_history_item(self, timestamp: int) -> bool:
        """
        Geçmiş kaydını sil
        
        Args:
            timestamp: Silinecek kaydın timestamp'ı
            
        Returns:
            Başarılı mı?
        """
        history = self.get_history()
        original_count = len(history)
        
        # İlgili dosyaları da sil
        entry = next((item for item in history if item.get("timestamp") == timestamp), None)
        if entry:
            # Görselleri sil
            for img_path in entry.get("images", []):
                full_path = os.path.join(os.getcwd(), img_path)
                if os.path.exists(full_path):
                    try:
                        os.remove(full_path)
                    except Exception as e:
                        logger.warning(f"Görsel silinemedi: {e}")
            
            # Videoyu sil
            video_path = entry.get("video")
            if video_path:
                full_path = os.path.join(os.getcwd(), video_path)
                if os.path.exists(full_path):
                    try:
                        os.remove(full_path)
                    except Exception as e:
                        logger.warning(f"Video silinemedi: {e}")
        
        history = [h for h in history if h.get("timestamp") != timestamp]
        
        if len(history) < original_count:
            success = self._save_updated_history(history)
            if success:
                logger.info(f"Geçmiş kaydı silindi: {timestamp}")
            return success
        
        return False
    
    def clear_history(self) -> bool:
        """Tüm geçmişi temizle"""
        logger.warning("Tüm geçmiş temizleniyor!")
        return save_json_file(config.HISTORY_FILE, [])

    # ========================
    # API İSTEKLERİ
    # ========================
    
    def _check_rate_limit(self):
        """Rate limit kontrolü"""
        if not rate_limiter.is_allowed():
            wait_time = rate_limiter.wait_time()
            logger.warning(f"Rate limit aşıldı, {wait_time:.1f}s bekleniyor")
            time.sleep(wait_time)
    
    @retry_with_backoff(max_retries=3, base_delay=2.0)
    def _make_text_request(
        self, 
        prompt: str, 
        json_response: bool = False
    ) -> str:
        """
        Gemini text API'ye istek yap
        
        Args:
            prompt: İstek promptu
            json_response: JSON yanıt mı bekleniyor?
            
        Returns:
            API yanıtı
        """
        self._check_rate_limit()
        
        if not self.client:
            raise APIError("Lütfen ayarlardan geçerli bir Gemini API anahtarı girin.")
        
        config_obj = types.GenerateContentConfig()
        if json_response:
            config_obj = types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        
        response = self.client.models.generate_content(
            model=Config.TEXT_MODEL,
            contents=prompt,
            config=config_obj
        )
        
        return response.text.strip()
    
    @retry_with_backoff(max_retries=3, base_delay=2.0)
    def _make_image_request(
        self,
        prompt: str,
        aspect_ratio: str = "9:16",
        image_size: str = "2K",
        reference_images: Optional[List[str]] = None
    ) -> Optional[bytes]:
        """
        Gemini image API'ye istek yap
        
        Args:
            prompt: Görsel promptu
            aspect_ratio: En-boy oranı
            image_size: Çözünürlük
            reference_images: Referans görsel yolları
            
        Returns:
            Görsel byte verisi veya None
        """
        self._check_rate_limit()
        
        if not self.client:
            raise APIError("Lütfen ayarlardan geçerli bir Gemini API anahtarı girin.")
        
        parts = [types.Part.from_text(text=prompt)]
        
        # Referans görselleri ekle
        if reference_images:
            for ref_path in reference_images:
                if ref_path and os.path.exists(ref_path):
                    try:
                        with open(ref_path, "rb") as img_file:
                            img_data = img_file.read()
                            mime_type = mimetypes.guess_type(ref_path)[0] or "image/png"
                            parts.append(types.Part.from_bytes(data=img_data, mime_type=mime_type))
                            logger.debug(f"Referans görsel eklendi: {ref_path}")
                    except Exception as e:
                        logger.warning(f"Referans görsel okunamadı: {e}")
        
        contents = [types.Content(role="user", parts=parts)]
        
        generate_config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio=aspect_ratio,
                image_size=image_size
            )
        )
        
        response = self.client.models.generate_content(
            model=Config.IMAGE_MODEL,
            contents=contents,
            config=generate_config
        )
        
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.data:
                    return part.inline_data.data
        
        return None

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
        """Karakter promptu oluştur"""
        characters = self._load_characters()
        
        if not characters:
            return ""
        
        prompt = "### KARAKTER PROFİLLERİ (BU TANIMLARI KULLAN):\n"
        for char in characters:
            prompt += f"- {char.get('name', 'Adsız')}: {char.get('description', '')}\n"
        prompt += "\n(Eğer hikayede bu karakterlerin adı geçiyorsa, görsel tasvirlerini birebir uygula.)"
        
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

    # ========================
    # GÖRSEL OLUŞTURMA
    # ========================
    
    def resim_uret(
        self,
        prompt: str,
        index: int,
        bolum_adi: str,
        aspect_ratio: str = "9:16",
        image_size: str = "2K",
        reference_images: Optional[List[str]] = None
    ) -> Optional[str]:
        """
        Tek bir görsel üret
        
        Args:
            prompt: Görsel promptu
            index: Sahne indeksi
            bolum_adi: Bölüm adı (dosya adı için)
            aspect_ratio: En-boy oranı
            image_size: Çözünürlük
            reference_images: Referans görseller
            
        Returns:
            Görsel yolu veya None
        """
        logger.info(f"Sahne {index + 1} oluşturuluyor...")
        
        # Prompt'u string'e çevir
        prompt_text = get_prompt_from_scene(prompt)
        
        try:
            image_data = self._make_image_request(
                prompt_text,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                reference_images=reference_images
            )
            
            if image_data:
                # Dosya adı oluştur
                timestamp = int(time.time())
                safe_bolum = sanitize_filename(bolum_adi, Constants.SAFE_FILENAME_LENGTH)
                file_name = f"{timestamp}_{index + 1}_{safe_bolum}.png"
                file_path = os.path.join(self.output_dir, file_name)
                
                with open(file_path, "wb") as f:
                    f.write(image_data)
                
                logger.info(f"Sahne {index + 1} kaydedildi: {file_name}")
                api_stats.record_request(True)
                api_stats.record_image()
                
                return f"static/output/{file_name}"
            
            logger.error(f"Sahne {index + 1}: Görsel verisi alınamadı")
            api_stats.record_request(False)
            return None
            
        except Exception as e:
            logger.error(f"Sahne {index + 1} oluşturma hatası: {e}")
            api_stats.record_request(False)
            return None

    def _generate_single_image_task(
        self,
        task_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Paralel işlem için tek görsel oluşturma görevi
        
        Args:
            task_data: Görev verileri (index, prompt, settings)
            
        Returns:
            Sonuç dict'i (index, path, success)
        """
        index = task_data["index"]
        prompt = task_data["prompt"]
        bolum_adi = task_data["bolum_adi"]
        aspect_ratio = task_data.get("aspect_ratio", "9:16")
        image_size = task_data.get("image_size", "2K")
        
        try:
            img_path = self.resim_uret(
                prompt,
                index,
                bolum_adi,
                aspect_ratio=aspect_ratio,
                image_size=image_size
            )
            
            return {
                "index": index,
                "path": img_path,
                "success": img_path is not None
            }
        except Exception as e:
            logger.error(f"Paralel sahne {index + 1} hatası: {e}")
            return {
                "index": index,
                "path": None,
                "success": False
            }

    def generate_images_parallel(
        self,
        prompts: List[str],
        bolum_adi: str,
        aspect_ratio: str = "9:16",
        image_size: str = "2K",
        progress_callback: Optional[callable] = None
    ) -> List[Optional[str]]:
        """
        Görselleri paralel olarak oluştur
        
        Args:
            prompts: Görsel promptları listesi
            bolum_adi: Bölüm adı
            aspect_ratio: En-boy oranı
            image_size: Çözünürlük
            progress_callback: İlerleme callback fonksiyonu
            
        Returns:
            Görsel yolları listesi (sıralı)
        """
        total = len(prompts)
        results = [None] * total
        completed = 0
        lock = threading.Lock()
        
        # Görev listesi oluştur
        tasks = [
            {
                "index": i,
                "prompt": prompt,
                "bolum_adi": bolum_adi,
                "aspect_ratio": aspect_ratio,
                "image_size": image_size
            }
            for i, prompt in enumerate(prompts)
        ]
        
        max_workers = min(config.MAX_PARALLEL_WORKERS, total)
        logger.info(f"Paralel görsel oluşturma başlatıldı: {total} sahne, {max_workers} worker")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_task = {
                executor.submit(self._generate_single_image_task, task): task
                for task in tasks
            }
            
            for future in as_completed(future_to_task):
                result = future.result()
                index = result["index"]
                results[index] = result["path"]
                
                with lock:
                    completed += 1
                    if progress_callback:
                        progress_callback(completed, total, index, result["path"])
        
        logger.info(f"Paralel görsel oluşturma tamamlandı: {sum(1 for r in results if r)}/{total} başarılı")
        return results

    # ========================
    # SESLENDİRME (TTS)
    # ========================
    
    def _parse_audio_mime_type(self, mime_type: str) -> Dict[str, int]:
        """
        Audio MIME type'dan bits per sample ve rate bilgisini çıkar
        
        Args:
            mime_type: Audio MIME tipi (ör: "audio/L16;rate=24000")
            
        Returns:
            bits_per_sample ve rate içeren dict
        """
        bits_per_sample = 16
        rate = 24000
        
        parts = mime_type.split(";")
        for param in parts:
            param = param.strip()
            if param.lower().startswith("rate="):
                try:
                    rate = int(param.split("=", 1)[1])
                except (ValueError, IndexError):
                    pass
            elif param.startswith("audio/L"):
                try:
                    bits_per_sample = int(param.split("L", 1)[1])
                except (ValueError, IndexError):
                    pass
        
        return {"bits_per_sample": bits_per_sample, "rate": rate}
    
    def _convert_to_wav(self, audio_data: bytes, mime_type: str) -> bytes:
        """
        Raw audio verisini WAV formatına dönüştür
        
        Args:
            audio_data: Ham audio verisi
            mime_type: Audio MIME tipi
            
        Returns:
            WAV formatında audio verisi
        """
        parameters = self._parse_audio_mime_type(mime_type)
        bits_per_sample = parameters["bits_per_sample"]
        sample_rate = parameters["rate"]
        num_channels = 1
        data_size = len(audio_data)
        bytes_per_sample = bits_per_sample // 8
        block_align = num_channels * bytes_per_sample
        byte_rate = sample_rate * block_align
        chunk_size = 36 + data_size
        
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF",
            chunk_size,
            b"WAVE",
            b"fmt ",
            16,
            1,
            num_channels,
            sample_rate,
            byte_rate,
            block_align,
            bits_per_sample,
            b"data",
            data_size
        )
        return header + audio_data
    
    def generate_voice_for_dialogue(
        self,
        dialogue_text: str,
        speaker_gender: str,
        output_path: str
    ) -> Optional[str]:
        """
        Tek bir diyalog için ses oluştur
        
        Args:
            dialogue_text: Diyalog metni
            speaker_gender: Konuşmacı cinsiyeti ('male' veya 'female')
            output_path: Çıktı dosya yolu
            
        Returns:
            Ses dosyası yolu veya None
        """
        if not config.TTS_ENABLED:
            logger.debug("TTS devre dışı")
            return None
        
        voice_name = config.TTS_VOICES.get(speaker_gender, config.TTS_VOICES["male"])
        
        try:
            logger.info(f"Seslendirme oluşturuluyor: {voice_name} - {dialogue_text[:50]}...")
            
            contents = [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(text=dialogue_text)
                    ]
                )
            ]
            
            generate_content_config = types.GenerateContentConfig(
                temperature=1,
                response_modalities=["audio"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=voice_name
                        )
                    )
                )
            )
            
            audio_data = b""
            mime_type = None
            
            for chunk in self.client.models.generate_content_stream(
                model=config.TTS_MODEL,
                contents=contents,
                config=generate_content_config
            ):
                if (chunk.candidates is None or 
                    chunk.candidates[0].content is None or 
                    chunk.candidates[0].content.parts is None):
                    continue
                    
                part = chunk.candidates[0].content.parts[0]
                if part.inline_data and part.inline_data.data:
                    audio_data += part.inline_data.data
                    if mime_type is None:
                        mime_type = part.inline_data.mime_type
            
            if audio_data:
                # WAV'a dönüştür
                wav_data = self._convert_to_wav(audio_data, mime_type or "audio/L16;rate=24000")
                
                with open(output_path, "wb") as f:
                    f.write(wav_data)
                
                logger.info(f"Ses dosyası kaydedildi: {output_path}")
                return output_path
            
            logger.warning("Ses verisi oluşturulamadı")
            return None
            
        except Exception as e:
            logger.error(f"Seslendirme hatası: {e}")
            return None
    
    def generate_multi_speaker_voice(
        self,
        dialogues: List[Dict[str, str]],
        output_path: str
    ) -> Optional[str]:
        """
        Çoklu konuşmacı için ses oluştur
        
        Args:
            dialogues: Diyalog listesi [{"speaker": "Kadın/Erkek", "text": "..."}]
            output_path: Çıktı dosya yolu
            
        Returns:
            Ses dosyası yolu veya None
        """
        if not config.TTS_ENABLED:
            return None
        
        if not dialogues:
            return None
        
        try:
            # Diyalogları formatlı metne çevir
            formatted_text = ""
            speaker_configs = []
            seen_speakers = set()
            
            for i, dialogue in enumerate(dialogues, 1):
                speaker = dialogue.get("speaker", "Karakter")
                text = dialogue.get("text", "")
                gender = dialogue.get("gender", "male")
                
                speaker_label = f"Speaker {i}"
                formatted_text += f"{speaker_label}: {text}\n"
                
                if speaker_label not in seen_speakers:
                    voice_name = config.TTS_VOICES.get(gender, config.TTS_VOICES["male"])
                    speaker_configs.append(
                        types.SpeakerVoiceConfig(
                            speaker=speaker_label,
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                    voice_name=voice_name
                                )
                            )
                        )
                    )
                    seen_speakers.add(speaker_label)
            
            logger.info(f"Çoklu seslendirme oluşturuluyor: {len(dialogues)} diyalog")
            
            contents = [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(text=f"Read aloud this Turkish conversation:\n{formatted_text}")
                    ]
                )
            ]
            
            generate_content_config = types.GenerateContentConfig(
                temperature=1,
                response_modalities=["audio"],
                speech_config=types.SpeechConfig(
                    multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
                        speaker_voice_configs=speaker_configs
                    )
                )
            )
            
            audio_data = b""
            mime_type = None
            
            for chunk in self.client.models.generate_content_stream(
                model=config.TTS_MODEL,
                contents=contents,
                config=generate_content_config
            ):
                if (chunk.candidates is None or 
                    chunk.candidates[0].content is None or 
                    chunk.candidates[0].content.parts is None):
                    continue
                    
                part = chunk.candidates[0].content.parts[0]
                if part.inline_data and part.inline_data.data:
                    audio_data += part.inline_data.data
                    if mime_type is None:
                        mime_type = part.inline_data.mime_type
            
            if audio_data:
                wav_data = self._convert_to_wav(audio_data, mime_type or "audio/L16;rate=24000")
                
                with open(output_path, "wb") as f:
                    f.write(wav_data)
                
                logger.info(f"Çoklu ses dosyası kaydedildi: {output_path}")
                return output_path
            
            return None
            
        except Exception as e:
            logger.error(f"Çoklu seslendirme hatası: {e}")
            return None
    
    def extract_dialogues_from_scene(self, scene_prompt: str) -> List[Dict[str, str]]:
        """
        Sahne promptundan diyalogları çıkar
        
        Args:
            scene_prompt: Sahne açıklaması
            
        Returns:
            Diyalog listesi
        """
        try:
            prompt = f"""
            Aşağıdaki sahne açıklamasından karakterlerin diyaloglarını çıkar.
            Her diyalog için konuşmacının adını, cinsiyetini (male/female) ve söylediği metni belirle.
            
            Sahne: {scene_prompt}
            
            SADECE JSON formatında yanıt ver:
            [
                {{"speaker": "Karakter Adı", "gender": "male/female", "text": "Diyalog metni Türkçe olarak"}}
            ]
            
            Eğer sahnede diyalog yoksa boş liste döndür: []
            """
            
            response_text = self._make_text_request(prompt, json_response=True)
            dialogues = extract_json_from_response(response_text)
            
            if dialogues and isinstance(dialogues, list):
                return dialogues
            
            return []
            
        except Exception as e:
            logger.error(f"Diyalog çıkarma hatası: {e}")
            return []
    
    def generate_scene_audio(
        self,
        scene_prompt: str,
        scene_index: int,
        bolum_adi: str
    ) -> Optional[str]:
        """
        Sahne için ses oluştur (diyalogları çıkar ve seslendir)
        
        Args:
            scene_prompt: Sahne açıklaması
            scene_index: Sahne indeksi
            bolum_adi: Bölüm adı
            
        Returns:
            Ses dosyası yolu veya None
        """
        if not config.TTS_ENABLED:
            return None
        
        # Diyalogları çıkar
        dialogues = self.extract_dialogues_from_scene(scene_prompt)
        
        if not dialogues:
            logger.info(f"Sahne {scene_index + 1}'de diyalog bulunamadı")
            return None
        
        # Çıktı dosya adı
        safe_name = sanitize_filename(bolum_adi)
        audio_file = f"{safe_name}_scene_{scene_index + 1}_audio.wav"
        audio_path = os.path.join(self.output_dir, audio_file)
        
        # Çoklu konuşmacı seslendirmesi
        if len(dialogues) > 1:
            result = self.generate_multi_speaker_voice(dialogues, audio_path)
        else:
            # Tek konuşmacı
            d = dialogues[0]
            result = self.generate_voice_for_dialogue(
                d.get("text", ""),
                d.get("gender", "male"),
                audio_path
            )
        
        if result:
            return f"static/output/{audio_file}"
        
        return None

    # ========================
    # KEN BURNS EFEKTİ
    # ========================
    
    def _apply_ken_burns_effect(
        self,
        clip,
        effect_type: str = "random",
        zoom_ratio: float = 1.2
    ):
        """
        Ken Burns efekti uygula (zoom/pan animasyonu)
        
        Args:
            clip: MoviePy ImageClip
            effect_type: Efekt tipi (zoom_in, zoom_out, pan_left, pan_right, pan_up, pan_down, random)
            zoom_ratio: Zoom oranı (1.2 = %20 zoom)
            
        Returns:
            Efekt uygulanmış clip
        """
        import random
        
        if effect_type == "none":
            return clip
        
        if effect_type == "random":
            effect_type = random.choice(["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"])
        
        duration = clip.duration
        w, h = clip.size
        
        # Zoom için büyütülmüş boyut
        new_w = int(w * zoom_ratio)
        new_h = int(h * zoom_ratio)
        
        def make_frame(get_frame):
            def new_frame(t):
                import numpy as np
                from PIL import Image
                
                frame = get_frame(t)
                img = Image.fromarray(frame)
                
                progress = t / duration  # 0.0 -> 1.0
                
                if effect_type == "zoom_in":
                    # Başta normal, sonda yakın
                    current_zoom = 1.0 + (zoom_ratio - 1.0) * progress
                    crop_w = int(w / current_zoom)
                    crop_h = int(h / current_zoom)
                    x = (w - crop_w) // 2
                    y = (h - crop_h) // 2
                    
                elif effect_type == "zoom_out":
                    # Başta yakın, sonda normal
                    current_zoom = zoom_ratio - (zoom_ratio - 1.0) * progress
                    crop_w = int(w / current_zoom)
                    crop_h = int(h / current_zoom)
                    x = (w - crop_w) // 2
                    y = (h - crop_h) // 2
                    
                elif effect_type == "pan_left":
                    # Sağdan sola kaydır
                    crop_w = int(w / zoom_ratio)
                    crop_h = int(h / zoom_ratio)
                    max_x = w - crop_w
                    x = int(max_x * (1 - progress))
                    y = (h - crop_h) // 2
                    
                elif effect_type == "pan_right":
                    # Soldan sağa kaydır
                    crop_w = int(w / zoom_ratio)
                    crop_h = int(h / zoom_ratio)
                    max_x = w - crop_w
                    x = int(max_x * progress)
                    y = (h - crop_h) // 2
                    
                elif effect_type == "pan_up":
                    # Aşağıdan yukarı kaydır
                    crop_w = int(w / zoom_ratio)
                    crop_h = int(h / zoom_ratio)
                    max_y = h - crop_h
                    x = (w - crop_w) // 2
                    y = int(max_y * (1 - progress))
                    
                elif effect_type == "pan_down":
                    # Yukarıdan aşağı kaydır
                    crop_w = int(w / zoom_ratio)
                    crop_h = int(h / zoom_ratio)
                    max_y = h - crop_h
                    x = (w - crop_w) // 2
                    y = int(max_y * progress)
                else:
                    return frame
                
                # Crop ve resize
                cropped = img.crop((x, y, x + crop_w, y + crop_h))
                resized = cropped.resize((w, h), Image.LANCZOS)
                
                return np.array(resized)
            
            return new_frame
        
        return clip.transform(make_frame)

    def _create_subtitle_clip(
        self,
        text: str,
        duration: float,
        video_size: tuple,
        settings: Dict[str, Any]
    ):
        """
        Altyazı klibi oluştur
        
        Args:
            text: Altyazı metni
            duration: Süre
            video_size: Video boyutu (width, height)
            settings: Altyazı ayarları
        """
        try:
            from moviepy import TextClip, CompositeVideoClip, ColorClip
            
            if not text or not text.strip():
                return None
            
            width, height = video_size
            font_size = settings.get("fontSize", 32)
            font_color = settings.get("fontColor", "white")
            bg_color = settings.get("bgColor", "black")
            bg_opacity = settings.get("bgOpacity", 0.7)
            position = settings.get("position", "bottom")
            margin = settings.get("margin", 50)
            max_chars = settings.get("maxCharsPerLine", 50)
            
            # Metni satırlara böl
            words = text.split()
            lines = []
            current_line = ""
            
            for word in words:
                if len(current_line) + len(word) + 1 <= max_chars:
                    current_line += (" " if current_line else "") + word
                else:
                    if current_line:
                        lines.append(current_line)
                    current_line = word
            if current_line:
                lines.append(current_line)
            
            wrapped_text = "\n".join(lines)
            
            # Text clip oluştur
            txt_clip = TextClip(
                text=wrapped_text,
                font_size=font_size,
                color=font_color,
                font="Arial",
                text_align="center",
                size=(width - 100, None)
            ).with_duration(duration)
            
            # Arkaplan oluştur
            txt_width, txt_height = txt_clip.size
            padding = 20
            
            bg_clip = ColorClip(
                size=(txt_width + padding * 2, txt_height + padding),
                color=self._hex_to_rgb(bg_color) if bg_color.startswith("#") else (0, 0, 0)
            ).with_duration(duration).with_opacity(bg_opacity)
            
            # Pozisyon hesapla
            if position == "top":
                y_pos = margin
            elif position == "center":
                y_pos = (height - txt_height) // 2
            else:  # bottom
                y_pos = height - txt_height - margin - padding
            
            x_pos = (width - txt_width - padding * 2) // 2
            
            # Composite oluştur
            subtitle_composite = CompositeVideoClip([
                bg_clip.with_position((x_pos, y_pos)),
                txt_clip.with_position((x_pos + padding, y_pos + padding // 2))
            ], size=video_size).with_duration(duration)
            
            return subtitle_composite
            
        except Exception as e:
            logger.warning(f"Altyazı oluşturulamadı: {e}")
            return None
    
    def _hex_to_rgb(self, hex_color: str) -> tuple:
        """Hex rengi RGB'ye çevir"""
        hex_color = hex_color.lstrip('#')
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    
    def _create_watermark_clip(
        self,
        text: str,
        duration: float,
        video_size: tuple,
        settings: Dict[str, Any]
    ):
        """
        Watermark klibi oluştur
        
        Args:
            text: Watermark metni
            duration: Video süresi
            video_size: Video boyutu
            settings: Watermark ayarları
        """
        try:
            from moviepy import TextClip
            
            if not text or not text.strip():
                return None
            
            width, height = video_size
            font_size = settings.get("fontSize", 20)
            font_color = settings.get("fontColor", "white")
            opacity = settings.get("opacity", 0.5)
            position = settings.get("position", "bottom_right")  # top_left, top_right, bottom_left, bottom_right
            margin = settings.get("margin", 20)
            
            # Text clip oluştur
            txt_clip = TextClip(
                text=text,
                font_size=font_size,
                color=font_color,
                font="Arial"
            ).with_duration(duration).with_opacity(opacity)
            
            txt_width, txt_height = txt_clip.size
            
            # Pozisyon hesapla
            if position == "top_left":
                pos = (margin, margin)
            elif position == "top_right":
                pos = (width - txt_width - margin, margin)
            elif position == "bottom_left":
                pos = (margin, height - txt_height - margin)
            else:  # bottom_right
                pos = (width - txt_width - margin, height - txt_height - margin)
            
            return txt_clip.with_position(pos)
            
        except Exception as e:
            logger.warning(f"Watermark oluşturulamadı: {e}")
            return None
    
    def _extract_subtitles_from_prompts(self, prompts: List) -> List[str]:
        """
        Prompt'lardan diyalogları/altyazıları çıkar
        
        Args:
            prompts: Sahne prompt'ları listesi
            
        Returns:
            Altyazı metinleri listesi
        """
        import re
        
        subtitles = []
        
        for prompt in prompts:
            prompt_text = get_prompt_from_scene(prompt) if isinstance(prompt, dict) else str(prompt)
            
            # Diyalog kalıplarını ara
            # "..." veya «...» veya -... formatları
            dialogue_patterns = [
                r'"([^"]+)"',  # "diyalog"
                r'«([^»]+)»',  # «diyalog»
                r"'([^']+)'",  # 'diyalog'
                r':\s*["\']?([^"\'\.!?]+[\.!?])',  # Karakter: diyalog
            ]
            
            dialogues = []
            for pattern in dialogue_patterns:
                matches = re.findall(pattern, prompt_text)
                dialogues.extend(matches)
            
            if dialogues:
                # İlk diyaloğu al veya hepsini birleştir
                subtitle = " ".join(dialogues[:2])  # Max 2 diyalog
                # Çok uzunsa kısalt
                if len(subtitle) > 150:
                    subtitle = subtitle[:147] + "..."
                subtitles.append(subtitle)
            else:
                # Diyalog yoksa sahne açıklamasından kısa bir özet
                # İlk cümleyi al
                sentences = prompt_text.split('.')
                if sentences:
                    first_sentence = sentences[0].strip()
                    if len(first_sentence) > 100:
                        first_sentence = first_sentence[:97] + "..."
                    subtitles.append(first_sentence)
                else:
                    subtitles.append("")
        
        return subtitles

    # ========================
    # VIDEO OLUŞTURMA
    # ========================
    
    def video_olustur(
        self,
        image_paths: List[str],
        user_input: str,
        frame_duration: float = 5.0,
        transition: str = "none",
        fade_duration: float = 0.5,
        music_file: Optional[str] = None,
        scene_audios: Optional[List[Optional[str]]] = None,
        audio_settings: Optional[Dict[str, Any]] = None,
        ken_burns_effect: str = "none",
        subtitles: Optional[List[str]] = None,
        subtitle_settings: Optional[Dict[str, Any]] = None,
        watermark_text: Optional[str] = None,
        watermark_settings: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Görsellerden video oluştur
        
        Args:
            image_paths: Görsel yolları
            user_input: Video adı için kullanıcı girdisi
            frame_duration: Kare süresi (saniye)
            transition: Geçiş efekti
            fade_duration: Geçiş süresi
            music_file: Arkaplan müzik dosyası (opsiyonel)
            scene_audios: Sahne ses dosyaları listesi (opsiyonel) - Audio ducking için
            audio_settings: Ses ayarları (opsiyonel)
            ken_burns_effect: Ken Burns efekti tipi
            subtitles: Sahne altyazıları listesi (opsiyonel)
            subtitle_settings: Altyazı ayarları (opsiyonel)
            watermark_text: Watermark metni (opsiyonel)
            watermark_settings: Watermark ayarları (opsiyonel)
            
        Returns:
            Video yolu veya None
        """
        if not MOVIEPY_AVAILABLE:
            logger.error("MoviePy yüklü değil, video oluşturulamıyor")
            return None
        
        if not image_paths:
            logger.warning("Video için görsel bulunamadı")
            return None
        
        # Varsayılan ses ayarları
        default_audio_settings = {
            "musicVolume": 0.7,
            "voiceVolume": 1.0,
            "duckingLevel": -10,
            "duckingFade": 0.5,
            "voiceDelay": 0.3,
            "musicFadeOut": 2.0
        }
        
        # Varsayılan altyazı ayarları
        default_subtitle_settings = {
            "enabled": True,
            "fontSize": 32,
            "fontColor": "white",
            "bgColor": "black",
            "bgOpacity": 0.7,
            "position": "bottom",  # bottom, top, center
            "margin": 50,
            "maxCharsPerLine": 50
        }
        
        # Audio settings'i varsayılanlarla birleştir
        if audio_settings:
            audio_cfg = {**default_audio_settings, **audio_settings}
        else:
            audio_cfg = default_audio_settings
        
        # Subtitle settings'i varsayılanlarla birleştir
        if subtitle_settings:
            sub_cfg = {**default_subtitle_settings, **subtitle_settings}
        else:
            sub_cfg = default_subtitle_settings
        
        has_voice = scene_audios and any(a for a in scene_audios)
        has_subtitles = subtitles and any(s for s in subtitles) and sub_cfg.get("enabled", True)
        music_info = f", müzik: {music_file}" if music_file else ""
        voice_info = ", sesli sahneler var" if has_voice else ""
        subtitle_info = ", altyazılı" if has_subtitles else ""
        logger.info(f"Video oluşturuluyor ({len(image_paths)} görsel, {frame_duration}s/kare, geçiş: {transition}{music_info}{voice_info}{subtitle_info})")
        
        try:
            from moviepy import AudioFileClip, CompositeAudioClip
            
            clips = []
            voice_clips = []  # Karakter sesleri
            voice_timing = []  # Ses zamanlamaları (start, end) - ducking için
            
            current_time = 0.0
            last_voice_end_time = 0.0  # Önceki sesin bitiş zamanı
            
            for i, img_rel_path in enumerate(image_paths):
                img_path = img_rel_path
                if not os.path.isabs(img_path):
                    img_path = os.path.join(os.getcwd(), img_rel_path)
                
                if not os.path.exists(img_path):
                    logger.warning(f"Görsel bulunamadı: {img_path}")
                    current_time += frame_duration
                    continue
                
                clip = ImageClip(img_path).with_duration(frame_duration)
                
                # Ken Burns efekti uygula
                if ken_burns_effect and ken_burns_effect != "none":
                    try:
                        clip = self._apply_ken_burns_effect(clip, ken_burns_effect)
                    except Exception as e:
                        logger.debug(f"Ken Burns efekti uygulanamadı: {e}")
                
                # Geçiş efekti uygula
                if transition == "fade" and i > 0:
                    try:
                        clip = clip.with_effects([CrossFadeIn(fade_duration)])
                    except Exception as e:
                        logger.debug(f"Fade efekti uygulanamadı: {e}")
                
                # Altyazı ekle (varsa)
                if has_subtitles and i < len(subtitles) and subtitles[i]:
                    try:
                        from moviepy import CompositeVideoClip
                        subtitle_text = subtitles[i]
                        video_size = clip.size
                        
                        subtitle_clip = self._create_subtitle_clip(
                            subtitle_text,
                            frame_duration,
                            video_size,
                            sub_cfg
                        )
                        
                        if subtitle_clip:
                            clip = CompositeVideoClip([clip, subtitle_clip])
                            logger.debug(f"Sahne {i+1} altyazı eklendi")
                    except Exception as e:
                        logger.warning(f"Sahne {i+1} altyazı eklenemedi: {e}")
                
                clips.append(clip)
                
                # Sahne sesini ekle (varsa)
                if scene_audios and i < len(scene_audios) and scene_audios[i]:
                    audio_rel_path = scene_audios[i]
                    audio_path = audio_rel_path
                    if not os.path.isabs(audio_path):
                        audio_path = os.path.join(os.getcwd(), audio_rel_path)
                    
                    if os.path.exists(audio_path):
                        try:
                            voice_audio = AudioFileClip(audio_path)
                            voice_delay = float(audio_cfg.get("voiceDelay", 0.3))
                            
                            # Önceki ses bitmeden yeni sesi başlatma!
                            # Sahne başlangıcı + gecikme
                            ideal_start = current_time + voice_delay
                            
                            # Eğer önceki ses hala devam ediyorsa, onun bitmesini bekle
                            # + 0.2 saniye boşluk bırak
                            if last_voice_end_time > ideal_start:
                                voice_start = last_voice_end_time + 0.2
                                logger.info(f"Sahne {i+1}: Ses üst üste binmemesi için geciktirildi ({ideal_start:.1f}s -> {voice_start:.1f}s)")
                            else:
                                voice_start = ideal_start
                            
                            voice_audio = voice_audio.with_start(voice_start)
                            
                            # Karakter ses seviyesini uygula
                            voice_volume = float(audio_cfg.get("voiceVolume", 1.0))
                            if voice_volume != 1.0:
                                voice_audio = voice_audio.with_volume_scaled(voice_volume)
                            
                            voice_clips.append(voice_audio)
                            
                            # Sesin bitiş zamanını kaydet
                            voice_end = voice_start + voice_audio.duration
                            last_voice_end_time = voice_end
                            
                            # Ducking için zamanlama kaydet
                            voice_timing.append({
                                "start": voice_start,
                                "end": voice_end,
                                "duration": voice_audio.duration
                            })
                            logger.debug(f"Sahne {i+1} sesi eklendi: {voice_start:.1f}s - {voice_end:.1f}s")
                        except Exception as e:
                            logger.warning(f"Sahne {i+1} ses dosyası yüklenemedi: {e}")
                
                current_time += frame_duration
            
            if not clips:
                logger.error("Video için geçerli görsel bulunamadı")
                return None
            
            # Video birleştir
            if transition == "fade" and len(clips) > 1:
                video = concatenate_videoclips(clips, method="compose")
            else:
                video = concatenate_videoclips(clips, method="compose")
            
            video_duration = video.duration
            
            # Watermark ekle (varsa)
            if watermark_text:
                try:
                    from moviepy import CompositeVideoClip
                    
                    default_watermark_settings = {
                        "fontSize": 20,
                        "fontColor": "white",
                        "opacity": 0.5,
                        "position": "bottom_right",
                        "margin": 20
                    }
                    
                    wm_cfg = {**default_watermark_settings, **(watermark_settings or {})}
                    
                    watermark_clip = self._create_watermark_clip(
                        watermark_text,
                        video_duration,
                        video.size,
                        wm_cfg
                    )
                    
                    if watermark_clip:
                        video = CompositeVideoClip([video, watermark_clip])
                        logger.info(f"Watermark eklendi: {watermark_text}")
                except Exception as e:
                    logger.warning(f"Watermark eklenemedi: {e}")
            
            final_audio = None
            
            # Müzik ekle (eğer belirtildiyse)
            if music_file:
                try:
                    music_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'music')
                    music_path = os.path.join(music_dir, music_file)
                    
                    if os.path.exists(music_path):
                        music_audio = AudioFileClip(music_path)
                        
                        # Video süresine göre müziği ayarla
                        if music_audio.duration > video_duration:
                            music_audio = music_audio.subclipped(0, video_duration)
                        elif music_audio.duration < video_duration:
                            from moviepy.audio.fx import AudioLoop
                            loops_needed = int(video_duration / music_audio.duration) + 1
                            music_audio = music_audio.with_effects([AudioLoop(nloops=loops_needed)])
                            music_audio = music_audio.subclipped(0, video_duration)
                        
                        # Audio Ducking uygula (karakter konuşurken müziği kıs)
                        if voice_timing:
                            logger.info(f"Audio ducking uygulanıyor: {len(voice_timing)} ses parçası")
                            
                            # audio_cfg'den ses ayarlarını al
                            ducking_level_db = float(audio_cfg.get("duckingLevel", -10))
                            fade_time = float(audio_cfg.get("duckingFade", 0.5))
                            music_volume_multiplier = float(audio_cfg.get("musicVolume", 0.7))
                            
                            # dB değerlerini çarpana çevir
                            # -10 dB = 10^(-10/20) ≈ 0.316
                            # 0 dB = 1.0
                            ducked_volume = 10 ** (ducking_level_db / 20)  # kullanıcı ayarından
                            normal_volume = music_volume_multiplier  # normal ses (kullanıcı ayarından)
                            
                            def volume_filter(get_frame, t):
                                """Zamana bağlı volume ayarlama - Audio Ducking (numpy vectorized)"""
                                import numpy as np
                                
                                frame = get_frame(t)
                                
                                # t scalar veya array olabilir
                                t_arr = np.atleast_1d(t)
                                volume_arr = np.ones_like(t_arr, dtype=float) * normal_volume
                                
                                for timing in voice_timing:
                                    start = timing["start"]
                                    end = timing["end"]
                                    
                                    # Fade in bölgesi (müzik kısılıyor)
                                    fade_in_start = start - fade_time
                                    fade_in_end = start
                                    
                                    # Fade out bölgesi (müzik yükseliyor)
                                    fade_out_start = end
                                    fade_out_end = end + fade_time
                                    
                                    # Ducked volume (normal volume * ducked ratio)
                                    actual_ducked = normal_volume * ducked_volume
                                    
                                    # Fade in mask (kısılıyor)
                                    fade_in_mask = (t_arr >= fade_in_start) & (t_arr < fade_in_end)
                                    if np.any(fade_in_mask):
                                        progress = (t_arr[fade_in_mask] - fade_in_start) / fade_time
                                        new_vol = normal_volume - progress * (normal_volume - actual_ducked)
                                        volume_arr[fade_in_mask] = np.minimum(volume_arr[fade_in_mask], new_vol)
                                    
                                    # Tam kısık bölge
                                    ducked_mask = (t_arr >= fade_in_end) & (t_arr < fade_out_start)
                                    volume_arr[ducked_mask] = np.minimum(volume_arr[ducked_mask], actual_ducked)
                                    
                                    # Fade out mask (yükseliyor)
                                    fade_out_mask = (t_arr >= fade_out_start) & (t_arr < fade_out_end)
                                    if np.any(fade_out_mask):
                                        progress = (t_arr[fade_out_mask] - fade_out_start) / fade_time
                                        new_vol = actual_ducked + progress * (normal_volume - actual_ducked)
                                        volume_arr[fade_out_mask] = np.minimum(volume_arr[fade_out_mask], new_vol)
                                
                                # Scalar ise tekrar scalar'a çevir
                                if np.isscalar(t):
                                    volume = float(volume_arr[0])
                                else:
                                    # frame shape'ine göre volume'u broadcast et
                                    volume = volume_arr.reshape(-1, 1) if len(frame.shape) > 1 else volume_arr
                                
                                return frame * volume
                            
                            # Filter uygula
                            music_audio = music_audio.transform(volume_filter, keep_duration=True)
                            logger.info(f"Audio ducking uygulandı: konuşmalarda müzik {ducking_level_db}dB")
                        else:
                            # Ducking yok, sadece müzik ses seviyesini ayarla
                            music_volume_multiplier = float(audio_cfg.get("musicVolume", 0.7))
                            if music_volume_multiplier != 1.0:
                                music_audio = music_audio.with_volume_scaled(music_volume_multiplier)
                        
                        # Sona doğru fade out ekle
                        from moviepy.audio.fx import AudioFadeOut
                        fade_out_duration = float(audio_cfg.get("musicFadeOut", 2.0))
                        if fade_out_duration > 0:
                            music_audio = music_audio.with_effects([AudioFadeOut(fade_out_duration)])
                        
                        final_audio = music_audio
                        logger.info(f"Müzik eklendi: {music_file}")
                    else:
                        logger.warning(f"Müzik dosyası bulunamadı: {music_path}")
                except Exception as e:
                    logger.warning(f"Müzik eklenirken hata: {e}")
            
            # Tüm sesleri birleştir
            if voice_clips or final_audio:
                all_audio_clips = []
                
                if final_audio:
                    all_audio_clips.append(final_audio)
                
                if voice_clips:
                    all_audio_clips.extend(voice_clips)
                    logger.info(f"{len(voice_clips)} karakter sesi eklendi")
                
                if len(all_audio_clips) == 1:
                    composite_audio = all_audio_clips[0]
                else:
                    composite_audio = CompositeAudioClip(all_audio_clips)
                
                video = video.with_audio(composite_audio)
            
            # Dosya adı
            timestamp = int(time.time())
            safe_name = sanitize_filename(user_input, Constants.SAFE_FILENAME_LENGTH)
            video_filename = f"{timestamp}_{safe_name}.mp4"
            video_path = os.path.join(self.output_dir, video_filename)
            
            # Video kaydet
            has_audio = music_file or voice_clips
            video.write_videofile(
                video_path,
                fps=config.VIDEO_FPS,
                codec=config.VIDEO_CODEC,
                audio_codec='aac' if has_audio else None,
                logger=None  # MoviePy loglarını sustur
            )
            
            # Klipleri temizle
            for clip in clips:
                clip.close()
            for vc in voice_clips:
                try:
                    vc.close()
                except:
                    pass
            video.close()
            
            logger.info(f"Video oluşturuldu: {video_filename}")
            api_stats.record_video()
            
            return f"static/output/{video_filename}"
            
        except Exception as e:
            logger.error(f"Video oluşturma hatası: {e}")
            return None

    # ========================
    # TAM HİKAYE AKIŞI
    # ========================
    
    def generate_full_story_stream(
        self,
        user_input: str,
        aspect_ratio: str = "9:16",
        image_size: str = "2K",
        scene_count: int = 7,
        frame_duration: float = 5.0,
        dialogue_style: str = "short",
        art_style: str = "comic",
        mood_style: str = "dynamic",
        camera_style: str = "balanced",
        time_of_day: str = "auto",
        season: str = "auto",
        weather: str = "auto",
        outfit_style: str = "auto",
        character_consistency: str = "strict",
        video_transition: str = "none",
        generate_audio: bool = False
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Tam hikaye oluşturma akışı (SSE stream)
        
        Args:
            user_input: Hikaye konusu
            ... diğer ayarlar
            
        Yields:
            İlerleme güncellemeleri
        """
        logger.info(f"Tam hikaye akışı başlatıldı: {user_input[:50]}...")
        
        # Input validation
        if not user_input or not user_input.strip():
            yield {"error": "Lütfen bir hikaye konusu girin."}
            return
        
        user_input = sanitize_input(user_input)
        
        # 1. Senaryo oluştur
        yield {"status": f"Senaryo oluşturuluyor ({scene_count} sahne)...", "progress": 10}
        
        prompts = self.senaryo_olustur(
            user_input,
            scene_count=scene_count,
            dialogue_style=dialogue_style,
            art_style=art_style,
            mood_style=mood_style,
            camera_style=camera_style,
            time_of_day=time_of_day,
            season=season,
            weather=weather,
            outfit_style=outfit_style,
            character_consistency=character_consistency
        )
        
        if not prompts:
            yield {"error": "Senaryo oluşturulamadı. Lütfen tekrar deneyin."}
            return
        
        yield {"status": f"Senaryo hazır! {len(prompts)} sahne resmedilecek.", "progress": 20}
        
        # 2. Görseller oluştur
        generated_images = []
        failed_scenes = []
        total_steps = len(prompts)
        generated_audios = []  # Ses dosyaları listesi
        
        # Paralel mi sıralı mı oluşturulacak?
        use_parallel = config.PARALLEL_IMAGE_GENERATION and total_steps > 1
        
        if use_parallel:
            # PARALEL GÖRSEL OLUŞTURMA
            yield {
                "status": f"🚀 {total_steps} sahne paralel olarak oluşturuluyor...",
                "progress": 25,
                "parallel_mode": True
            }
            
            # Tamamlanan sahneleri takip et
            completed_scenes = []
            
            def on_progress(completed, total, index, path):
                completed_scenes.append({
                    "index": index,
                    "path": path,
                    "completed": completed,
                    "total": total
                })
            
            # Paralel görsel oluşturma
            generated_images = self.generate_images_parallel(
                prompts,
                user_input,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                progress_callback=on_progress
            )
            
            # Sonuçları yield et
            for i, img_path in enumerate(generated_images):
                if img_path:
                    yield {
                        "status": f"Sahne {i + 1} tamamlandı!",
                        "progress": 25 + int(((i + 1) / total_steps) * 50),
                        "scene_completed": {
                            "index": i,
                            "path": img_path,
                            "audio": None
                        }
                    }
                else:
                    failed_scenes.append(i + 1)
            
            # Seslendirme (paralel görsellerden sonra sıralı)
            if generate_audio and config.TTS_ENABLED:
                yield {"status": "Sahneler seslendiriliyor...", "progress": 75}
                for i, prompt in enumerate(prompts):
                    if generated_images[i]:
                        audio_path = self.generate_scene_audio(prompt, i, user_input)
                        generated_audios.append(audio_path)
                    else:
                        generated_audios.append(None)
            else:
                generated_audios = [None] * total_steps
        
        else:
            # SIRALI GÖRSEL OLUŞTURMA (eski yöntem)
            for i, prompt in enumerate(prompts):
                progress = 20 + int((i / total_steps) * 55)  # 20% - 75%
                yield {
                    "status": f"Sahne {i + 1}/{total_steps} çiziliyor...",
                    "progress": progress,
                    "current_scene": i + 1
                }
                
                img_path = self.resim_uret(
                    prompt,
                    i,
                    user_input,
                    aspect_ratio=aspect_ratio,
                    image_size=image_size
                )
                
                if img_path:
                    generated_images.append(img_path)
                    
                    # Seslendirme oluştur (eğer aktifse)
                    audio_path = None
                    if generate_audio and config.TTS_ENABLED:
                        yield {
                            "status": f"Sahne {i + 1} seslendiriliyor...",
                            "progress": progress + 3
                        }
                        audio_path = self.generate_scene_audio(prompt, i, user_input)
                        if audio_path:
                            generated_audios.append(audio_path)
                        else:
                            generated_audios.append(None)
                    else:
                        generated_audios.append(None)
                    
                    yield {
                        "status": f"Sahne {i + 1} tamamlandı!" + (" (sesli)" if audio_path else ""),
                        "progress": progress + 5,
                        "scene_completed": {
                            "index": i,
                            "path": img_path,
                            "audio": audio_path
                        }
                    }
                else:
                    failed_scenes.append(i + 1)
                    generated_images.append(None)  # Placeholder
                    generated_audios.append(None)
                    yield {
                        "warning": f"Sahne {i + 1} oluşturulamadı",
                        "progress": progress
                    }
        
        # Başarılı görselleri filtrele
        valid_images = [img for img in generated_images if img]
        
        if not valid_images:
            yield {"error": "Hiçbir görsel oluşturulamadı."}
            return
        
        # Uyarı mesajı
        if failed_scenes:
            yield {"warning": f"Bazı sahneler oluşturulamadı: {failed_scenes}"}
        
        yield {"status": "Sahneler hazır! İnceleyebilirsiniz.", "progress": 85}
        
        # 3. Geçmişe kaydet
        history_entry = {
            "timestamp": int(time.time()),
            "prompt": user_input,
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
            "scene_count": scene_count,
            "art_style": art_style,
            "mood_style": mood_style,
            "camera_style": camera_style,
            "dialogue_style": dialogue_style,
            "environment": f"{time_of_day}/{season}/{weather}",
            "outfit_style": outfit_style,
            "character_consistency": character_consistency,
            "video_transition": video_transition,
            "prompts_generated": prompts,
            "images": generated_images,
            "audios": generated_audios,  # Ses dosyaları
            "generate_audio": generate_audio,  # Seslendirme aktif miydi
            "video": None,
            "frame_duration": frame_duration,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        self._save_to_history(history_entry)
        
        yield {
            "status": "Tamamlandı! Videoyu oluşturmak için butona basın.",
            "progress": 100,
            "result": history_entry
        }
        
        logger.info(f"Hikaye akışı tamamlandı: {len(valid_images)}/{len(prompts)} görsel")

    # ========================
    # SAHNE YENİLEME
    # ========================
    
    def regenerate_scene(
        self,
        history_timestamp: int,
        scene_index: int
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Belirli bir sahneyi yeniden oluştur
        
        Args:
            history_timestamp: Geçmiş kaydı timestamp'ı
            scene_index: Sahne indeksi
            
        Returns:
            (Yeni görsel yolu, Hata mesajı)
        """
        logger.info(f"Sahne {scene_index + 1} yeniden oluşturuluyor...")
        
        try:
            history = self.get_history()
            entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not entry:
                return None, "Geçmiş kaydı bulunamadı."
            
            prompts = entry.get("prompts_generated", [])
            if scene_index < 0 or scene_index >= len(prompts):
                return None, "Geçersiz sahne indeksi."
            
            prompt = prompts[scene_index]
            bolum_adi = entry.get("prompt", "Bolum")
            aspect_ratio = entry.get("aspect_ratio", "9:16")
            image_size = entry.get("image_size", "2K")
            
            new_img_path = self.resim_uret(prompt, scene_index, bolum_adi, aspect_ratio, image_size)
            
            if new_img_path:
                entry["images"][scene_index] = new_img_path
                self._save_updated_history(history)
                return new_img_path, None
            
            return None, "Görsel oluşturulamadı."
            
        except Exception as e:
            logger.error(f"Sahne yenileme hatası: {e}")
            return None, str(e)
    
    def generate_scene_variations(
        self,
        history_timestamp: int,
        scene_index: int,
        variation_count: int = 3
    ) -> Tuple[Optional[List[str]], Optional[str]]:
        """
        Bir sahne için birden fazla varyasyon oluştur
        
        Args:
            history_timestamp: Geçmiş kaydı timestamp'ı
            scene_index: Sahne indeksi
            variation_count: Oluşturulacak varyasyon sayısı (2-5)
            
        Returns:
            (Varyasyon görsel yolları listesi, Hata mesajı)
        """
        variation_count = max(2, min(5, variation_count))  # 2-5 arası sınırla
        logger.info(f"Sahne {scene_index + 1} için {variation_count} varyasyon oluşturuluyor...")
        
        try:
            history = self.get_history()
            entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not entry:
                return None, "Geçmiş kaydı bulunamadı."
            
            prompts = entry.get("prompts_generated", [])
            if scene_index < 0 or scene_index >= len(prompts):
                return None, "Geçersiz sahne indeksi."
            
            prompt = prompts[scene_index]
            bolum_adi = entry.get("prompt", "Bolum")
            aspect_ratio = entry.get("aspect_ratio", "9:16")
            image_size = entry.get("image_size", "2K")
            
            variations = []
            
            # Paralel oluşturma için görevler
            if config.PARALLEL_IMAGE_GENERATION:
                tasks = [
                    {
                        "index": scene_index,
                        "prompt": prompt,
                        "bolum_adi": f"{bolum_adi}_var{v+1}",
                        "aspect_ratio": aspect_ratio,
                        "image_size": image_size
                    }
                    for v in range(variation_count)
                ]
                
                max_workers = min(config.MAX_PARALLEL_WORKERS, variation_count)
                
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [
                        executor.submit(self._generate_single_image_task, task)
                        for task in tasks
                    ]
                    
                    for future in as_completed(futures):
                        result = future.result()
                        if result["path"]:
                            variations.append(result["path"])
            else:
                # Sıralı oluşturma
                for v in range(variation_count):
                    img_path = self.resim_uret(
                        prompt, 
                        scene_index, 
                        f"{bolum_adi}_var{v+1}",
                        aspect_ratio, 
                        image_size
                    )
                    if img_path:
                        variations.append(img_path)
            
            if variations:
                # Varyasyonları geçmişe kaydet
                if "variations" not in entry:
                    entry["variations"] = {}
                entry["variations"][str(scene_index)] = variations
                self._save_updated_history(history)
                
                logger.info(f"Sahne {scene_index + 1}: {len(variations)} varyasyon oluşturuldu")
                return variations, None
            
            return None, "Hiçbir varyasyon oluşturulamadı."
            
        except Exception as e:
            logger.error(f"Varyasyon oluşturma hatası: {e}")
            return None, str(e)
    
    def select_variation(
        self,
        history_timestamp: int,
        scene_index: int,
        variation_path: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Varyasyonlardan birini ana görsel olarak seç
        
        Args:
            history_timestamp: Geçmiş kaydı timestamp'ı
            scene_index: Sahne indeksi
            variation_path: Seçilen varyasyonun yolu
            
        Returns:
            (Başarılı mı, Hata mesajı)
        """
        try:
            history = self.get_history()
            entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not entry:
                return False, "Geçmiş kaydı bulunamadı."
            
            # Eski görseli varyasyonlara taşı
            old_image = entry["images"][scene_index]
            
            if "variations" not in entry:
                entry["variations"] = {}
            
            if str(scene_index) not in entry["variations"]:
                entry["variations"][str(scene_index)] = []
            
            # Eski görseli varyasyonlara ekle (eğer yoksa)
            if old_image and old_image not in entry["variations"][str(scene_index)]:
                entry["variations"][str(scene_index)].append(old_image)
            
            # Yeni görseli ana görsel yap
            entry["images"][scene_index] = variation_path
            
            # Seçilen varyasyonu listeden çıkar
            if variation_path in entry["variations"][str(scene_index)]:
                entry["variations"][str(scene_index)].remove(variation_path)
            
            self._save_updated_history(history)
            logger.info(f"Sahne {scene_index + 1}: Varyasyon seçildi")
            return True, None
            
        except Exception as e:
            logger.error(f"Varyasyon seçme hatası: {e}")
            return False, str(e)
    
    def regenerate_scene_with_prompt(
        self,
        history_timestamp: int,
        scene_index: int,
        new_prompt: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Yeni prompt ile sahneyi yeniden oluştur
        
        Args:
            history_timestamp: Geçmiş kaydı timestamp'ı
            scene_index: Sahne indeksi
            new_prompt: Yeni prompt
            
        Returns:
            (Yeni görsel yolu, Hata mesajı)
        """
        logger.info(f"Sahne {scene_index + 1} yeni prompt ile oluşturuluyor...")
        
        try:
            history = self.get_history()
            entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not entry:
                return None, "Geçmiş kaydı bulunamadı."
            
            prompts = entry.get("prompts_generated", [])
            if scene_index < 0 or scene_index >= len(prompts):
                return None, "Geçersiz sahne indeksi."
            
            # Prompt'u güncelle
            prompts[scene_index] = sanitize_input(new_prompt)
            entry["prompts_generated"] = prompts
            
            bolum_adi = entry.get("prompt", "Bolum")
            aspect_ratio = entry.get("aspect_ratio", "9:16")
            image_size = entry.get("image_size", "2K")
            
            # Mevcut görseli referans olarak kullan
            current_image = entry.get("images", [])[scene_index] if scene_index < len(entry.get("images", [])) else None
            reference_path = None
            if current_image:
                ref_full_path = os.path.join(os.getcwd(), current_image.replace("/", os.sep))
                if os.path.exists(ref_full_path):
                    reference_path = ref_full_path
            
            new_img_path = self.resim_uret(
                new_prompt,
                scene_index,
                bolum_adi,
                aspect_ratio,
                image_size,
                reference_images=[reference_path] if reference_path else None
            )
            
            if new_img_path:
                entry["images"][scene_index] = new_img_path
                self._save_updated_history(history)
                return new_img_path, None
            
            return None, "Görsel oluşturulamadı."
            
        except Exception as e:
            logger.error(f"Prompt ile yenileme hatası: {e}")
            return None, str(e)
    
    def regenerate_scene_match_style(
        self,
        history_timestamp: int,
        scene_index: int
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Diğer sahnelerin stiline uygun olarak yeniden oluştur
        
        Args:
            history_timestamp: Geçmiş kaydı timestamp'ı
            scene_index: Sahne indeksi
            
        Returns:
            (Yeni görsel yolu, Hata mesajı)
        """
        logger.info(f"Sahne {scene_index + 1} stil eşleştirmeli oluşturuluyor...")
        
        try:
            history = self.get_history()
            entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not entry:
                return None, "Geçmiş kaydı bulunamadı."
            
            prompts = entry.get("prompts_generated", [])
            images = entry.get("images", [])
            
            if scene_index < 0 or scene_index >= len(prompts):
                return None, "Geçersiz sahne indeksi."
            
            # Referans görselleri topla
            reference_paths = []
            
            # İlk sahneyi referans al (kendisi değilse)
            if scene_index != 0 and len(images) > 0 and images[0]:
                ref_path = os.path.join(os.getcwd(), images[0].replace("/", os.sep))
                if os.path.exists(ref_path):
                    reference_paths.append(ref_path)
            
            # Önceki sahneyi de referans al
            if scene_index > 1 and images[scene_index - 1]:
                ref_path = os.path.join(os.getcwd(), images[scene_index - 1].replace("/", os.sep))
                if ref_path not in reference_paths and os.path.exists(ref_path):
                    reference_paths.append(ref_path)
            
            # İndeks 0 ise sonraki sahneyi kullan
            if scene_index == 0 and len(images) > 1 and images[1]:
                ref_path = os.path.join(os.getcwd(), images[1].replace("/", os.sep))
                if os.path.exists(ref_path):
                    reference_paths.append(ref_path)
            
            logger.info(f"Referans sayısı: {len(reference_paths)}")
            
            prompt = prompts[scene_index]
            bolum_adi = entry.get("prompt", "Bolum")
            aspect_ratio = entry.get("aspect_ratio", "9:16")
            image_size = entry.get("image_size", "2K")
            
            new_img_path = self.resim_uret(
                prompt,
                scene_index,
                bolum_adi,
                aspect_ratio,
                image_size,
                reference_images=reference_paths if reference_paths else None
            )
            
            if new_img_path:
                entry["images"][scene_index] = new_img_path
                self._save_updated_history(history)
                return new_img_path, None
            
            return None, "Görsel oluşturulamadı."
            
        except Exception as e:
            logger.error(f"Stil eşleştirme hatası: {e}")
            return None, str(e)
    
    def regenerate_scene_audio(
        self,
        history_timestamp: int,
        scene_index: int
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Belirli bir sahnenin seslendirmesini yeniden oluştur
        
        Args:
            history_timestamp: Geçmiş kaydı timestamp'ı
            scene_index: Sahne indeksi
            
        Returns:
            (Yeni ses dosyası yolu, Hata mesajı)
        """
        logger.info(f"Sahne {scene_index + 1} seslendirmesi yeniden oluşturuluyor...")
        
        if not config.TTS_ENABLED:
            return None, "Seslendirme özelliği devre dışı."
        
        try:
            history = self.get_history()
            entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not entry:
                return None, "Geçmiş kaydı bulunamadı."
            
            prompts = entry.get("prompts_generated", [])
            if scene_index < 0 or scene_index >= len(prompts):
                return None, "Geçersiz sahne indeksi."
            
            prompt = prompts[scene_index]
            bolum_adi = entry.get("prompt", "Bolum")
            
            # Yeni ses oluştur
            new_audio_path = self.generate_scene_audio(prompt, scene_index, bolum_adi)
            
            if new_audio_path:
                # Audios listesini güncelle
                if "audios" not in entry:
                    entry["audios"] = [None] * len(prompts)
                
                # Liste boyutunu eşitle
                while len(entry["audios"]) <= scene_index:
                    entry["audios"].append(None)
                
                entry["audios"][scene_index] = new_audio_path
                self._save_updated_history(history)
                return new_audio_path, None
            
            return None, "Seslendirme oluşturulamadı."
            
        except Exception as e:
            logger.error(f"Seslendirme yenileme hatası: {e}")
            return None, str(e)

    # ========================
    # HİKAYE DALLANMASI
    # ========================
    
    def create_story_branch(
        self,
        history_timestamp: int,
        branch_point: int,
        branch_prompt: str,
        branch_name: str = "Alternatif Son",
        scene_count: int = 5
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Mevcut hikayeden alternatif bir dal oluştur
        
        Args:
            history_timestamp: Ana hikaye timestamp'ı
            branch_point: Dallanma noktası (sahne indeksi, bu sahneden sonra dallanır)
            branch_prompt: Alternatif hikaye yönü
            branch_name: Dal adı
            scene_count: Yeni dal için sahne sayısı
            
        Returns:
            (Yeni hikaye entry, Hata mesajı)
        """
        logger.info(f"Hikaye dallanması oluşturuluyor: sahne {branch_point + 1}'den sonra")
        
        try:
            history = self.get_history()
            parent_entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not parent_entry:
                return None, "Ana hikaye bulunamadı."
            
            parent_images = parent_entry.get("images", [])
            parent_prompts = parent_entry.get("prompts_generated", [])
            
            if branch_point < 0 or branch_point >= len(parent_images):
                return None, "Geçersiz dallanma noktası."
            
            # Dallanma noktasına kadar olan görselleri ve promptları kopyala
            branch_images = parent_images[:branch_point + 1].copy()
            branch_prompts = parent_prompts[:branch_point + 1].copy()
            
            # Yeni sahneler için prompt oluştur
            original_prompt = parent_entry.get("prompt", "")
            branch_context = f"""
            ÖNCEKİ HİKAYE: {original_prompt}
            
            MEVCUT DURUM (Sahne {branch_point + 1}'e kadar):
            {' '.join([get_prompt_from_scene(p)[:100] for p in branch_prompts[-3:]])}
            
            YENİ YÖN: {branch_prompt}
            
            Bu noktadan itibaren hikayeyi yeni yöne çevir.
            """
            
            # Yeni sahneleri oluştur
            new_prompts = self.senaryo_olustur(
                branch_context,
                scene_count=scene_count,
                dialogue_style=parent_entry.get("dialogue_style", "short"),
                art_style=parent_entry.get("art_style", "comic"),
                mood_style=parent_entry.get("mood_style", "dynamic"),
                camera_style=parent_entry.get("camera_style", "balanced")
            )
            
            if not new_prompts:
                return None, "Alternatif senaryo oluşturulamadı."
            
            # Yeni görselleri oluştur
            aspect_ratio = parent_entry.get("aspect_ratio", "9:16")
            image_size = parent_entry.get("image_size", "2K")
            
            if config.PARALLEL_IMAGE_GENERATION:
                new_images = self.generate_images_parallel(
                    new_prompts,
                    f"{branch_name}",
                    aspect_ratio=aspect_ratio,
                    image_size=image_size
                )
            else:
                new_images = []
                for i, prompt in enumerate(new_prompts):
                    img_path = self.resim_uret(
                        prompt,
                        branch_point + 1 + i,
                        branch_name,
                        aspect_ratio=aspect_ratio,
                        image_size=image_size
                    )
                    new_images.append(img_path)
            
            # Tüm görselleri ve promptları birleştir
            branch_images.extend(new_images)
            branch_prompts.extend(new_prompts)
            
            # Yeni hikaye kaydı oluştur
            branch_entry = {
                "timestamp": int(time.time()),
                "prompt": f"{branch_name}: {branch_prompt}",
                "parent_timestamp": history_timestamp,
                "branch_point": branch_point,
                "branch_name": branch_name,
                "is_branch": True,
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
                "scene_count": len(branch_images),
                "art_style": parent_entry.get("art_style", "comic"),
                "mood_style": parent_entry.get("mood_style", "dynamic"),
                "camera_style": parent_entry.get("camera_style", "balanced"),
                "dialogue_style": parent_entry.get("dialogue_style", "short"),
                "prompts_generated": branch_prompts,
                "images": branch_images,
                "audios": [None] * len(branch_images),
                "video": None,
                "frame_duration": parent_entry.get("frame_duration", 5.0),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            
            # Ana hikayeye dal referansı ekle
            if "branches" not in parent_entry:
                parent_entry["branches"] = []
            parent_entry["branches"].append({
                "timestamp": branch_entry["timestamp"],
                "name": branch_name,
                "branch_point": branch_point
            })
            
            # Kaydet
            self._save_to_history(branch_entry)
            self._save_updated_history(history)
            
            logger.info(f"Hikaye dalı oluşturuldu: {branch_name} ({len(new_images)} yeni sahne)")
            return branch_entry, None
            
        except Exception as e:
            logger.error(f"Hikaye dallanması hatası: {e}")
            return None, str(e)
    
    def get_story_branches(self, history_timestamp: int) -> List[Dict[str, Any]]:
        """
        Bir hikayenin tüm dallarını getir
        
        Args:
            history_timestamp: Ana hikaye timestamp'ı
            
        Returns:
            Dal listesi
        """
        history = self.get_history()
        
        # Ana hikayeyi bul
        parent_entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
        
        if not parent_entry:
            return []
        
        branches = []
        
        # Kayıtlı dalları getir
        branch_refs = parent_entry.get("branches", [])
        for ref in branch_refs:
            branch = next((item for item in history if item.get("timestamp") == ref.get("timestamp")), None)
            if branch:
                branches.append({
                    "timestamp": branch["timestamp"],
                    "name": branch.get("branch_name", "Alternatif"),
                    "branch_point": branch.get("branch_point", 0),
                    "scene_count": len(branch.get("images", [])),
                    "created_at": branch.get("created_at", ""),
                    "first_image": next((img for img in branch.get("images", []) if img), None)
                })
        
        return branches

    # ========================
    # VİDEO (GEÇMİŞTEN)
    # ========================
    
    def create_video_from_history(
        self,
        history_timestamp: int,
        music_file: Optional[str] = None,
        audio_settings: Optional[Dict[str, Any]] = None,
        ken_burns_effect: str = "none",
        enable_subtitles: bool = False,
        subtitle_settings: Optional[Dict[str, Any]] = None,
        watermark_text: Optional[str] = None,
        watermark_settings: Optional[Dict[str, Any]] = None,
        selected_scenes: Optional[List[int]] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Geçmiş kaydından video oluştur
        
        Args:
            history_timestamp: Geçmiş kaydı timestamp'ı
            music_file: Arkaplan müzik dosyası (opsiyonel)
            audio_settings: Ses ayarları (opsiyonel)
                - musicVolume: Müzik ses seviyesi (0-1)
                - voiceVolume: Karakter ses seviyesi (0-1)
                - duckingLevel: Ducking miktarı (dB)
                - duckingFade: Fade süresi (saniye)
                - voiceDelay: Ses başlangıç gecikmesi (saniye)
                - musicFadeOut: Video sonu fade out süresi (saniye)
            enable_subtitles: Altyazı eklensin mi
            subtitle_settings: Altyazı ayarları (opsiyonel)
            selected_scenes: Sadece bu indekslerdeki sahneleri kullan (opsiyonel)
            
        Returns:
            (Video yolu, Hata mesajı)
        """
        music_info = f" (müzik: {music_file})" if music_file else ""
        subtitle_info = " (altyazılı)" if enable_subtitles else ""
        selection_info = f" ({len(selected_scenes)} sahne seçildi)" if selected_scenes else ""
        logger.info(f"Geçmişten video oluşturuluyor: {history_timestamp}{music_info}{subtitle_info}{selection_info}")
        
        try:
            history = self.get_history()
            entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not entry:
                return None, "Geçmiş kaydı bulunamadı."
            
            # Tüm sahneleri al
            all_images = entry.get("images", [])
            all_audios = entry.get("audios", [])
            all_prompts = entry.get("prompts_generated", [])
            
            # Seçili sahneleri filtrele
            images = []
            scene_audios = []
            prompts = []
            
            if selected_scenes and len(selected_scenes) > 0:
                # İndeksleri kontrol et ve geçerli olanları al
                valid_indices = [i for i in selected_scenes if 0 <= i < len(all_images)]
                if not valid_indices:
                    return None, "Geçersiz sahne seçimi."
                
                # Sırayı korumak için selected_scenes sırasına göre değil, orijinal sıraya göre alalım?
                # Kullanıcı sıralama değiştirmiş olabilir ama burada sadece "seçilenler" diyor.
                # Genelde seçilenler orijinal sırasıyla işlenmeli, yoksa kullanıcı custom sıralama yapmış olur.
                # Ancak kullanıcı UI'da sahnelerin yerini değiştirebiliyor. Eğer UI'daki sıraya göre indeks geliyorsa sorun yok.
                # Burada gelen indeksler, o anki history listesindeki indekslerdir.
                
                # İndeksleri küçükten büyüğe sıralayalım ki sahne akışı bozulmasın
                # (Kullanıcı isterse reorder endpoint'i ile zaten sırayı değiştirebiliyor)
                valid_indices.sort()
                
                for i in valid_indices:
                    images.append(all_images[i])
                    
                    # Audio varsa al, yoksa None
                    if i < len(all_audios):
                        scene_audios.append(all_audios[i])
                    else:
                        scene_audios.append(None)
                        
                    # Prompt varsa al
                    if i < len(all_prompts):
                        prompts.append(all_prompts[i])
            else:
                # Filtre yoksa hepsini al (None olmayanları)
                # Ancak orijinal kodda [img for img in images if img] vardı.
                # İndekslerin kaymaması için dikkatli olmalıyız.
                # Eğer audio ve prompt eşleşmesi önemliyse, boş olan image'ları atlamak indeksleri kaydırabilir.
                # `video_olustur` metodu muhtemelen non-None imagelar bekliyor.
                
                # Orijinal mantık: Sadece resmi olan sahneler
                for i, img in enumerate(all_images):
                    if img:
                        images.append(img)
                        if i < len(all_audios):
                            scene_audios.append(all_audios[i])
                        else:
                            scene_audios.append(None)
                        if i < len(all_prompts):
                            prompts.append(all_prompts[i])

            if not images:
                return None, "Görsel bulunamadı."
            
            user_input = entry.get("prompt", "Video")
            frame_duration = entry.get("frame_duration", config.DEFAULT_FRAME_DURATION)
            transition = entry.get("video_transition", "none")
            
            # Altyazıları hazırla (prompt'lardan diyalogları çıkar)
            subtitles = None
            if enable_subtitles:
                subtitles = self._extract_subtitles_from_prompts(prompts)
            
            video_path = self.video_olustur(
                images,
                user_input,
                frame_duration=frame_duration,
                transition=transition,
                music_file=music_file,
                scene_audios=scene_audios if scene_audios else None,
                audio_settings=audio_settings,
                ken_burns_effect=ken_burns_effect,
                subtitles=subtitles,
                subtitle_settings=subtitle_settings,
                watermark_text=watermark_text,
                watermark_settings=watermark_settings
            )
            
            if video_path:
                entry["video"] = video_path
                if music_file:
                    entry["video_music"] = music_file
                self._save_updated_history(history)
                return video_path, None
            
            return None, "Video oluşturulamadı."
            
        except Exception as e:
            logger.error(f"Geçmişten video hatası: {e}")
            return None, str(e)

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

    def merge_stories_to_video(
        self,
        timestamps: List[int],
        music_file: Optional[str] = None,
        video_name: str = "Birleşik Hikaye",
        audio_settings: Optional[Dict[str, Any]] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Birden fazla hikayeyi birleştirerek tek video oluştur
        
        Args:
            timestamps: Hikaye timestamp'ları (sıralı)
            music_file: Arkaplan müzik dosyası (opsiyonel)
            video_name: Video adı
            audio_settings: Ses ayarları (opsiyonel)
            
        Returns:
            (Video yolu, Hata mesajı)
        """
        logger.info(f"Hikayeler birleştiriliyor: {len(timestamps)} hikaye")
        
        try:
            history = self.get_history()
            
            # Tüm görselleri ve sesleri topla (sıralı)
            all_images = []
            all_audios = []
            
            for ts in timestamps:
                entry = next((item for item in history if item.get("timestamp") == ts), None)
                
                if not entry:
                    logger.warning(f"Hikaye bulunamadı: {ts}")
                    continue
                
                images = entry.get("images", [])
                audios = entry.get("audios", [])
                
                # Her görseli ve karşılık gelen sesi ekle
                for i, img in enumerate(images):
                    if img:  # None değilse
                        all_images.append(img)
                        # Karşılık gelen ses varsa ekle, yoksa None
                        audio = audios[i] if audios and i < len(audios) else None
                        all_audios.append(audio)
            
            if not all_images:
                return None, "Birleştirilecek görsel bulunamadı."
            
            logger.info(f"Toplam {len(all_images)} görsel, {sum(1 for a in all_audios if a)} ses birleştirilecek")
            
            # Video oluştur
            video_path = self.video_olustur(
                image_paths=all_images,
                user_input=video_name,
                frame_duration=config.DEFAULT_FRAME_DURATION,
                transition="fade",
                music_file=music_file,
                scene_audios=all_audios if any(all_audios) else None,
                audio_settings=audio_settings
            )
            
            if video_path:
                logger.info(f"Birleşik video oluşturuldu: {video_path}")
                return video_path, None
            
            return None, "Video oluşturulamadı."
            
        except Exception as e:
            logger.error(f"Hikaye birleştirme hatası: {e}")
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
    # SAHNE YÖNETİMİ (YENİ)
    # ========================
    
    def delete_scene(
        self,
        history_timestamp: int,
        scene_index: int
    ) -> Tuple[bool, Optional[str]]:
        """
        Belirli bir sahneyi sil
        
        Args:
            history_timestamp: Geçmiş kaydı timestamp'ı
            scene_index: Sahne indeksi
            
        Returns:
            (Başarılı mı?, Hata mesajı)
        """
        logger.info(f"Sahne {scene_index + 1} siliniyor...")
        
        try:
            history = self.get_history()
            entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not entry:
                return False, "Geçmiş kaydı bulunamadı."
            
            prompts = entry.get("prompts_generated", [])
            images = entry.get("images", [])
            
            if scene_index < 0 or scene_index >= len(prompts):
                return False, "Geçersiz sahne indeksi."
            
            # En az 2 sahne kalmalı
            if len(prompts) <= 1:
                return False, "En az bir sahne kalmalıdır."
            
            # Görseli sil
            if scene_index < len(images) and images[scene_index]:
                img_path = os.path.join(os.getcwd(), images[scene_index])
                if os.path.exists(img_path):
                    try:
                        os.remove(img_path)
                    except Exception as e:
                        logger.warning(f"Görsel silinemedi: {e}")
            
            # Listelerden çıkar
            prompts.pop(scene_index)
            if scene_index < len(images):
                images.pop(scene_index)
            
            entry["prompts_generated"] = prompts
            entry["images"] = images
            entry["scene_count"] = len(prompts)
            
            self._save_updated_history(history)
            logger.info(f"Sahne {scene_index + 1} silindi")
            return True, None
            
        except Exception as e:
            logger.error(f"Sahne silme hatası: {e}")
            return False, str(e)
    
    def add_scene(
        self,
        history_timestamp: int,
        after_index: int,
        prompt: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Belirli bir konumdan sonra yeni sahne ekle
        
        Args:
            history_timestamp: Geçmiş kaydı timestamp'ı
            after_index: Ardından ekleneceği sahne indeksi
            prompt: Yeni sahne promptu
            
        Returns:
            (Yeni görsel yolu, Hata mesajı)
        """
        logger.info(f"Sahne {after_index + 2} ekleniyor...")
        
        try:
            history = self.get_history()
            entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not entry:
                return None, "Geçmiş kaydı bulunamadı."
            
            prompts = entry.get("prompts_generated", [])
            images = entry.get("images", [])
            
            if after_index < -1 or after_index >= len(prompts):
                return None, "Geçersiz indeks."
            
            # Prompt'u ekle
            new_index = after_index + 1
            clean_prompt = sanitize_input(prompt)
            prompts.insert(new_index, clean_prompt)
            
            # Görsel oluştur
            bolum_adi = entry.get("prompt", "Bolum")
            aspect_ratio = entry.get("aspect_ratio", "9:16")
            image_size = entry.get("image_size", "2K")
            
            new_img_path = self.resim_uret(
                clean_prompt,
                new_index,
                bolum_adi,
                aspect_ratio,
                image_size
            )
            
            if new_img_path:
                images.insert(new_index, new_img_path)
                entry["prompts_generated"] = prompts
                entry["images"] = images
                entry["scene_count"] = len(prompts)
                
                self._save_updated_history(history)
                return new_img_path, None
            
            return None, "Görsel oluşturulamadı."
            
        except Exception as e:
            logger.error(f"Sahne ekleme hatası: {e}")
            return None, str(e)
    
    def reorder_scenes(
        self,
        history_timestamp: int,
        new_order: List[int]
    ) -> Tuple[bool, Optional[str]]:
        """
        Sahnelerin sırasını değiştir
        
        Args:
            history_timestamp: Geçmiş kaydı timestamp'ı
            new_order: Yeni sıralama (indeks listesi)
            
        Returns:
            (Başarılı mı?, Hata mesajı)
        """
        logger.info(f"Sahne sırası değiştiriliyor: {new_order}")
        
        try:
            history = self.get_history()
            entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not entry:
                return False, "Geçmiş kaydı bulunamadı."
            
            prompts = entry.get("prompts_generated", [])
            images = entry.get("images", [])
            
            # Validate new_order
            if sorted(new_order) != list(range(len(prompts))):
                return False, "Geçersiz sıralama."
            
            # Yeniden sırala
            entry["prompts_generated"] = [prompts[i] for i in new_order]
            entry["images"] = [images[i] if i < len(images) else None for i in new_order]
            
            self._save_updated_history(history)
            logger.info("Sahne sırası güncellendi")
            return True, None
            
        except Exception as e:
            logger.error(f"Sıralama hatası: {e}")
            return False, str(e)

    # ========================
    # HİKAYE DEVAMI
    # ========================
    
    def continue_story(
        self,
        history_timestamp: int,
        continuation_prompt: str = "",
        scene_count: int = 5
    ) -> Tuple[Optional[List[str]], Optional[str]]:
        """
        Mevcut hikayeye devam et - karakter tutarlılığını koruyarak
        
        Args:
            history_timestamp: Geçmiş kaydı timestamp'ı
            continuation_prompt: Devam için konu (boş bırakılırsa otomatik devam)
            scene_count: Eklenecek sahne sayısı
            
        Returns:
            (Yeni görsel yolları, Hata mesajı)
        """
        logger.info(f"Hikaye devam ettiriliyor...")
        
        try:
            history = self.get_history()
            entry = next((item for item in history if item.get("timestamp") == history_timestamp), None)
            
            if not entry:
                return None, "Geçmiş kaydı bulunamadı."
            
            original_prompt = entry.get("prompt", "")
            existing_prompts = entry.get("prompts_generated", [])
            art_style = entry.get("art_style", "comic")
            mood_style = entry.get("mood_style", "dynamic")
            camera_style = entry.get("camera_style", "balanced")
            dialogue_style = entry.get("dialogue_style", "short")
            
            if not existing_prompts:
                return None, "Devam edilecek sahne bulunamadı."
            
            # ========================
            # KARAKTER ANALİZİ
            # ========================
            
            # Son sahnelerden karakter bilgilerini çıkar
            last_scenes = existing_prompts[-5:] if len(existing_prompts) >= 5 else existing_prompts
            last_scene_texts = [get_prompt_from_scene(p) for p in last_scenes]
            
            # Karakterleri analiz et
            character_analysis_prompt = f"""
            Aşağıdaki sahne açıklamalarından TÜM karakterlerin detaylı fiziksel özelliklerini çıkar.
            
            SAHNELER:
            {chr(10).join([f"Sahne {i+1}: {text}" for i, text in enumerate(last_scene_texts)])}
            
            Her karakter için şunları belirle:
            - İsim veya tanımlayıcı (örn: "ana karakter", "genç kız", "yaşlı adam")
            - Saç rengi ve stili
            - Göz rengi
            - Ten rengi
            - Yaş aralığı
            - Kıyafet detayları
            - Ayırt edici özellikler (yara izi, dövme, aksesuar vb.)
            
            JSON formatında döndür:
            {{
                "characters": [
                    {{
                        "identifier": "karakter adı/tanımı",
                        "hair": "saç detayı",
                        "eyes": "göz rengi",
                        "skin": "ten rengi",
                        "age": "yaş aralığı",
                        "clothing": "kıyafet",
                        "distinctive_features": "ayırt edici özellikler"
                    }}
                ],
                "setting": "mevcut mekan/ortam",
                "time_of_day": "gündüz/gece/akşam",
                "mood": "sahne atmosferi"
            }}
            """
            
            character_response = self._make_text_request(character_analysis_prompt, json_response=True)
            character_data = extract_json_from_response(character_response)
            
            # character_data bir liste olabilir, dict olmalı
            if not character_data or not isinstance(character_data, dict):
                character_data = {"characters": [], "setting": "", "time_of_day": "", "mood": ""}
            
            # Karakter profili oluştur
            character_profiles = ""
            if character_data.get("characters"):
                character_profiles = "KARAKTERLERİN FİZİKSEL ÖZELLİKLERİ (MUTLAKA AYNI KALMALI):\n"
                for char in character_data["characters"]:
                    character_profiles += f"""
                    - {char.get('identifier', 'Karakter')}:
                      Saç: {char.get('hair', 'belirtilmemiş')}
                      Gözler: {char.get('eyes', 'belirtilmemiş')}
                      Ten: {char.get('skin', 'belirtilmemiş')}
                      Yaş: {char.get('age', 'belirtilmemiş')}
                      Kıyafet: {char.get('clothing', 'belirtilmemiş')}
                      Özellikler: {char.get('distinctive_features', 'yok')}
                    """
            
            # ========================
            # DEVAM SENARYOSU OLUŞTUR
            # ========================
            
            # Son sahnenin tam metnini al
            last_scene_full = get_prompt_from_scene(existing_prompts[-1])
            second_last_scene = get_prompt_from_scene(existing_prompts[-2]) if len(existing_prompts) > 1 else ""
            
            # Devam promptu
            if continuation_prompt.strip():
                continuation_direction = f"YENİ YÖN/OLAY: {continuation_prompt}"
            else:
                continuation_direction = "Hikayeyi doğal akışında devam ettir. Son sahnedeki olayların mantıksal sonuçlarını göster."
            
            summary_prompt = f"""
            SEN BİR HİKAYE YAZARISIN. Mevcut hikayenin DEVAMINI yazacaksın.
            
            ========================
            ANA HİKAYE KONUSU:
            {original_prompt}
            
            ========================
            {character_profiles}
            
            ========================
            MEVCUT ORTAM:
            Mekan: {character_data.get('setting', 'belirtilmemiş')}
            Zaman: {character_data.get('time_of_day', 'belirtilmemiş')}
            Atmosfer: {character_data.get('mood', 'belirtilmemiş')}
            
            ========================
            SON İKİ SAHNE (BURADAN DEVAM EDECEK):
            
            Sahne {len(existing_prompts) - 1}: {second_last_scene}
            
            Sahne {len(existing_prompts)} (EN SON): {last_scene_full}
            
            ========================
            {continuation_direction}
            
            ========================
            ÖNEMLİ KURALLAR:
            1. KARAKTERLERİN FİZİKSEL ÖZELLİKLERİ KESİNLİKLE DEĞİŞMEMELİ!
               - Aynı saç rengi ve stili
               - Aynı göz rengi
               - Aynı ten rengi
               - Aynı yaş görünümü
               - Kıyafetler mantıklı şekilde değişebilir ama karakter tanınabilir olmalı
            
            2. HİKAYE SÜREKLİLİĞİ:
               - Son sahnedeki olayların doğrudan devamı olmalı
               - Mekan değişikliği varsa geçiş mantıklı olmalı
               - Karakterlerin duygu durumu tutarlı olmalı
            
            3. GÖRSEL PROMPT FORMATI:
               Her sahne için detaylı görsel açıklama yaz:
               - Karakterlerin TAM fiziksel tanımı (her sahnede tekrarla!)
               - Mekan detayları
               - Işık ve atmosfer
               - Aksiyon/poz
               - Kamera açısı
            
            ========================
            ÇIKTI: {scene_count} yeni sahne için JSON array döndür.
            Her prompt en az 100 kelime olmalı ve karakter özelliklerini içermeli.
            
            Örnek format:
            [
                "Sahne açıklaması... [karakter adı]: [tam fiziksel özellikler tekrar edilmeli]...",
                "..."
            ]
            """
            
            response_text = self._make_text_request(summary_prompt, json_response=True)
            new_prompts = extract_json_from_response(response_text)
            
            if not new_prompts or not isinstance(new_prompts, list):
                return None, "Devam senaryosu oluşturulamadı."
            
            # ========================
            # KARAKTER TUTARLILIĞI İÇİN PROMPT ZENGİNLEŞTİRME
            # ========================
            
            enriched_prompts = []
            for prompt in new_prompts[:scene_count]:
                # Her prompt'a karakter özelliklerini ekle
                if character_profiles and character_data.get("characters"):
                    char_reminder = "\n\n[KARAKTER TUTARLILIĞI - "
                    for char in character_data["characters"][:3]:  # İlk 3 karakter
                        char_reminder += f"{char.get('identifier', '')}: {char.get('hair', '')} saç, {char.get('eyes', '')} gözler, {char.get('skin', '')} ten, {char.get('age', '')} yaş. "
                    char_reminder += "]"
                    
                    # Prompt'un sonuna karakter hatırlatması ekle
                    enriched_prompt = f"{prompt}{char_reminder}"
                else:
                    enriched_prompt = prompt
                
                enriched_prompts.append(enriched_prompt)
            
            # ========================
            # GÖRSELLER OLUŞTUR
            # ========================
            
            bolum_adi = entry.get("prompt", "Bolum")
            aspect_ratio = entry.get("aspect_ratio", "9:16")
            image_size = entry.get("image_size", "2K")
            
            # Son görseli referans olarak kullan (varsa)
            last_image_path = None
            for img in reversed(entry.get("images", [])):
                if img and os.path.exists(img if os.path.isabs(img) else os.path.join(os.getcwd(), img)):
                    last_image_path = img if os.path.isabs(img) else os.path.join(os.getcwd(), img)
                    break
            
            reference_images = [last_image_path] if last_image_path else None
            
            if config.PARALLEL_IMAGE_GENERATION:
                new_images = self.generate_images_parallel(
                    enriched_prompts,
                    bolum_adi,
                    aspect_ratio=aspect_ratio,
                    image_size=image_size
                )
            else:
                new_images = []
                for i, prompt in enumerate(enriched_prompts):
                    img_path = self.resim_uret(
                        prompt,
                        len(existing_prompts) + i,
                        bolum_adi,
                        aspect_ratio,
                        image_size
                    )
                    new_images.append(img_path)
            
            # Audios listesini de genişlet
            if "audios" not in entry:
                entry["audios"] = [None] * len(existing_prompts)
            
            # ========================
            # SESLENDİRME OLUŞTUR
            # ========================
            new_audios = []
            
            for i, prompt in enumerate(enriched_prompts):
                scene_index = len(existing_prompts) + i
                try:
                    # Ses oluştur (fonksiyon içinde diyalog çıkarılıyor)
                    audio_path = self.generate_scene_audio(
                        prompt,
                        scene_index,
                        bolum_adi
                    )
                    new_audios.append(audio_path)
                    if audio_path:
                        logger.info(f"Devam sahnesi {scene_index + 1} seslendirmesi oluşturuldu")
                except Exception as e:
                    logger.warning(f"Devam sahnesi {scene_index + 1} seslendirme hatası: {e}")
                    new_audios.append(None)
            
            # Mevcut kayda ekle
            entry["prompts_generated"].extend(enriched_prompts)
            entry["images"].extend(new_images)
            entry["audios"].extend(new_audios)
            entry["scene_count"] = len(entry["prompts_generated"])
            entry["video"] = None  # Video yeniden oluşturulmalı
            
            # Karakter verilerini kaydet (gelecek devamlar için)
            if character_data.get("characters"):
                entry["character_profiles"] = character_data
            
            self._save_updated_history(history)
            
            valid_images = [img for img in new_images if img]
            valid_audios = [a for a in new_audios if a]
            logger.info(f"Hikaye devamı eklendi: {len(valid_images)} yeni sahne, {len(valid_audios)} seslendirme")
            return valid_images, None
            
        except Exception as e:
            logger.error(f"Hikaye devam hatası: {e}")
            return None, str(e)

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
