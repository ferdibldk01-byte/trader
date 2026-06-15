"""Olay-tabanlı backtest motoru.

Stratejiyi geçmiş veri üzerinde mum-mum çalıştırır ve gerçekçi sonuç verir:
  - İşleme bir sonraki mumun açılışında girer (geleceği görme/lookahead yok)
  - Stop-loss ve take-profit'i mumun high/low'una göre kontrol eder
  - Her alım-satımda komisyon keser
  - Sonuçta: toplam getiri, max düşüş (drawdown), kazanma oranı vb.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .risk import build_long_plan
from .strategy import generate_signals


@dataclass
class Trade:
    entry_time: pd.Timestamp
    entry: float
    exit_time: pd.Timestamp | None = None
    exit: float | None = None
    quantity: float = 0.0
    pnl: float = 0.0
    reason: str = ""


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: pd.Series
    final_balance: float
    initial_balance: float
    stats: dict = field(default_factory=dict)


def run_backtest(df: pd.DataFrame, config: dict) -> BacktestResult:
    risk_cfg = config["risk"]
    bt_cfg = config["backtest"]
    fee = bt_cfg["fee_pct"] / 100.0

    sig = generate_signals(df, config["strategy"]).dropna()

    balance = float(bt_cfg["initial_balance"])
    initial = balance
    position: dict | None = None
    trades: list[Trade] = []
    equity_times: list[pd.Timestamp] = []
    equity_values: list[float] = []

    rows = sig.itertuples()
    prev = None
    for row in rows:
        price = row.close

        # --- Açık pozisyon varsa: stop / hedef / çıkış sinyali kontrol et ---
        if position is not None:
            hit_stop = row.low <= position["stop"]
            hit_tp = row.high >= position["take_profit"]
            exit_price = None
            reason = ""

            # Aynı mumda ikisi de değerse, kötümser ol: önce stop varsay
            if hit_stop:
                exit_price, reason = position["stop"], "stop"
            elif hit_tp:
                exit_price, reason = position["take_profit"], "take_profit"
            elif row.exit_signal:
                exit_price, reason = price, "trend_exit"

            if exit_price is not None:
                gross = position["quantity"] * exit_price
                gross_cost = position["quantity"] * position["entry"]
                pnl = (gross - gross_cost) - (gross + gross_cost) * fee
                balance += pnl
                trades.append(Trade(
                    entry_time=position["entry_time"], entry=position["entry"],
                    exit_time=row.Index, exit=exit_price,
                    quantity=position["quantity"], pnl=pnl, reason=reason,
                ))
                position = None

        # --- Pozisyon yoksa ve önceki mumda giriş sinyali varsa: gir ---
        if position is None and prev is not None and prev.long_entry:
            entry_price = row.open  # sinyal sonrası mumun açılışından gir
            plan = build_long_plan(balance, entry_price, prev.atr, risk_cfg)
            if plan is not None and plan.quantity * entry_price <= balance:
                position = {
                    "entry_time": row.Index, "entry": entry_price,
                    "stop": plan.stop, "take_profit": plan.take_profit,
                    "quantity": plan.quantity,
                }

        # --- Equity (anlık varlık değeri) kaydı ---
        if position is not None:
            unreal = position["quantity"] * (price - position["entry"])
            equity_values.append(balance + unreal)
        else:
            equity_values.append(balance)
        equity_times.append(row.Index)
        prev = row

    equity = pd.Series(equity_values, index=equity_times)
    stats = _compute_stats(trades, equity, initial, balance)
    return BacktestResult(trades, equity, balance, initial, stats)


def _compute_stats(trades, equity, initial, final) -> dict:
    n = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]

    roll_max = equity.cummax()
    drawdown = (equity - roll_max) / roll_max
    max_dd = drawdown.min() if len(drawdown) else 0.0

    rets = equity.pct_change().dropna()
    sharpe = (rets.mean() / rets.std() * np.sqrt(365)) if rets.std() else 0.0

    gross_win = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    profit_factor = (gross_win / gross_loss) if gross_loss else float("inf")

    return {
        "trades": n,
        "win_rate_pct": round(100 * len(wins) / n, 1) if n else 0.0,
        "total_return_pct": round(100 * (final - initial) / initial, 2),
        "max_drawdown_pct": round(100 * max_dd, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else None,
        "sharpe": round(sharpe, 2),
        "final_balance": round(final, 2),
    }
