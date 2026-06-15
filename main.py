"""Trading bot komut satırı arayüzü.

Kullanım:
  python main.py fetch              # Geçmiş veriyi indir
  python main.py backtest           # Stratejiyi geçmiş veride test et
  python main.py paper              # Canlı döngü (varsayılan: DRY-RUN, emir göndermez)

Gerçek emir göndermek için .env'de USE_TESTNET / LIVE_TRADING ayarlarını
değiştirin. Bot varsayılan olarak güvenlidir: hiçbir gerçek emir göndermez.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

# Windows konsolunda Türkçe karakter ve emoji'lerin düzgün görünmesi için
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from src.backtest import run_backtest
from src.broker import Broker
from src.config import load_config, load_secrets
from src.data import fetch_ohlcv, load_csv, save_csv
from src.dca import DCAConfig, backtest_dca
from src import notifier
from src.optimize import grid_search, walk_forward
from src.risk import build_long_plan
from src.strategy import generate_signals

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


def cmd_fetch(config: dict) -> None:
    for symbol in config["symbols"]:
        log.info("Veri indiriliyor: %s ...", symbol)
        df = fetch_ohlcv(
            symbol,
            timeframe=config["timeframe"],
            days=config["backtest"]["history_days"],
            market_type=config["market_type"],
        )
        path = save_csv(df, symbol, config["timeframe"])
        log.info("  -> %d mum kaydedildi: %s", len(df), path)


def cmd_backtest(config: dict) -> None:
    print("\n" + "=" * 64)
    print(f"  BACKTEST  |  strateji: rejim-farkında trend takibi")
    print(f"  zaman dilimi: {config['timeframe']}  |  risk/işlem: "
          f"%{config['risk']['risk_per_trade_pct']}")
    print("=" * 64)
    for symbol in config["symbols"]:
        try:
            df = load_csv(symbol, config["timeframe"])
        except FileNotFoundError:
            log.warning("%s için veri yok. Önce 'python main.py fetch' çalıştırın.", symbol)
            continue
        result = run_backtest(df, config)
        s = result.stats
        print(f"\n  {symbol}")
        print(f"    İşlem sayısı     : {s['trades']}")
        print(f"    Kazanma oranı    : %{s['win_rate_pct']}")
        print(f"    Toplam getiri    : %{s['total_return_pct']}")
        print(f"    Max düşüş (DD)   : %{s['max_drawdown_pct']}")
        print(f"    Profit factor    : {s['profit_factor']}")
        print(f"    Sharpe           : {s['sharpe']}")
        print(f"    Son bakiye       : {s['final_balance']} USDT "
              f"(başlangıç {result.initial_balance})")
    print("\n" + "=" * 64)
    print("  NOT: Geçmiş performans geleceği garanti ETMEZ. Bu sadece bir testtir.")
    print("=" * 64 + "\n")


def cmd_dca(config: dict) -> None:
    d = config["dca"]
    cfg = DCAConfig(
        base_order=d["base_order"], safety_order=d["safety_order"],
        max_safety_orders=d["max_safety_orders"],
        price_deviation_pct=d["price_deviation_pct"],
        safety_step_scale=d["safety_step_scale"],
        safety_volume_scale=d["safety_volume_scale"],
        take_profit_pct=d["take_profit_pct"],
        use_trend_filter=d.get("use_trend_filter", True),
        trend_ema_period=d.get("trend_ema_period", 200),
        stop_loss_pct=d.get("stop_loss_pct", 0.0),
        fee_pct=config["backtest"]["fee_pct"],
    )
    print("\n" + "=" * 64)
    print("  DCA BOT BACKTEST (3Commas tarzı)")
    print(f"  base {cfg.base_order} | safety {cfg.safety_order} x{cfg.max_safety_orders}"
          f" | TP %{cfg.take_profit_pct} | sapma %{cfg.price_deviation_pct}")
    print("=" * 64)
    for symbol in config["symbols"]:
        try:
            df = load_csv(symbol, config["timeframe"])
        except FileNotFoundError:
            log.warning("%s için veri yok. Önce 'python main.py fetch' çalıştırın.", symbol)
            continue
        s = backtest_dca(df, cfg, config["backtest"]["initial_balance"])
        print(f"\n  {symbol}")
        print(f"    Tamamlanan deal      : {s['completed_deals']}")
        print(f"    Toplam getiri        : %{s['total_return_pct']}")
        print(f"    Ort. safety order    : {s['avg_safety_orders_used']}")
        print(f"    En kötü deal kağıt-DD: %{s['worst_deal_paper_drawdown_pct']}")
        print(f"    Sonda açık deal var mı: {'EVET' if s['open_deal_at_end'] else 'hayır'}"
              + (f"  (kağıt zarar %{s['open_deal_paper_drawdown_pct']})"
                 if s['open_deal_at_end'] else ""))
        print(f"    Son varlık           : {s['final_equity']} USDT")
    print("\n" + "=" * 64)
    print("  DİKKAT: 'açık deal' + büyük kağıt-zarar = para dipte kilitlenmiş.")
    print("  DCA sert düşüşte risklidir; safety order sayısı/sermaye önemli.")
    print("=" * 64 + "\n")


def _buy_and_hold_return(df, fee_pct: float) -> float:
    """Al-tut kıyas ölçütü: ilk mumda al, son mumda sat (komisyon dahil)."""
    first = df["close"].iloc[0]
    last = df["close"].iloc[-1]
    gross = (last / first) - 1.0
    return round(100 * (gross - 2 * fee_pct / 100.0), 2)


def cmd_compare(config: dict) -> None:
    """Tüm stratejileri AYNI veride yarıştırır ve al-tut ile karşılaştırır.

    Dürüstlük kuralı: hiçbir strateji al-tut'u geçemiyorsa, bunu açıkça yazar.
    """
    strategies = ["trend", "meanrev", "breakout"]
    print("\n" + "=" * 72)
    print("  STRATEJİ YARIŞMASI  (hepsi aynı veride, komisyon dahil)")
    print("  Kıyas ölçütü: AL-TUT (sadece alıp beklemek)")
    print("=" * 72)

    for symbol in config["symbols"]:
        try:
            df = load_csv(symbol, config["timeframe"])
        except FileNotFoundError:
            log.warning("%s için veri yok. Önce 'python main.py fetch' çalıştırın.", symbol)
            continue

        bh = _buy_and_hold_return(df, config["backtest"]["fee_pct"])
        print(f"\n  {symbol}   (al-tut: %{bh})")
        print(f"    {'strateji':<10} {'getiri':>9} {'işlem':>6} {'kazanma%':>9} "
              f"{'maxDD':>8} {'PF':>5}  al-tut'u geçti mi?")
        print("    " + "-" * 64)

        results = []
        for st in strategies:
            cfg = dict(config)
            cfg["strategy"] = dict(config["strategy"], type=st)
            res = run_backtest(df, cfg)
            s = res.stats
            beat = "EVET ✓" if s["total_return_pct"] > bh else "hayır"
            pf = s["profit_factor"] if s["profit_factor"] is not None else "—"
            print(f"    {st:<10} {s['total_return_pct']:>8}% {s['trades']:>6} "
                  f"{s['win_rate_pct']:>8}% {s['max_drawdown_pct']:>7}% {str(pf):>5}  {beat}")
            results.append((st, s["total_return_pct"]))

        best = max(results, key=lambda x: x[1])
        if best[1] <= bh:
            print(f"    >> SONUÇ: Hiçbir strateji al-tut'u (%{bh}) geçemedi. "
                  f"En iyisi '{best[0]}' %{best[1]}.")
        else:
            print(f"    >> SONUÇ: '{best[0]}' al-tut'u geçti (%{best[1]} > %{bh}). "
                  f"Ama walk-forward ile doğrulamadan güvenme.")

    print("\n" + "=" * 72)
    print("  UYARI: Buradaki getiri GEÇMİŞTEKİ en iyi haldir. Gerçek/gelecek")
    print("  performans için 'optimize' (walk-forward) sonucuna bak. Geçmişte")
    print("  iyi = gelecekte iyi DEĞİLDİR. Al-tut'u geçemeyen bot, bot değildir.")
    print("=" * 72 + "\n")


def cmd_optimize(config: dict) -> None:
    strategies = ["trend", "meanrev", "breakout"]
    print("\n" + "=" * 72)
    print("  OPTIMIZASYON + WALK-FORWARD DOĞRULAMA (3 strateji)")
    print("  (parametreler sadece geçmişte aranır, GÖRÜLMEMİŞ veride test edilir)")
    print("=" * 72)
    for symbol in config["symbols"]:
        try:
            df = load_csv(symbol, config["timeframe"])
        except FileNotFoundError:
            log.warning("%s için veri yok. Önce 'python main.py fetch' çalıştırın.", symbol)
            continue
        print(f"\n  ### {symbol} ###")
        summary = []
        for st in strategies:
            log.info("%s / %s optimize ediliyor...", symbol, st)
            wf = walk_forward(df, config, strategy_type=st)
            ret = wf["total_oos_return_pct"]
            trades = wf["total_oos_trades"]
            summary.append((st, ret, trades))
            verdict = "✓ pozitif" if ret > 0 else "✗ negatif"
            sample = "  (ÇOK AZ işlem!)" if trades < 20 else ""
            print(f"\n  [{st}] görülmemiş-veri getirisi: %{ret}  | "
                  f"toplam {trades} işlem{sample}  -> {verdict}")
            for f in wf["folds"]:
                print(f"      Dilim {f['fold']}: getiri %{f['oos_return_pct']:>6}  "
                      f"düşüş %{f['oos_drawdown_pct']:>6}  işlem {f['oos_trades']:>2}")
        best = max(summary, key=lambda x: x[1])
        print(f"\n  >> {symbol} en iyi (görülmemiş veride): '{best[0]}' "
              f"%{best[1]} ({best[2]} işlem)")
    print("\n" + "=" * 72)
    print("  DÜRÜSTLÜK NOTU:")
    print("  - Pozitif + YETERLİ işlem (>20) = strateji gerçek bir kenara sahip OLABİLİR.")
    print("  - Pozitif ama az işlem = ŞANS olabilir, güvenme.")
    print("  - Negatif = parametre değil, strateji bu coin/dönem için yanlış.")
    print("=" * 72 + "\n")


def _analyze_symbol(symbol: str, config: dict) -> dict:
    """Bir coin için en son mumun durumunu ve sinyali hesaplar."""
    df = fetch_ohlcv(symbol, config["timeframe"], days=60,
                     market_type=config["market_type"])
    sig = generate_signals(df, config["strategy"]).dropna()
    last = sig.iloc[-1]
    if last["long_entry"]:
        action, emoji = "AL", "🟢"
    elif last["exit_signal"]:
        action, emoji = "SAT / UZAK DUR", "🔴"
    else:
        action, emoji = "BEKLE", "⚪"
    trend = "yukarı" if last["ema_fast"] > last["ema_slow"] else "aşağı"
    return {
        "symbol": symbol, "action": action, "emoji": emoji,
        "price": last["close"], "rsi": last["rsi"], "adx": last["adx"],
        "trend": trend,
    }


def cmd_signals(config: dict) -> None:
    """Tüm coinleri tarar, sinyalleri ekrana yazar VE Telegram'a gönderir."""
    lines = ["📊 <b>Trader Bot Sinyalleri</b>",
             f"<i>{config['timeframe']} | trend takibi</i>", ""]
    ok_count = 0
    errors: list[str] = []
    for symbol in config["symbols"]:
        try:
            a = _analyze_symbol(symbol, config)
        except Exception as e:
            log.warning("%s analiz edilemedi: %s", symbol, e)
            errors.append(f"{symbol}: {type(e).__name__}: {e}")
            continue
        ok_count += 1
        line = (f"{a['emoji']} <b>{a['symbol']}</b> → {a['action']}\n"
                f"   fiyat {a['price']:.4f} | RSI {a['rsi']:.0f} | "
                f"ADX {a['adx']:.0f} | trend {a['trend']}")
        lines.append(line)
        print(f"{a['emoji']} {a['symbol']:12} {a['action']:14} "
              f"fiyat={a['price']:.4f} RSI={a['rsi']:.0f} ADX={a['adx']:.0f}")

    # Hiç coin işlenemediyse: sessiz kalma, gerçek hatayı bildir (teşhis için)
    if ok_count == 0:
        diag = errors[0] if errors else "bilinmeyen sebep"
        lines.append("⚠️ <b>Hiçbir coin verisi çekilemedi.</b>")
        lines.append(f"Sebep: <code>{diag}</code>")
        print("HATA TEŞHİSİ — ilk hata:", diag)

    message = "\n".join(lines)
    if notifier.is_configured():
        ok = notifier.send_message(message)
        log.info("Telegram'a gönderildi." if ok else "Telegram gönderilemedi.")
    else:
        log.warning("Telegram ayarlı değil. .env içine TELEGRAM_BOT_TOKEN ve "
                    "TELEGRAM_CHAT_ID ekleyin. (chat id için: python -m src.notifier)")


