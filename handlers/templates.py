"""Шаблоны быстрых ответов: подстановка переменных и загрузка из файла templates.txt."""
import logging
from datetime import datetime

log = logging.getLogger("templates")


def render(template: str, name: str) -> str:
    """Подставляет переменные. Используем replace, чтобы чужие фигурные скобки не ломали текст."""
    now = datetime.now()
    return (
        template.replace("{name}", name)
        .replace("{time}", now.strftime("%H:%M"))
        .replace("{date}", now.strftime("%d.%m.%Y"))
    )


async def sync_from_file(db, path) -> int:
    """Загружает шаблоны из templates.txt в базу (добавляет новые и обновляет изменённые).

    Формат строки:  ключ1; ключ2; ключ3 | текст ответа
    Строки с # в начале — комментарии. \\n в тексте = перенос строки.
    Возвращает количество загруженных ключей.
    """
    try:
        if not path.exists():
            return 0
        count = 0
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "|" not in line:
                continue
            keys_part, text = line.split("|", 1)
            text = text.strip().replace("\\n", "\n")
            if not text:
                continue
            for key in keys_part.split(";"):
                key = key.strip()
                if key:
                    await db.add_template(key, text)
                    count += 1
        return count
    except Exception:
        log.exception("Не удалось прочитать %s", path)
        return 0
