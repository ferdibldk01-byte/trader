"""DCA (Dollar-Cost Averaging) bot — 3Commas'ın amiral gemisi mantığı.

NASIL ÇALIŞIR (3Commas mantığıyla aynı):
  1. BASE ORDER     : İlk alım yapılır.
  2. SAFETY ORDERS  : Fiyat belli % düşerse, daha fazla alır -> ortalama maliyet
                      düşer. Adım sayısı ve her adımın boyutu/aralığı ayarlanır.
  3. TAKE PROFIT    : Fiyat ortalama maliyetin belli % üstüne çıkınca HEPSİ
                      satılır, kâr alınır, döngü baştan başlar.

NEDEN trend stratejisinden daha sağlam? Tahmin yapmaz. Volatiliteyi (iniş-çıkış)
mekanik olarak paraya çevirir. Ama riski de vardır: SERT ve UZUN düşüşte tüm
safety order'lar dolup pozisyon dipte kilitlenebilir. O yüzden risk ayarları
(max safety order, sermaye tahsisi) kritiktir.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .strategy import ema


@dataclass
class DCAConfig:
    base_order: float = 100.0          # ilk alım (USDT)
    safety_order: float = 100.0        # ilk safety order boyutu (USDT)
    max_safety_orders: int = 5         # en fazla kaç safety order
    price_deviation_pct: float = 2.0   # ilk safety order için fiyat ne kadar düşmeli
    safety_step_scale: float = 1.5     # her adımda aralık bu kat artar
    safety_volume_scale: float = 1.5   # her adımda alım miktarı bu kat artar
    take_profit_pct: float = 2.0       # ortalama maliyetin % kaç üstünde sat
    fee_pct: float = 0.1
    # --- Trend filtresi: yeni deal SADECE yukarı trendde açılır ---
    use_trend_filter: bool = True      # düşüş trendinde yeni deal açma
    trend_ema_period: int = 200        # fiyat bu EMA'nın üstündeyse "yukarı trend"
    # --- Stop-loss: ortalama maliyetin % altına inince zararı kabul et, kapat ---
    stop_loss_pct: float = 0.0         # 0 = kapalı. Örn 15 = ortalama -%15'te çık


@dataclass
class DCADeal:
    start_time: pd.Timestamp
    end_time: pd.Timestamp | None = None
    safety_orders_used: int = 0
    avg_entry: float = 0.0
    exit_price: float = 0.0
    pnl: float = 0.0
    max_drawdown_pct: float = 0.0      # bu deal sırasındaki en kötü kağıt zarar
    reason: str = "take_profit"        # "take_profit" veya "stop_loss"


def _safety_levels(base_price: float, cfg: DCAConfig) -> list[tuple[float, float]]:
    """Her safety order için (tetikleme fiyatı, USDT miktarı) listesi üretir."""
    levels = []
    cum_dev = 0.0
    step = cfg.price_deviation_pct
    size = cfg.safety_order
    for _ in range(cfg.max_safety_orders):
        cum_dev += step
        trigger = base_price * (1 - cum_dev / 100.0)
        levels.append((trigger, size))
        step *= cfg.safety_step_scale
        size *= cfg.safety_volume_scale
    return levels


def backtest_dca(df: pd.DataFrame, cfg: DCAConfig, initial_balance: float = 1000.0) -> dict:
    """DCA stratejisini geçmiş veride mum-mum çalıştırır."""
    fee = cfg.fee_pct / 100.0
    balance = initial_balance
    deals: list[DCADeal] = []

    # Trend filtresi için EMA'yı önceden hesapla (fiyat > EMA = yukarı trend)
    trend_ema = ema(df["close"], cfg.trend_ema_period) if cfg.use_trend_filter else None

    in_deal = False
    coin_qty = 0.0
    cost_basis = 0.0          # toplam harcanan USDT (komisyon dahil)
    pending: list[tuple[float, float]] = []
    deal: DCADeal | None = None
    worst_paper = 0.0

    for row in df.itertuples():
        price = row.close

        if not in_deal:
            # Trend filtresi: düşüş trendindeyse yeni deal AÇMA, bekle
            if trend_ema is not None:
                ema_now = trend_ema.loc[row.Index]
                if np.isnan(ema_now) or price < ema_now:
                    continue
            # Yeni deal başlat: base order
            spend = cfg.base_order
            if spend > balance:
                break  # para bitti
            qty = (spend * (1 - fee)) / price
            balance -= spend
            coin_qty = qty
            cost_basis = spend
            pending = _safety_levels(price, cfg)
            deal = DCADeal(start_time=row.Index)
            worst_paper = 0.0
            in_deal = True
            continue

        # Deal içindeyiz: önce safety order'ları kontrol et (low'a göre)
        while pending and row.low <= pending[0][0]:
            trig, size = pending.pop(0)
            if size > balance:
                break
            qty = (size * (1 - fee)) / trig
            balance -= size
            coin_qty += qty
            cost_basis += size
            deal.safety_orders_used += 1

        avg_entry = cost_basis / coin_qty if coin_qty else price
        # kağıt üstü en kötü düşüş takibi
        paper = (row.low - avg_entry) / avg_entry * 100
        worst_paper = min(worst_paper, paper)

        # Stop-loss: ortalama maliyetin altına çok inersek zararı kabul edip çık.
        # (Take profit'ten ÖNCE kontrol edilir — kötümser/güvenli varsayım.)
        if cfg.stop_loss_pct > 0:
            sl_price = avg_entry * (1 - cfg.stop_loss_pct / 100.0)
            if row.low <= sl_price:
                proceeds = coin_qty * sl_price * (1 - fee)
                pnl = proceeds - cost_basis
                balance += proceeds
                deal.end_time = row.Index
                deal.avg_entry = avg_entry
                deal.exit_price = sl_price
                deal.pnl = pnl
                deal.max_drawdown_pct = round(worst_paper, 2)
                deal.reason = "stop_loss"
                deals.append(deal)
                in_deal = False
                coin_qty = 0.0
                cost_basis = 0.0
                continue

        # Take profit: high, hedefe değdi mi?
        tp_price = avg_entry * (1 + cfg.take_profit_pct / 100.0)
        if row.high >= tp_price:
            proceeds = coin_qty * tp_price * (1 - fee)
            pnl = proceeds - cost_basis
            balance += proceeds
            deal.end_time = row.Index
            deal.avg_entry = avg_entry
            deal.exit_price = tp_price
            deal.pnl = pnl
            deal.max_drawdown_pct = round(worst_paper, 2)
            deals.append(deal)
            in_deal = False
            coin_qty = 0.0
            cost_basis = 0.0

    # Açık kalan deal'i son fiyattan değerle (kapatılmamış)
    open_value = 0.0
    if in_deal and coin_qty:
        open_value = coin_qty * df["close"].iloc[-1]

    final_equity = balance + open_value
    return _dca_stats(deals, initial_balance, final_equity, in_deal, worst_paper)


def _dca_stats(deals, initial, final, open_deal, worst_paper) -> dict:
    n = len(deals)
    worst_deal_dd = min((d.max_drawdown_pct for d in deals), default=0.0)
    avg_safety = np.mean([d.safety_orders_used for d in deals]) if n else 0.0
    stops = sum(1 for d in deals if d.reason == "stop_loss")
    return {
        "completed_deals": n,
        "stop_loss_exits": stops,
        "total_return_pct": round(100 * (final - initial) / initial, 2),
        "avg_safety_orders_used": round(float(avg_safety), 1),
        "worst_deal_paper_drawdown_pct": round(worst_deal_dd, 2),
        "open_deal_at_end": open_deal,
        "open_deal_paper_drawdown_pct": round(worst_paper, 2) if open_deal else 0.0,
        "final_equity": round(final, 2),
    }
