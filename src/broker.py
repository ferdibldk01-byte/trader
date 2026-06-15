"""Emir gönderme katmanı.

ÜÇ KATMANLI GÜVENLİK:
  1. DRY-RUN  : Hiçbir gerçek emir gitmez, sadece ekrana yazılır (varsayılan).
  2. TESTNET  : Binance testnet'inde SAHTE parayla gerçek emir.
  3. LIVE     : GERÇEK para. Sadece .env'de iki bayrak da açıksa devreye girer.

Para çekme (withdraw) fonksiyonu bilerek YOKTUR. Bot paranızı çekemez.
"""
from __future__ import annotations

import logging

import ccxt

from .config import Secrets

log = logging.getLogger("broker")


class Broker:
    def __init__(self, secrets: Secrets, market_type: str = "spot"):
        self.secrets = secrets
        self.market_type = market_type
        self.dry_run = not secrets.is_live_armed
        self.exchange: ccxt.binance | None = None

        if not self.dry_run:
            self.exchange = ccxt.binance({
                "apiKey": secrets.api_key,
                "secret": secrets.api_secret,
                "enableRateLimit": True,
                "options": {
                    "defaultType": "future" if market_type == "futures" else "spot"
                },
            })
            if secrets.use_testnet:
                self.exchange.set_sandbox_mode(True)
                log.warning("TESTNET modu: sahte parayla gerçek emirler.")
            else:
                log.warning("!!! CANLI mod: GERÇEK PARA kullanılıyor !!!")

    @property
    def mode(self) -> str:
        if self.dry_run:
            return "DRY-RUN (emir gönderilmez)"
        return "TESTNET" if self.secrets.use_testnet else "LIVE (gerçek para)"

    def get_balance(self, asset: str = "USDT") -> float:
        if self.dry_run or self.exchange is None:
            return 0.0
        bal = self.exchange.fetch_balance()
        return float(bal["total"].get(asset, 0.0))

    def market_buy(self, symbol: str, quantity: float) -> dict:
        if self.dry_run:
            log.info("[DRY-RUN] ALIM: %s adet %s", quantity, symbol)
            return {"dry_run": True, "side": "buy", "symbol": symbol, "qty": quantity}
        assert self.exchange is not None
        log.info("ALIM gönderiliyor: %s adet %s", quantity, symbol)
        return self.exchange.create_market_buy_order(symbol, quantity)

    def market_sell(self, symbol: str, quantity: float) -> dict:
        if self.dry_run:
            log.info("[DRY-RUN] SATIM: %s adet %s", quantity, symbol)
            return {"dry_run": True, "side": "sell", "symbol": symbol, "qty": quantity}
        assert self.exchange is not None
        log.info("SATIM gönderiliyor: %s adet %s", quantity, symbol)
        return self.exchange.create_market_sell_order(symbol, quantity)
