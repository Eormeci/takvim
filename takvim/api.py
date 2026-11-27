"""Sunucu bağlantısı ve ortak yardımcılar (CLI ve tam ekran ikisi de kullanır)."""
import calendar
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

import httpx
from rich.text import Text

SERVER = os.environ.get("TAKVIM_SERVER", "http://127.0.0.1:8000")
SESSION_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "takvim" / "session.json"

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
         "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
GUNLER = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
KISA_GUNLER = ["Pt", "Sa", "Ça", "Pe", "Cu", "Ct", "Pz"]
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ApiError(Exception):
    def __init__(self, message: str, status: int = 0, suggestions: list[str] | None = None):
        super().__init__(message)
        self.status = status
        self.suggestions = suggestions or []


class Api:
    def __init__(self):
        self.token = ""
        self.username = ""
        self.http = httpx.Client(base_url=SERVER, timeout=10)
        if SESSION_FILE.exists():
            try:
                data = json.loads(SESSION_FILE.read_text())
                if data.get("server") == SERVER:
                    self.token, self.username = data["token"], data["username"]
            except (ValueError, KeyError):
                pass

    def _req(self, method: str, path: str, **kw):
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            r = self.http.request(method, path, headers=headers, **kw)
        except httpx.HTTPError:
            raise ApiError(f"Sunucuya bağlanılamadı ({SERVER}).")
        if r.is_success:
            return r.json()
        detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else None
        if isinstance(detail, dict):
            raise ApiError(detail.get("message", "Hata"), r.status_code, detail.get("suggestions"))
        if isinstance(detail, list):  # doğrulama hatası
            field = detail[0].get("loc", ["", ""])[-1]
            msg = {
                "username": "Kullanıcı adı 3-24 karakter olmalı (harf, rakam, _ .)",
                "password": "Şifre en az 4 karakter olmalı.",
            }.get(field, "Geçersiz bilgi.")
            raise ApiError(msg, r.status_code)
        raise ApiError(detail or f"Hata ({r.status_code})", r.status_code)

    def _save(self, data):
        self.token, self.username = data["token"], data["username"]
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(json.dumps({"server": SERVER, "token": self.token, "username": self.username}))
        SESSION_FILE.chmod(0o600)

    def login(self, u, p):
        self._save(self._req("POST", "/login", json={"username": u, "password": p}))

    def register(self, u, p):
        self._save(self._req("POST", "/register", json={"username": u, "password": p}))

    def logout(self):
        try:
            self._req("POST", "/logout")
        except ApiError:
            pass
        self.token = self.username = ""
        SESSION_FILE.unlink(missing_ok=True)

    def events(self, start: date, end: date) -> list[dict]:
        return self._req("GET", "/events", params={"start": start.isoformat(), "end": end.isoformat()})

    def save_event(self, ev: dict) -> dict:
        body = {k: ev.get(k, "") for k in ("day", "start", "end", "title", "note")}
        if ev.get("id"):
            return self._req("PUT", f"/events/{ev['id']}", json=body)
        return self._req("POST", "/events", json=body)

    def delete_event(self, event_id: int) -> None:
        self._req("DELETE", f"/events/{event_id}")


# ---------- ortak yardımcılar ----------

def fmt_time(ev: dict) -> str:
    if not ev["start"]:
        return "Tüm gün"
    return f"{ev['start']}–{ev['end']}" if ev["end"] else ev["start"]


def fmt_day(d: date) -> str:
    return f"{d.day} {AYLAR[d.month - 1]} {d.year}, {GUNLER[d.weekday()]}"


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def add_months(d: date, delta: int) -> date:
    y, m = d.year, d.month + delta
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


