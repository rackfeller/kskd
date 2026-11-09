"""Автоответчик с очередью задач «нужно ответить».

Каждое входящее сообщение (в т.ч. пересланное, с медиа, ответ-цитата) сразу записывается в БД
как задача (таблица tasks). Задача закрывается только когда на неё реально ответили (или вы
ответили сами). Поэтому ответ не теряется при:
  * перезапуске бота и выключенном компьютере (задачи подхватываются при старте, плюс
    просматриваются непрочитанные чаты, пришедшие пока бот был выключен);
  * паузе (/pause) — задачи ждут, /resume их разбирает;
  * лимите автоответов в час и блокировке по FloodWait — ответ откладывается, а не пропускается;
  * сбое ИИ или отправки — несколько повторных попыток, затем уведомление в «Избранное».
"""
import asyncio
import logging
import random
import re
import time

from telethon import events

import config
from handlers import ai_reply, msgtext, repeat, style
from handlers.templates import render

log = logging.getLogger("autoreply")

DEBOUNCE = (3, 7)        # секунд ждём, пока собеседник допишет всё, что хотел
AI_ATTEMPTS = 3          # столько раз пробуем ИИ, потом — ответ по умолчанию
MAX_ATTEMPTS = 5         # столько неудачных отправок подряд, потом задача закрывается как failed
BAD_PHRASES = ["#", "Кто-нибудь знает", "As an AI", "На самом деле отвечу", "Извините"]

_ctx = None                                  # (client, db, ab) — задаётся в register()
_pending: dict[int, asyncio.Task] = {}       # чат -> запланированный разбор задач
_sending: set[int] = set()                   # чаты, в которые прямо сейчас идёт отправка
_bg: set[asyncio.Task] = set()


def cancel_all_pending():
    """Отменяет таймеры в памяти. Сами задачи в БД остаются открытыми."""
    for task in list(_pending.values()):
        task.cancel()
    _pending.clear()


def pending_count() -> int:
    return sum(1 for t in _pending.values() if not t.done())


def _allowed(sender) -> bool:
    ids = {str(sender.id), (getattr(sender, "username", None) or "").lower()} - {""}
    if ids & config.BLACKLIST:
        return False
    if config.WHITELIST and not (ids & config.WHITELIST):
        return False
    return True


async def _notify_owner(text: str):
    if _ctx is None:
        return
    try:
        await _ctx[2].safe_send("me", text[:4000], force=True)
    except Exception:
        log.exception("Не удалось отправить уведомление в «Избранное»")


async def _choose_reply(client, db, chat_id: int, plain: str, name: str, n_open: int):
    """Шаблон -> ИИ -> фраза по умолчанию. None = ИИ включён, но ответа не дал (нужен повтор)."""
    tpl = await db.find_template(plain) if plain else None
    if tpl:
        return render(tpl, name)
    if ai_reply.enabled():
        ai = await ai_reply.generate(client, chat_id, limit=max(6, n_open + 3))
        if ai and any(bad in ai for bad in BAD_PHRASES):
            log.warning("ИИ выдал бред, ответ отброшен")
            return None
        return style.humanize(ai, name) if ai else None
    return render(config.DEFAULT_REPLY, name)


async def _mark_read(client, chat_id: int, msg_id: int):
    try:
        if msg_id:
            await client.send_read_acknowledge(chat_id, max_id=msg_id)
    except Exception:
        pass


def _split_text(text: str) -> list[str]:
    """Умное разбиение текста на короткие сообщения."""
    # Если есть шаблонный разделитель '|', используем его
    if "|" in text:
        return [p.strip() for p in text.split("|") if p.strip()]
    
    # Если есть перенос строки, используем его
    if "\n" in text:
        return [p.strip() for p in text.split("\n") if p.strip()]
    
    # Иначе режем по точкам (если предложение длиннее 15 символов)
    # Регулярка ищет точку, вопросительный или восклицательный знак
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    
    parts = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        # Если предложение очень длинное, можно было бы резать по запятым, 
        # но для чата хватит и просто разделения по точкам
        parts.append(s)
        
    return parts


