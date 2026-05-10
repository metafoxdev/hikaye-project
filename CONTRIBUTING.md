# Contributing to AI Story Illustrator

Projeye katkıda bulunmak istediğiniz için teşekkürler! 🎉

## Nasıl Katkıda Bulunabilirim?

### Bug Raporlama

1. GitHub Issues'da yeni bir issue açın
2. Hatanın detaylı açıklamasını yazın
3. Hata adımlarını listeleyin
4. Beklenen ve gerçekleşen davranışı belirtin
5. Ekran görüntüsü veya log ekleyin

### Özellik Önerisi

1. Önce mevcut issue'ları kontrol edin
2. Yeni bir Feature Request issue açın
3. Özelliği ve kullanım senaryosunu açıklayın

### Pull Request

1. Projeyi fork edin
2. Yeni bir branch oluşturun: `git checkout -b feature/amazing-feature`
3. Değişikliklerinizi commit edin: `git commit -m 'Add amazing feature'`
4. Branch'ı push edin: `git push origin feature/amazing-feature`
5. Pull Request açın

## Geliştirme Ortamı

```bash
# Klonlama
git clone https://github.com/username/hikaye_olustur.git
cd hikaye_olustur

# Sanal ortam
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac

# Bağımlılıklar
pip install -r requirements.txt

# .env dosyası
cp .env.example .env
# .env dosyasına GEMINI_API_KEY ekleyin

# Çalıştırma
python app.py
```

## Kod Standartları

- **Python**: PEP 8 uyumlu, Black formatlı
- **JavaScript**: ES6+, camelCase
- **CSS**: BEM metodolojisi, CSS değişkenleri
- **Commit mesajları**: Conventional Commits

## Test

```bash
python -m pytest tests/ -v
```

## Lisans

MIT License - detaylar için LICENSE dosyasına bakın.
