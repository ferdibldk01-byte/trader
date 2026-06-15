"""Telegram bildirim gönderici.

Bot bir sinyal bulunca Telegram'dan mesaj atar. Ekstra kütüphane gerekmez
(Python'un kendi urllib'i kullanılır).

KURULUM (bir kez):
  1. Telegram'da @BotFather ile konuşup /newbot yazın, bir bot oluşturun.
     Size bir TOKEN verir (örn: 123456:ABC-DEF...).
  2. Oluşturduğunuz bota Telegram'dan bir "merhaba" mesajı atın.
  3. chat id'nizi öğrenmek için:  python -m src.notifier
  4. Token ve chat id'yi .env dosyasına yazın:
       TELEGRAM_BOT_TOKEN=...
       TELEGRAM_CHAT_ID=...
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from .config import load_secrets  # noqa: F401  (.env'i yüklemek için)

API = "https://api.telegram.org/bot{token}/{method}"


def _token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def _chat_id() -> str:
    return os.getenv("TELEGRAM_CHAT_ID", "").strip()


def is_configured() -> bool:
    return bool(_token() and _chat_id())


def send_message(text: str) -> bool:
    """Telegram'a bir mesaj gönderir. Ayar yoksa sessizce False döner."""
    token, chat_id = _token(), _chat_id()
    if not token or not chat_id:
        return False
    url = API.format(token=token, method="sendMessage")
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:  # ağ hatası botu durdurmasın
        print(f"[Telegram] gönderilemedi: {e}")
        return False


def discover_chat_id() -> None:
    """Bota yazdığınız mesajlardan chat id'nizi bulup ekrana yazar."""
    token = _token()
    if not token:
        print("Önce .env içine TELEGRAM_BOT_TOKEN yazın.")
        return
    url = API.format(token=token, method="getUpdates")
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read())
    updates = data.get("result", [])
    if not updates:
        print("Mesaj bulunamadı. Önce Telegram'dan botunuza bir mesaj atın, sonra tekrar deneyin.")
        return
    seen = {}
    for u in updates:
        msg = u.get("message") or u.get("edited_message") or {}
        chat = msg.get("chat", {})
        if chat.get("id"):
            seen[chat["id"]] = chat.get("first_name") or chat.get("title") or "?"
    print("Bulunan chat id'ler:")
    for cid, name in seen.items():
        print(f"  chat_id = {cid}   ({name})")
    print("\nBunu .env dosyasına yazın:  TELEGRAM_CHAT_ID=<yukarıdaki id>")


if __name__ == "__main__":
    discover_chat_id()
