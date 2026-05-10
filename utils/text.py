import re
import json
from typing import Optional, Any

def sanitize_filename(filename: str, max_length: int = 20) -> str:
    safe = "".join(c for c in filename if c.isalnum() or c in (' ', '_', '-'))
    safe = safe.replace(' ', '_')
    return safe[:max_length]

def sanitize_input(text: str, max_length: int = 5000) -> str:
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'javascript:', '', text, flags=re.IGNORECASE)
    text = re.sub(r'on\w+\s*=', '', text, flags=re.IGNORECASE)
    return text[:max_length].strip()

def extract_json_from_response(raw_text: str) -> Optional[Any]:
    text = raw_text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    
    if text.endswith("```"):
        text = text[:-3]
    
    text = text.strip()
    
    start_bracket = text.find('[')
    end_bracket = text.rfind(']')
    
    if start_bracket != -1 and end_bracket != -1 and end_bracket > start_bracket:
        json_str = text[start_bracket:end_bracket + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
            
    start_brace = text.find('{')
    end_brace = text.rfind('}')
    
    if start_brace != -1 and end_brace != -1 and end_brace > start_brace:
        json_str = text[start_brace:end_brace + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
            
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None

def get_prompt_from_scene(scene_data) -> str:
    if isinstance(scene_data, str):
        return scene_data
    
    if isinstance(scene_data, dict):
        keys = ['image_prompts', 'visual_description', 'description', 'prompt', 'text', 'content']
        for key in keys:
            if key in scene_data:
                value = scene_data[key]
                if isinstance(value, str):
                    return value
                elif isinstance(value, list) and value:
                    return str(value[0])
        return str(scene_data)
    
    return str(scene_data)
