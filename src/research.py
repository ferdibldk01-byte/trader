"""Araştırma motoru: en iyi DCA konfigürasyonunu DÜRÜSTÇE bulur.

Dünyanın en iyi trader'larının yöntemi:
  1. BENCHMARK: Her şeyi 'al-ve-tut'a (buy & hold) karşı kıyasla. Bir bot
     sadece coini alıp beklemeyi yenmiyorsa, işe yaramaz.
  2. OUT-OF-SAMPLE: Veriyi ikiye böl. Konfigürasyonu SADECE ilk yarıda seç,
     sonra HİÇ GÖRMEDİĞİ ikinci yarıda test et. Geçmişi ezberlemeyi önler.
  3. RİSK-AYARLI: Sadece getiriye değil, düşüşe de bak. Çantada kalma
     (bag-holding) ağır cezalandırılır.
"""
from __future__ import annotations

import itertools

import pandas as pd

from .dca import DCAConfig, backtest_dca

# Taranacak konfigürasyon ızgarası
GRID = {
    "take_profit_pct": [1.5, 2.5, 4.0],
    "max_safety_orders": [3, 5, 7],
    "price_deviation_pct": [1.5, 2.5],
    "safety_volume_scale": [1.0, 1.5, 2.0],
    "use_trend_filter": [False, True],
    # KURAL: stop-loss'suz (0) işlem yok. Veri kanıtladı: stopsuz = felaket.
    "stop_loss_pct": [8.0, 12.0, 20.0],
}


def buy_and_hold_return(df: pd.DataFrame) -> float:
    """Baştan sona sadece alıp tutsaydık yüzde kaç kazanırdık?"""
    return round(100 * (df["close"].iloc[-1] / df["close"].iloc[0] - 1), 2)


def _score(stats: dict) -> float:
    """Risk-ayarlı puan: getiri eksi düşüş cezası.

    Sermaye korumaya öncelik verir:
      - Sonda büyük batık deal (bag-holding) ile bitmek = DİSKALİFİYE.
      - Aksi halde getiriden, en kötü kağıt-zararın yarısı kadar düşülür.
    """
    ret = stats["total_return_pct"]
    open_dd = abs(stats["open_deal_paper_drawdown_pct"]) if stats["open_deal_at_end"] else 0.0
    paper_dd = abs(stats["worst_deal_paper_drawdown_pct"])

    # Çantada -%25'ten fazla batık kalmak kabul edilemez -> ağır diskalifiye
    if open_dd > 25.0:
        return -999.0 + ret
    return ret - 0.5 * paper_dd


def _make_cfg(combo: dict, fee: float) -> DCAConfig:
    return DCAConfig(
        base_order=100, safety_order=100,
        take_profit_pct=combo["take_profit_pct"],
        max_safety_orders=combo["max_safety_orders"],
        price_deviation_pct=combo["price_deviation_pct"],
        safety_volume_scale=combo["safety_volume_scale"],
        use_trend_filter=combo["use_trend_filter"],
        stop_loss_pct=combo["stop_loss_pct"],
        fee_pct=fee,
    )


def research_symbol(df: pd.DataFrame, fee: float = 0.1, initial: float = 1000.0) -> dict:
    """Bir veri seti için en iyi konfigürasyonu in-sample'da seçip OOS'ta test eder."""
    n = len(df)
    split = n // 2
    in_sample = df.iloc[:split]
    out_sample = df.iloc[split:]

    keys = list(GRID)
    best = None
    for values in itertools.product(*GRID.values()):
        combo = dict(zip(keys, values))
        cfg = _make_cfg(combo, fee)
        stats = backtest_dca(in_sample, cfg, initial)
        score = _score(stats)
        if best is None or score > best["score"]:
            best = {"combo": combo, "score": round(score, 2), "in_stats": stats}

    # En iyi in-sample konfigürasyonunu GÖRÜLMEMİŞ veride test et
    best_cfg = _make_cfg(best["combo"], fee)
    oos = backtest_dca(out_sample, best_cfg, initial)

    return {
        "best_combo": best["combo"],
        "in_sample_return_pct": best["in_stats"]["total_return_pct"],
        "oos_return_pct": oos["total_return_pct"],
        "oos_open_bag_pct": oos["open_deal_paper_drawdown_pct"] if oos["open_deal_at_end"] else 0.0,
        "oos_stop_exits": oos["stop_loss_exits"],
        "oos_deals": oos["completed_deals"],
        "bh_in_sample_pct": buy_and_hold_return(in_sample),
        "bh_oos_pct": buy_and_hold_return(out_sample),
    }
