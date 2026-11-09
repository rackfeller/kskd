"""Команды управления. Отправляются самому себе в «Избранное» (Saved Messages)."""
import logging
import re
from datetime import datetime

from telethon import events

import config
from handlers import ai_reply, autoreply, templates

log = logging.getLogger("commands")

# Типографские кавычки -> обычные (мобильные клавиатуры их подставляют сами)
_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "‟": '"', "«": '"', "»": '"'})
_ADD_RE = re.compile(r'^\s*"(.+?)"\s+"(.+)"\s*$', re.DOTALL)

HELP = """📖 Команды (писать в «Избранное»):
/autoreply on | off — включить/выключить автоответчик
/pause — пауза автоответов
/resume — возобновить
/status — состояние бота
/tasks — кому ещё нужно ответить
/add "ключ" "текст" — добавить шаблон ({name} {time} {date})
/del "ключ" — удалить шаблон
/list — все шаблоны
/reload — перечитать файл templates.txt
/stats — статистика
/count @username — сколько сообщений от пользователя
/log — последние 20 автоответов
/panic — АВАРИЙНЫЙ СТОП и отключение от аккаунта
/help — эта справка"""


def _dt(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m %H:%M")


def register(client, db, ab):
    async def say(text: str):
        # force=True: ответы в «Избранное» — служебные, их не блокирует только FloodWait-стоп
        await ab.safe_send("me", text[:4000], force=True)

    async def cmd_status():
        floods = await db.flood_count_24h()
        tpl = await db.list_templates()
        await say(
            "📊 Статус\n"
            f"Автоответчик: {'ВКЛ' if ab.autoreply_enabled else 'ВЫКЛ'}\n"
            f"Пауза: {'да' if ab.paused else 'нет'}\n"
            f"Аварийный стоп: {'ДА' if ab.panic else 'нет'}\n"
            f"Ждут ответа: {await db.count_open_tasks()} "
            f"(запланировано чатов: {autoreply.pending_count()})\n"
            f"Автоответов за час: {ab.auto_last_hour()}/{config.MAX_REPLIES_PER_HOUR}\n"
            f"FloodWait за 24ч: {floods}/{config.FLOOD_LIMIT_24H}"
            f"{' (отправка заблокирована)' if floods >= config.FLOOD_LIMIT_24H else ''}\n"
            f"Шаблонов: {len(tpl)}\n"
            f"AI: {'включён (' + config.OPENAI_MODEL + ')' if ai_reply.enabled() else 'выключен'}\n"
            f"Задержка: {config.DELAY_MIN}–{config.DELAY_MAX} сек\n"
            f"Белый список: {', '.join(sorted(config.WHITELIST)) or '—'}\n"
            f"Чёрный список: {', '.join(sorted(config.BLACKLIST)) or '—'}"
        )

    async def cmd_stats():
        s = await db.stats()
        top = "\n".join(
            f"{i}. {('@' + u) if u else uid} — {c}" for i, (uid, u, c) in enumerate(s["top"], 1)
        ) or "—"
        await say(
            "📈 Статистика\n"
            f"Обработано входящих: {s['total_in']}\n"
            f"Отправлено автоответов: {s['total_auto']}\n"
            f"FloodWait за сутки: {s['floods_24h']}\n"
            f"Топ-3 собеседника:\n{top}"
        )

    async def cmd_tasks():
        tasks = await db.open_tasks()
        if not tasks:
            await say("✅ Все сообщения обработаны, ждущих ответа нет")
            return
        by_chat: dict[int, list] = {}
        for t in tasks:
            by_chat.setdefault(t["chat_id"], []).append(t)
        lines = []
        for chat_id, ts in by_chat.items():
            t = ts[-1]
            who = t["name"] or (("@" + t["username"]) if t["username"] else chat_id)
            fwd = " (пересланное)" if t["is_forward"] else ""
            lines.append(f"• {who} — {len(ts)} сообщ.{fwd}: «{(t['body'] or '')[:70]}»")
        await say(f"📬 Ждут ответа (чатов {len(by_chat)}):\n" + "\n".join(lines[:30]))

    async def cmd_panic():
        log.critical("PANIC! Аварийный стоп и отключение от аккаунта.")
        autoreply.cancel_all_pending()
        await ab.set_panic(True)
        await db.close_all_tasks("cancelled")
        await ab.safe_send(
            "me",
            "🚨 PANIC: все отправки остановлены, бот отключается от аккаунта.\n"
            "Чтобы запустить снова: python main.py --reset-panic",
            force=True,
        )
        await client.disconnect()

    @client.on(events.NewMessage(chats="me", outgoing=True))
    async def on_command(event):
        try:
            text = (event.raw_text or "").strip()
            if not text.startswith("/"):
                return
            parts = text.split(None, 1)
            cmd = parts[0].lower()
            args = parts[1].strip() if len(parts) > 1 else ""
            log.info("Команда: %s", cmd)

            if cmd == "/panic":
                await cmd_panic()
            elif cmd == "/help":
                await say(HELP)
            elif cmd == "/status":
                await cmd_status()
            elif cmd == "/autoreply":
                if args.lower() == "on":
                    await ab.set_autoreply(True)
                    await say("✅ Автоответчик включён")
                    await autoreply.recover()
                elif args.lower() == "off":
                    await ab.set_autoreply(False)
                    autoreply.cancel_all_pending()
                    await say("⛔ Автоответчик выключен")
                else:
                    await say("Использование: /autoreply on | off")
            elif cmd == "/pause":
                await ab.set_paused(True)
                await say("⏸ Пауза. Для продолжения: /resume")
            elif cmd == "/resume":
                await ab.set_paused(False)
                n = await autoreply.recover()
                await say("▶️ Автоответы возобновлены"
                          + (f". Отвечу на накопившееся: чатов {n}" if n else ""))
            elif cmd == "/tasks":
                await cmd_tasks()
            elif cmd == "/add":
                m = _ADD_RE.match(args.translate(_QUOTES))
                if not m:
                    await say('Использование: /add "ключ" "текст ответа"')
                else:
                    await db.add_template(m.group(1).strip(), m.group(2).strip())
                    await say(f"✅ Шаблон «{m.group(1).strip().lower()}» сохранён")
            elif cmd == "/del":
                key = args.translate(_QUOTES).strip().strip('"').strip()
                if not key:
                    await say('Использование: /del "ключ"')
                elif await db.del_template(key):
                    await say(f"🗑 Шаблон «{key.lower()}» удалён")
                else:
                    await say("Такого шаблона нет")
            elif cmd == "/list":
                rows = await db.list_templates()
                if not rows:
                    await say("Шаблонов пока нет. Добавьте: /add \"ключ\" \"текст\"")
                else:
                    chunk = "📝 Шаблоны:\n"
                    for k, t in rows:
                        line = f"• «{k}» → {t}\n"
                        if len(chunk) + len(line) > 3500:
                            await say(chunk)
                            chunk = ""
                        chunk += line
                    if chunk:
                        await say(chunk)
            elif cmd == "/reload":
                n = await templates.sync_from_file(db, config.TEMPLATES_FILE)
                await say(f"🔄 Из templates.txt загружено ключей: {n}")
            elif cmd == "/stats":
                await cmd_stats()
            elif cmd == "/count":
                if not args:
                    await say("Использование: /count @username")
                else:
                    n = await db.count_from_username(args.split()[0])
                    await say(f"✉️ Сообщений от {args.split()[0]}: {n}")
            elif cmd == "/log":
                rows = await db.last_autoreplies(20)
                if not rows:
                    await say("Автоответов пока не было")
                else:
                    lines = [
                        f"[{_dt(ts)}] {('@' + u) if u else uid}: {(t or '')[:80]}"
                        for ts, u, uid, t in rows
                    ]
                    await say("🧾 Последние автоответы:\n" + "\n".join(lines))
            else:
                await say("Неизвестная команда. Список: /help")
        except Exception:
            log.exception("Ошибка выполнения команды")
