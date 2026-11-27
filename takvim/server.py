"""Takvim sunucusu: kullanıcılar ve etkinlikler. Şimdilik SQLite, sonra buluta taşınacak."""
import hashlib
import os
import random
import secrets
import sqlite3
from contextlib import contextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

DB_PATH = os.environ.get("TAKVIM_DB", "takvim.db")
app = FastAPI(title="Takvim")


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                salt BLOB NOT NULL,
                pw_hash BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                day TEXT NOT NULL,          -- YYYY-MM-DD
                start TEXT NOT NULL DEFAULT '',  -- HH:MM (boş = tüm gün)
                end TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_events_user_day ON events(user_id, day);
            """
        )


init_db()


# ---------- yardımcılar ----------

def hash_pw(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)


def username_taken(c, username: str) -> bool:
    return c.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone() is not None


def suggest(c, username: str, count: int = 3) -> list[str]:
    base = username.strip()[:20]
    candidates = [f"{base}{n}" for n in range(1, 10)]
    candidates += [f"{base}_{random.randint(10, 99)}" for _ in range(6)]
    candidates += [f"{base}.{random.randint(100, 999)}" for _ in range(6)]
    out = []
    for cand in candidates:
        if cand not in out and not username_taken(c, cand):
            out.append(cand)
        if len(out) == count:
            break
    return out


def new_session(c, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    c.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user_id))
    return token


def current_user(authorization: str = Header(default="")) -> int:
    token = authorization.removeprefix("Bearer ").strip()
    with db() as c:
        row = c.execute("SELECT user_id FROM sessions WHERE token = ?", (token,)).fetchone()
    if not row:
        raise HTTPException(401, "Oturum geçersiz, tekrar giriş yap.")
    return row["user_id"]


# ---------- modeller ----------

class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=24, pattern=r"^[A-Za-z0-9_.]+$")
    password: str = Field(min_length=4, max_length=128)


class EventIn(BaseModel):
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    start: str = Field(default="", pattern=r"^(\d{2}:\d{2})?$")
    end: str = Field(default="", pattern=r"^(\d{2}:\d{2})?$")
    title: str = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=2000)


# ---------- kullanıcı ----------

@app.get("/check/{username}")
def check(username: str):
    with db() as c:
        if username_taken(c, username):
            return {"available": False, "suggestions": suggest(c, username)}
    return {"available": True, "suggestions": []}


@app.post("/register")
def register(cred: Credentials):
    with db() as c:
        if username_taken(c, cred.username):
            raise HTTPException(
                409,
                {"message": "Bu kullanıcı adı alınmış.", "suggestions": suggest(c, cred.username)},
            )
        salt = secrets.token_bytes(16)
        cur = c.execute(
            "INSERT INTO users (username, salt, pw_hash) VALUES (?, ?, ?)",
            (cred.username, salt, hash_pw(cred.password, salt)),
        )
        return {"token": new_session(c, cur.lastrowid), "username": cred.username}


@app.post("/login")
def login(cred: Credentials):
    with db() as c:
        row = c.execute(
            "SELECT id, username, salt, pw_hash FROM users WHERE username = ?", (cred.username,)
        ).fetchone()
        if not row or not secrets.compare_digest(hash_pw(cred.password, row["salt"]), row["pw_hash"]):
            raise HTTPException(401, "Kullanıcı adı veya şifre yanlış.")
        return {"token": new_session(c, row["id"]), "username": row["username"]}


@app.post("/logout")
def logout(authorization: str = Header(default="")):
    token = authorization.removeprefix("Bearer ").strip()
    with db() as c:
        c.execute("DELETE FROM sessions WHERE token = ?", (token,))
    return {"ok": True}


# ---------- etkinlikler ----------

@app.get("/events")
def list_events(start: str, end: str, uid: int = Depends(current_user)):
    """start..end (dahil) arasındaki etkinlikler. Tarihler YYYY-MM-DD."""
    with db() as c:
        rows = c.execute(
            "SELECT id, day, start, end, title, note FROM events "
            "WHERE user_id = ? AND day BETWEEN ? AND ? ORDER BY day, start = '' DESC, start",
            (uid, start, end),
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/events")
def create_event(ev: EventIn, uid: int = Depends(current_user)):
    with db() as c:
        cur = c.execute(
            "INSERT INTO events (user_id, day, start, end, title, note) VALUES (?, ?, ?, ?, ?, ?)",
            (uid, ev.day, ev.start, ev.end, ev.title, ev.note),
        )
        return {"id": cur.lastrowid, **ev.model_dump()}


@app.put("/events/{event_id}")
def update_event(event_id: int, ev: EventIn, uid: int = Depends(current_user)):
    with db() as c:
        cur = c.execute(
            "UPDATE events SET day=?, start=?, end=?, title=?, note=? WHERE id=? AND user_id=?",
            (ev.day, ev.start, ev.end, ev.title, ev.note, event_id, uid),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Etkinlik bulunamadı.")
    return {"id": event_id, **ev.model_dump()}


@app.delete("/events/{event_id}")
def delete_event(event_id: int, uid: int = Depends(current_user)):
    with db() as c:
        cur = c.execute("DELETE FROM events WHERE id=? AND user_id=?", (event_id, uid))
        if cur.rowcount == 0:
            raise HTTPException(404, "Etkinlik bulunamadı.")
    return {"ok": True}


def main():
    import uvicorn

    uvicorn.run(app, host=os.environ.get("TAKVIM_HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 8000)))


if __name__ == "__main__":
    main()