async def _send_messages(client, ab, db, chat_id: int, username: str, reply: str,
                         reply_to: int | None, upto: int) -> int:
    """Отправляет ответ частями. Задачи закрываются сразу после первой удачной части,
    чтобы при остановке посреди ответа он не ушёл повторно. Возвращает число отправленных частей."""
    parts = _split_text(reply)
    if not parts:
        parts = [reply.strip()]
    parts = [p for p in parts if p]

    sent = 0
    for i, part in enumerate(parts):
        if not config.LOWERCASE_REPLIES:
            part = part[0].upper() + part[1:]

        # Время "печатает" зависит от длины сообщения
        typing_time = min(8.0, max(1.5, len(part) * 0.05 + random.uniform(1, 2)))
        if i > 0:
            await asyncio.sleep(random.uniform(0.5, 1.5))

        async with client.action(chat_id, "typing"):
            await asyncio.sleep(typing_time)
            # первое сообщение — реплаем на (пересланное) сообщение собеседника
            msg = await ab.safe_send(chat_id, part, auto=True,
                                     reply_to=reply_to if i == 0 else None)
        if not msg:
            break
        sent += 1
        await db.log_message(chat_id, chat_id, username, part, "auto")
        if sent == 1:
            await db.close_tasks(chat_id, "done", upto)
        log.info("Автоответ (часть %d из %d) отправлен в чат %s", i + 1, len(parts), chat_id)
    return sent


async def _give_up(db, chat_id: int, why: str):
    tasks = await db.open_tasks(chat_id)
    await db.close_tasks(chat_id, "failed")
    if tasks:
        who = tasks[-1]["name"] or tasks[-1]["username"] or chat_id
        last = (tasks[-1]["body"] or "")[:150]
        await _notify_owner(
            f"⚠️ Не удалось ответить: {who} ({why}).\n"
            f"Сообщений без ответа: {len(tasks)}. Последнее: «{last}»\n"
            f"Ответьте сами."
        )


async def _fail(db, chat_id: int, why: str) -> float | None:
    """Неудачная попытка: возвращает через сколько повторить или None, если сдались."""
    n = await db.bump_attempts(chat_id)
    if n >= MAX_ATTEMPTS:
        log.error("Чат %s: %d неудачных попыток подряд — задача закрыта (%s)", chat_id, n, why)
        await _give_up(db, chat_id, why)
        return None
    return min(60.0 * n, 600.0)


