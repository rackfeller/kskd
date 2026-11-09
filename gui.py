"""Окно управления userbot (тёмная тема). Запуск: app.bat (Windows) / ./app.sh или Userbot.exe."""
import logging
import os
import queue
import sys
import time
import tkinter as tk
from datetime import datetime
from tkinter import font as tkfont
from tkinter import ttk

# При запуске через pythonw (без консоли) stdout/stderr отсутствуют — подменяем заглушкой.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# Чёткий текст на экранах с масштабом 125–200 %, а не «мыло» от растягивания Windows.
if sys.platform == "win32":
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("userbot.control.panel")
    except Exception:
        pass

from dotenv import set_key

import bot_runner as br
import config
from utils.logger import setup_logger

TICK_MS = 1000

# ----------------------------------------------------------------------
#  Палитра и шрифты
# ----------------------------------------------------------------------
BG = "#0c0e13"          # фон окна
SURFACE = "#141720"     # карточки
SURFACE2 = "#1b1f2b"    # кнопки «ghost», заголовки таблиц
FIELD = "#0f1218"       # поля ввода, журнал, таблицы
ROW_ALT = "#131722"     # зебра в таблицах
BORDER = "#252a3a"
TEXT = "#e6e9f2"
MUTED = "#8b93a7"
DIM = "#5b6378"
ACCENT = "#7c6bff"
ACCENT_H = "#8e7fff"
SEL = "#33307a"         # выделение

GREEN, GREEN_BTN, GREEN_BTN_H = "#34d399", "#1fa971", "#27c283"
RED, RED_BTN, RED_BTN_H = "#f4616d", "#d9414f", "#ec5a68"
ORANGE = "#f5a524"
YELLOW = "#f2c94c"
GRAY = "#6b7388"

FONT = "Segoe UI"
MONO = "Consolas"
SCALE = 1.0


def px(n: float) -> int:
    """Пиксели с учётом масштаба экрана."""
    return int(round(n * SCALE))


def F(size: int, weight: str = "normal"):
    return (FONT, size, weight)


def mix(c1: str, c2: str, t: float) -> str:
    """Смешать два цвета: t=0 → c1, t=1 → c2."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _round_pts(x1, y1, x2, y2, r):
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]


def draw_round(cv: tk.Canvas, x1, y1, x2, y2, r, **kw):
    r = max(1, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return cv.create_polygon(_round_pts(x1, y1, x2, y2, r), smooth=True, **kw)


def _dark_titlebar(win):
    """Тёмная рамка окна в Windows 10/11 (на других ОС ничего не делает)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
        one = ctypes.c_int(1)
        for attr in (20, 19):                       # USE_IMMERSIVE_DARK_MODE
            if dwm(hwnd, attr, ctypes.byref(one), ctypes.sizeof(one)) == 0:
                break
        r, g, b = (int(BG[i:i + 2], 16) for i in (1, 3, 5))
        cap = ctypes.c_int(r | (g << 8) | (b << 16))  # цвет заголовка (Windows 11)
        dwm(hwnd, 35, ctypes.byref(cap), ctypes.sizeof(cap))
    except Exception:
        pass


# ----------------------------------------------------------------------
#  Собственные виджеты
# ----------------------------------------------------------------------
class Card(tk.Frame):
    """Скруглённая карточка. Содержимое кладём в card.body. Высота берётся из содержимого."""

    def __init__(self, master, *, parent_bg=BG, fill=SURFACE, outline=BORDER,
                 radius=14, pad=14, stretch=False):
        super().__init__(master, bg=parent_bg)
        self._fill, self._outline, self._r = fill, outline, px(radius)
        self._bgcv = tk.Canvas(self, bg=parent_bg, highlightthickness=0, bd=0)
        self._bgcv.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=fill)
        self.body.pack(fill="both", expand=True, padx=px(pad), pady=px(pad))
        tk.Misc.lower(self._bgcv)
        self._bgcv.bind("<Configure>", self._on_canvas)

    def _on_canvas(self, e):
        self._bgcv.delete("all")
        draw_round(self._bgcv, 1, 1, e.width - 1, e.height - 1, self._r,
                   fill=self._fill, outline=self._outline)


