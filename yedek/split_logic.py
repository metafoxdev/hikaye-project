import os
import re

LOGIC_FILE = "logic.py"
CORE_DIR = "core"

if not os.path.exists(CORE_DIR):
    os.makedirs(CORE_DIR)

with open(LOGIC_FILE, "r", encoding="utf-8") as f:
    content = f.read()

# Split the content into top imports/definitions and the StoryGenerator class
class_start_match = re.search(r"class StoryGenerator\b.*?:", content)
if not class_start_match:
    print("Could not find StoryGenerator class")
    exit(1)

class_start_idx = class_start_match.start()
top_content = content[:class_start_idx]

class_content = content[class_start_idx:]
# Find the __init__ method
init_start_match = re.search(r"    def __init__\(self\):", class_content)
init_start_idx = init_start_match.start()

# Split the class content by the header blocks
headers = list(re.finditer(r"    # ={5,}\r?\n\s*# (.*?)\r?\n\s*# ={5,}\r?\n", class_content))

sections = []
# __init__ section
first_header_idx = headers[0].start() if headers else len(class_content)
init_section = class_content[init_start_idx:first_header_idx]

for i, header in enumerate(headers):
    title = header.group(1).strip()
    start_idx = header.end()
    end_idx = headers[i+1].start() if i + 1 < len(headers) else len(class_content)
    sections.append({
        "title": title,
        "content": class_content[start_idx:end_idx]
    })

# Define the mapping of section titles to mixin class names and files
mapping = {
    "AYAR YÖNETİMİ": ("SettingsMixin", "settings_mixin.py"),
    "KARAKTER YÖNETİMİ": ("CharacterMixin", "character_mixin.py"),
    "GEÇMİŞ YÖNETİMİ": ("HistoryMixin", "history_mixin.py"),
    "API İSTEKLERİ": ("APIMixin", "api_mixin.py"),
    "SENARYO OLUŞTURMA": ("ScenarioMixin", "scenario_mixin.py"),
    "GÖRSEL OLUŞTURMA": ("ImageMixin", "image_mixin.py"),
    "SESLENDİRME (TTS)": ("AudioMixin", "audio_mixin.py"),
    "KEN BURNS EFEKTİ": ("VideoMixin", "video_mixin.py"),
    "VIDEO OLUŞTURMA": ("VideoMixin", "video_mixin.py"),
    "TAM HİKAYE AKIŞI": ("StoryFlowMixin", "story_flow_mixin.py"),
    "SAHNE YENİLEME": ("SceneMixin", "scene_mixin.py"),
    "HİKAYE DALLANMASI": ("StoryBranchMixin", "story_branch_mixin.py"),
    "VİDEO (GEÇMİŞTEN)": ("VideoMixin", "video_mixin.py"),
    "İSTATİSTİKLER": ("MiscMixin", "misc_mixin.py"),
    "SAHNE YÖNETİMİ (YENİ)": ("SceneMixin", "scene_mixin.py"),
    "HİKAYE DEVAMI": ("StoryBranchMixin", "story_branch_mixin.py"),
    "KARAKTER ANALİZİ": ("CharacterMixin", "character_mixin.py"),
    "DEVAM SENARYOSU OLUŞTUR": ("StoryBranchMixin", "story_branch_mixin.py"),
    "KARAKTER TUTARLILIĞI İÇİN PROMPT ZENGİNLEŞTİRME": ("StoryBranchMixin", "story_branch_mixin.py"),
    "GÖRSELLER OLUŞTUR": ("ImageMixin", "image_mixin.py"),
    "SESLENDİRME OLUŞTUR": ("AudioMixin", "audio_mixin.py"),
    "KARAKTER AVATAR": ("CharacterMixin", "character_mixin.py"),
    "ŞABLON YÖNETİMİ": ("MiscMixin", "misc_mixin.py"),
    "HİKAYE ÖNERİLERİ": ("MiscMixin", "misc_mixin.py"),
    "EXPORT FONKSİYONLARI": ("MiscMixin", "misc_mixin.py"),
    "AI HİKAYE ÖNERİLERİ": ("MiscMixin", "misc_mixin.py"),
}

mixins = {}
for sec in sections:
    title = sec["title"].replace("İ", "I").replace("Ö", "O").replace("Ü", "U").replace("Ş", "S").replace("Ç", "C").replace("Ğ", "G")
    # Fuzzy match since we saw encoding issues
    matched = False
    for k, v in mapping.items():
        k_norm = k.replace("İ", "I").replace("Ö", "O").replace("Ü", "U").replace("Ş", "S").replace("Ç", "C").replace("Ğ", "G")
        if k_norm in title or title in k_norm:
            mixin_name, filename = v
            if mixin_name not in mixins:
                mixins[mixin_name] = {"filename": filename, "content": ""}
            mixins[mixin_name]["content"] += sec["content"]
            matched = True
            break
    if not matched:
        # Default to MiscMixin
        mixin_name, filename = "MiscMixin", "misc_mixin.py"
        if mixin_name not in mixins:
            mixins[mixin_name] = {"filename": filename, "content": ""}
        mixins[mixin_name]["content"] += sec["content"]

imports = """import os
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

try:
    from moviepy import ImageClip, concatenate_videoclips, CompositeVideoClip
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
"""

# Write mixin files
mixin_classes = []
for mixin_name, data in mixins.items():
    filename = data["filename"]
    content = data["content"]
    mixin_classes.append(mixin_name)
    
    file_content = imports + f"\n\nclass {mixin_name}:\n" + content
    with open(os.path.join(CORE_DIR, filename), "w", encoding="utf-8") as f:
        f.write(file_content)

# Generate new logic.py
new_logic_content = top_content

# Add imports for mixins
for mixin_name, data in mixins.items():
    new_logic_content += f"from core.{data['filename'][:-3]} import {mixin_name}\n"

new_logic_content += f"\nclass StoryGenerator({', '.join(mixin_classes)}):\n"
new_logic_content += init_section

with open("logic_new.py", "w", encoding="utf-8") as f:
    f.write(new_logic_content)

print("Split logic.py into mixins successfully")
