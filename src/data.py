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


def fetch_ohlcv(
    symbol: str,
    timeframe: str = "4h",
    days: int = 500,
    market_type: str = "spot",
) -> pd.DataFrame:
    """Belirtilen coin için geçmiş mum verisini DataFrame olarak getirir.

    Sütunlar: timestamp (index), open, high, low, close, volume
    """
    exchange = make_exchange(market_type)
    timeframe_ms = exchange.parse_timeframe(timeframe) * 1000
    since = exchange.milliseconds() - days * 24 * 60 * 60 * 1000

    all_rows: list[list] = []
    cursor = since
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=1000)
        if not batch:
            break
        all_rows += batch
        cursor = batch[-1][0] + timeframe_ms
        if len(batch) < 1000:
            break
        time.sleep(exchange.rateLimit / 1000)  # rate limit'e saygı

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
