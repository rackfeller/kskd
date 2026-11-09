"""Работа с SQLite: логи сообщений, шаблоны, FloodWait, флаги состояния."""
import re
import time

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    user_id INTEGER,
    username TEXT,
    text TEXT,
    direction TEXT,          -- 'in' (входящее) или 'auto' (наш автоответ)
    timestamp INTEGER        -- unix-время, UTC
);
CREATE INDEX IF NOT EXISTS idx_msg_dir_ts ON messages(direction, timestamp);
CREATE TABLE IF NOT EXISTS templates (
    key TEXT PRIMARY KEY,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS flood_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER,
    seconds INTEGER
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT
);
-- Задачи «нужно ответить». Живут в базе, поэтому не теряются при перезапуске, паузе и лимитах.
-- status: open | done | owner (вы ответили сами) | ignored | failed | expired | cancelled
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    msg_id INTEGER NOT NULL,
    username TEXT,
    name TEXT,
    plain TEXT,              -- собственный текст сообщения (для поиска шаблона)
    body TEXT,               -- описание: пересылка / медиа / цитата + текст
    is_forward INTEGER DEFAULT 0,
    status TEXT DEFAULT 'open',
    attempts INTEGER DEFAULT 0,
    created INTEGER,
    closed INTEGER,
    UNIQUE(chat_id, msg_id)
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, chat_id);
"""


def _norm(s: str) -> str:
    """Нижний регистр и ё -> е, чтобы «живёшь» и «живешь» считались одним словом."""
    return s.lower().replace("ё", "е").strip()


class Database:
    def __init__(self, path):
        self.path = str(path)
        self.conn: aiosqlite.Connection | None = None

    async def connect(self):
        self.conn = await aiosqlite.connect(self.path)
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self):
        if self.conn:
            await self.conn.close()
            self.conn = None

    # --- вспомогательные ---
    async def _all(self, sql, params=()):
        cur = await self.conn.execute(sql, params)
        rows = await cur.fetchall()
        await cur.close()
        return rows

    async def _one(self, sql, params=()):
        rows = await self._all(sql, params)
        return rows[0] if rows else None

    # --- сообщения ---
    async def log_message(self, chat_id, user_id, username, text, direction):
        await self.conn.execute(
            "INSERT INTO messages (chat_id, user_id, username, text, direction, timestamp) "
            "VALUES (?,?,?,?,?,?)",
            (chat_id, user_id, username, text, direction, int(time.time())),
        )
        await self.conn.commit()

    async def repeat_count(self, chat_id, text: str, window: int = 900) -> int:
        """Сколько раз за последние `window` сек собеседник уже спрашивал то же самое
        И мы на это уже успели ответить. 0 = вопрос новый."""
        loose = lambda s: re.sub(r"[^\w]", "", _norm(s or ""))
        key = loose(text)
        if len(key) < 3:
            return 0
        since = int(time.time()) - window
        rows = await self._all(
            "SELECT id, text FROM messages WHERE chat_id=? AND direction='in' "
            "AND timestamp>=? ORDER BY id", (chat_id, since))
        same = [r[0] for r in rows if loose(r[1]) == key]
        if len(same) < 2:
            return 0
        row = await self._one(
            "SELECT MAX(id) FROM messages WHERE chat_id=? AND direction='auto' "
            "AND timestamp>=?", (chat_id, since))
        last_auto = row[0] or 0
        # earlier-копии, после которых мы уже отвечали (текущее сообщение — последнее)
        return sum(1 for i in same[:-1] if i < last_auto)

    async def auto_timestamps_since(self, since: float) -> list[int]:
        rows = await self._all(
            "SELECT timestamp FROM messages WHERE direction='auto' AND timestamp>=?",
            (int(since),),
        )
        return [r[0] for r in rows]

    async def last_autoreplies(self, n: int = 20):
        return await self._all(
            "SELECT timestamp, username, user_id, text FROM messages "
            "WHERE direction='auto' ORDER BY id DESC LIMIT ?",
            (n,),
        )

    async def count_from_username(self, username: str) -> int:
        row = await self._one(
            "SELECT COUNT(*) FROM messages WHERE direction='in' AND lower(username)=?",
            (username.lstrip("@").lower(),),
        )
        return row[0]

    async def stats(self) -> dict:
        total_in = (await self._one("SELECT COUNT(*) FROM messages WHERE direction='in'"))[0]
        total_auto = (await self._one("SELECT COUNT(*) FROM messages WHERE direction='auto'"))[0]
        top = await self._all(
            "SELECT user_id, MAX(username), COUNT(*) AS c FROM messages "
            "WHERE direction='in' GROUP BY user_id ORDER BY c DESC LIMIT 3"
        )
        return {
            "total_in": total_in,
            "total_auto": total_auto,
            "top": top,
            "floods_24h": await self.flood_count_24h(),
        }

    # --- шаблоны ---
    async def add_template(self, key: str, text: str):
        await self.conn.execute(
            "INSERT OR REPLACE INTO templates (key, text) VALUES (?, ?)", (_norm(key), text)
        )
        await self.conn.commit()

    async def del_template(self, key: str) -> bool:
        cur = await self.conn.execute("DELETE FROM templates WHERE key=?", (_norm(key),))
        await self.conn.commit()
        deleted = cur.rowcount > 0
        await cur.close()
        return deleted

    async def list_templates(self):
        return await self._all("SELECT key, text FROM templates ORDER BY key")

    async def find_template(self, message_text: str) -> str | None:
        """Ищет шаблон, ключ которого есть в тексте (длинные ключи — приоритетнее).

        Ключ должен начинаться с границы слова, а сообщение быть «про это»: не длиннее ключа
        больше чем на TEMPLATE_EXTRA_WORDS слов. Иначе «сколько лет уже в магазине работаешь?»
        ответило бы шаблоном про возраст — такие вопросы уходят в ИИ."""
        import config
        low = _norm(message_text)
        n_words = len(re.findall(r"\w+", low))
        rows = await self._all("SELECT key, text FROM templates ORDER BY length(key) DESC")
        for key, text in rows:
            k = _norm(key)
            if not k or not re.search(r"(?<!\w)" + re.escape(k), low):
                continue
            if n_words > len(re.findall(r"\w+", k)) + config.TEMPLATE_EXTRA_WORDS:
                continue
            return text
        return None

    # --- FloodWait ---
    async def add_flood(self, seconds: int):
        await self.conn.execute(
            "INSERT INTO flood_events (timestamp, seconds) VALUES (?, ?)",
            (int(time.time()), int(seconds)),
        )
        await self.conn.commit()

    async def flood_count_24h(self) -> int:
        row = await self._one(
            "SELECT COUNT(*) FROM flood_events WHERE timestamp>=?", (int(time.time()) - 86400,)
        )
        return row[0]

    # --- флаги состояния (переживают перезапуск) ---
    async def get_state(self, key: str, default: str = "") -> str:
        row = await self._one("SELECT value FROM state WHERE key=?", (key,))
        return row[0] if row else default

    async def set_state(self, key: str, value: str):
        await self.conn.execute(
            "INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (key, value)
        )
        await self.conn.commit()

    # --- задачи «нужно ответить» ---
    async def add_task(self, chat_id, msg_id, username, name, plain, body, is_forward) -> bool:
        """Создаёт задачу. False, если такая задача уже есть (в т.ч. закрытая)."""
        cur = await self.conn.execute(
            "INSERT OR IGNORE INTO tasks (chat_id, msg_id, username, name, plain, body, "
            "is_forward, status, created) VALUES (?,?,?,?,?,?,?, 'open', ?)",
            (chat_id, msg_id, username, name, plain, body, 1 if is_forward else 0,
             int(time.time())),
        )
        await self.conn.commit()
        created = cur.rowcount > 0
        await cur.close()
        return created

    async def open_tasks(self, chat_id=None) -> list[dict]:
        sql = ("SELECT id, chat_id, msg_id, username, name, plain, body, is_forward, attempts, "
               "created FROM tasks WHERE status='open'")
        params: tuple = ()
        if chat_id is not None:
            sql += " AND chat_id=?"
            params = (chat_id,)
        rows = await self._all(sql + " ORDER BY msg_id", params)
        keys = ("id", "chat_id", "msg_id", "username", "name", "plain", "body", "is_forward",
                "attempts", "created")
        return [dict(zip(keys, r)) for r in rows]

    async def open_chats(self) -> list[int]:
        rows = await self._all(
            "SELECT chat_id FROM tasks WHERE status='open' GROUP BY chat_id ORDER BY MIN(id)")
        return [r[0] for r in rows]

    async def count_open_tasks(self) -> int:
        return (await self._one("SELECT COUNT(*) FROM tasks WHERE status='open'"))[0]

    async def close_tasks(self, chat_id, status: str, upto_msg_id: int | None = None) -> int:
        """Закрывает открытые задачи чата (до msg_id включительно, если указан)."""
        sql = "UPDATE tasks SET status=?, closed=? WHERE status='open' AND chat_id=?"
        params = [status, int(time.time()), chat_id]
        if upto_msg_id is not None:
            sql += " AND msg_id<=?"
            params.append(upto_msg_id)
        cur = await self.conn.execute(sql, params)
        await self.conn.commit()
        n = cur.rowcount
        await cur.close()
        return n

    async def close_all_tasks(self, status: str) -> int:
        cur = await self.conn.execute(
            "UPDATE tasks SET status=?, closed=? WHERE status='open'", (status, int(time.time())))
        await self.conn.commit()
        n = cur.rowcount
        await cur.close()
        return n

    async def bump_attempts(self, chat_id) -> int:
        """+1 к числу неудачных попыток по чату; возвращает новое значение."""
        await self.conn.execute(
            "UPDATE tasks SET attempts=attempts+1 WHERE status='open' AND chat_id=?", (chat_id,))
        await self.conn.commit()
        row = await self._one(
            "SELECT MAX(attempts) FROM tasks WHERE status='open' AND chat_id=?", (chat_id,))
        return row[0] or 0

    async def expire_tasks(self, older_than_ts: int) -> list[dict]:
        """Закрывает слишком старые открытые задачи, возвращает их список."""
        rows = await self._all(
            "SELECT chat_id, username, name, plain FROM tasks WHERE status='open' AND created<?",
            (older_than_ts,))
        if rows:
            await self.conn.execute(
                "UPDATE tasks SET status='expired', closed=? WHERE status='open' AND created<?",
                (int(time.time()), older_than_ts))
            await self.conn.commit()
        return [dict(zip(("chat_id", "username", "name", "plain"), r)) for r in rows]
