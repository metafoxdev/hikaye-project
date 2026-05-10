# Bu kodu çalıştırmak için aşağıdaki bağımlılığı yüklemeniz gerekir:
# pip install google-genai

import base64
import mimetypes
import os
import sys
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

def save_binary_file(file_name, data):
    try:
        with open(file_name, "wb") as f:
            f.write(data)
        print(f"Dosya başarıyla kaydedildi: {file_name}")
    except Exception as e:
        print(f"Dosya kaydedilirken hata oluştu: {e}")

def generate_image(prompt_text):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("HATA: GEMINI_API_KEY çevre değişkeni bulunamadı.")
        print("Lütfen API anahtarınızı ayarlayın. Windows için:")
        print("set GEMINI_API_KEY=api_anahtariniz")
        return

    client = genai.Client(api_key=api_key)

    # Kullanıcının belirttiği model
    model = "gemini-3-pro-image-preview"
    
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=prompt_text),
            ],
        ),
    ]
    
    tools = [
        types.Tool(googleSearch=types.GoogleSearch()),
    ]

    generate_content_config = types.GenerateContentConfig(
        response_modalities=[
            "IMAGE",
            "TEXT",
        ],
        image_config=types.ImageConfig(
            aspect_ratio="9:16",
            image_size="2K",
        ),
        tools=tools,
    )

    print(f"İstek gönderiliyor... Prompt: '{prompt_text}'")

    try:
        file_index = 0
        response_stream = client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=generate_content_config,
        )

        for chunk in response_stream:
            if (
                chunk.candidates is None
                or not chunk.candidates
                or chunk.candidates[0].content is None
                or chunk.candidates[0].content.parts is None
            ):
                continue
            
            for part in chunk.candidates[0].content.parts:
                # Eğer görsel verisi varsa
                if part.inline_data and part.inline_data.data:
                    file_index += 1
                    inline_data = part.inline_data
                    data_buffer = inline_data.data
                    
                    # Dosya uzantısını tahmin et
                    file_extension = mimetypes.guess_extension(inline_data.mime_type) or ".png"
                    
                    # Dosya adını oluştur
                    safe_prompt = "".join(c if c.isalnum() else "_" for c in prompt_text[:20])
                    file_name = f"generated_{safe_prompt}_{file_index}{file_extension}"
                    
                    save_binary_file(file_name, data_buffer)
                
                # Eğer metin çıktısı varsa (genellikle görselle birlikte gelmeyebilir ama kontrol etmekte fayda var)
                if part.text:
                    print(f"Gelen Metin: {part.text}")

    except Exception as e:
        print(f"Bir hata oluştu: {e}")

if __name__ == "__main__":
    # Konsol argümanı varsa onu prompt olarak kullan, yoksa kullanıcıdan iste
    if len(sys.argv) > 1:
        user_prompt = " ".join(sys.argv[1:])
    else:
        user_prompt = input("Lütfen oluşturmak istediğiniz görseli tarif edin: ")
    
    generate_image(user_prompt)
