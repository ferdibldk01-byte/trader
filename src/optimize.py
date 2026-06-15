"""Parametre optimizasyonu + walk-forward doğrulama.

NEDEN walk-forward? Bir parametre setini TÜM geçmişe uydurursak (overfitting),
geçmişte harika görünür ama gelecekte batar. Walk-forward bunu engeller:

  - Veriyi dilimlere böleriz.
  - Parametreleri SADECE geçmiş dilimde (in-sample) optimize ederiz.
  - Sonra HİÇ GÖRMEDİĞİ bir sonraki dilimde (out-of-sample) test ederiz.
  - Gerçek başarı = görülmemiş veride elde edilen sonuç.

Böylece "geçmişi ezberleyen" sahte kazançları eler, gerçek kenarı görürüz.
"""
from __future__ import annotations

import itertools
from copy import deepcopy

import pandas as pd

from .backtest import run_backtest

# Her strateji için ayrı parametre ızgarası. Geniş tutarsanız yavaşlar.
PARAM_GRIDS = {
    "trend": {
        "ema_fast": [20, 50],
        "ema_slow": [100, 200],
        "adx_min": [20, 25, 30],
        "rsi_overbought": [70, 75],
    },
    "meanrev": {
        "rsi_oversold": [25, 30, 35, 40],
        "rsi_exit": [50, 55, 60],
    },
    "breakout": {
        "breakout_entry": [20, 30, 55],
        "breakout_exit": [10, 15, 20],
    },
}

# Geriye dönük uyumluluk (eski kod PARAM_GRID bekliyorsa)
PARAM_GRID = PARAM_GRIDS["trend"]


def _apply_params(config: dict, combo: dict) -> dict:
    cfg = deepcopy(config)
    cfg["strategy"].update(combo)
    return cfg


def _score(stats: dict) -> float:
    """Bir sonucu tek sayıyla puanlar.

    Sadece getiriye değil, RİSKE göre ödüllendiririz: yüksek getiri + düşük
    düşüş iyi. İşlem sayısı çok azsa (şans eseri) cezalandırırız.
    """
    if stats["trades"] < 10:
        return -999.0
    ret = stats["total_return_pct"]
    dd = abs(stats["max_drawdown_pct"]) or 1.0
    return ret / dd  # getiri/düşüş oranı (yüksek = iyi)


def grid_search(df: pd.DataFrame, config: dict,
                strategy_type: str = "trend") -> list[dict]:
    """Bir stratejinin tüm parametre kombinasyonlarını tek veri diliminde dener."""
    grid = PARAM_GRIDS[strategy_type]
    keys = list(grid)
    results = []
    for values in itertools.product(*grid.values()):
        combo = dict(zip(keys, values))
        # trend için mantık kısıtı: hızlı EMA yavaştan büyük olamaz
        if strategy_type == "trend" and combo["ema_fast"] >= combo["ema_slow"]:
            continue
        combo["type"] = strategy_type
        cfg = _apply_params(config, combo)
        res = run_backtest(df, cfg)
        results.append({"params": combo, "score": _score(res.stats), "stats": res.stats})
    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def walk_forward(df: pd.DataFrame, config: dict, folds: int = 4,
                 strategy_type: str = "trend") -> dict:
    """Veriyi dilimlere bölüp her dilimde optimize-et-sonra-test-et uygular."""
    n = len(df)
    fold_size = n // (folds + 1)
    oos_results = []  # out-of-sample (görülmemiş) sonuçlar

    for i in range(folds):
        train_end = fold_size * (i + 1)
        test_end = fold_size * (i + 2)
        train = df.iloc[:train_end]
        test = df.iloc[train_end:test_end]
        if len(test) < 50:
            continue

        ranked = grid_search(train, config, strategy_type)
        if not ranked:
            continue
        # 1) Sadece geçmiş (train) üzerinde en iyi parametreyi bul
        best = ranked[0]
        # 2) O parametreyi HİÇ GÖRMEDİĞİ test diliminde dene
        cfg = _apply_params(config, best["params"])
        oos = run_backtest(test, cfg)
        oos_results.append({
            "fold": i + 1,
            "best_params": best["params"],
            "oos_return_pct": oos.stats["total_return_pct"],
            "oos_drawdown_pct": oos.stats["max_drawdown_pct"],
            "oos_trades": oos.stats["trades"],
        })

    total_oos = sum(r["oos_return_pct"] for r in oos_results)
    total_trades = sum(r["oos_trades"] for r in oos_results)
    return {
        "folds": oos_results,
        "total_oos_return_pct": round(total_oos, 2),
        "total_oos_trades": total_trades,
        "strategy_type": strategy_type,
    }
