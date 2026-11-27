"""takvim: terminalde akan, tamamen klavyeyle kullanılan sade takvim."""
import getpass
import os
import re
import select as _select
import sys
import termios
import tty
from contextlib import contextmanager
from datetime import date, timedelta

from rich.console import Console
from rich.text import Text
from rich.theme import Theme

from .api import RENK, TIME_RE, Api, ApiError, add_months, fmt_day, fmt_time, month_bounds, month_text

console = Console(highlight=False, theme=Theme(RENK))


# ---------------- terminal: tuş okuma ve yerinde çizim ----------------

KEYS = {b"[A": "up", b"[B": "down", b"[C": "right", b"[D": "left",
        b"OA": "up", b"OB": "down", b"OC": "right", b"OD": "left", b"[3~": "del"}


class Term:
    """Tuşları tek tek okumak için terminali cbreak moduna alır."""

    def __init__(self):
        self.fd = sys.stdin.fileno()
        self.saved = termios.tcgetattr(self.fd)

    def _raw(self):
        tty.setcbreak(self.fd)
        sys.stdout.write("\x1b[?25l")  # imleci gizle
        sys.stdout.flush()

    def _restore(self):
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()

    def __enter__(self):
        self._raw()
        return self

    def __exit__(self, *exc):
        self._restore()

    @contextmanager
    def cooked(self):
        """Yazı girişi (kullanıcı adı, başlık...) için geçici olarak normal mod."""
        self._restore()
        try:
            yield
        finally:
            self._raw()

    def _ready(self) -> bool:
        return bool(_select.select([self.fd], [], [], 0.03)[0])

    def key(self) -> str:
        b = os.read(self.fd, 1)
        if b == b"\x1b":
            if not self._ready():
                return "esc"
            seq = b""
            while self._ready():
                seq += os.read(self.fd, 1)
                if len(seq) >= 2 and (seq[-1:].isalpha() or seq[-1:] == b"~"):
                    break
            return KEYS.get(seq, "")
        if b in (b"\r", b"\n"):
            return "enter"
        if b == b"\x03":
            raise KeyboardInterrupt
        if b == b"\x04":
            raise EOFError
        if b[0] >= 0xC0:  # çok baytlı UTF-8 (ç, ş, ...)
            b += os.read(self.fd, 1 if b[0] < 0xE0 else 2 if b[0] < 0xF0 else 3)
        return b.decode(errors="ignore").lower()


class Region:
    """Terminalin son birkaç satırını yerinde yeniden çizer (ekranı kaplamaz)."""

    def __init__(self):
        self.lines = 0

    def draw(self, renderable):
        with console.capture() as cap:
            console.print(renderable)
        out = cap.get()
        self.clear(flush=False)
        sys.stdout.write(out)
        sys.stdout.flush()
        self.lines = out.count("\n")

    def clear(self, flush=True):
        if self.lines:
            sys.stdout.write(f"\x1b[{self.lines}F\x1b[J")
            self.lines = 0
        if flush:
            sys.stdout.flush()


def choose(term: Term, title: str, options: list[str]) -> int | None:
    """Ok tuşlarıyla seçim. Seçilenin sırasını döner, esc ile None."""
    region, i = Region(), 0
    while True:
        t = Text(title + "\n", style="baslik")
        for j, opt in enumerate(options):
            t.append("› " if j == i else "  ", style="vurgu")
            t.append(f" {opt} " if j == i else f" {opt}", style="secili" if j == i else "")
            t.append("\n")
        t.append("↑↓ seç · enter onayla", style="soluk")
        region.draw(t)
        k = term.key()
        if k == "up":
            i = (i - 1) % len(options)
        elif k == "down":
            i = (i + 1) % len(options)
        elif k == "enter":
            region.clear()
            console.print(f"[baslik]{title}[/] [vurgu]{options[i]}[/]")
            return i
        elif k == "esc":
            region.clear()
            return None


def ask(prompt: str) -> str:
    return console.input(f"[vurgu]{prompt}[/]").strip()


def err(msg: str):
    console.print(f"[hata]{msg}[/]")


def ok(msg: str):
    console.print(f"[tamam]✓[/] {msg}")


