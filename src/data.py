"""Binance'ten geçmiş mum (OHLCV) verisi çeker.

Veri çekmek için API anahtarı GEREKMEZ (genel/public veri). Anahtar sadece
gerçek emir göndermek için lazım.
"""
from __future__ import annotations

import time
from pathlib import Path

import ccxt
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def make_exchange(market_type: str = "spot") -> ccxt.binance:
    """Veri çekmek için (anahtarsız) bir Binance bağlantısı oluşturur.

    NOT: Binance ana API'si (api.binance.com) bazı bölgelerin/veri merkezi
    IP'lerinin (örn. GitHub Actions = ABD) erişimini yasal olarak engeller.
    Bu yüzden halka açık, coğrafi-engelsiz veri sunucusunu kullanırız:
    data-api.binance.vision — aynı veri, anahtar gerekmez, her yerden çalışır.
    """
    exchange = ccxt.binance({
        "enableRateLimit": True,
        "options": {"defaultType": "future" if market_type == "futures" else "spot"},
    })
    # Spot halka açık veriyi coğrafi-engelsiz aynasından çek
    if market_type != "futures":
        exchange.urls["api"]["public"] = "https://data-api.binance.vision/api/v3"
    return exchange


# Veri kaynağı önceliği. Binance erişilemezse (coğrafi engel) sıradakine geçilir.
# ÖNEMLİ: Kaynak SADECE BİR KEZ seçilir ve TÜM coinler için aynısı kullanılır.
# Böylece her coin farklı borsadan gelip fiyat tutarsızlığı OLUŞMAZ.
_SOURCE_ORDER = ["binance", "bybit", "kucoin", "okx", "gate"]

# Süreç boyunca seçilen tek kaynak (borsa adı). Tutarlılık için cache'lenir.
SELECTED_SOURCE: str | None = None
_selected_exchange = None


def _make_source(name: str, market_type: str = "spot"):
    """Verilen borsa için anahtarsız ccxt bağlantısı kurar."""
    if name == "binance":
        return make_exchange(market_type)
    return getattr(ccxt, name)({"enableRateLimit": True})


def _select_source(market_type: str = "spot"):
    """Çalışan ilk veri kaynağını BİR KEZ seçer (BTC/USDT ile test ederek).

    Seçilen kaynak süreç boyunca sabit kalır → tüm coinler aynı borsadan,
    fiyatlar tutarlı.
    """
    global SELECTED_SOURCE, _selected_exchange
    if _selected_exchange is not None:
        return _selected_exchange

    last_error: Exception | None = None
    for name in _SOURCE_ORDER:
        try:
            ex = _make_source(name, market_type)
            # Tek mumluk hızlı sağlık testi
            probe = ex.fetch_ohlcv("BTC/USDT", "1h", limit=2)
            if probe and len(probe) >= 1:
                SELECTED_SOURCE = name
                _selected_exchange = ex
                return ex
        except Exception as e:
            last_error = e
            continue
    raise RuntimeError(f"Hiçbir veri kaynağı erişilebilir değil. Son hata: {last_error}")


def _paginate(exchange, symbol: str, timeframe: str, days: int) -> list[list]:
    """Bir borsadan sayfalama yaparak geçmiş mumları toplar.

    ÖNEMLİ: Bazı borsalar tek istekte az mum verir (OKX ~300, Binance 1000).
    Bu yüzden "batch < 1000" ile DURMAYIZ — ŞİMDİYE ulaşana kadar ileri
    sayfalanırız. Aksi halde veri haftalarca eski kalır (yanlış fiyat!).
    """
    timeframe_ms = exchange.parse_timeframe(timeframe) * 1000
    now = exchange.milliseconds()
    cursor = now - days * 24 * 60 * 60 * 1000
    all_rows: list[list] = []

    while cursor < now:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=1000)
        if not batch:
            break
        all_rows += batch
        last_ts = batch[-1][0]
        if last_ts < cursor:   # ilerleme yok → sonsuz döngüyü önle
            break
        cursor = last_ts + timeframe_ms
        if len(batch) < 2:
            break
        time.sleep(exchange.rateLimit / 1000)  # rate limit'e saygı
    return all_rows


def fetch_ohlcv(
    symbol: str,
    timeframe: str = "4h",
    days: int = 500,
    market_type: str = "spot",
) -> pd.DataFrame:
    """Belirtilen coin için geçmiş mum verisini DataFrame olarak getirir.

    TÜM coinler için TEK ortak kaynak kullanılır (tutarlı fiyatlar).
    Binance erişilemezse otomatik olarak tek bir alternatif borsaya geçilir.

    Sütunlar: timestamp (index), open, high, low, close, volume
    """
    exchange = _select_source(market_type)
    all_rows = _paginate(exchange, symbol, timeframe, days)

    if not all_rows:
        raise RuntimeError(
            f"{symbol} için {SELECTED_SOURCE} kaynağından veri gelmedi."
        )

    df = pd.DataFrame(
        all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df = df.drop_duplicates(subset="timestamp")
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.set_index("timestamp").sort_index()
    return df


def save_csv(df: pd.DataFrame, symbol: str, timeframe: str) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    safe = symbol.replace("/", "")
    path = DATA_DIR / f"{safe}_{timeframe}.csv"
    df.to_csv(path)
    return path


def load_csv(symbol: str, timeframe: str) -> pd.DataFrame:
    safe = symbol.replace("/", "")
    path = DATA_DIR / f"{safe}_{timeframe}.csv"
    df = pd.read_csv(path, index_col="timestamp", parse_dates=True)
    return df
