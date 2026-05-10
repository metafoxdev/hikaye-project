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


class ImageMixin:

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

    def resim_uret_dual(
        self,
        prompt: str,
        index: int,
        bolum_adi: str,
        image_size: str = "2K",
        reference_images: Optional[List[str]] = None
    ) -> Dict[str, Optional[str]]:
        """
        Aynı sahne için hem 9:16 (dikey) hem 16:9 (yatay) görsel üret.
        İki API isteği paralel olarak gönderilir.

        Args:
            prompt: Görsel promptu
            index: Sahne indeksi
            bolum_adi: Bölüm adı (dosya adı için)
            image_size: Çözünürlük (1K / 2K / 4K)
            reference_images: Referans görseller

        Returns:
            {"portrait": "static/output/...", "landscape": "static/output/..."} sözlüğü
            Başarısız olanlar None olur.
        """
        logger.info(f"Sahne {index + 1} çift format üretiliyor (9:16 + 16:9)...")
        prompt_text = get_prompt_from_scene(prompt)
        timestamp = int(time.time())
        safe_bolum = sanitize_filename(bolum_adi, Constants.SAFE_FILENAME_LENGTH)
        results: Dict[str, Optional[str]] = {"portrait": None, "landscape": None}

        def _generate(ratio: str, suffix: str) -> Optional[str]:
            try:
                image_data = self._make_image_request(
                    prompt_text,
                    aspect_ratio=ratio,
                    image_size=image_size,
                    reference_images=reference_images
                )
                if image_data:
                    file_name = f"{timestamp}_{index + 1}_{safe_bolum}_{suffix}.png"
                    file_path = os.path.join(self.output_dir, file_name)
                    with open(file_path, "wb") as f:
                        f.write(image_data)
                    api_stats.record_request(True)
                    api_stats.record_image()
                    logger.info(f"Sahne {index + 1} [{ratio}] kaydedildi: {file_name}")
                    return f"static/output/{file_name}"
                api_stats.record_request(False)
                return None
            except Exception as e:
                logger.error(f"Sahne {index + 1} [{ratio}] hata: {e}")
                api_stats.record_request(False)
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            fut_portrait  = executor.submit(_generate, "9:16",  "portrait")
            fut_landscape = executor.submit(_generate, "16:9", "landscape")
            results["portrait"]  = fut_portrait.result()
            results["landscape"] = fut_landscape.result()

        ok = sum(1 for v in results.values() if v)
        logger.info(f"Sahne {index + 1} çift format tamamlandı: {ok}/2 başarılı")
        return results


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
        reference_images = task_data.get("reference_images")
        
        try:
            img_path = self.resim_uret(
                prompt,
                index,
                bolum_adi,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                reference_images=reference_images
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
        reference_images: Optional[List[str]] = None,
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
                "image_size": image_size,
                "reference_images": reference_images
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