def cmd_signals_loop(config: dict, every_minutes: int = 30) -> None:
    """Sinyalleri belirli aralıkla (varsayılan 30 dk) sürekli üretip gönderir."""
    log.info("Sürekli sinyal modu: her %d dakikada bir. Durdurmak için Ctrl+C.",
             every_minutes)
    if not notifier.is_configured():
        log.warning("Telegram AYARLI DEĞİL — sinyaller sadece ekrana yazılacak. "
                    "Telefona göndermek için .env'e Telegram bilgilerini ekleyin.")
    try:
        while True:
            log.info("--- Tarama başladı (%s) ---",
                     time.strftime("%Y-%m-%d %H:%M:%S"))
            try:
                cmd_signals(config)
            except Exception as e:
                log.warning("Tarama hatası: %s", e)
            log.info("Sonraki tarama %d dk sonra. (Ctrl+C ile çık)", every_minutes)
            time.sleep(every_minutes * 60)
    except KeyboardInterrupt:
        log.info("Sürekli sinyal modu durduruldu.")


def cmd_paper(config: dict, poll_seconds: int = 60) -> None:
    secrets = load_secrets()
    broker = Broker(secrets, market_type=config["market_type"])
    log.info("Çalışma modu: %s", broker.mode)
    if broker.dry_run:
        log.info("Güvenli mod: emirler sadece ekrana yazılır, gerçekte gönderilmez.")

    open_positions: dict[str, dict] = {}
    log.info("Canlı döngü başladı. Durdurmak için Ctrl+C.")
    try:
        while True:
            for symbol in config["symbols"]:
                df = fetch_ohlcv(symbol, config["timeframe"], days=60,
                                 market_type=config["market_type"])
                sig = generate_signals(df, config["strategy"]).dropna()
                if sig.empty:
                    continue
                last = sig.iloc[-1]
                price = last["close"]

                pos = open_positions.get(symbol)
                if pos:  # açık pozisyon yönetimi
                    if price <= pos["stop"] or price >= pos["take_profit"] or last["exit_signal"]:
                        broker.market_sell(symbol, pos["quantity"])
                        log.info("%s pozisyon kapatıldı @ %.4f", symbol, price)
                        notifier.send_message(
                            f"🔴 <b>SAT</b> {symbol} @ {price:.4f}  ({broker.mode})")
                        open_positions.pop(symbol)
                    continue

                # yeni giriş
                if last["long_entry"] and len(open_positions) < config["risk"]["max_open_positions"]:
                    bal = config["backtest"]["initial_balance"]  # dry-run referans bakiye
                    plan = build_long_plan(bal, price, last["atr"], config["risk"])
                    if plan:
                        broker.market_buy(symbol, plan.quantity)
                        open_positions[symbol] = {
                            "stop": plan.stop, "take_profit": plan.take_profit,
                            "quantity": plan.quantity,
                        }
                        log.info("%s ALINDI @ %.4f | stop %.4f | hedef %.4f",
                                 symbol, price, plan.stop, plan.take_profit)
                        notifier.send_message(
                            f"🟢 <b>AL</b> {symbol} @ {price:.4f}\n"
                            f"stop {plan.stop:.4f} | hedef {plan.take_profit:.4f}  "
                            f"({broker.mode})")
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        log.info("Döngü durduruldu.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Binance trading bot")
    parser.add_argument(
        "command",
        choices=["fetch", "backtest", "compare", "optimize", "dca", "signals", "paper"],
    )
    parser.add_argument(
        "--every", type=int, default=0, metavar="DAKIKA",
        help="signals komutunu her N dakikada bir tekrarla (örn: --every 30)",
    )
    args = parser.parse_args()

    config = load_config()

    if args.command == "signals" and args.every > 0:
        cmd_signals_loop(config, every_minutes=args.every)
        return

    {
        "fetch": cmd_fetch,
        "backtest": cmd_backtest,
        "compare": cmd_compare,
        "optimize": cmd_optimize,
        "dca": cmd_dca,
        "signals": cmd_signals,
        "paper": cmd_paper,
    }[args.command](config)


if __name__ == "__main__":
    main()
