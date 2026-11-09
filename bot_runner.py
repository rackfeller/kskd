"""Запуск userbot в фоновом потоке — для окна управления (gui.py).

Вся логика бота (handlers/, utils/) остаётся прежней. Здесь только:
  * BotRunner — старт / стоп / пауза / автоответчик / panic из другого потока;
  * функции db_* — быстрый доступ к SQLite (шаблоны, статистика), работают и когда бот остановлен.
"""
import asyncio
import logging
import sqlite3
import threading

from telethon import TelegramClient
from telethon.sessions import StringSession

import config
from database import SCHEMA, Database, _norm
from handlers import autoreply, commands, templates
from utils.anti_ban import AntiBan

log = logging.getLogger("userbot")

STOPPED, STARTING, RUNNING, STOPPING = "stopped", "starting", "running", "stopping"


class BotRunner:
    def __init__(self):
        self.state = STOPPED
        self.error = ""          # последняя причина, почему бот не запустился / упал
        self.me_name = ""
        self.ab: AntiBan | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    # ---------- управление жизненным циклом ----------
    def start(self) -> list[str]:
        """Запускает бота. Возвращает список ошибок конфигурации (пусто = запущено)."""
        if self.state != STOPPED:
            return []
        errors = config.validate()
        if errors:
            return errors
        self.error = ""
        self.state = STARTING
        self._thread = threading.Thread(target=self._thread_main, name="userbot", daemon=True)
        self._thread.start()
        return []

    def stop(self):
        """Корректная остановка (отмена запланированных ответов + отключение)."""
        async def job(ab):
            autoreply.cancel_all_pending()
            await ab.client.disconnect()

        if self._submit(job):
            self.state = STOPPING

    def wait_stopped(self, timeout: float = 8.0):
        t = self._thread
        if t and t.is_alive():
            t.join(timeout)

    # ---------- команды боту (потокобезопасно) ----------
    def set_paused(self, value: bool):
        async def job(ab):
            await ab.set_paused(value)
            if not value:
                await autoreply.recover()      # разобрать задачи, накопившиеся за паузу
        self._submit(job)

    def set_autoreply(self, value: bool):
        async def job(ab):
            await ab.set_autoreply(value)
            if not value:
                autoreply.cancel_all_pending()
            else:
                await autoreply.recover()
        self._submit(job)

    def panic(self):
        """Аварийный стоп — то же, что команда /panic."""
        async def job(ab):
            log.critical("PANIC! Аварийный стоп и отключение от аккаунта.")
            autoreply.cancel_all_pending()
            await ab.set_panic(True)
            await ab.db.close_all_tasks("cancelled")
            await ab.safe_send("me", "🚨 PANIC: все отправки остановлены, бот отключается "
                                     "от аккаунта.", force=True)
            await ab.client.disconnect()
        if self._submit(job):
            self.state = STOPPING

    def pending_count(self) -> int:
        return db_open_tasks() if self.state == RUNNING else 0

    def _submit(self, make_job) -> bool:
        ab, loop = self.ab, self._loop
        if self.state != RUNNING or ab is None or loop is None:
            return False
        asyncio.run_coroutine_threadsafe(make_job(ab), loop)
        return True

    # ---------- внутренности ----------
    def _thread_main(self):
        try:
            asyncio.run(self._run())
        except Exception as e:
            log.exception("Критическая ошибка")
            self.error = f"Критическая ошибка: {e}"
        finally:
            self.ab = None
            self._loop = None
            self.state = STOPPED

    async def _run(self):
        self._loop = asyncio.get_running_loop()
        db = Database(config.DB_PATH)
        await db.connect()
        client = None
        try:
            try:
                client = TelegramClient(
                    StringSession(config.SESSION_STRING), config.API_ID, config.API_HASH
                )
            except ValueError:
                self.error = "SESSION_STRING недействительна — получите новую (session.bat)."
                log.error(self.error)
                return

            await client.connect()
            if not await client.is_user_authorized():
                self.error = "Сессия недействительна или завершена — получите новую (session.bat)."
                log.error(self.error)
                return

            me = await client.get_me()
            ab = AntiBan(client, db, me.id)
            await ab.load()
            if ab.panic:
                self.error = "Включён аварийный стоп. Нажмите «Сбросить аварийный стоп»."
                log.error(self.error)
                return

            n = await templates.sync_from_file(db, config.TEMPLATES_FILE)
            log.info("Шаблонов загружено из templates.txt: %s", n)

            autoreply.register(client, db, ab)
            commands.register(client, db, ab)
            autoreply.start_background()       # догнать пропущенное и восстановить задачи

            self.ab = ab
            self.me_name = me.first_name or me.username or str(me.id)
            self.state = RUNNING
            log.info("Userbot запущен как %s (id=%s)", self.me_name, me.id)
            await client.run_until_disconnected()
            log.info("Userbot остановлен")
        except Exception as e:
            log.exception("Критическая ошибка")
            self.error = f"Ошибка: {e}"
        finally:
            self.state = STOPPING
            autoreply.cancel_all_pending()
            if client is not None and client.is_connected():
                await client.disconnect()
            await db.close()


# ======================================================================
#  Синхронный доступ к базе (для окна). Работает независимо от бота.
# ======================================================================
def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(config.DB_PATH), timeout=5)
    c.executescript(SCHEMA)
    return c


def _query(sql: str, params=(), *, write: bool = False):
    c = _conn()
    try:
        cur = c.execute(sql, params)
        rows = cur.fetchall()
        if write:
            c.commit()
        return rows
    finally:
        c.close()


def db_get_state(key: str, default: str = "") -> str:
    rows = _query("SELECT value FROM state WHERE key=?", (key,))
    return rows[0][0] if rows else default


def db_open_tasks() -> int:
    """Сколько сообщений ждут ответа (открытые задачи)."""
    return _query("SELECT COUNT(*) FROM tasks WHERE status='open'")[0][0]


def db_reset_panic():
    _query("INSERT OR REPLACE INTO state (key, value) VALUES ('panic', '0')", write=True)


def db_stats() -> dict:
    import time
    q = lambda sql, p=(): _query(sql, p)[0][0]
    return {
        "in": q("SELECT COUNT(*) FROM messages WHERE direction='in'"),
        "auto": q("SELECT COUNT(*) FROM messages WHERE direction='auto'"),
        "hour": q("SELECT COUNT(*) FROM messages WHERE direction='auto' AND timestamp>=?",
                  (int(time.time()) - 3600,)),
        "floods": q("SELECT COUNT(*) FROM flood_events WHERE timestamp>=?",
                    (int(time.time()) - 86400,)),
        "templates": q("SELECT COUNT(*) FROM templates"),
    }


def db_last_autoreplies(n: int = 30):
    return _query(
        "SELECT timestamp, username, user_id, text FROM messages "
        "WHERE direction='auto' ORDER BY id DESC LIMIT ?", (n,)
    )


def db_templates():
    return _query("SELECT key, text FROM templates ORDER BY key")


def db_template_save(key: str, text: str):
    _query("INSERT OR REPLACE INTO templates (key, text) VALUES (?, ?)",
           (_norm(key), text), write=True)


def db_template_delete(key: str):
    _query("DELETE FROM templates WHERE key=?", (_norm(key),), write=True)


def db_templates_reload_from_file() -> int:
    """Перечитывает templates.txt (то же, что /reload)."""
    c = _conn()
    c.close()

    async def go():
        db = Database(config.DB_PATH)
        await db.connect()
        try:
            return await templates.sync_from_file(db, config.TEMPLATES_FILE)
        finally:
            await db.close()

    return asyncio.run(go())
