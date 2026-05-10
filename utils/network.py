import time
import functools
import logging
from typing import Callable, Dict, Any

def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exceptions: tuple = (Exception,)
) -> Callable:
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger = logging.getLogger('hikaye_resimleyici')
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt + 1 < max_retries:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        logger.warning(
                            f"{func.__name__} başarısız (deneme {attempt + 1}/{max_retries}). "
                            f"{delay:.1f}s sonra tekrar denenecek. Hata: {e}"
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            f"{func.__name__} tüm denemeler başarısız. Son hata: {e}"
                        )
            raise last_exception
        return wrapper
    return decorator

class RateLimiter:
    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self.calls = []
    
    def is_allowed(self) -> bool:
        now = time.time()
        self.calls = [t for t in self.calls if now - t < self.period]
        if len(self.calls) < self.max_calls:
            self.calls.append(now)
            return True
        return False
    
    def wait_time(self) -> float:
        if len(self.calls) < self.max_calls:
            return 0
        now = time.time()
        oldest_call = min(self.calls)
        wait = self.period - (now - oldest_call)
        return max(0, wait)

class APIStats:
    def __init__(self):
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.total_images_generated = 0
        self.total_videos_created = 0
        self.start_time = time.time()
    
    def record_request(self, success: bool = True):
        self.total_requests += 1
        if success:
            self.successful_requests += 1
        else:
            self.failed_requests += 1
    
    def record_image(self):
        self.total_images_generated += 1
    
    def record_video(self):
        self.total_videos_created += 1
    
    def get_stats(self) -> Dict[str, Any]:
        uptime = time.time() - self.start_time
        return {
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "success_rate": (self.successful_requests / self.total_requests * 100) if self.total_requests > 0 else 0,
            "total_images_generated": self.total_images_generated,
            "total_videos_created": self.total_videos_created,
            "uptime_seconds": uptime,
            "uptime_formatted": f"{int(uptime // 3600)}h {int((uptime % 3600) // 60)}m"
        }

api_stats = APIStats()
