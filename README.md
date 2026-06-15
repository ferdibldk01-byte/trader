# Trader Bot — Kullanım Kılavuzu

Bu bot **kendi bilgisayarınızda**, terminal (PowerShell) üzerinden çalışır.
Bir web sitesi değildir. API anahtarınız her zaman sizde kalır (`.env` dosyası).

---

## Kurulum (ilk seferde bir kez)

```powershell
cd "C:\Users\Lenovo ThinkPad\trader"
pip install -r requirements.txt
```

---

## AŞAMA 1 — Güvenli test (para yok, anahtar yok, risk yok)

Sadece geçmiş veriyle simülasyon. İstediğiniz kadar çalıştırın:

```powershell
python main.py fetch       # Binance'ten güncel geçmiş veriyi indir
python main.py backtest    # Trend stratejisini geçmişte test et
python main.py dca         # DCA botunu geçmişte test et
python main.py optimize    # En iyi parametreleri ara (walk-forward)
```

Ayarları `config.yaml` dosyasından değiştirebilirsiniz (coinler, risk %, vb.).

---

## AŞAMA 2 — Kağıt üzerinde canlı (sahte para, gerçek piyasa)

Gerçek parçaya geçmeden önce ZORUNLU adım.

1. `.env.example` -> `.env` olarak kopyalayın.
2. https://testnet.binance.vision adresinden ücretsiz TESTNET anahtarı alın.
3. `.env` içine yapıştırın. `USE_TESTNET=true` kalsın.
4. Çalıştırın:

```powershell
python main.py paper
```

Durdurmak için: `Ctrl + C`. Bot bilgisayar açıkken çalışır (7/24 için sonra VPS).

---

## AŞAMA 3 — Gerçek para (DİKKAT)

Aylarca paper trading başarılı olmadan YAPMAYIN. O zaman bile çok küçük miktarla.

1. Binance'te API anahtarı oluştururken:
   - [x] Enable Reading
   - [x] Enable Spot Trading
   - [ ] **Enable Withdrawals  <-- ASLA AÇMAYIN**
2. `.env` dosyasında:
   ```
   USE_TESTNET=false
   LIVE_TRADING=true
   I_UNDERSTAND_THE_RISK=true
   ```
3. `python main.py paper`

---

## Güvenlik garantileri (kodda gömülü)

- Varsayılan mod DRY-RUN: hiçbir gerçek emir gitmez.
- Gerçek emir için iki ayrı bayrak gerekir (çift onay).
- Para çekme (withdraw) fonksiyonu YOKTUR — bot paranızı çekemez.
- API anahtarınız sadece `.env` dosyanızdadır; kimseyle paylaşılmaz.

## Unutmayın

- Garanti kâr diye bir şey yoktur. Backtest geçmişi gösterir, geleceği değil.
- Long-only spot bot ayı piyasasında para kazanamaz, sadece daha az kaybeder.
- Kaybetmeyi göze alamayacağınız parayla asla işlem yapmayın.
