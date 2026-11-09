"""Превращает сообщение Telegram в понятный боту текст: пересланные, ответы-цитаты, медиа без подписи."""
import logging
import re

log = logging.getLogger("msgtext")

# Служебные метки, которые нельзя пропускать в текст ответа
_LABEL_RE = re.compile(r"\[(?:В ответ на[^«\]]*«.*?»|Пересланное сообщение[^\]]*)\]\s*", re.S)


def strip_labels(text: str) -> str:
    """Убирает служебные метки из ответа ИИ (модель иногда копирует их из истории)."""
    text = _LABEL_RE.sub("", text or "")
    # метка в самом начале вида [фото] / [голосовое сообщение] и т. п.
    text = re.sub(r"^\s*\[[^\]]{1,40}\]\s*", "", text)
    return text.strip()


def is_forward(m) -> bool:
    return getattr(m, "fwd_from", None) is not None


def _media_label(m) -> str:
    """Короткая метка вложения или '' если вложений нет."""
    checks = (
        ("voice", "голосовое сообщение"),
        ("video_note", "видеокружок"),
        ("sticker", "стикер"),
        ("gif", "гифка"),
        ("photo", "фото"),
        ("video", "видео"),
        ("audio", "аудио"),
        ("poll", "опрос"),
        ("contact", "контакт"),
        ("geo", "геопозиция"),
        ("document", "файл"),
    )
    for attr, label in checks:
        try:
            if getattr(m, attr, None):
                return label
        except Exception:
            continue
    return ""


def _forward_source(m) -> str:
    """Откуда переслано (без сетевых запросов — только то, что уже есть в сообщении)."""
    fwd = getattr(m, "forward", None) or getattr(m, "fwd_from", None)
    if fwd is None:
        return ""
    try:
        name = getattr(fwd, "from_name", None)
        if name:
            return str(name)
        for ent_attr in ("sender", "chat"):
            ent = getattr(fwd, ent_attr, None)
            if ent is not None:
                title = (getattr(ent, "first_name", None) or getattr(ent, "title", None)
                         or getattr(ent, "username", None))
                if title:
                    return str(title)
    except Exception:
        pass
    return ""


async def _quote(m) -> str:
    """Текст сообщения, на которое собеседник ответил (reply)."""
    try:
        if not getattr(m, "is_reply", False):
            return ""
        r = await m.get_reply_message()
        if r is None:
            return ""
        txt = (r.raw_text or "").strip() or _media_label(r)
        if not txt:
            return ""
        # важно для ИИ: чьё сообщение процитировано (его собственное или сообщение Юли)
        whose = "твоё сообщение" if getattr(r, "out", False) else "своё прошлое сообщение"
        return f"{whose} «{txt[:200]}»"
    except Exception:
        return ""


async def describe(m) -> str:
    """Полное описание сообщения для логов и для ИИ. Никогда не бросает исключений.

    Примеры:
      'привет'
      '[Пересланное сообщение от Иван] смотри, какая штука'
      '[Пересланное сообщение] [фото]'
      '[В ответ на «ты где?»] дома'
    """
    try:
        text = (m.raw_text or "").strip()
        media = _media_label(m)
        content = text or (f"[{media}]" if media else "")
        if getattr(m, "out", False):
            return content            # свои сообщения — без меток, иначе ИИ их копирует
        if text and media and media not in ("файл",):
            content = f"[{media}] {text}"

        if is_forward(m):
            src = _forward_source(m)
            head = f"[Пересланное сообщение от {src}]" if src else "[Пересланное сообщение]"
            return f"{head} {content}".strip()

        quote = await _quote(m)
        if quote:
            return f"[В ответ на {quote}] {content}".strip()
        return content
    except Exception:
        log.exception("Не удалось описать сообщение")
        return (getattr(m, "raw_text", "") or "").strip()
