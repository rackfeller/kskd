"""Генерация StringSession. Запуск: python utils/session_gen.py

Нужны API_ID, API_HASH и PHONE в файле .env. Telegram пришлёт код в приложение.
"""
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


async def main():
    try:
        api_id = int(os.getenv("API_ID", "0").strip() or 0)
    except ValueError:
        api_id = 0
    api_hash = os.getenv("API_HASH", "").strip()
    phone = os.getenv("PHONE", "").strip()

    if not api_id or not api_hash or not phone:
        print("Заполните API_ID, API_HASH и PHONE в файле .env и сохраните его.")
        return

    client = TelegramClient(StringSession(), api_id, api_hash)
    # start() сам запросит код из Telegram и пароль 2FA (если включён)
    await client.start(phone=phone)
    session_string = client.session.save()
    await client.disconnect()

    print("\n" + "=" * 60)
    print("ВАША SESSION_STRING (скопируйте ВСЮ строку в .env):\n")
    print(session_string)
    print("\n" + "=" * 60)
    print("Никому не показывайте эту строку — она даёт полный доступ к аккаунту!")


if __name__ == "__main__":
    asyncio.run(main())
