"""Стиль ответов ИИ: правила, которые нельзя оставлять на усмотрение модели.

Применяется к тексту от нейросети (шаблоны и фразы по умолчанию остаются как вы их написали).
"""
import random
import re

import config

_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U0000FE0F\U0000200D]+")
_SMILE_RE = re.compile(r"\)+")


def humanize(text: str, name: str = "") -> str:
    """Убирает эмодзи, лишние скобочки, точку в конце и заглавную первую букву."""
    text = _EMOJI_RE.sub("", text or "").strip()

    # скобочки-смайлы: если «(» в тексте нет, все «)» — это смайлы
    if "(" not in text and ")" in text:
        text = _SMILE_RE.sub(")", text)                  # ))) -> )
        if text.count(")") > 1:                          # не больше одной на сообщение
            first = text.index(")")
            text = text[: first + 1] + text[first + 1:].replace(")", "")
        if random.random() * 100 >= config.SMILE_KEEP_CHANCE:
            text = text.replace(")", "")                 # чаще всего — совсем без скобочки

    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    if text.endswith(".") and not text.endswith(".."):
        text = text[:-1].rstrip()

    if config.LOWERCASE_REPLIES and text:
        first = text.split(None, 1)[0].strip(",.!?:;")
        if first and first.lower() != (name or "").lower() and not first.isupper():
            text = text[0].lower() + text[1:]
    return text
