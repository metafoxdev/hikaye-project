import os
import re
from collections import defaultdict

LOGIC_FILE = "logic.py"
CORE_DIR = "core"

with open(LOGIC_FILE, "r", encoding="utf-8") as f:
    content = f.read()

# Splitting logic.py using regex to find methods
class_start_match = re.search(r"class StoryGenerator\b.*?:", content)
class_start_idx = class_start_match.start()
top_content = content[:class_start_idx]

class_content = content[class_start_idx:]

# Find __init__
init_match = re.search(r"    def __init__\(self\):.*?(?=\n    def |\Z)", class_content, re.DOTALL)
init_section = init_match.group(0)

# Find all other methods
methods = re.findall(r"(    def [a-zA-Z0-9_]+\(self.*?(?=\n    def |\Z))", class_content[init_match.end():], re.DOTALL)

# Let's map methods to mixins
method_mapping = {
    # Settings
    "settings": ["_load_settings", "get_settings", "update_settings"],
    # Character
    "character": ["_load_characters", "_save_characters", "get_characters", "add_character", "delete_character", "update_character", "enhance_character", "generate_character_avatar"],
    # History
    "history": ["get_history", "_save_to_history", "_save_updated_history", "delete_history_item", "clear_history"],
    # API
    "api": ["_check_rate_limit", "_make_text_request", "_make_image_request"],
    # Scenario
    "scenario": ["_get_system_prompt", "_build_character_prompt", "senaryo_olustur", "enhance_story_prompt"],
    # Image
    "image": ["resim_uret", "_generate_single_image_task", "generate_images_parallel", "regenerate_scene_with_prompt", "regenerate_scene_match_style", "generate_scene_variations", "select_variation"],
    # Audio
    "audio": ["_parse_audio_mime_type", "_convert_to_wav", "generate_voice_for_dialogue", "generate_multi_speaker_voice", "extract_dialogues_from_scene", "generate_scene_audio", "regenerate_scene_audio"],
    # Video
    "video": ["_apply_ken_burns_effect", "_create_subtitle_clip", "_hex_to_rgb", "_create_watermark_clip", "_extract_subtitles_from_prompts", "video_olustur", "create_video_from_history", "merge_stories_to_video"],
    # Story flow
    "story_flow": ["generate_full_story_stream"],
    # Scene
    "scene": ["regenerate_scene", "delete_scene", "add_scene", "reorder_scenes"],
    # Story branch
    "story_branch": ["create_story_branch", "get_story_branches", "continue_story"],
    # Misc (Export, Templates, Suggestions)
    "misc": ["export_for_social_media", "export_storyboard_pdf", "get_stats", "get_templates", "get_template", "get_story_suggestions", "_get_fallback_suggestions", "export_project", "import_project"]
}

mixin_files = {}
reverse_mapping = {}
for mixin_key, method_list in method_mapping.items():
    mixin_name = mixin_key.capitalize() + "Mixin"
    if mixin_name == "ApiMixin":
        mixin_name = "APIMixin"
    mixin_files[mixin_name] = {"filename": f"{mixin_key}_mixin.py", "methods": []}
    for m in method_list:
        reverse_mapping[m] = mixin_name

# Assign methods to mixins
for m_str in methods:
    m_name_match = re.search(r"def ([a-zA-Z0-9_]+)\(", m_str)
    if m_name_match:
        m_name = m_name_match.group(1)
        mixin_name = reverse_mapping.get(m_name, "MiscMixin")
        if mixin_name not in mixin_files:
            mixin_files[mixin_name] = {"filename": "misc_mixin.py", "methods": []}
        mixin_files[mixin_name]["methods"].append(m_str)

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

for mixin_name, data in mixin_files.items():
    content = imports + f"\n\nclass {mixin_name}:\n"
    if not data["methods"]:
        content += "    pass\n"
    else:
        for m_str in data["methods"]:
            # Need to capture any preceding comments that were left behind?
            # methods regex captures everything up to the next def, so it includes preceding comments of the NEXT method...
            pass
            
    # To fix the trailing comments issue, we split content by `    def ` and keep the stuff before it attached to it
    pass

# A better split approach
chunks = re.split(r"(?=\n    def [a-zA-Z0-9_]+\(self)", "\n" + class_content[init_match.end():])
# chunks[0] is empty or whitespace
for chunk in chunks:
    if not chunk.strip(): continue
    m_name_match = re.search(r"def ([a-zA-Z0-9_]+)\(", chunk)
    if m_name_match:
        m_name = m_name_match.group(1)
        mixin_name = reverse_mapping.get(m_name, "MiscMixin")
        if mixin_name not in mixin_files:
            mixin_files[mixin_name] = {"filename": "misc_mixin.py", "methods": []}
        mixin_files[mixin_name]["methods"].append(chunk)

for mixin_name, data in mixin_files.items():
    content = imports + f"\n\nclass {mixin_name}:\n"
    if not data["methods"]:
        content += "    pass\n"
    else:
        for chunk in data["methods"]:
            content += chunk
    with open(os.path.join(CORE_DIR, data["filename"]), "w", encoding="utf-8") as f:
        f.write(content)

# Update logic_new.py
new_logic_content = top_content
mixin_classes = []
for mixin_name, data in mixin_files.items():
    if data["methods"]:
        new_logic_content += f"from core.{data['filename'][:-3]} import {mixin_name}\n"
        mixin_classes.append(mixin_name)

new_logic_content += f"\nclass StoryGenerator({', '.join(mixin_classes)}):\n"
new_logic_content += init_section + "\n"

with open("logic_new.py", "w", encoding="utf-8") as f:
    f.write(new_logic_content)

print("Split logic.py into mixins successfully with method-based approach")
