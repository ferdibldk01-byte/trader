"""Rejim-farkında trend takip stratejisi.

Göstergeler pandas/numpy ile elle hesaplanır (TA-Lib gerekmez).

Giriş (LONG) kuralları — HEPSİ aynı anda doğru olmalı:
  1. Hızlı EMA > Yavaş EMA            (yukarı trend)
  2. ADX >= adx_min                   (trend yeterince güçlü)
  3. RSI < rsi_overbought             (tepeden alım yapma)
  4. Fiyat hızlı EMA'nın üzerinde     (geri çekilme değil, momentum)

Çıkış:
  - Stop-loss veya take-profit (risk.py + backtest yönetir), VEYA
  - Hızlı EMA tekrar yavaş EMA'nın altına inerse (trend bitti)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index — trendin gücünü ölçer (yön değil)."""
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr_ = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(
        alpha=1 / period, adjust=False).mean() / atr_
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(
        alpha=1 / period, adjust=False).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def add_indicators(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Tüm göstergeleri hesaplayıp DataFrame'e sütun olarak ekler."""
    out = df.copy()
    out["ema_fast"] = ema(out["close"], params["ema_fast"])
    out["ema_slow"] = ema(out["close"], params["ema_slow"])
    out["rsi"] = rsi(out["close"], params["rsi_period"])
    out["adx"] = adx(out, params["adx_period"])
    out["atr"] = atr(out, params["atr_period"])
    return out


def _signals_trend(out: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Rejim-farkında trend takibi (orijinal strateji)."""
    uptrend = out["ema_fast"] > out["ema_slow"]
    strong_trend = out["adx"] >= params["adx_min"]
    not_overbought = out["rsi"] < params["rsi_overbought"]
    momentum = out["close"] > out["ema_fast"]

    out["long_entry"] = uptrend & strong_trend & not_overbought & momentum
    out["exit_signal"] = out["ema_fast"] < out["ema_slow"]
    return out


def _signals_meanrev(out: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Ortalamaya dönüş: YUKARI trendde sert geri çekilmeyi (oversold) satın al.

    Mantık: ana trend yukarı (fiyat > yavaş EMA) AMA RSI aşırı satım bölgesinde.
    Yani 'düşüşü değil, yükselen trend içindeki indirimi' alırız. Çıkış: RSI
    normale dönünce (orta bant) ya da trend kırılınca.
    """
    rsi_buy = params.get("rsi_oversold", 35)
    rsi_exit = params.get("rsi_exit", 55)
    in_uptrend = out["close"] > out["ema_slow"]

    out["long_entry"] = in_uptrend & (out["rsi"] < rsi_buy)
    out["exit_signal"] = (out["rsi"] > rsi_exit) | (out["close"] < out["ema_slow"])
    return out


def _signals_breakout(out: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Donchian kırılması: son N mumun en yükseğini aşınca al (momentum).

    Çıkış: son M mumun en düşüğünün altına inince (trend takip stilinde
    kırılma stratejisi — turtle traders mantığı).
    """
    entry_n = params.get("breakout_entry", 20)
    exit_n = params.get("breakout_exit", 10)
    # shift(1): mevcut mumu dahil etmeden ÖNCEKİ N mumun seviyesi (lookahead yok)
    upper = out["high"].rolling(entry_n).max().shift(1)
    lower = out["low"].rolling(exit_n).min().shift(1)

    out["long_entry"] = out["close"] > upper
    out["exit_signal"] = out["close"] < lower
    return out


_STRATEGIES = {
    "trend": _signals_trend,
    "meanrev": _signals_meanrev,
    "breakout": _signals_breakout,
}


def generate_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Her mum için 'long_entry' ve 'exit_signal' (bool) sütunları üretir.

    params['type'] ile strateji seçilir: 'trend' (varsayılan), 'meanrev',
    'breakout'. Tanımsızsa trend kullanılır.
    """
    out = add_indicators(df, params)
    strategy_type = params.get("type", "trend")
    fn = _STRATEGIES.get(strategy_type, _signals_trend)
    return fn(out, params)
