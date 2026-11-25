"""One-time login of the READ-ONLY Telegram account.

Run it yourself in the terminal:   python3 login.py
Telegram will send you a code; type it here (and your 2-step password if you have one).
Nothing is sent to anyone: the code stays between you and Telegram.
"""
import asyncio
import os

from bot.config import load_settings
from bot.telegram_source import make_client


async def main() -> None:
    settings = load_settings()
    if not settings.api_id or not settings.api_hash:
        raise SystemExit("حط TELEGRAM_API_ID و TELEGRAM_API_HASH في ملف .env أولاً")
    client = make_client(settings.data_dir, settings.api_id, settings.api_hash)
    await client.start()  # asks for phone number, code and password in this terminal
    me = await client.get_me()
    print(f"\n✅ تم ربط حساب القراءة: {me.first_name} (@{me.username or '-'})")
    await client.disconnect()
    session = settings.data_dir / "reader.session"
    if session.exists():
        os.chmod(session, 0o600)  # only your Mac user can read this file
    print("ملف الدخول محفوظ في data/reader.session — ما تعطيه لحتى واحد.")


if __name__ == "__main__":
    asyncio.run(main())
