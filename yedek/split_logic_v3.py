import os
import ast
from collections import defaultdict

LOGIC_FILE = "logic.py"
CORE_DIR = "core"

with open(LOGIC_FILE, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Parse AST
with open(LOGIC_FILE, "r", encoding="utf-8") as f:
    tree = ast.parse(f.read())

story_gen_class = None
for node in tree.body:
    if isinstance(node, ast.ClassDef) and node.name == "StoryGenerator":
        story_gen_class = node
        break

if not story_gen_class:
    print("Class not found")
    exit(1)

# Map methods to mixins
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
    # Misc
    "misc": ["export_for_social_media", "export_storyboard_pdf", "get_stats", "get_templates", "get_template", "get_story_suggestions", "_get_fallback_suggestions", "export_project", "import_project"]
}

reverse_mapping = {}
for mixin_key, method_list in method_mapping.items():
    mixin_name = mixin_key.capitalize() + "Mixin"
    if mixin_name == "ApiMixin":
        mixin_name = "APIMixin"
    for m in method_list:
        reverse_mapping[m] = (mixin_name, f"{mixin_key}_mixin.py")

methods_info = []
for node in story_gen_class.body:
    if isinstance(node, ast.FunctionDef):
        if node.name == "__init__":
            continue
        methods_info.append({
            "name": node.name,
            "start": node.lineno - 1, # 0-indexed
            "end": node.end_lineno # end_lineno is 1-indexed, so it's the index of the next line
        })

# Include decorators and preceding comments
# We will just divide the lines between methods
for i in range(len(methods_info)):
    if i == 0:
        # Start from the line after __init__ ends
        # Actually, let's just find where __init__ ends
        init_node = next(n for n in story_gen_class.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
        methods_info[i]["real_start"] = init_node.end_lineno
    else:
        methods_info[i]["real_start"] = methods_info[i-1]["end"]

# Last method goes to the end of the class
methods_info[-1]["end"] = story_gen_class.end_lineno

mixin_content = defaultdict(list)
for m in methods_info:
    mixin_name, filename = reverse_mapping.get(m["name"], ("MiscMixin", "misc_mixin.py"))
    # Extract the lines
    m_lines = lines[m["real_start"]:m["end"]]
    mixin_content[(mixin_name, filename)].extend(m_lines)

imports = """import os
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
"""

for (mixin_name, filename), m_lines in mixin_content.items():
    content = imports + f"\n\nclass {mixin_name}:\n"
    if not m_lines:
        content += "    pass\n"
    else:
        content += "".join(m_lines)
    with open(os.path.join(CORE_DIR, filename), "w", encoding="utf-8") as f:
        f.write(content)

# Update logic.py
# Get lines up to __init__
init_node = next(n for n in story_gen_class.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")

top_content = "".join(lines[:story_gen_class.lineno - 1])
init_content = "".join(lines[init_node.lineno - 1:init_node.end_lineno])

new_logic_content = top_content
mixin_classes = []
for (mixin_name, filename) in mixin_content.keys():
    new_logic_content += f"from core.{filename[:-3]} import {mixin_name}\n"
    mixin_classes.append(mixin_name)

new_logic_content += f"\nclass StoryGenerator({', '.join(mixin_classes)}):\n"
# Also need to preserve the class docstring and any class variables if there are any
class_docstring = ast.get_docstring(story_gen_class)
if class_docstring:
    new_logic_content += f'    """{class_docstring}"""\n'
new_logic_content += init_content + "\n"

with open("logic_new.py", "w", encoding="utf-8") as f:
    f.write(new_logic_content)

print("Split logic.py into mixins successfully with AST-based approach")
