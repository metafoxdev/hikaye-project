# AI Hikaye Resimleyici v2.0

![Version](https://img.shields.io/badge/version-2.0.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.8+-green.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)

AI ile hikayelerinizi sinematik görsel deneyimlere dönüştürün!

## ✨ Özellikler

### Temel Özellikler
- 🎨 **AI Görsel Üretimi** - Google Gemini ile yüksek kaliteli görseller
- 🎬 **Otomatik Video** - Sahneleri geçiş efektleriyle videoya dönüştürme
- 👥 **Karakter Yönetimi** - Karakter profilleri ve AI avatar oluşturma
- 📖 **Geçmiş Yönetimi** - Tüm projelerinizi kaydedin ve düzenleyin

### Gelişmiş Özellikler
- 🔄 **Sahne Yönetimi** - Sürükle-bırak sıralama, silme, ekleme
- ➕ **Hikaye Devamı** - Mevcut hikayeye yeni sahneler ekleyin
- 📋 **Şablonlar** - Hazır hikaye başlangıçları
- 📤 **Export** - JSON ve ZIP formatında dışa aktarma
- 🌍 **Çoklu Dil** - Türkçe ve İngilizce desteği
- 🌙 **Temalar** - Dark ve Light mod

### Teknik Özellikler
- ⚡ **SSE Streaming** - Gerçek zamanlı ilerleme
- 🔒 **Güvenlik** - CSRF koruması, input sanitization
- 📊 **İstatistikler** - Kullanım takibi
- 🔄 **Rate Limiting** - API koruma
- 📝 **Logging** - Detaylı log kayıtları

## 🚀 Kurulum

### Gereksinimler
- Python 3.8+
- Google Gemini API anahtarı

### Adımlar

```bash
# Klonlama
git clone https://github.com/username/hikaye_olustur.git
cd hikaye_olustur

# Sanal ortam (önerilen)
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac

# Bağımlılıklar
pip install -r requirements.txt

# Ortam değişkenleri
copy .env.example .env  # Windows
cp .env.example .env    # Linux/Mac

# .env dosyasını düzenleyin ve API anahtarınızı ekleyin
```

### Çalıştırma

```bash
# Development
python app.py

# Production (Linux)
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

Tarayıcıda açın: `http://localhost:5000`

## 📁 Proje Yapısı

```
hikaye_olustur/
├── app.py              # Flask uygulaması
├── logic.py            # İş mantığı
├── config.py           # Konfigürasyon
├── utils.py            # Yardımcı fonksiyonlar
├── requirements.txt    # Bağımlılıklar
├── .env.example        # Örnek ortam değişkenleri
├── data/
│   ├── characters.json # Karakter veritabanı
│   ├── history.json    # Geçmiş kayıtları
│   ├── settings.json   # Uygulama ayarları
│   └── templates.json  # Hikaye şablonları
├── locales/
│   ├── tr.json         # Türkçe çeviriler
│   └── en.json         # İngilizce çeviriler
├── static/
│   ├── css/style.css   # Stiller
│   ├── audio/          # Müzik dosyaları
│   └── output/         # Üretilen içerikler
├── templates/
│   └── index.html      # Ana sayfa
├── logs/               # Log dosyaları
└── tests/              # Unit testler
```

## 🎮 Kullanım

1. **Hikaye Yazın**: Ana metin kutusuna hikayenizi yazın
2. **Karakter Ekleyin**: `@karakter` ile mevcut karakterleri mention edin
3. **Ayarları Seçin**: Sanat stili, atmosfer, sahne sayısı vb.
4. **Oluşturun**: "Görselleri Oluştur" butonuna tıklayın
5. **Düzenleyin**: Sahneleri sürükleyerek sıralayın, düzenleyin veya silin
6. **Video**: "Videoyu Oluştur" ile MP4 olarak kaydedin
7. **Export**: ZIP veya JSON olarak dışa aktarın

## 🔧 API Endpoints

| Endpoint | Method | Açıklama |
|----------|--------|----------|
| `/generate` | POST | Hikaye oluştur (SSE) |
| `/history` | GET | Geçmiş listesi |
| `/characters` | GET | Karakter listesi |
| `/scene/delete` | POST | Sahne sil |
| `/scene/add` | POST | Sahne ekle |
| `/story/continue` | POST | Hikayeye devam et |
| `/templates` | GET | Şablon listesi |
| `/export` | POST | Dışa aktar |
| `/stats` | GET | İstatistikler |

## ⌨️ Klavye Kısayolları

- `Ctrl+Enter` - Oluştur
- `Escape` - Modal/Lightbox kapat

## 🤝 Katkıda Bulunma

Detaylar için [CONTRIBUTING.md](CONTRIBUTING.md) dosyasına bakın.

## 📄 Lisans

MIT License - [LICENSE](LICENSE)

## 📝 Değişiklik Günlüğü

Detaylar için [CHANGELOG.md](CHANGELOG.md) dosyasına bakın.

---

Made with ❤️ and AI