async def _process(chat_id: int, delay: float) -> float | None:
    """Разбирает открытые задачи чата. Возвращает задержку до следующей попытки или None."""
    client, db, ab = _ctx
    await asyncio.sleep(delay)

    if not ab.autoreply_active:
        return None                      # пауза / выключено: задачи остаются, recover() подхватит

    tasks = await db.open_tasks(chat_id)
    if not tasks:
        return None
    last = tasks[-1]
    upto = last["msg_id"]
    name = last["name"] or "друг"

    ok, reason = await ab.can_auto_send()
    if not ok:
        if ab.panic:
            return None
        wait = ab.retry_delay()
        log.warning("Чат %s: %s — ответ отложен на %.0f мин (задача сохранена)",
                    chat_id, reason, wait / 60)
        return wait

    times = await db.repeat_count(chat_id, last["body"])
    if times >= repeat.MAX_REPEATS:
        log.info("Чат %s: вопрос повторён %d раз — игнорируем", chat_id, times)
        await db.close_tasks(chat_id, "ignored", upto)
        return None
    if times > 0:
        reply = repeat.pick(times)
        log.info("Чат %s: повтор вопроса (%d) — отвечаем «%s»", chat_id, times, reply)
    else:
        reply = await _choose_reply(client, db, chat_id, last["plain"] or "", name, len(tasks))
        if reply is None:                # ИИ не ответил
            n = await db.bump_attempts(chat_id)
            if n < AI_ATTEMPTS:
                log.warning("Чат %s: ИИ не дал ответ (попытка %d/%d) — повтор позже",
                            chat_id, n, AI_ATTEMPTS)
                return 45.0 * n
            log.warning("Чат %s: ИИ не отвечает — используем ответ по умолчанию", chat_id)
            reply = render(config.DEFAULT_REPLY, name)

    reply_to = next((t["msg_id"] for t in reversed(tasks) if t["is_forward"]), None)
    if reply_to is None:
        # иногда отвечаем реплаем-цитатой, как живой человек; на пачку сообщений — чаще
        chance = config.REPLY_QUOTE_CHANCE / 100
        if len(tasks) > 1:
            chance = max(chance, 0.75) if config.REPLY_QUOTE_CHANCE > 0 else 0
        if random.random() < chance:
            reply_to = random.choice(tasks[-3:])["msg_id"]
    if reply_to is not None:
        log.info("Чат %s: ответ реплаем на сообщение %s", chat_id, reply_to)
    await _mark_read(client, chat_id, upto)

    _sending.add(chat_id)
    try:
        sent = await _send_messages(client, ab, db, chat_id, last["username"], reply,
                                    reply_to, upto)
    finally:
        _sending.discard(chat_id)

    if sent == 0:
        ok, _ = await ab.can_auto_send()
        if not ok and not ab.panic:       # упёрлись в лимит между проверкой и отправкой
            return ab.retry_delay()
        return await _fail(db, chat_id, "ошибка отправки")

    if await db.open_tasks(chat_id):      # пока отвечали, написали ещё
        return random.uniform(*DEBOUNCE)
    return None


async def _run(chat_id: int, delay: float):
    me = asyncio.current_task()
    next_delay = None
    try:
        next_delay = await _process(chat_id, delay)
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("Ошибка в отложенном автоответе")
        try:
            next_delay = await _fail(_ctx[1], chat_id, "внутренняя ошибка")
        except Exception:
            next_delay = None
    finally:
        _sending.discard(chat_id)
        if _pending.get(chat_id) is me:
            _pending.pop(chat_id, None)
    if next_delay is not None and _ctx is not None and _ctx[2].autoreply_active:
        _schedule(chat_id, next_delay)


def _schedule(chat_id: int, delay: float):
    old = _pending.pop(chat_id, None)
    if old and not old.done() and old is not asyncio.current_task():
        old.cancel()
    _pending[chat_id] = asyncio.create_task(_run(chat_id, delay))


# ---------------------------------------------------------------- восстановление задач
async def _expire_old():
    client, db, ab = _ctx
    hours = config.TASK_MAX_AGE_HOURS
    if hours <= 0:
        return
    expired = await db.expire_tasks(int(time.time()) - hours * 3600)
    if not expired:
        return
    seen, lines = set(), []
    for t in expired:
        if t["chat_id"] in seen:
            continue
        seen.add(t["chat_id"])
        who = t["name"] or (("@" + t["username"]) if t["username"] else t["chat_id"])
        lines.append(f"• {who}: «{(t['plain'] or '')[:60]}»")
    log.warning("Закрыто устаревших задач: %d", len(expired))
    await _notify_owner(
        f"⏳ Без ответа дольше {hours} ч, автоответ уже не нужен ({len(expired)} сообщ.). "
        "Ответьте сами:\n" + "\n".join(lines[:15]))


async def recover() -> int:
    """Ставит в работу все открытые задачи (старт, /resume, /autoreply on). Возвращает число чатов."""
    if _ctx is None:
        return 0
    _, db, ab = _ctx
    if not ab.autoreply_active:
        return 0
    await _expire_old()
    n = 0
    for chat_id in await db.open_chats():
        t = _pending.get(chat_id)
        if (t and not t.done()) or chat_id in _sending:
            continue
        # разносим по времени, чтобы не отвечать всем разом
        _schedule(chat_id, random.uniform(5, 15) + n * random.uniform(10, 30))
        n += 1
    if n:
        log.info("Восстановлено задач на ответ: чатов %d", n)
    return n