class RoundButton(tk.Canvas):
    """Скруглённая кнопка. Совместима с .state([...]) и .config(text=..., state=...)."""

    STYLES = {   # заливка, заливка при наведении, цвет текста, рамка
        "primary": (ACCENT, ACCENT_H, "#ffffff", None),
        "success": (GREEN_BTN, GREEN_BTN_H, "#ffffff", None),
        "danger": (RED_BTN, RED_BTN_H, "#ffffff", None),
        "ghost": (SURFACE2, mix(SURFACE2, "#ffffff", 0.07), TEXT, BORDER),
        "ghost-danger": (SURFACE2, mix(SURFACE2, RED, 0.22), RED, BORDER),
    }

    def __init__(self, master, text, command=None, *, style="ghost", width=130, height=36,
                 parent_bg=SURFACE, font=None, radius=10):
        super().__init__(master, width=px(width), height=px(height), bg=parent_bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._text, self._cmd, self._style = text, command, style
        self._font = font or F(10, "bold")
        self._r = px(radius)
        self._bw, self._bh = px(width), px(height)
        self._enabled, self._hover, self._pressed = True, False, False
        self.bind("<Configure>", self._on_size)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._draw()

    # ---- состояние ----
    def state(self, spec=None):
        if spec is None:
            return ("normal",) if self._enabled else ("disabled",)
        for s in spec:
            if s == "disabled":
                self._set_enabled(False)
            elif s == "!disabled":
                self._set_enabled(True)

    def configure(self, cnf=None, **kw):
        if "text" in kw:
            text = kw.pop("text")
            if text != self._text:
                self._text = text
                self._draw()
        if "state" in kw:
            self._set_enabled(kw.pop("state") != "disabled")
        if cnf or kw:
            return super().configure(cnf, **kw)

    config = configure

    def _set_enabled(self, value: bool):
        if value == self._enabled:
            return
        self._enabled = value
        if not value:
            self._hover = self._pressed = False
        tk.Canvas.configure(self, cursor="hand2" if value else "arrow")
        self._draw()

    def _set_hover(self, value):
        self._hover = value
        if not value:
            self._pressed = False
        self._draw()

    def _on_size(self, e):
        self._bw, self._bh = e.width, e.height
        self._draw()

    def _on_press(self, _):
        if self._enabled:
            self._pressed = True
            self._draw()

    def _on_release(self, e):
        was = self._pressed
        self._pressed = False
        self._draw()
        if was and self._enabled and 0 <= e.x <= self._bw and 0 <= e.y <= self._bh and self._cmd:
            self._cmd()

    def _draw(self):
        self.delete("all")
        base, hov, fg, border = self.STYLES[self._style]
        if not self._enabled:
            fill, fg, outline = mix(SURFACE, "#ffffff", 0.04), DIM, mix(SURFACE, "#ffffff", 0.06)
        else:
            fill = mix(base, "#000000", 0.18) if self._pressed else hov if self._hover else base
            outline = border or fill
        draw_round(self, 1, 1, self._bw - 1, self._bh - 1, self._r, fill=fill, outline=outline)
        self.create_text(self._bw / 2, self._bh / 2, text=self._text, fill=fg, font=self._font)


class Switch(tk.Frame):
    """Переключатель (вместо чекбокса)."""

    def __init__(self, master, text, variable, command=None, parent_bg=SURFACE):
        super().__init__(master, bg=parent_bg)
        self._var, self._cmd, self._enabled, self._bg = variable, command, True, parent_bg
        self._bw, self._bh = px(42), px(24)
        self._cv = tk.Canvas(self, width=self._bw, height=self._bh, bg=parent_bg,
                             highlightthickness=0, bd=0, cursor="hand2")
        self._cv.pack(side="left")
        self._lbl = tk.Label(self, text=text, bg=parent_bg, fg=TEXT, font=F(10), cursor="hand2")
        self._lbl.pack(side="left", padx=(px(10), 0))
        for w in (self._cv, self._lbl):
            w.bind("<Button-1>", self._toggle)
        variable.trace_add("write", lambda *_: self._draw())
        self._draw()

    def state(self, spec=None):
        if spec is None:
            return ("normal",) if self._enabled else ("disabled",)
        for s in spec:
            if s == "disabled":
                self._enabled = False
            elif s == "!disabled":
                self._enabled = True
        cur = "hand2" if self._enabled else "arrow"
        self._cv.configure(cursor=cur)
        self._lbl.configure(cursor=cur, fg=TEXT if self._enabled else DIM)
        self._draw()

    def configure(self, cnf=None, **kw):
        if "text" in kw:
            self._lbl.configure(text=kw.pop("text"))
        if cnf or kw:
            return super().configure(cnf, **kw)

    config = configure

    def _toggle(self, _=None):
        if not self._enabled:
            return
        self._var.set(not self._var.get())
        if self._cmd:
            self._cmd()

    def _draw(self):
        cv, w, h = self._cv, self._bw, self._bh
        cv.delete("all")
        on = bool(self._var.get())
        if self._enabled:
            track = ACCENT if on else mix(SURFACE2, "#ffffff", 0.10)
            knob = "#ffffff" if on else MUTED
        else:
            track = mix(SURFACE, "#ffffff", 0.06)
            knob = DIM
        draw_round(cv, 1, 1, w - 1, h - 1, h / 2, fill=track, outline=track)
        d, m = px(18), px(3)
        x = w - m - d if on else m
        cv.create_oval(x, (h - d) / 2, x + d, (h + d) / 2, fill=knob, outline=knob)


class StatusPill(tk.Canvas):
    """Плашка статуса с пульсирующей точкой."""

    def __init__(self, master, parent_bg=BG):
        super().__init__(master, width=px(150), height=px(36), bg=parent_bg,
                         highlightthickness=0, bd=0)
        self._font = tkfont.Font(family=FONT, size=10, weight="bold")
        self._parent_bg = parent_bg
        self._text, self._color, self._pulse, self._phase = "", GRAY, False, 0.0
        self._bw, self._bh = px(150), px(36)

    def set(self, text, color, pulse=False):
        if (text, color, pulse) == (self._text, self._color, self._pulse):
            return
        self._text, self._color, self._pulse = text, color, pulse
        self._bw = self._font.measure(text) + px(52)
        tk.Canvas.configure(self, width=self._bw)
        self._draw()

    def tick(self):
        if self._pulse:
            self._phase = (time.monotonic() % 1.5) / 1.5
            self._draw()

    def _draw(self):
        self.delete("all")
        w, h, c = self._bw, self._bh, self._color
        fill = mix(c, self._parent_bg, 0.86)
        draw_round(self, 1, 1, w - 1, h - 1, h / 2, fill=fill, outline=mix(c, self._parent_bg, 0.6))
        cx, cy = px(19), h / 2
        if self._pulse:
            r = px(4) + px(6) * self._phase
            halo = mix(c, fill, 0.25 + 0.75 * self._phase)
            self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=halo, outline="")
        r = px(4.5)
        self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=c, outline="")
        self.create_text(px(34), cy, text=self._text, fill=c, font=self._font, anchor="w")


