import os
import json
import logging
import time
from typing import Any, Optional, List

def validate_json_file(file_path: str, default_content: Any = None) -> Any:
    try:
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger = logging.getLogger('hikaye_resimleyici')
        logger.error(f"JSON okuma hatası ({file_path}): {e}")
    
    return default_content if default_content is not None else {}

def save_json_file(file_path: str, content: Any, create_backup: bool = True) -> bool:
    logger = logging.getLogger('hikaye_resimleyici')
    try:
        if create_backup and os.path.exists(file_path):
            backup_path = f"{file_path}.backup"
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    backup_content = f.read()
                with open(backup_path, 'w', encoding='utf-8') as f:
                    f.write(backup_content)
            except Exception as e:
                logger.warning(f"Yedekleme oluşturulamadı: {e}")
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(content, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"JSON kaydetme hatası ({file_path}): {e}")
        return False

def get_file_size_mb(file_path: str) -> float:
    try:
        size_bytes = os.path.getsize(file_path)
        return size_bytes / (1024 * 1024)
    except Exception:
        return 0.0

def clean_old_files(
    directory: str,
    max_age_days: int = 7,
    file_extensions: Optional[List[str]] = None
) -> int:
    logger = logging.getLogger('hikaye_resimleyici')
    deleted_count = 0
    if not os.path.exists(directory):
        return 0
    current_time = time.time()
    max_age_seconds = max_age_days * 24 * 60 * 60
    
    try:
        for filename in os.listdir(directory):
            file_path = os.path.join(directory, filename)
            if os.path.isdir(file_path):
                continue
            if file_extensions:
                ext = os.path.splitext(filename)[1].lower().lstrip('.')
                if ext not in file_extensions:
                    continue
            file_age = current_time - os.path.getmtime(file_path)
            if file_age > max_age_seconds:
                try:
                    os.remove(file_path)
                    deleted_count += 1
                    logger.info(f"Eski dosya silindi: {filename}")
                except Exception as e:
                    logger.warning(f"Dosya silinemedi ({filename}): {e}")
    except Exception as e:
        logger.error(f"Temizlik hatası: {e}")
    
    return deleted_count
