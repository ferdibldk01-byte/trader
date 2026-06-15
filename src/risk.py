"""Risk yönetimi: pozisyon büyüklüğü ve stop/hedef seviyeleri.

Bu modül botun "düşük risk" kalbidir. Strateji ne sinyal verirse versin,
her işlemde kaybedebileceğiniz miktar sermayenizin küçük bir yüzdesiyle
sınırlanır.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TradePlan:
    entry: float        # giriş fiyatı
    stop: float         # stop-loss fiyatı
    take_profit: float  # kâr-al fiyatı
    quantity: float     # alınacak coin miktarı
    risk_amount: float  # bu işlemde riske atılan USDT


def position_size(
    balance: float,
    entry: float,
    stop: float,
    risk_per_trade_pct: float,
) -> float:
    """Stop'a kadar olan mesafeye göre kaç adet coin alınacağını hesaplar.

    Mantık: riske atılacak para = bakiye * risk%.
    Adet = riske atılacak para / (giriş - stop mesafesi).
    Böylece stop'a takılırsak tam olarak o yüzdeyi kaybederiz, fazlasını değil.
    """
    risk_amount = balance * (risk_per_trade_pct / 100.0)
    per_unit_risk = entry - stop
    if per_unit_risk <= 0:
        return 0.0
    return risk_amount / per_unit_risk


def build_long_plan(
    balance: float,
    entry: float,
    atr_value: float,
    risk_cfg: dict,
) -> TradePlan | None:
    """ATR'ye göre stop ve hedef belirleyip tam bir işlem planı kurar."""
    stop = entry - atr_value * risk_cfg["atr_stop_multiplier"]
    if stop <= 0 or stop >= entry:
        return None

    risk_distance = entry - stop
    take_profit = entry + risk_distance * risk_cfg["take_profit_rr"]
    qty = position_size(balance, entry, stop, risk_cfg["risk_per_trade_pct"])
    if qty <= 0:
        return None

    return TradePlan(
        entry=entry,
        stop=stop,
        take_profit=take_profit,
        quantity=qty,
        risk_amount=balance * (risk_cfg["risk_per_trade_pct"] / 100.0),
    )
