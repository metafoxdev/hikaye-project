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


class AudioMixin:

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
