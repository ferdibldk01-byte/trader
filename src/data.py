"""Binance'ten geçmiş mum (OHLCV) verisi çeker.

Veri çekmek için API anahtarı GEREKMEZ (genel/public veri). Anahtar sadece
gerçek emir göndermek için lazım.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
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


# --- Doğrudan Binance (coğrafi-engelsiz) veri yolu ---------------------------
# ccxt'nin Binance bağlantısı, piyasa listesini yüklerken coğrafi-engelli
# adreslere takılıyor (GitHub ABD IP). Bunu aşmak için klines'ı DOĞRUDAN
# data-api.binance.vision'dan HTTP ile çekeriz — piyasa yükleme adımı yok.
_BINANCE_VISION = "https://data-api.binance.vision/api/v3/klines"
_TF_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def _tf_ms(timeframe: str) -> int:
    return int(timeframe[:-1]) * _TF_SECONDS[timeframe[-1]] * 1000


def _binance_direct(symbol: str, timeframe: str, days: int) -> list[list]:
    """Binance verisini doğrudan (anahtarsız, coğrafi-engelsiz) çeker."""
    market = symbol.replace("/", "")
    tf_ms = _tf_ms(timeframe)
    now = int(time.time() * 1000)
    cursor = now - days * 86400 * 1000
    rows: list[list] = []
    while cursor < now:
        q = urllib.parse.urlencode({
            "symbol": market, "interval": timeframe,
            "startTime": cursor, "limit": 1000,
        })
        req = urllib.request.Request(f"{_BINANCE_VISION}?{q}",
                                     headers={"User-Agent": "trader-bot"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        if not data:
            break
        for k in data:
            rows.append([int(k[0]), float(k[1]), float(k[2]),
                         float(k[3]), float(k[4]), float(k[5])])
        last_open = int(data[-1][0])
        if last_open < cursor:
            break
        cursor = last_open + tf_ms
        if len(data) < 2:
            break
    return rows


def _make_source(name: str, market_type: str = "spot"):
    """Alternatif borsa için anahtarsız ccxt bağlantısı kurar."""
    return getattr(ccxt, name)({"enableRateLimit": True})


def _paginate(exchange, symbol: str, timeframe: str, days: int) -> list[list]:
    """ccxt borsasından sayfalama yaparak geçmiş mumları toplar.

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


# Veri kaynağı önceliği: ÖNCE doğrudan Binance, erişilemezse alternatif borsalar.
# Kaynak SADECE BİR KEZ seçilir ve TÜM coinler için aynısı kullanılır → fiyatlar
# tutarlı (her coin farklı borsadan gelmez).
_SOURCE_ORDER = ["binance", "bybit", "kucoin", "okx", "gate"]

SELECTED_SOURCE: str | None = None
_fetch_fn = None


def _source_fetch(name: str, market_type: str):
    """Bir kaynağın (symbol, timeframe, days) -> rows fetch fonksiyonunu verir."""
    if name == "binance" and market_type != "futures":
        return lambda s, tf, d: _binance_direct(s, tf, d)
    return lambda s, tf, d: _paginate(_make_source(name, market_type), s, tf, d)


def _select_source(market_type: str = "spot"):
    """Çalışan ilk veri kaynağını BİR KEZ seçer (BTC/USDT ile test ederek)."""
    global SELECTED_SOURCE, _fetch_fn
    if _fetch_fn is not None:
        return _fetch_fn

    last_error: Exception | None = None
    for name in _SOURCE_ORDER:
        try:
            fn = _source_fetch(name, market_type)
            probe = fn("BTC/USDT", "1h", 1)  # ~24 mumluk hızlı sağlık testi
            if probe and len(probe) >= 1:
                SELECTED_SOURCE = name
                _fetch_fn = fn
                return fn
        except Exception as e:
            last_error = e
            continue
    raise RuntimeError(f"Hiçbir veri kaynağı erişilebilir değil. Son hata: {last_error}")


def fetch_ohlcv(
    symbol: str,
    timeframe: str = "4h",
    days: int = 500,
    market_type: str = "spot",
) -> pd.DataFrame:
    """Belirtilen coin için geçmiş mum verisini DataFrame olarak getirir.

    TÜM coinler için TEK ortak kaynak kullanılır (tutarlı fiyatlar). Öncelik
    doğrudan Binance; erişilemezse tek bir alternatif borsaya geçilir.

    Sütunlar: timestamp (index), open, high, low, close, volume
    """
    fn = _select_source(market_type)
    all_rows = fn(symbol, timeframe, days)

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
