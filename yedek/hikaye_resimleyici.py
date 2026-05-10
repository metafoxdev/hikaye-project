import os
import sys
import json
import mimetypes
from dotenv import load_dotenv
from google import genai
from google.genai import types
from moviepy import ImageClip, concatenate_videoclips

# .env dosyasını yükle
load_dotenv()

# API Anahtarı kontrolü
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    print("HATA: GEMINI_API_KEY çevre değişkeni bulunamadı.")
    sys.exit(1)

client = genai.Client(api_key=api_key)

# --------------------------------------------------------------------------------
# 1. HİKAYE VE KARAKTER TANIMLARI (SİSTEM PROMPTU)
# --------------------------------------------------------------------------------

HIKAYE_KONTEXTSI = """
SEN BİR ÇİZGİ ROMAN SENARİSTİ VE SANAT YÖNETMENİSİN.
Görevin, verilen hikaye bölümüne dayanarak, Görsel Üretim Modeli (AI Image Generator) için 6-7 adet detaylı "Image Prompt" (Görsel İstemi) yazmaktır.

### GÖRSEL STİL KURALLARI (KESİN VE TARTIŞILAMAZ):
1. **ÇERÇEVE ASLA YOK (NO FRAMES):** Resimler "Borderless", "Full bleed", "Edge-to-edge" olmalıdır. Asla panel çerçevesi, beyaz kenarlık veya kağıt efekti ekleme.
2. **HER ZAMAN RENKLİ (ALWAYS COLOR):** Resimler "Vibrant colors", "High saturation", "Cinematic lighting" içermelidir. Asla siyah-beyaz, monokrom veya eskiz tarzı olmamalıdır.
3. **TUTARLILIK:** Karakter yüzleri ve kıyafetleri her karede birebir aynı kalmalıdır.

### KARAKTER PROFİLLERİ (BU TANIMLAR HER PROMPTTA AYNEN KULLANILACAK):
1. **DOKTOR MAYA:**
   - Prompt Tanımı: "Maya, a stunningly beautiful female doctor, long wavy brown hair, piercing blue eyes, wearing a fitted white medical coat over a blue blouse."
2. **YAMAN (MAFYA BABASI):**
   - Prompt Tanımı: "Yaman, a massive intimidating mafia boss, tall muscular build, short dark hair, thick full beard, wearing a sharp black unbuttoned dress shirt."

### GÖREV:
Kullanıcının istediği "Bölüm" için 6-7 sahneli bir akış belirle.
Her sahne için İngilizce çok detaylı bir görsel promptu yaz.

### PROMPT YAPISI (HER SAHNE İÇİN BU YAPIYI KULLAN):
"[Karakter Tanımı 1] and [Karakter Tanımı 2] in [Sahne Ortamı]. [Aksiyon Detayı]. [Yüz İfadeleri]. [Işıklandırma]. Speech bubble saying in Turkish: '[TÜRKÇE KONUŞMA METNİ]'. Style features: Comic book style, masterpiece, vibrant colors, highly detailed, dramatic cinematic lighting, borderless, full frame, no white borders."

ÖNEMLİ KURALLAR:
1. Karakterlerin fiziksel özelliklerini (yukarıdaki tanımları) HER promptta tekrar yaz.
2. Konuşma balonu varsa metin KESİNLİKLE TÜRKÇE olsun.
3. Asla "black and white", "sketch", "pencil drawing" kullanma.
4. Asla "frame", "border", "panel borders" kullanma.

### ÇIKTI FORMATI:
Sadece saf bir JSON listesi döndür. Markdown bloğu kullanma.
Örnek:
[
  "Maya, a stunningly beautiful female doctor... in a dark room... Speech bubble saying in Turkish: 'Kimsin sen?', vibrant colors, no borders...",
  "..."
]
"""

# --------------------------------------------------------------------------------
# 2. METİN ÜRETİMİ (SENARYO -> PROMPTLAR)
# --------------------------------------------------------------------------------