# ----------------------------------------------------------------------
#  Фабрики полей и диалоги
# ----------------------------------------------------------------------
def make_entry(parent, **kw):
    return tk.Entry(parent, bg=FIELD, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0,
                    highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
                    selectbackground=SEL, selectforeground="#ffffff", font=F(10), **kw)


def make_text(parent, **kw):
    return tk.Text(parent, bg=FIELD, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0,
                   highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
                   selectbackground=SEL, selectforeground="#ffffff", font=F(10), wrap="word",
                   padx=px(10), pady=px(8), **kw)


def make_spin(parent, lo, hi, step=1, width=8):
    return tk.Spinbox(parent, from_=lo, to=hi, increment=step, width=width, bg=FIELD, fg=TEXT,
                      insertbackground=TEXT, buttonbackground=SURFACE2, relief="flat", bd=0,
                      highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
                      selectbackground=SEL, selectforeground="#ffffff", font=F(10))


def set_spin(spin, value):
    spin.delete(0, "end")
    spin.insert(0, str(value))


def themed_box(parent, title, message, kind="info", *, yes="OK", no=None, danger=False) -> bool:
    """Модальное окно в стиле приложения. Возвращает True, если нажата главная кнопка."""
    icon_color, glyph = {"info": (ACCENT, "i"), "warning": (ORANGE, "!"),
                         "error": (RED, "✕"), "question": (ACCENT, "?")}.get(kind, (ACCENT, "i"))
    if danger:
        icon_color = RED
    result = {"v": False}
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title(title)
    win.configure(bg=SURFACE)
    win.resizable(False, False)
    win.transient(parent)

    def close(value):
        result["v"] = value
        win.destroy()

    body = tk.Frame(win, bg=SURFACE, padx=px(24), pady=px(22))
    body.pack(fill="both", expand=True)
    ic = tk.Canvas(body, width=px(44), height=px(44), bg=SURFACE, highlightthickness=0, bd=0)
    ic.grid(row=0, column=0, sticky="n", padx=(0, px(16)))
    ic.create_oval(1, 1, px(44) - 1, px(44) - 1, fill=mix(icon_color, SURFACE, 0.82),
                   outline=mix(icon_color, SURFACE, 0.55))
    ic.create_text(px(22), px(22), text=glyph, fill=icon_color, font=F(15, "bold"))
    tk.Label(body, text=message, bg=SURFACE, fg=TEXT, font=F(10), justify="left", anchor="w",
             wraplength=px(340)).grid(row=0, column=1, sticky="w")

    bar = tk.Frame(win, bg=BG, padx=px(20), pady=px(14))
    bar.pack(fill="x")
    RoundButton(bar, yes, lambda: close(True), style="danger" if danger else "primary",
                width=120, height=34, parent_bg=BG).pack(side="right")
    if no:
        RoundButton(bar, no, lambda: close(False), style="ghost", width=100, height=34,
                    parent_bg=BG).pack(side="right", padx=(0, px(10)))

    win.bind("<Escape>", lambda e: close(False))
    win.bind("<Return>", lambda e: close(True))
    win.protocol("WM_DELETE_WINDOW", lambda: close(False))

    win.update_idletasks()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - h) // 3
    win.geometry(f"+{max(0, x)}+{max(0, y)}")
    _dark_titlebar(win)
    win.deiconify()
    try:
        win.wait_visibility()
        win.grab_set()
    except tk.TclError:
        pass
    win.focus_set()
    parent.wait_window(win)
    return result["v"]


# ----------------------------------------------------------------------
class QueueLogHandler(logging.Handler):
    """Кладёт строки журнала в очередь, из которой окно их забирает."""

    def __init__(self, q: queue.Queue):
        super().__init__(logging.INFO)
        self.q = q
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))

    def emit(self, record):
        try:
            self.q.put((record.levelno, self.format(record)))
        except Exception:
            pass


def _parse_set(raw: str) -> set[str]:
    return {x.strip().lstrip("@").lower() for x in raw.split(",") if x.strip()}


