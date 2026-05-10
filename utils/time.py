from datetime import datetime

def format_timestamp(timestamp: int) -> str:
    try:
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return "Bilinmeyen tarih"

def calculate_video_duration(scene_count: int, frame_duration: float) -> float:
    return scene_count * frame_duration