def senaryo_olustur(bolum_istegi):
    print(f"\n--- '{bolum_istegi}' için sahneler kurgulanıyor... ---\n")
    
    full_prompt = f"""
    {HIKAYE_KONTEXTSI}
    
    KULLANICI İSTEĞİ: {bolum_istegi}
    
    Lütfen bu bölüm için 6-7 adet sıralı görsel promptunu JSON formatında oluştur.
    """
    
    try:
        response = client.models.generate_content(
            model="gemini-3-pro-preview",
            contents=full_prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        
        # JSON temizliği (Markdown bloklarını kaldır)
        raw_text = response.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        elif raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]

        prompts = json.loads(raw_text.strip())
        return prompts
    except Exception as e:
        print(f"Senaryo oluşturulurken hata: {e}")
        return []

# --------------------------------------------------------------------------------
# 3. KARAKTER GELİŞTİRME (AI ENHANCEMENT)
# --------------------------------------------------------------------------------

def enhance_character_details(current_data):
    """
    Karakter özelliklerini AI ile zenginleştirir.
    """
    try:
        print(f"\n--- Karakter Detaylandırılıyor: {current_data.get('name', 'Adsız')} ---\n")
        
        prompt = f"""
        SEN BİR KARAKTER TASARIM UZMANISIN.
        Aşağıdaki basit karakter taslağını alıp, görsel üretim (AI Image Gen) için çok daha zengin, detaylı ve tutarlı hale getirmelisin.

        MEVCUT VERİ:
        {json.dumps(current_data, indent=2)}

        GÖREVİN:
        1. Karakterin ismini koru veya daha epik/uygun hale getir (eğer çok basitse).
        2. 'Description' alanını İngilizce olarak, görsel odaklı, detaylı bir prompt haline getir (Yüz hatları, kıyafet dokusu, duruş, atmosfer).
        3. Diğer özellikleri (Hair, Eyes, Outfit, Feature) zenginleştir ama ana fikri koru.
        4. Rolüne uygun bir kişilik kat.

        KISITLAMALAR (SELECT ALANLARI İÇİN SADECE BU DEĞERLERİ KULLAN, EN UYGUNUNU SEÇ):
        - Gender: Male, Female, Non-binary, Child Boy, Child Girl, Robot, Creature
        - Eyes (Göz Rengi): Blue, Brown, Green, Hazel, Black, Red, Grey, Glowing
        - Outfit (Kıyafet): Casual, Suit, Detective, Medieval, Sci-Fi, Military, Doctor, Gothic, Summer
        - Role (Rol): Protagonist, Antagonist, Sidekick, Mentor, Extra

        Hair ve Feature alanları serbest metindir (İngilizce).

        ÇIKTI FORMATI (SADECE JSON):
        {{
          "name": "...",
          "description": " Detailed reliable visual description...",
          "attributes": {{
            "gender": "...",
            "hair": "...",
            "eyes": "...",
            "outfit": "...",
            "feature": "...",
            "role": "..."
          }}
        }}
        """

        response = client.models.generate_content(
            model="gemini-2.0-flash-exp", # Hızlı model
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        
        # JSON temizliği
        raw_text = response.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        elif raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]

        enhanced_data = json.loads(raw_text.strip())
        return enhanced_data
    except Exception as e:
        print(f"Karakter geliştirilirken hata: {e}")
        # Hata durumunda boş değil None dönelim ki UI anlasın
        return None

# --------------------------------------------------------------------------------
# 3. GÖRSEL ÜRETİMİ (PROMPT -> RESİM)
# --------------------------------------------------------------------------------