def parse_time(s: str) -> tuple[str, str] | None:
    """'14' '14:00' '9:30' '14-15' '14:00-15:30' -> (başlangıç, bitiş)"""
    def one(t):
        t = t.strip()
        if re.fullmatch(r"\d{1,2}", t):
            t = f"{int(t):02d}:00"
        elif re.fullmatch(r"\d:\d{2}", t):
            t = "0" + t
        return t if TIME_RE.match(t) else None

    parts = s.split("-")
    if len(parts) > 2:
        return None
    times = [one(p) for p in parts]
    if None in times:
        return None
    start, end = times[0], times[1] if len(times) == 2 else ""
    if end and end < start:
        return None
    return start, end


# ---------------- giriş / kayıt ----------------

def auth(api: Api, term: Term):
    while not api.token:
        c = choose(term, "Takvim", ["Giriş yap", "Kayıt ol"])
        if c is None:
            raise EOFError
        try:
            login(api, term) if c == 0 else register(api, term)
        except ApiError as e:
            err(str(e))
    ok(f"Hoş geldin, [baslik]{api.username}[/].\n")


def login(api: Api, term: Term):
    with term.cooked():
        u = ask("Kullanıcı adı: ")
        p = getpass.getpass("Şifre: ")
    api.login(u, p)


def register(api: Api, term: Term):
    with term.cooked():
        u = ask("Kullanıcı adı: ")
    while True:
        info = api._req("GET", f"/check/{u}") if u else {"available": False, "suggestions": []}
        if info["available"]:
            break
        sugg = info["suggestions"]
        title = f"“{u}” alınmış. Öneriler:" if u else "Kullanıcı adı boş olamaz."
        i = choose(term, title, sugg + ["Başka bir ad yaz"])
        if i is None or i == len(sugg):
            with term.cooked():
                u = ask("Kullanıcı adı: ")
        else:
            u = sugg[i]
    with term.cooked():
        while True:
            p = getpass.getpass("Şifre: ")
            if p == getpass.getpass("Şifre (tekrar): "):
                break
            err("Şifreler aynı değil.")
    api.register(u, p)


# ---------------- takvim ----------------

HELP = {
    "day": "←↑↓→ gez · enter planlar · a ekle · t bugün · ç çıkış yap · q kapat",
    "arrow": "← önceki ay · → sonraki ay · ↓ günlere dön · q kapat",
    "plans": "↑↓ seç · enter düzenle · s sil · a ekle · esc geri",
}


