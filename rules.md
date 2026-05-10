# 📋 Salontity — Geliştirme Kuralları

> Bu dosya her çalışma oturumunun başında okunmalıdır. Tüm geliştirme süreci bu kurallara uygun yürütülür.

---

## 1. 🔄 Versiyon Yönetimi
- Her değişiklik sonrası git push yapılırken **yeni bir versiyon** olarak pushlayacağız

## 2. 🖥️ Çalışma Ortamı
- Localde değil **sürekli sunucuda** çalışacağız
- Kod yazılır → git push → Coolify otomatik deploy eder

## 3. ☁️ Sunucu
- Sunucumuz **Coolify** panelde çalışıyor

## 4. 🔗 Coolify Panel & Deploy
- Coolify panel adresi: **http://151.241.154.70:8000/**
- **Coolify Giriş:** `metafoxdev@gmail.com` / `MDKteam_242`
- **API Token:** `6|pEXgIIKyI6vSH952Na8iYWUZDx8pXxmraSSV8IHSf577a3bb`
- **Servis UUID'leri:**
  | Servis | UUID | Domain |
  |---|---|---|
  | salontity-web | `dhabkl3truuzvlib6anvx0we` | salontity.com |
  | salontity-backend | `cs26rvhggbkekno7ji4wguhp` | api.salontity.com |
  | salontity-realtime | `as7sk7scbg7r1vmdswh0g562` | ws.salontity.com |
- **Deploy komutu (web):**
  ```
  curl -X POST "http://151.241.154.70:8000/api/v1/applications/dhabkl3truuzvlib6anvx0we/restart" \
    -H "Authorization: Bearer 6|pEXgIIKyI6vSH952Na8iYWUZDx8pXxmraSSV8IHSf577a3bb"
  ```

## 5. 🌍 Çoklu Dil / Para / Bölge
- Tüm sistemde **Çoklu Dil/Para/Bölge** kullanılacaktır

## 6. 🌙 Dark / Light Mode
- Tüm sistemde **dark/light mode** kullanılacaktır

## 7. 📝 Açıklama Zorunluluğu
- Kod yazmadan önce **mutlaka geniş ve detaylı açıklamalar** yapılacak

## 8. 📊 İlerleme Takibi
- Her işlem bittikten sonra **ilerleme.md** dosyası güncellenecek
- Bir readme dosyası gibi yapılanlar açıklanacak
- **Tarih ve saat** eklenecek

## 9. 🔑 Üyelik Bilgileri (Test Hesapları)

| Rol | E-posta / Kullanıcı | Şifre |
|---|---|---|
| **Admin** | admin@salontity.com | password123 |
| **Üye & Personel** | MDKaratopraK | MDKteam_242 |
| **Vendor (Satıcı)** | satici@salontity.com | password123 |
| **Freelancer** | freelancer@salontity.com | password123 |

## 10. 🔗 URL Slug Kuralı
- Tüm sayfa yolları (slug) **İngilizce** olmalıdır
- Örnek: `/kategoriler` ❌ → `/categories` ✅
- Örnek: `/ara` ❌ → `/search` ✅
- Örnek: `/salon-ekle` ❌ → `/register-salon` ✅

## 11. 📱 Responsive Tasarım
- Site tasarımı **her zaman responsive** olmalıdır
- Desktop, tablet ve mobil cihazlarda düzgün görüntülenmelidir

## 12. 🔍 Önce Analiz, Sonra Geliştirme
- Bir sistem, özellik veya geliştirmeye başlamadan önce **o konuyla ilgili tüm sunucu, proje ve mevcut yapı analiz edilmelidir**
- Ayrı/paralel bir sistem oluşturmak yerine **mevcut yapının üzerine geliştirme** yapılmalıdır
- Uyumluluk oranı her zaman yüksek tutulmalı, sistemin dağınıklaşması engellenmelidir
- Mevcut bileşenler, servisler ve modüller önce incelenmeli; yeni dosya/modül oluşturmak son çare olmalıdır

## 13. 🏗️ Evrensel Kod Mimarisi
- **Dosya şişmesini** engellemek için bileşenler küçük, modüler ve tek sorumluluk prensibine uygun olmalıdır
- Ortak işlevler **paylaşılan utility/helper** dosyalarında toplanmalı, tekrar eden kod yazılmamalıdır
- Her dosya **maksimum 300-400 satır** sınırında tutulmalı; aşıldığında bileşen ayrıştırması yapılmalıdır
- Import döngüleri ve gereksiz bağımlılıklar engellenmeli, temiz bir bağımlılık ağacı korunmalıdır

## 14. 🔎 OpenGraph & SEO Uyumluluğu
- Tüm public sayfalar **OpenGraph meta etiketlerine** sahip olmalıdır (`og:title`, `og:description`, `og:image`, `og:url`, `og:type`)
- Her sayfada **benzersiz title ve meta description** bulunmalıdır
- Yapılandırılmış veri (**JSON-LD Schema.org**) uygun sayfalarda eklenmelidir
- Canonical URL'ler doğru ayarlanmalıdır
- Sosyal medya paylaşımlarında doğru önizleme görseli ve açıklaması çıkmalıdır

## 15. ✅ Her İşlem Sonrası Test Zorunluluğu
- Her kod değişikliği veya düzeltme sonrası **mutlaka test yapılmalıdır**
- Deploy sonrası canlı sitede (salontity.com) ilgili sayfa/özellik **browser üzerinden doğrulanmalıdır**
- Test yapılmadan bir sonraki işleme geçilmemelidir

## 16. 🌐 Coolify İşlemleri Browser Üzerinden
- Coolify panelinde deploy, restart, log kontrolü ve benzeri tüm işlemler **browser üzerinden** yapılmalıdır
- Coolify panel adresi: **http://151.241.154.70:8000/**
- Terminal üzerinden API çağrısı yerine **browser arayüzü** tercih edilmelidir

## 17. 🎨 İkon ve Görsel Materyal Kuralı
- Site tasarımında kullanılacak tüm ikonlar ve benzeri materyaller için **Google Material Symbols** veya **Font Awesome** kullanmak zorunludur
- Özel SVG veya emoji tabanlı ikonlar kullanılmamalıdır
- Tutarlılık için proje genelinde aynı ikon kütüphanesi tercih edilmelidir
