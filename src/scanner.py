"""Akıllı sinyal motoru: çoklu strateji konsensüsü + long/short + risk planı.

Her coin için 3 stratejiyi (trend, meanrev, breakout) çalıştırır, oylarını
toplar ve tek bir KONSENSÜS sinyali üretir:
  - yön: LONG / SHORT / FLAT
  - güç: kaç strateji hemfikir (1-3). 3 = en güçlü.
  - stop-loss ve kâr hedefi (ATR'ye göre)

Ayrıca durum (state) takibi yapar: sinyal DEĞİŞTİĞİNDE anlar ve geçmiş
sinyallerin gerçek (kağıt) performansını kaydeder.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

from .data import fetch_ohlcv
from .risk import build_long_plan, build_short_plan
from .strategy import add_indicators, generate_signals

STRATEGIES = ["trend", "meanrev", "breakout"]

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
STATE_FILE = STATE_DIR / "last_signals.json"
LOG_FILE = STATE_DIR / "signal_log.csv"


@dataclass
class Signal:
    symbol: str
    direction: str          # LONG / SHORT / FLAT
    strength: int           # kaç strateji hemfikir (0-3)
    votes: dict             # {"trend": "LONG", ...}
    price: float
    rsi: float
    adx: float
    entry: float | None = None
    stop: float | None = None
    target: float | None = None


def analyze_symbol(symbol: str, config: dict) -> Signal:
    """Bir coin için 3 stratejinin konsensüs sinyalini üretir."""
    df = fetch_ohlcv(symbol, config["timeframe"], days=60,
                     market_type=config["market_type"])

    votes: dict[str, str] = {}
    longs = shorts = 0
    for st in STRATEGIES:
        params = dict(config["strategy"], type=st)
        sig = generate_signals(df, params).dropna()
        if sig.empty:
            votes[st] = "FLAT"
            continue
        last = sig.iloc[-1]
        if bool(last.get("long_entry", False)):
            votes[st] = "LONG"; longs += 1
        elif bool(last.get("short_entry", False)):
            votes[st] = "SHORT"; shorts += 1
        else:
            votes[st] = "FLAT"

    if longs > shorts:
        direction, strength = "LONG", longs
    elif shorts > longs:
        direction, strength = "SHORT", shorts
    else:
        direction, strength = "FLAT", 0

    ind = add_indicators(df, config["strategy"]).dropna()
    last = ind.iloc[-1]
    price = float(last["close"])
    atr = float(last["atr"])

    sig_obj = Signal(
        symbol=symbol, direction=direction, strength=strength, votes=votes,
        price=price, rsi=float(last["rsi"]), adx=float(last["adx"]),
    )

    bal = config["backtest"]["initial_balance"]
    plan = None
    if direction == "LONG":
        plan = build_long_plan(bal, price, atr, config["risk"])
    elif direction == "SHORT":
        plan = build_short_plan(bal, price, atr, config["risk"])
    if plan:
        sig_obj.entry = round(price, 6)
        sig_obj.stop = round(plan.stop, 6)
        sig_obj.target = round(plan.take_profit, 6)
    return sig_obj


# ---------------------------------------------------------------------------
#  Durum (state) takibi + performans kaydı
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(state: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _append_log(row: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    header = ("time,symbol,event,direction,entry,exit,pnl_pct,strength\n")
    if not LOG_FILE.exists():
        LOG_FILE.write_text(header, encoding="utf-8")
    line = (f"{row['time']},{row['symbol']},{row['event']},{row['direction']},"
            f"{row.get('entry','')},{row.get('exit','')},"
            f"{row.get('pnl_pct','')},{row.get('strength','')}\n")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def detect_changes(signals: list[Signal]) -> list[dict]:
    """Önceki taramaya göre yön değişen coinleri bulur, performansı kaydeder.

    Her coin için "açık pozisyon" mantığı (kağıt üzerinde):
      - Yön LONG/SHORT'a döndüyse: pozisyon AÇ (giriş fiyatını kaydet).
      - Yön değiştiyse/FLAT olduysa: pozisyonu KAPAT, kâr/zararı hesapla+kaydet.
    """
    state = _load_state()
    changes: list[dict] = []

    for s in signals:
        prev = state.get(s.symbol, {})
        prev_dir = prev.get("direction", "FLAT")

        if s.direction == prev_dir:
            continue  # değişiklik yok

        # Önceki açık pozisyonu kapat (kağıt P/L hesapla)
        if prev_dir in ("LONG", "SHORT") and prev.get("entry"):
            entry = float(prev["entry"])
            if prev_dir == "LONG":
                pnl = (s.price - entry) / entry * 100
            else:  # SHORT
                pnl = (entry - s.price) / entry * 100
            _append_log({
                "time": _now(), "symbol": s.symbol, "event": "CLOSE",
                "direction": prev_dir, "entry": entry, "exit": round(s.price, 6),
                "pnl_pct": round(pnl, 2), "strength": prev.get("strength", ""),
            })
            changes.append({
                "symbol": s.symbol, "type": "CLOSE", "direction": prev_dir,
                "pnl_pct": round(pnl, 2),
            })

        # Yeni yön LONG/SHORT ise pozisyon aç
        if s.direction in ("LONG", "SHORT"):
            _append_log({
                "time": _now(), "symbol": s.symbol, "event": "OPEN",
                "direction": s.direction, "entry": round(s.price, 6),
                "strength": s.strength,
            })
            changes.append({
                "symbol": s.symbol, "type": "OPEN", "direction": s.direction,
                "strength": s.strength, "entry": s.price,
                "stop": s.stop, "target": s.target,
            })

        # Durumu güncelle
        state[s.symbol] = {
            "direction": s.direction,
            "entry": s.price if s.direction in ("LONG", "SHORT") else None,
            "strength": s.strength,
            "updated": _now(),
        }

    _save_state(state)
    return changes


def performance_report() -> dict:
    """Kaydedilmiş kapanmış sinyallerin DÜRÜST (kağıt) performansını çıkarır."""
    if not LOG_FILE.exists():
        return {"closed": 0, "msg": "Henüz kapanmış sinyal yok."}
    closed = []
    for ln in LOG_FILE.read_text(encoding="utf-8").splitlines()[1:]:
        parts = ln.split(",")
        if len(parts) >= 7 and parts[2] == "CLOSE" and parts[6]:
            try:
                closed.append(float(parts[6]))
            except ValueError:
                continue
    if not closed:
        return {"closed": 0, "msg": "Henüz kapanmış sinyal yok (hepsi açık)."}
    wins = [p for p in closed if p > 0]
    return {
        "closed": len(closed),
        "win_rate_pct": round(100 * len(wins) / len(closed), 1),
        "avg_pnl_pct": round(sum(closed) / len(closed), 2),
        "total_pnl_pct": round(sum(closed), 2),
        "best_pct": round(max(closed), 2),
        "worst_pct": round(min(closed), 2),
    }