async def _add_task_for(db, m, sender, chat_id: int) -> bool:
    body = await msgtext.describe(m)
    if not body:
        return False
    name = getattr(sender, "first_name", None) or getattr(sender, "username", None) or "друг"
    return await db.add_task(chat_id, m.id, getattr(sender, "username", None), name,
                             (m.raw_text or "").strip(), body, msgtext.is_forward(m))


async def catch_up():
    """Берёт в работу непрочитанные сообщения, пришедшие, пока бот был выключен."""
    client, db, ab = _ctx
    if not ab.autoreply_enabled or ab.panic or config.CATCHUP_HOURS <= 0:
        return
    cutoff = time.time() - config.CATCHUP_HOURS * 3600
    found = 0
    async for d in client.iter_dialogs(limit=200):
        if not d.is_user or d.unread_count <= 0:
            continue
        ent = d.entity
        if (ent is None or getattr(ent, "bot", False) or getattr(ent, "deleted", False)
                or ent.id in (ab.me_id, 777000) or not _allowed(ent)):
            continue
        msgs = await client.get_messages(d.id, limit=min(d.unread_count, 20))
        for m in reversed(msgs):
            if m.out or m.date.timestamp() < cutoff:
                continue
            if await _add_task_for(db, m, ent, d.id):
                found += 1
                await db.log_message(d.id, ent.id, getattr(ent, "username", None),
                                     await msgtext.describe(m) or "[медиа]", "in")
    if found:
        log.info("Найдено пропущенных сообщений, пока бот был выключен: %d", found)


async def startup():
    try:
        await catch_up()
    except Exception:
        log.exception("Не удалось проверить пропущенные сообщения")
    try:
        await recover()
    except Exception:
        log.exception("Не удалось восстановить задачи")


def start_background():
    """Запускает startup() в фоне (client.run_until_disconnected() блокирует поток)."""
    t = asyncio.create_task(startup())
    _bg.add(t)
    t.add_done_callback(_bg.discard)


def register(client, db, ab):
    global _ctx
    _ctx = (client, db, ab)

    @client.on(events.NewMessage(incoming=True))
    async def on_incoming(event):
        try:
            if not event.is_private:
                return
            sender = await event.get_sender()
            if sender is None or getattr(sender, "bot", False):
                return
            if sender.id in (ab.me_id, 777000):
                return

            m = event.message
            body = await msgtext.describe(m)
            await db.log_message(event.chat_id, sender.id, sender.username, body or "[медиа]", "in")

            # «Выкл» и аварийный стоп — задач не создаём. Пауза — создаём: после /resume ответим.
            if not body or not ab.autoreply_enabled or ab.panic or not _allowed(sender):
                return
            if body == "[стикер]":          # одиночный стикер — это реакция, а не вопрос
                return

            await _add_task_for(db, m, sender, event.chat_id)

            if not ab.autoreply_active or event.chat_id in _sending:
                return        # пауза: подхватит recover(); идёт отправка: подхватит _process
            # новое сообщение сдвигает таймер — ждём, пока собеседник допишет
            _schedule(event.chat_id, random.uniform(*DEBOUNCE))
        except Exception:
            log.exception("Ошибка обработки входящего сообщения")

    @client.on(events.NewMessage(outgoing=True))
    async def on_outgoing(event):
        try:
            if not event.is_private or event.chat_id == ab.me_id:
                return
            if event.chat_id in ab.sending_chats or event.message.id in ab.bot_ids:
                return
            task = _pending.pop(event.chat_id, None)
            if task and not task.done():
                task.cancel()
            n = await db.close_tasks(event.chat_id, "owner")
            if n:
                log.info("Вы ответили в чат %s — автоответ отменён (задач закрыто: %d)",
                         event.chat_id, n)
        except Exception:
            log.exception("Ошибка обработки исходящего сообщения")
