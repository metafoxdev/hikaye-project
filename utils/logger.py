import os
import logging
from logging.handlers import RotatingFileHandler

def setup_logging(
    log_file: str = 'logs/app.log',
    log_level: str = 'INFO',
    log_format: str = '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5
) -> logging.Logger:
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    
    logger = logging.getLogger('hikaye_resimleyici')
    logger.setLevel(getattr(logging, log_level.upper()))
    
    logger.handlers.clear()
    
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    file_handler.setFormatter(logging.Formatter(log_format))
    logger.addHandler(file_handler)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(log_format))
    logger.addHandler(console_handler)
    
    return logger