# ----------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        global SCALE
        try:
            SCALE = max(1.0, self.winfo_fpixels("1i") / 96.0)
        except Exception:
            SCALE = 1.0

        self.title("Userbot — панель управления")
        self.configure(bg=BG)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(px(980), sw - px(40)), min(px(800), sh - px(90))
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 2 - px(20))}")
        self.minsize(min(px(880), w), min(px(660), h))
        self._set_icon()
        self._setup_styles()

        setup_logger()
        self.log_q: queue.Queue = queue.Queue()
        logging.getLogger().addHandler(QueueLogHandler(self.log_q))

        self.runner = br.BotRunner()
        self._tick_n = 0
        self._shown_error = ""
        self._autoreply_var = tk.BooleanVar(value=True)

        self.main = tk.Frame(self, bg=BG)
        self.main.pack(fill="both", expand=True, padx=px(22), pady=px(18))
        self._build_header()
        self._build_controls()
        self._build_stats()
        self._build_tabs()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        _dark_titlebar(self)
        self._refresh_stats()
        self._refresh_templates()
        self._refresh_history()
        self._tick()
        self._anim()

    # ================= оформление =================
    def _set_icon(self):
        try:
            from icon_data import ICON_PNG_B64
            self._icon = tk.PhotoImage(data=ICON_PNG_B64)
            self.iconphoto(True, self._icon)
        except Exception:
            pass

    def _setup_styles(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("Dark.Treeview", background=FIELD, fieldbackground=FIELD, foreground=TEXT,
                     rowheight=px(32), borderwidth=0, relief="flat", font=F(10))
        st.map("Dark.Treeview", background=[("selected", SEL)], foreground=[("selected", "#ffffff")])
        st.configure("Dark.Treeview.Heading", background=SURFACE2, foreground=MUTED, relief="flat",
                     borderwidth=0, font=F(9, "bold"), padding=(px(10), px(7)))
        st.map("Dark.Treeview.Heading", background=[("active", SURFACE2)])
        try:
            st.layout("Dark.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        except tk.TclError:
            pass
        st.configure("Dark.Vertical.TScrollbar", background=BORDER, troughcolor=FIELD,
                     bordercolor=FIELD, lightcolor=BORDER, darkcolor=BORDER, arrowcolor=MUTED,
                     relief="flat", gripcount=0, arrowsize=px(11))
        st.map("Dark.Vertical.TScrollbar", background=[("active", DIM), ("pressed", MUTED)])
        try:
            st.layout("Dark.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {
                "sticky": "ns", "children": [("Vertical.Scrollbar.thumb",
                                              {"expand": "1", "sticky": "nswe"})]})])
        except tk.TclError:
            pass

    # ================= построение интерфейса =================
    def _build_header(self):
        f = tk.Frame(self.main, bg=BG)
        f.pack(fill="x")

        s = px(46)
        logo = tk.Canvas(f, width=s, height=s, bg=BG, highlightthickness=0, bd=0)
        logo.pack(side="left")
        draw_round(logo, 0, 0, s, s, px(12), fill=ACCENT, outline=ACCENT)
        k = s / 46.0
        plane = [(8, 23), (38, 9), (31, 37), (22, 28)]
        logo.create_polygon([c * k for xy in plane for c in xy], fill="#ffffff", outline="")
        logo.create_line(22 * k, 28 * k, 38 * k, 9 * k, fill=ACCENT, width=max(1, int(1.6 * k)))

        txt = tk.Frame(f, bg=BG)
        txt.pack(side="left", padx=(px(14), 0))
        tk.Label(txt, text="Userbot", font=F(19, "bold"), fg=TEXT, bg=BG).pack(anchor="w")
        self.sub_lbl = tk.Label(txt, text="Панель управления", font=F(10), fg=MUTED, bg=BG,
                                anchor="w", justify="left", wraplength=px(560))
        self.sub_lbl.pack(anchor="w")

        self.pill = StatusPill(f)
        self.pill.pack(side="right")
        self.pill.set("Остановлен", GRAY)

    def _build_controls(self):
        card = Card(self.main)
        card.pack(fill="x", pady=(px(16), 0))
        b = card.body
        b.columnconfigure(3, weight=1)

        self.btn_start = RoundButton(b, "▶  Запустить", self._start, style="success", width=140)
        self.btn_stop = RoundButton(b, "■  Остановить", self._stop, width=140)
        self.btn_pause = RoundButton(b, "⏸  Пауза", self._toggle_pause, width=140)
        self.chk_auto = Switch(b, "Автоответчик включён", self._autoreply_var,
                               self._toggle_autoreply)
        self.btn_reset = RoundButton(b, "Сбросить аварийный стоп", self._reset_panic,
                                     style="ghost-danger", width=210)
        self.btn_panic = RoundButton(b, "⚠  PANIC", self._panic, style="danger", width=120)

        self.btn_start.grid(row=0, column=0, padx=(0, px(8)))
        self.btn_stop.grid(row=0, column=1, padx=px(8))
        self.btn_pause.grid(row=0, column=2, padx=px(8))
        self.chk_auto.grid(row=0, column=3, padx=px(20), sticky="w")
        self.btn_reset.grid(row=0, column=4, padx=px(8))
        self.btn_reset.grid_remove()
        self.btn_panic.grid(row=0, column=5, padx=(px(8), 0))

    def _build_stats(self):
        row = tk.Frame(self.main, bg=BG)
        row.pack(fill="x", pady=(px(12), 0))
        self.stat_vars, self.stat_lbls = {}, {}
        items = [
            ("pending", "Ждут ответа"), ("hour", "Автоответов за час"),
            ("floods", "FloodWait за 24 ч"), ("in", "Входящих всего"),
            ("auto", "Автоответов всего"), ("templates", "Шаблонов"),
        ]
        last = len(items) - 1
        for i, (key, title) in enumerate(items):
            row.columnconfigure(i, weight=1, uniform="stat")
            c = Card(row, radius=12, pad=13)
            c.grid(row=0, column=i, sticky="nsew",
                   padx=(0 if i == 0 else px(5), 0 if i == last else px(5)))
            tk.Label(c.body, text=title, font=F(9), fg=MUTED, bg=SURFACE, anchor="w",
                     justify="left", wraplength=px(105)).pack(anchor="w")
            v = tk.StringVar(value="—")
            lbl = tk.Label(c.body, textvariable=v, font=F(18, "bold"), fg=TEXT, bg=SURFACE)
            lbl.pack(anchor="w", pady=(px(2), 0))
            self.stat_vars[key], self.stat_lbls[key] = v, lbl

    def _build_tabs(self):
        card = Card(self.main, stretch=True)
        card.pack(fill="both", expand=True, pady=(px(12), 0))
        body = card.body

        bar = tk.Frame(body, bg=SURFACE)
        bar.pack(fill="x")
        holder = tk.Frame(body, bg=SURFACE)
        holder.pack(fill="both", expand=True, pady=(px(12), 0))

        self._pages, self._tab_btns = {}, {}
        tabs = [("log", "Журнал", self._tab_log), ("hist", "Последние ответы", self._tab_history),
                ("tpl", "Шаблоны", self._tab_templates), ("set", "Настройки", self._tab_settings)]
        for key, title, builder in tabs:
            btn = tk.Label(bar, text=title, font=F(10, "bold"), fg=MUTED, bg=SURFACE,
                           padx=px(14), pady=px(7), cursor="hand2")
            btn.pack(side="left", padx=(0, px(6)))
            btn.bind("<Button-1>", lambda e, k=key: self._select_tab(k))
            btn.bind("<Enter>", lambda e, k=key: self._tab_hover(k, True))
            btn.bind("<Leave>", lambda e, k=key: self._tab_hover(k, False))
            self._tab_btns[key] = btn
            page = tk.Frame(holder, bg=SURFACE)
            self._pages[key] = page
            builder(page)
        self._active_tab = ""
        self._select_tab("log")

    def _select_tab(self, key):
        self._active_tab = key
        for k, page in self._pages.items():
            if k == key:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
        for k in self._tab_btns:
            self._tab_hover(k, False)

    def _tab_hover(self, key, over):
        btn = self._tab_btns[key]
        if key == self._active_tab:
            btn.config(bg=mix(ACCENT, SURFACE, 0.72), fg="#ffffff")
        elif over:
            btn.config(bg=SURFACE2, fg=TEXT)
        else:
            btn.config(bg=SURFACE, fg=MUTED)

    def _framed(self, parent):
        """Рамка 1px вокруг таблицы / журнала."""
        return tk.Frame(parent, bg=FIELD, highlightthickness=1, highlightbackground=BORDER)

    @staticmethod
    def _scroll(parent, widget):
        sb = ttk.Scrollbar(parent, orient="vertical", command=widget.yview,
                           style="Dark.Vertical.TScrollbar")
        widget.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")

    def _empty_label(self, parent, text):
        lbl = tk.Label(parent, text=text, bg=FIELD, fg=DIM, font=F(10))
        lbl.place(relx=0.5, rely=0.5, anchor="center")
        return lbl

    # ---- журнал ----
    def _tab_log(self, page):
        frame = self._framed(page)
        frame.pack(fill="both", expand=True)
        self.log_box = tk.Text(frame, state="disabled", font=(MONO, 10), wrap="word", bg=FIELD,
                               fg=mix(TEXT, FIELD, 0.15), relief="flat", bd=0, highlightthickness=0,
                               padx=px(12), pady=px(10), spacing1=px(2), insertbackground=TEXT,
                               selectbackground=SEL, selectforeground="#ffffff")
        self._scroll(frame, self.log_box)
        self.log_box.pack(side="left", fill="both", expand=True)
        self.log_box.tag_config("ts", foreground=DIM)
        self.log_box.tag_config("warn", foreground=ORANGE)
        self.log_box.tag_config("err", foreground=RED)
        RoundButton(page, "Очистить", self._clear_log, width=100, height=32).pack(
            anchor="e", pady=(px(10), 0))

    def _clear_log(self):
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")

    # ---- последние автоответы ----
    def _tab_history(self, page):
        frame = self._framed(page)
        frame.pack(fill="both", expand=True)
        self.hist = ttk.Treeview(frame, columns=("time", "who", "text"), show="headings",
                                 style="Dark.Treeview", selectmode="browse")
        for c, t, w, stretch in (("time", "Время", 130, False), ("who", "Кому", 160, False),
                                 ("text", "Текст", 480, True)):
            self.hist.heading(c, text=t, anchor="w")
            self.hist.column(c, width=px(w), anchor="w", stretch=stretch)
        self._scroll(frame, self.hist)
        self.hist.pack(side="left", fill="both", expand=True)
        self.hist.tag_configure("odd", background=ROW_ALT)
        self.hist_empty = self._empty_label(frame, "Автоответов пока нет")

    # ---- шаблоны ----
    def _tab_templates(self, page):
        page.columnconfigure(0, weight=3)
        page.columnconfigure(1, weight=2, minsize=px(300))
        page.rowconfigure(0, weight=1)

        frame = self._framed(page)
        frame.grid(row=0, column=0, sticky="nsew", padx=(0, px(14)))
        self.tpl_tree = ttk.Treeview(frame, columns=("key", "text"), show="headings",
                                     style="Dark.Treeview", selectmode="browse")
        self.tpl_tree.heading("key", text="Ключ (слово в сообщении)", anchor="w")
        self.tpl_tree.heading("text", text="Ответ", anchor="w")
        self.tpl_tree.column("key", width=px(170), anchor="w", stretch=False)
        self.tpl_tree.column("text", width=px(300), anchor="w", stretch=True)
        self._scroll(frame, self.tpl_tree)
        self.tpl_tree.pack(side="left", fill="both", expand=True)
        self.tpl_tree.tag_configure("odd", background=ROW_ALT)
        self.tpl_tree.bind("<<TreeviewSelect>>", self._tpl_select)
        self.tpl_empty = self._empty_label(frame, "Шаблонов пока нет")

        form = tk.Frame(page, bg=SURFACE)
        form.grid(row=0, column=1, sticky="nsew")
        tk.Label(form, text="КЛЮЧ", font=F(8, "bold"), fg=MUTED, bg=SURFACE).pack(anchor="w")
        self.tpl_key = make_entry(form)
        self.tpl_key.pack(fill="x", pady=(px(4), px(10)), ipady=px(6))
        tk.Label(form, text="ОТВЕТ", font=F(8, "bold"), fg=MUTED, bg=SURFACE).pack(anchor="w")
        self.tpl_text = make_text(form, height=4)
        self.tpl_text.pack(fill="both", expand=True, pady=(px(4), px(10)))

        row = tk.Frame(form, bg=SURFACE)
        row.pack(fill="x")
        RoundButton(row, "Сохранить", self._tpl_save, style="primary", width=120,
                    height=34).pack(side="left")
        RoundButton(row, "Удалить", self._tpl_delete, style="ghost-danger", width=100,
                    height=34).pack(side="left", padx=px(8))
        RoundButton(form, "Перечитать templates.txt", self._tpl_reload, height=34,
                    width=232).pack(anchor="w", pady=(px(8), 0))
        tk.Label(form, fg=MUTED, bg=SURFACE, font=F(9), justify="left", anchor="w",
                 wraplength=px(300),
                 text="В ответе: {name} {time} {date}. Символ | делит ответ на несколько сообщений."
                 ).pack(anchor="w", pady=(px(10), 0))

    def _tpl_select(self, _=None):
        sel = self.tpl_tree.selection()
        if not sel:
            return
        key, text = self.tpl_tree.item(sel[0], "values")
        self.tpl_key.delete(0, "end")
        self.tpl_key.insert(0, key)
        self.tpl_text.delete("1.0", "end")
        self.tpl_text.insert("1.0", text.replace(" ⏎ ", "\n"))

    def _tpl_save(self):
        key = self.tpl_key.get().strip()
        text = self.tpl_text.get("1.0", "end").strip()
        if not key or not text:
            themed_box(self, "Шаблон", "Заполните ключ и ответ.", "warning")
            return
        br.db_template_save(key, text)
        self._refresh_templates()
        self._refresh_stats()

    def _tpl_delete(self):
        key = self.tpl_key.get().strip()
        if not key:
            return
        if themed_box(self, "Удалить", f"Удалить шаблон «{key}»?", "question",
                      yes="Удалить", no="Отмена", danger=True):
            br.db_template_delete(key)
            self.tpl_key.delete(0, "end")
            self.tpl_text.delete("1.0", "end")
            self._refresh_templates()
            self._refresh_stats()

    def _tpl_reload(self):
        n = br.db_templates_reload_from_file()
        self._refresh_templates()
        self._refresh_stats()
        themed_box(self, "templates.txt", f"Загружено ключей: {n}", "info")

    def _refresh_templates(self):
        self.tpl_tree.delete(*self.tpl_tree.get_children())
        rows = br.db_templates()
        for i, (key, text) in enumerate(rows):
            self.tpl_tree.insert("", "end", values=(key, text.replace("\n", " ⏎ ")),
                                 tags=("odd",) if i % 2 else ())
        if rows:
            self.tpl_empty.place_forget()
        else:
            self.tpl_empty.place(relx=0.5, rely=0.5, anchor="center")

    # ---- настройки ----
    def _field(self, parent, title, row, col, span=1):
        f = tk.Frame(parent, bg=SURFACE)
        f.grid(row=row, column=col, columnspan=span, sticky="nsew",
               padx=(0, px(14)) if col == 0 and span == 1 else 0, pady=(0, px(12)))
        tk.Label(f, text=title.upper(), font=F(8, "bold"), fg=MUTED, bg=SURFACE).pack(anchor="w")
        return f

    @staticmethod
    def _hint(parent, text):
        tk.Label(parent, text=text, font=F(9), fg=DIM, bg=SURFACE, anchor="w", justify="left",
                 wraplength=px(380)).pack(anchor="w", pady=(px(4), 0))

    def _tab_settings(self, page):
        page.columnconfigure(0, weight=1, uniform="s")
        page.columnconfigure(1, weight=1, uniform="s")
        page.rowconfigure(3, weight=1)

        f = self._field(page, "Макс. автоответов в час", 0, 0)
        self.set_max = make_spin(f, 1, 200)
        set_spin(self.set_max, config.MAX_REPLIES_PER_HOUR)
        self.set_max.pack(anchor="w", pady=(px(4), 0), ipady=px(5))

        f = self._field(page, "Мат в ответах ИИ, %", 0, 1)
        self.set_swear = make_spin(f, 0, 100, 5)
        set_spin(self.set_swear, config.SWEAR_CHANCE)
        self.set_swear.pack(anchor="w", pady=(px(4), 0), ipady=px(5))
        self._hint(f, "шанс, что в ответе будет мат; 0 = никогда")

        f = self._field(page, "Белый список", 1, 0)
        self.set_white = make_entry(f)
        self.set_white.insert(0, ", ".join(sorted(config.WHITELIST)))
        self.set_white.pack(fill="x", pady=(px(4), 0), ipady=px(6))
        self._hint(f, "только им отвечать; username без @ или id через запятую; пусто = всем")

        f = self._field(page, "Чёрный список", 1, 1)
        self.set_black = make_entry(f)
        self.set_black.insert(0, ", ".join(sorted(config.BLACKLIST)))
        self.set_black.pack(fill="x", pady=(px(4), 0), ipady=px(6))

        f = self._field(page, "Ответ по умолчанию", 2, 0, span=2)
        self.set_default = make_entry(f)
        self.set_default.insert(0, config.DEFAULT_REPLY)
        self.set_default.pack(fill="x", pady=(px(4), 0), ipady=px(6))

        f = self._field(page, "Промпт ИИ", 3, 0, span=2)
        self.set_prompt = make_text(f, height=4)
        self.set_prompt.insert("1.0", config.AI_SYSTEM_PROMPT)
        self.set_prompt.pack(fill="both", expand=True, pady=(px(4), 0))

        row = tk.Frame(page, bg=SURFACE)
        row.grid(row=4, column=0, columnspan=2, sticky="w")
        RoundButton(row, "Сохранить настройки", self._save_settings, style="primary", width=190,
                    height=36).pack(side="left")
        tk.Label(row, text="Применяется сразу, без перезапуска, и записывается в .env.",
                 font=F(9), fg=DIM, bg=SURFACE).pack(side="left", padx=px(14))

    def _save_settings(self):
        try:
            max_h = int(str(self.set_max.get()).strip())
            if max_h < 1:
                raise ValueError
        except ValueError:
            themed_box(self, "Настройки", "Лимит в час — целое число от 1.", "warning")
            return
        try:
            swear = int(str(self.set_swear.get()).strip())
            if not 0 <= swear <= 100:
                raise ValueError
        except ValueError:
            themed_box(self, "Настройки", "Мат: целое число от 0 до 100.", "warning")
            return
        white = self.set_white.get().strip()
        black = self.set_black.get().strip()
        default = self.set_default.get().strip()
        prompt = " ".join(self.set_prompt.get("1.0", "end").split())

        env_path = config.BASE_DIR / ".env"
        try:
            if not env_path.exists():
                env_path.touch()
            for k, v in (("MAX_REPLIES_PER_HOUR", str(max_h)), ("WHITELIST", white),
                         ("BLACKLIST", black), ("DEFAULT_REPLY", default), ("SWEAR_CHANCE", str(swear)),
                         ("AI_SYSTEM_PROMPT", prompt)):
                set_key(str(env_path), k, v, quote_mode="always")
        except Exception as e:
            themed_box(self, "Настройки", f"Не удалось записать .env:\n{e}", "error")
            return

        # применяем «вживую»
        config.MAX_REPLIES_PER_HOUR = max_h
        config.SWEAR_CHANCE = swear
        config.WHITELIST = _parse_set(white)
        config.BLACKLIST = _parse_set(black)
        config.DEFAULT_REPLY = default
        config.AI_SYSTEM_PROMPT = prompt
        logging.getLogger("gui").info("Настройки сохранены")
        themed_box(self, "Настройки", "Сохранено.", "info")

    # ================= действия кнопок =================
    def _start(self):
        errors = self.runner.start()
        if errors:
            themed_box(self, "Не хватает настроек", "\n".join(errors), "error")

    def _stop(self):
        self.runner.stop()

    def _toggle_pause(self):
        ab = self.runner.ab
        if ab is not None:
            self.runner.set_paused(not ab.paused)

    def _toggle_autoreply(self):
        self.runner.set_autoreply(self._autoreply_var.get())

    def _panic(self):
        if themed_box(
                self, "Аварийный стоп",
                "Остановить все отправки и отключить бота от аккаунта?\n\n"
                "Чтобы запустить снова, понадобится «Сбросить аварийный стоп».",
                "warning", yes="Остановить", no="Отмена", danger=True):
            self.runner.panic()

    def _reset_panic(self):
        br.db_reset_panic()
        self.runner.error = ""
        logging.getLogger("gui").info("Аварийный стоп сброшен")

    def _on_close(self):
        if self.runner.state in (br.RUNNING, br.STARTING):
            if not themed_box(self, "Выход", "Бот работает. Остановить и выйти?", "question",
                              yes="Выйти", no="Отмена"):
                return
            self.runner.stop()
            self.runner.wait_stopped(8)
        self.destroy()

    # ================= периодическое обновление =================
    def _anim(self):
        try:
            self.pill.tick()
        except Exception:
            pass
        self.after(70, self._anim)

    def _tick(self):
        try:
            self._drain_log()
            self._update_status()
            self._tick_n += 1
            if self._tick_n % 5 == 0:
                self._refresh_stats()
                self._refresh_history()
        except Exception:
            logging.getLogger("gui").exception("Ошибка обновления окна")
        self.after(TICK_MS, self._tick)

    def _drain_log(self):
        box = self.log_box
        at_bottom = box.yview()[1] >= 0.98
        n = 0
        box.config(state="normal")
        while n < 200:
            try:
                level, text = self.log_q.get_nowait()
            except queue.Empty:
                break
            tag = "err" if level >= logging.ERROR else "warn" if level >= logging.WARNING else ""
            ts, _, msg = text.partition("  ")
            box.insert("end", ts + "  ", "ts")
            box.insert("end", msg + "\n", tag)
            n += 1
        if n:
            # не даём журналу расти бесконечно
            lines = int(box.index("end-1c").split(".")[0])
            if lines > 1500:
                box.delete("1.0", f"{lines - 1000}.0")
            if at_bottom:
                box.see("end")
        box.config(state="disabled")

    def _update_status(self):
        r = self.runner
        state, ab = r.state, r.ab

        if ab is not None:
            paused, autoreply_on, panic = ab.paused, ab.autoreply_enabled, ab.panic
        else:
            paused = br.db_get_state("paused", "0") == "1"
            autoreply_on = br.db_get_state("autoreply", "1") == "1"
            panic = br.db_get_state("panic", "0") == "1"

        # ---- статус ----
        sub, pulse = "", False
        if state == br.RUNNING:
            if paused:
                text, color = "На паузе", ORANGE
                sub = "Бот подключён, но автоответы не отправляются."
            elif not autoreply_on:
                text, color = "Автоответчик выключен", ORANGE
                sub = "Бот подключён, но автоответы отключены."
            else:
                text, color, pulse = "Работает", GREEN, True
                sub = f"Аккаунт: {r.me_name}"
        elif state == br.STARTING:
            text, color, pulse = "Запускается…", YELLOW, True
        elif state == br.STOPPING:
            text, color, pulse = "Останавливается…", YELLOW, True
        elif panic:
            text, color = "Аварийный стоп", RED
            sub = "Нажмите «Сбросить аварийный стоп», затем «Запустить»."
        else:
            text, color = "Остановлен", GRAY
        is_error = bool(r.error) and state != br.RUNNING
        if is_error:
            sub = r.error
            if r.error != self._shown_error:
                self._shown_error = r.error
        self.pill.set(text, color, pulse)
        self.sub_lbl.config(text=sub or "Панель управления", fg=RED if is_error else MUTED)

        # ---- кнопки ----
        running = state == br.RUNNING
        self._enable(self.btn_start, state == br.STOPPED and not panic)
        self._enable(self.btn_stop, running)
        self._enable(self.btn_pause, running)
        self._enable(self.chk_auto, running)
        self.btn_panic.config(state="normal" if running else "disabled")
        self.btn_pause.config(text="▶  Продолжить" if paused else "⏸  Пауза")

        if state == br.STOPPED and panic:
            self.btn_reset.grid()
        else:
            self.btn_reset.grid_remove()

        if self._autoreply_var.get() != autoreply_on:
            self._autoreply_var.set(autoreply_on)
        self.chk_auto.config(text="Автоответчик включён" if autoreply_on
                             else "Автоответчик выключен")

        self.stat_vars["pending"].set(str(r.pending_count()) if running else "—")

    @staticmethod
    def _enable(widget, enabled: bool):
        widget.state(["!disabled"] if enabled else ["disabled"])

    def _refresh_stats(self):
        try:
            s = br.db_stats()
        except Exception:
            return
        self.stat_vars["hour"].set(f"{s['hour']} / {config.MAX_REPLIES_PER_HOUR}")
        self.stat_vars["floods"].set(f"{s['floods']} / {config.FLOOD_LIMIT_24H}")
        self.stat_vars["in"].set(str(s["in"]))
        self.stat_vars["auto"].set(str(s["auto"]))
        self.stat_vars["templates"].set(str(s["templates"]))
        # подсветка, когда подходим к лимитам
        self.stat_lbls["hour"].config(
            fg=ORANGE if s["hour"] >= config.MAX_REPLIES_PER_HOUR else TEXT)
        self.stat_lbls["floods"].config(
            fg=RED if s["floods"] >= config.FLOOD_LIMIT_24H else ORANGE if s["floods"] else TEXT)

    def _refresh_history(self):
        try:
            rows = br.db_last_autoreplies(30)
        except Exception:
            return
        self.hist.delete(*self.hist.get_children())
        for i, (ts, username, uid, text) in enumerate(rows):
            when = datetime.fromtimestamp(ts).strftime("%d.%m %H:%M:%S")
            who = f"@{username}" if username else str(uid)
            self.hist.insert("", "end", values=(when, who, (text or "").replace("\n", " ")),
                             tags=("odd",) if i % 2 else ())
        if rows:
            self.hist_empty.place_forget()
        else:
            self.hist_empty.place(relx=0.5, rely=0.5, anchor="center")


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
