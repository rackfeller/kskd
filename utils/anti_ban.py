"""Защита от бана: задержки, FloodWait, лимиты, аварийный стоп.

ВСЕ исходящие сообщения бота должны идти через AntiBan.safe_send().
"""
import asyncio
import logging
import random
import time
from collections import deque

from telethon.errors import FloodWaitError

import config

log = logging.getLogger("anti_ban")


class AntiBan:
    def __init__(self, client, db, me_id: int):
        self.client = client
        self.db = db
        self.me_id = me_id

        # Флаги состояния (сохраняются в БД)
        self.autoreply_enabled = True
        self.paused = False
        self.panic = False

        self.sending_chats: set = set()     # чаты, в которые бот отправляет прямо сейчас
        self.bot_ids: set[int] = set()      # id сообщений, отправленных ботом
        self._auto_times: deque[float] = deque()   # время автоответов за последний час
        self._lock = asyncio.Lock()         # автоответы уходят строго по одному
        self._flood_notified = 0.0

    # ---------- состояние ----------
    async def load(self):
        self.autoreply_enabled = (await self.db.get_state("autoreply", "1")) == "1"
        self.paused = (await self.db.get_state("paused", "0")) == "1"
        self.panic = (await self.db.get_state("panic", "0")) == "1"
        stamps = await self.db.auto_timestamps_since(time.time() - 3600)
        self._auto_times = deque(sorted(stamps))

    async def set_autoreply(self, value: bool):
        self.autoreply_enabled = value
        await self.db.set_state("autoreply", "1" if value else "0")

    async def set_paused(self, value: bool):
        self.paused = value
        await self.db.set_state("paused", "1" if value else "0")

    async def set_panic(self, value: bool):
        self.panic = value
        await self.db.set_state("panic", "1" if value else "0")

    @property
    def autoreply_active(self) -> bool:
        return self.autoreply_enabled and not self.paused and not self.panic

    # ---------- лимиты ----------
    def auto_last_hour(self) -> int:
        cutoff = time.time() - 3600
        while self._auto_times and self._auto_times[0] < cutoff:
            self._auto_times.popleft()
        return len(self._auto_times)

    async def can_auto_send(self) -> tuple[bool, str]:
        if self.panic:
            return False, "аварийный стоп (/panic)"
        floods = await self.db.flood_count_24h()
        if floods >= config.FLOOD_LIMIT_24H:
            return False, f"{floods} FloodWait за 24ч — отправка заблокирована"
        if self.auto_last_hour() >= config.MAX_REPLIES_PER_HOUR:
            return False, f"достигнут лимит {config.MAX_REPLIES_PER_HOUR} автоответов/час"
        return True, ""

    def retry_delay(self) -> float:
        """Через сколько секунд имеет смысл повторить автоответ, если can_auto_send() сказал «нет»."""
        if self.auto_last_hour() >= config.MAX_REPLIES_PER_HOUR and self._auto_times:
            wait = self._auto_times[0] + 3600 - time.time()
            return max(5.0, wait) + random.uniform(5, 30)
        return 600.0   # блокировка по FloodWait: проверяем раз в 10 минут

    # ---------- отправка ----------
    async def safe_send(self, entity, text: str, *, auto: bool = False, force: bool = False,
                        reply_to: int | None = None):
        """Безопасная отправка сообщения.

        auto  — это автоответ: действуют лимит в час и блокировка по FloodWait.
        force — игнорировать только аварийный стоп (для служебных сообщений в «Избранное»).
        Возвращает отправленное сообщение или None, если отправка не состоялась.
        """
        if auto:
            async with self._lock:
                return await self._send(entity, text, auto=True, force=force, reply_to=reply_to)
        return await self._send(entity, text, auto=False, force=force, reply_to=reply_to)

    async def _send(self, entity, text, *, auto: bool, force: bool, reply_to=None):
        if self.panic and not force:
            log.warning("Отправка отменена: аварийный стоп")
            return None
        if auto:
            ok, reason = await self.can_auto_send()
            if not ok:
                log.warning("Автоответ пропущен: %s", reason)
                return None

        # Случайная задержка 1–3 сек перед каждой отправкой
        await asyncio.sleep(random.uniform(1, 3))
        if self.panic and not force:
            log.warning("Отправка отменена во время задержки: аварийный стоп")
            return None

        for attempt in (1, 2):
            try:
                self.sending_chats.add(entity)
                try:
                    msg = await self.client.send_message(entity, text, reply_to=reply_to)
                    self._remember(msg.id)
                finally:
                    self.sending_chats.discard(entity)
                if auto:
                    self._auto_times.append(time.time())
                return msg
            except FloodWaitError as e:
                await self.db.add_flood(e.seconds)
                count = await self.db.flood_count_24h()
                log.warning("FloodWait %s сек (за 24ч: %s/%s)", e.seconds, count,
                            config.FLOOD_LIMIT_24H)
                if count >= config.FLOOD_LIMIT_24H:
                    await self._notify_flood(count)
                    return None
                if attempt == 1 and not self.panic:
                    await asyncio.sleep(e.seconds * 1.3)
                    continue
                return None
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Ошибка при отправке сообщения")
                return None
        return None

    def _remember(self, msg_id: int):
        if len(self.bot_ids) > 2000:
            self.bot_ids.clear()
        self.bot_ids.add(msg_id)

    async def _notify_flood(self, count: int):
        """Один раз в сутки сообщаем в «Избранное», что отправка остановлена."""
        if time.time() - self._flood_notified < 86400:
            return
        self._flood_notified = time.time()
        try:
            msg = await self.client.send_message(
                "me",
                f"⚠️ Получено {count} FloodWait за 24 часа. Автоответы ОСТАНОВЛЕНЫ "
                f"автоматически и возобновятся, когда события старше 24 часов "
                f"(см. /status). Проверьте активность аккаунта.",
            )
            self._remember(msg.id)
        except Exception:
            log.exception("Не удалось отправить уведомление о FloodWait")