# Renk paletleri. Takvim stil adlarını kullanır (secili, bugun...), renkleri tema belirler.
PALETLER = {
    "orman": {
        "baslik": "bold #e5e9f0", "secili": "bold #1d2021 on #a3be8c", "bugun": "bold #ebcb8b",
        "plan": "#d08770", "haftasonu": "#b5a68a", "soluk": "#5f6b62", "vurgu": "#a3be8c",
        "tamam": "#a3be8c", "hata": "#bf616a",
    },
    "toprak": {
        "baslik": "bold #ebdbb2", "secili": "bold #282828 on #fabd2f", "bugun": "bold #b8bb26",
        "plan": "#fe8019", "haftasonu": "#d3869b", "soluk": "#7c6f64", "vurgu": "#fabd2f",
        "tamam": "#b8bb26", "hata": "#fb4934",
    },
    "gul": {
        "baslik": "bold #e0def4", "secili": "bold #191724 on #ebbcba", "bugun": "bold #f6c177",
        "plan": "#eb6f92", "haftasonu": "#c4a7e7", "soluk": "#6e6a86", "vurgu": "#ebbcba",
        "tamam": "#f6c177", "hata": "#eb6f92",
    },
    "lavanta": {
        "baslik": "bold #f5e0dc", "secili": "bold #1e1e2e on #cba6f7", "bugun": "bold #a6e3a1",
        "plan": "#f9e2af", "haftasonu": "#f5c2e7", "soluk": "#6c7086", "vurgu": "#cba6f7",
        "tamam": "#a6e3a1", "hata": "#f38ba8",
    },
    "sade": {
        "baslik": "bold #f2f2f2", "secili": "bold #111111 on #e6e6e6", "bugun": "bold #ff6b6b",
        "plan": "#ff6b6b", "haftasonu": "#9a9a9a", "soluk": "#5c5c5c", "vurgu": "#e6e6e6",
        "tamam": "#7bd88f", "hata": "#ff6b6b",
    },
}
TEMA = os.environ.get("TAKVIM_TEMA", "gul")
RENK = PALETLER.get(TEMA, PALETLER["gul"])


def month_text(year: int, month: int, busy: set[date], selected: date | None = None,
               focus: str = "day") -> Text:
    """`cal` benzeri küçük takvim. focus: "day", "<" veya ">" (başlıktaki oklar)."""
    today = date.today()
    # Başlık 3 satır: içinde bulunduğumuz ayın adı çerçeveli, diğer aylarda çerçeve yeri boş.
    this_month = (year, month) == (today.year, today.month)
    inner = f" {AYLAR[month - 1]} {year} "
    left = (18 - len(inner) - 2) // 2
    right = 18 - len(inner) - 2 - left
    frame = "bugun"
    t = Text()
    for top in (True, False):
        if top:
            border = ("╭" + "─" * len(inner) + "╮") if this_month else " " * (len(inner) + 2)
            t.append(" " + " " * left + border + " " * right + " \n", style=frame)
            continue
        t.append("<", style="secili" if focus == "<" else "vurgu")
        t.append(" " * left)
        t.append("│" if this_month else " ", style=frame)
        t.append(inner, style="baslik")
        t.append("│" if this_month else " ", style=frame)
        t.append(" " * right)
        t.append(">", style="secili" if focus == ">" else "vurgu")
    border = ("╰" + "─" * len(inner) + "╯") if this_month else " " * (len(inner) + 2)
    t.append("\n " + " " * left + border + " " * right + " ", style=frame)
    t.append("\n" + " ".join(KISA_GUNLER) + "\n", style="soluk")
    for week in calendar.Calendar().monthdatescalendar(year, month):
        for d in week:
            if d.month != month:
                t.append("   ")
                continue
            if d == selected and focus == "day":
                style = "secili"
            elif d == today:
                style = "bugun"
            elif d.weekday() >= 5:
                style = "haftasonu"
            else:
                style = ""
            t.append(f"{d.day:>2}", style=style)
            t.append("•" if d in busy else " ", style="plan")
        t.append("\n")
    t.rstrip()
    return t
