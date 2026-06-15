"""Ayarları ve ortam değişkenlerini (API anahtarları) yükler."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

# .env dosyasındaki gizli bilgileri yükle (varsa)
load_dotenv(ROOT / ".env")


def load_config(path: str | Path = ROOT / "config.yaml") -> dict:
    """config.yaml dosyasını okuyup sözlük olarak döndürür."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass
class Secrets:
    """API anahtarları ve güvenlik bayrakları (.env'den okunur)."""
    api_key: str
    api_secret: str
    use_testnet: bool
    live_trading: bool
    understand_risk: bool

    @property
    def is_live_armed(self) -> bool:
        """Gerçek emir gönderilmesine izin var mı? (çift onay gerekli)"""
        return self.live_trading and self.understand_risk


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "evet", "on"}


def load_secrets() -> Secrets:
    return Secrets(
        api_key=os.getenv("BINANCE_API_KEY", ""),
        api_secret=os.getenv("BINANCE_API_SECRET", ""),
        use_testnet=_as_bool(os.getenv("USE_TESTNET"), default=True),
        live_trading=_as_bool(os.getenv("LIVE_TRADING"), default=False),
        understand_risk=_as_bool(os.getenv("I_UNDERSTAND_THE_RISK"), default=False),
    )