def resim_uret(prompt, index, bolum_adi):
    print(f"\n> Sahne {index+1} oluşturuluyor...")
    # print(f"  Prompt: {prompt[:100]}...") # Promptun başını göster

    model = "gemini-3-pro-image-preview" # Veya erişiminiz olan en iyi görsel modeli
    
    contents = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt)],
        ),
    ]

    generate_content_config = types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=types.ImageConfig(
            aspect_ratio="9:16",
            image_size="2K", 
        ),
    )

    try:
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=generate_content_config,
        )
        
        # Yanıtı işle
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.data:
                    extension = mimetypes.guess_extension(part.inline_data.mime_type) or ".png"
                    
                    # Dosya isimlendirme: Bolum1_Sahne1.png
                    safe_bolum = "".join(c for c in bolum_adi if c.isalnum() or c in (' ', '_', '-')).replace(' ', '_')
                    file_name = f"{safe_bolum}_Sahne_{index+1}{extension}"
                    
                    with open(file_name, "wb") as f:
                        f.write(part.inline_data.data)
                    print(f"  [OK] Kaydedildi: {file_name}")
                    return file_name
        
        print("  [HATA] Resim verisi alınamadı.")
        return None

    except Exception as e:
        print(f"  [HATA] Resim oluşturma hatası: {e}")
        return None

# --------------------------------------------------------------------------------
# 4. VİDEO ÜRETİMİ (YENİ ÖZELLİK)
# --------------------------------------------------------------------------------

def video_olustur(image_paths, output_name="hikaye_videosu.mp4"):
    print(f"\n--- Video oluşturuluyor: {output_name} ---")
    
    if not image_paths:
        print("Video için resim bulunamadı.")
        return

    # Kullanıcı isteği: Toplam 49-55 saniyeyi geçmesin.
    # En az 6-7 resim olacak.
    # Hedef süre yaklaşık 50-54 saniye olsun.
    target_total_duration = 52.0
    duration_per_image = target_total_duration / len(image_paths)
    
    # Kullanıcı "her görsel 8-10 saniye" dedi ama "toplam 55'i geçmesin" dedi.
    # Eğer 7 resim varsa: 52 / 7 = 7.4 sn. (Kriteri biraz zorluyor ama toplam süreye öncelik veriyoruz)
    # Eğer 6 resim varsa: 52 / 6 = 8.6 sn. (Tam kriterde)
    
    print(f"  Toplam Resim: {len(image_paths)}")
    print(f"  Resim Başına Süre: {duration_per_image:.2f} saniye")
    print(f"  Hedef Toplam Süre: {target_total_duration:.2f} saniye")

    try:
        clips = []
        for img_path in image_paths:
            # Her resmi belirtilen süre kadar gösteren bir klip oluştur
            clip = ImageClip(img_path).with_duration(duration_per_image)
            clips.append(clip)
        
        # Klipleri birleştir
        video = concatenate_videoclips(clips, method="compose")
        
        # Dosyayı kaydet
        video.write_videofile(output_name, fps=24, codec="libx264")
        print(f"\n[BAŞARILI] Video kaydedildi: {output_name}")
        
    except Exception as e:
        print(f"\n[HATA] Video oluşturulurken hata: {e}")

# --------------------------------------------------------------------------------
# 5. ANA AKIŞ
# --------------------------------------------------------------------------------

def main():
    if len(sys.argv) > 1:
        user_input = " ".join(sys.argv[1:])
    else:
        print("Hangi bölümü oluşturmak istiyorsunuz?")
        print("Örnek: '1. Bölüm: Hastanede Karşılaşma'")
        user_input = input("Giriş yapın: ")

    # 1. Adım: Sahneleri (Promptları) Çıkar
    scene_prompts = senaryo_olustur(user_input)
    
    if not scene_prompts:
        print("Sahne oluşturulamadı, işlem iptal.")
        return

    print(f"\nToplam {len(scene_prompts)} sahne oluşturulacak.")
    
    # 2. Adım: Her sahne için resim üret
    # 2. Adım: Her sahne için resim üret
    generated_images = []
    for i, prompt in enumerate(scene_prompts):
        file_path = resim_uret(prompt, i, user_input)
        if file_path:
            generated_images.append(file_path)

    # 3. Adım: Video oluştur
    if generated_images:
        # Bölüm adına uygun video ismi
        safe_bolum = "".join(c for c in user_input if c.isalnum() or c in (' ', '_', '-')).replace(' ', '_')
        video_name = f"{safe_bolum}_Hikaye.mp4"
        video_olustur(generated_images, video_name)

    print("\n--- Tüm işlem tamamlandı! ---")

if __name__ == "__main__":
    main()