class CalendarView:
    def __init__(self, api: Api, term: Term):
        self.api, self.term = api, term
        self.region = Region()
        self.day = date.today()
        self.loaded: tuple[int, int] | None = None
        self.events: list[dict] = []
        self.focus = "day"  # "day" | "<" | ">" | "plans"
        self.cursor = 0  # plan listesindeki seçili satır

    def load(self, force=False):
        ym = (self.day.year, self.day.month)
        if force or ym != self.loaded:
            self.events = self.api.events(*month_bounds(*ym))
            self.loaded = ym

    def day_events(self) -> list[dict]:
        return [e for e in self.events if e["day"] == self.day.isoformat()]

    def frame(self, help_line=True) -> Text:
        busy = {date.fromisoformat(e["day"]) for e in self.events}
        cal_focus = self.focus if self.focus in ("<", ">") else "day" if self.focus == "day" else "none"
        t = month_text(self.day.year, self.day.month, busy, self.day, cal_focus)
        t.append("\n\n")
        t.append(fmt_day(self.day), style="baslik")
        if self.day == date.today():
            t.append("  · bugün", style="bugun")
        evs = self.day_events()
        if not evs:
            t.append("\n  Plan yok.", style="soluk")
        for i, ev in enumerate(evs):
            on = self.focus == "plans" and self.cursor == i
            t.append("\n› " if on else "\n  ", style="vurgu")
            line = Text(f"{fmt_time(ev):<13}", style="secili" if on else "plan")
            line.append(ev["title"], style="secili" if on else "")
            if ev["note"]:
                line.append(f"  ({ev['note']})", style="soluk")
            t.append(line)
        if help_line:
            key = "plans" if self.focus == "plans" else "day" if self.focus == "day" else "arrow"
            t.append("\n\n" + HELP[key], style="soluk")
        return t

    def run(self):
        while True:
            self.load()
            self.region.draw(self.frame())
            k = self.term.key()
            if k == "q" or (k == "esc" and self.focus != "plans"):
                self.region.draw(self.frame(help_line=False))
                return
            handler = {"day": self.on_day, "<": self.on_arrow, ">": self.on_arrow, "plans": self.on_plans}
            if handler[self.focus](k) == "logout":
                return

    def on_day(self, k: str):
        moves = {"left": -1, "right": 1, "up": -7, "down": 7}
        if k in moves:
            d = self.day + timedelta(days=moves[k])
            if d.month == self.day.month or k in ("left", "right"):
                self.day = d  # ←→ ay kenarında direkt önceki/sonraki aya geçer
            elif k == "up":  # ilk haftadan yukarı: başlıktaki oka çık
                self.focus = "<" if self.day.weekday() < 4 else ">"
        elif k == "t":
            self.day = date.today()
        elif k == "enter":
            if self.day_events():
                self.focus, self.cursor = "plans", 0
            else:
                self.edit({"day": self.day.isoformat()})
        elif k == "a":
            self.edit({"day": self.day.isoformat()})
        elif k in ("ç", "c"):
            self.region.clear()
            self.api.logout()
            console.print("Çıkış yapıldı.")
            return "logout"

    def on_arrow(self, k: str):
        if k in ("left", "right"):  # ← önceki ay, → sonraki ay (enter gerekmez)
            self.focus = "<" if k == "left" else ">"
            self.day = add_months(self.day, -1 if k == "left" else 1)
        elif k == "down":
            self.focus = "day"

    def on_plans(self, k: str):
        evs = self.day_events()
        if k == "up":
            self.cursor = max(0, self.cursor - 1)
        elif k == "down":
            self.cursor = min(len(evs) - 1, self.cursor + 1)
        elif k in ("enter", "d"):
            self.edit(evs[self.cursor])
        elif k in ("s", "del"):
            self.delete(evs[self.cursor])
        elif k == "a":
            self.edit({"day": self.day.isoformat()})
        elif k in ("esc", "left"):
            self.focus = "day"
        n = len(self.day_events())
        if self.focus == "plans":
            if n:
                self.cursor = min(self.cursor, n - 1)
            else:
                self.focus = "day"

    def edit(self, ev: dict):
        self.region.clear()
        console.print(f"[baslik]{fmt_day(self.day)}[/] [soluk]·[/] "
                      + ("planı düzenle" if ev.get("id") else "yeni plan"))
        with self.term.cooked():
            saved = edit_event(self.api, ev)
        if saved:
            self.load(force=True)
        console.print()

    def delete(self, ev: dict):
        self.region.clear()
        if choose(self.term, f"“{ev['title']}” silinsin mi?", ["Hayır", "Evet"]) == 1:
            self.api.delete_event(ev["id"])
            ok("Silindi.")
            self.load(force=True)
        console.print()


def edit_event(api: Api, ev: dict) -> bool:
    editing = bool(ev.get("id"))
    hint = lambda v: f" [soluk]\\[{v}][/]" if editing and v else ""  # noqa: E731

    title = ask(f"Başlık{hint(ev.get('title'))}: ") or ev.get("title", "")
    if not title:
        err("Başlık boş, vazgeçildi.")
        return False
    cur_time = f"{ev['start']}-{ev['end']}" if ev.get("end") else ev.get("start", "")
    while True:
        t = ask(f"Saat [soluk](ör. 14:00 ya da 9-10:30, boş = tüm gün)[/]{hint(cur_time)}: ")
        if not t and editing:
            t = cur_time
        if not t:
            start, end = "", ""
            break
        if parsed := parse_time(t):
            start, end = parsed
            break
        err("Saat anlaşılamadı.")
    note = ask(f"Not [soluk](isteğe bağlı)[/]{hint(ev.get('note'))}: ") or (ev.get("note", "") if editing else "")
    api.save_event({**ev, "title": title, "start": start, "end": end, "note": note})
    ok("Kaydedildi.")
    return True


def main():
    if not sys.stdin.isatty():
        print("takvim bir terminalde çalıştırılmalı.", file=sys.stderr)
        sys.exit(1)
    api = Api()
    try:
        with Term() as term:
            auth(api, term)
            CalendarView(api, term).run()
    except ApiError as e:
        if e.status == 401:
            api.logout()
            err("Oturum süresi dolmuş, tekrar aç: takvim")
        else:
            err(str(e))
    except (KeyboardInterrupt, EOFError):
        console.print()
