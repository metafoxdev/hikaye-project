"""
Unit Tests for AI Hikaye Resimleyici
"""

import os
import sys
import json
import unittest
from unittest.mock import Mock, patch, MagicMock

# Proje dizinini path'e ekle
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import (
    sanitize_filename,
    sanitize_input,
    validate_json_file,
    extract_json_from_response,
    get_prompt_from_scene,
    format_timestamp,
    RateLimiter
)


class TestSanitizeFunctions(unittest.TestCase):
    """Sanitization fonksiyonları için testler"""
    
    def test_sanitize_filename_basic(self):
        """Temel dosya adı temizleme"""
        self.assertEqual(sanitize_filename("test file"), "test_file")
        self.assertEqual(sanitize_filename("test@file!"), "testfile")
    
    def test_sanitize_filename_max_length(self):
        """Maksimum uzunluk kontrolü"""
        long_name = "a" * 100
        result = sanitize_filename(long_name, max_length=20)
        self.assertEqual(len(result), 20)
    
    def test_sanitize_filename_special_chars(self):
        """Özel karakterlerin temizlenmesi"""
        self.assertEqual(sanitize_filename("test<>file"), "testfile")
        self.assertEqual(sanitize_filename("test/\\file"), "testfile")
    
    def test_sanitize_input_basic(self):
        """Temel input temizleme"""
        self.assertEqual(sanitize_input("  hello world  "), "hello world")
    
    def test_sanitize_input_html(self):
        """HTML etiketlerinin temizlenmesi"""
        self.assertEqual(sanitize_input("<script>alert('xss')</script>test"), "alert('xss')test")
    
    def test_sanitize_input_max_length(self):
        """Input maksimum uzunluk"""
        long_text = "a" * 10000
        result = sanitize_input(long_text, max_length=5000)
        self.assertEqual(len(result), 5000)
    
    def test_sanitize_input_empty(self):
        """Boş input"""
        self.assertEqual(sanitize_input(""), "")
        self.assertEqual(sanitize_input(None), "")


class TestJsonFunctions(unittest.TestCase):
    """JSON işleme fonksiyonları için testler"""
    
    def test_validate_json_file_not_exists(self):
        """Var olmayan dosya"""
        result = validate_json_file("/nonexistent/path.json", {"default": True})
        self.assertEqual(result, {"default": True})
    
    def test_extract_json_array(self):
        """JSON array çıkarma"""
        text = '```json\n["item1", "item2"]\n```'
        result = extract_json_from_response(text)
        self.assertEqual(result, ["item1", "item2"])
    
    def test_extract_json_object(self):
        """JSON object çıkarma"""
        text = '{"key": "value"}'
        result = extract_json_from_response(text)
        self.assertEqual(result, {"key": "value"})
    
    def test_extract_json_with_text(self):
        """Metin içindeki JSON"""
        text = 'Some text before [1, 2, 3] some text after'
        result = extract_json_from_response(text)
        self.assertEqual(result, [1, 2, 3])
    
    def test_extract_json_invalid(self):
        """Geçersiz JSON"""
        text = 'not json at all'
        result = extract_json_from_response(text)
        self.assertIsNone(result)


class TestPromptFunctions(unittest.TestCase):
    """Prompt işleme fonksiyonları için testler"""
    
    def test_get_prompt_string(self):
        """String prompt"""
        self.assertEqual(get_prompt_from_scene("test prompt"), "test prompt")
    
    def test_get_prompt_dict(self):
        """Dict prompt"""
        data = {"image_prompts": "visual description"}
        self.assertEqual(get_prompt_from_scene(data), "visual description")
    
    def test_get_prompt_dict_fallback(self):
        """Dict prompt fallback"""
        data = {"description": "scene description"}
        self.assertEqual(get_prompt_from_scene(data), "scene description")


class TestRateLimiter(unittest.TestCase):
    """Rate limiter için testler"""
    
    def test_rate_limiter_allows(self):
        """İzin verilen çağrılar"""
        limiter = RateLimiter(max_calls=5, period=60)
        for _ in range(5):
            self.assertTrue(limiter.is_allowed())
    
    def test_rate_limiter_blocks(self):
        """Engellenen çağrılar"""
        limiter = RateLimiter(max_calls=2, period=60)
        limiter.is_allowed()
        limiter.is_allowed()
        self.assertFalse(limiter.is_allowed())
    
    def test_rate_limiter_wait_time(self):
        """Bekleme süresi hesaplama"""
        limiter = RateLimiter(max_calls=1, period=10)
        limiter.is_allowed()
        wait = limiter.wait_time()
        self.assertGreater(wait, 0)
        self.assertLessEqual(wait, 10)


class TestFormatTimestamp(unittest.TestCase):
    """Timestamp formatı için testler"""
    
    def test_valid_timestamp(self):
        """Geçerli timestamp"""
        # 1 Ocak 2024 00:00:00 UTC
        result = format_timestamp(1704067200)
        self.assertIn("2024", result)
    
    def test_invalid_timestamp(self):
        """Geçersiz timestamp"""
        result = format_timestamp(-1)
        self.assertEqual(result, "Bilinmeyen tarih")


if __name__ == '__main__':
    unittest.main()
