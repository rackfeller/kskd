"""Точка входа userbot. Запуск: python main.py  (после аварийного стопа: python main.py --reset-panic)"""
import asyncio
import logging
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession

import config
from database import Database
from handlers import autoreply, commands, templates
from utils.anti_ban import AntiBan
from utils.logger import setup_logger


async def main() -> int:
    setup_logger()
    log = logging.getLogger("userbot")

    errors = config.validate()
    if errors:
        for e in errors:
            log.error(e)
        return 1

    db = Database(config.DB_PATH)
    await db.connect()
    client = None
    try:
        if "--reset-panic" in sys.argv:
            await db.set_state("panic", "0")
            log.info("Флаг аварийного стопа сброшен")

        try:
            client = TelegramClient(
                StringSession(config.SESSION_STRING), config.API_ID, config.API_HASH
            )
        except ValueError:
            log.error("SESSION_STRING недействительна. Сгенерируйте заново (README, ШАГ 5).")
            return 1

        await client.connect()
        if not await client.is_user_authorized():
            log.error("SESSION_STRING недействительна или сессия завершена. "
                      "Сгенерируйте заново (README, ШАГ 5).")
            return 1

        me = await client.get_me()
        ab = AntiBan(client, db, me.id)
        await ab.load()
        if ab.panic:
            log.error("Включён аварийный стоп (/panic). Запустите: python main.py --reset-panic")
            return 1

        n = await templates.sync_from_file(db, config.TEMPLATES_FILE)
        log.info("Шаблонов загружено из templates.txt: %s", n)

        autoreply.register(client, db, ab)
        commands.register(client, db, ab)
        autoreply.start_background()   # догнать пропущенное и восстановить задачи

        log.info("Userbot запущен как %s (id=%s). Команды — в «Избранное», /help",
                 me.first_name, me.id)
        await client.run_until_disconnected()
        log.info("Userbot остановлен")
        return 0
    except Exception:
        log.exception("Критическая ошибка")
        return 1
    finally:
        if client is not None and client.is_connected():
            await client.disconnect()
        await db.close()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nОстановлено (Ctrl+C)")
