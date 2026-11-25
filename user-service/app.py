import os
import sqlite3
from contextlib import asynccontextmanager, closing

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

DB_PATH = os.getenv("DB_PATH", "users.db")


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@asynccontextmanager
async def lifespan(app):
    with closing(connect()) as conn, conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL)"
        )
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO users (name, email) VALUES (?, ?)",
                [("Иван Петров", "ivan@example.com"), ("Мария Сидорова", "maria@example.com")],
            )
    yield


app = FastAPI(lifespan=lifespan)


class UserIn(BaseModel):
    name: str
    email: str


def get_or_404(conn, user_id):
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")
    return dict(row)


@app.get("/users")
def list_users():
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY id")]


@app.get("/users/{user_id}")
def get_user(user_id: int):
    with closing(connect()) as conn:
        return get_or_404(conn, user_id)


@app.post("/users", status_code=201)
def create_user(data: UserIn):
    with closing(connect()) as conn, conn:
        cur = conn.execute("INSERT INTO users (name, email) VALUES (?, ?)", (data.name, data.email))
        return get_or_404(conn, cur.lastrowid)


@app.put("/users/{user_id}")
def update_user(user_id: int, data: UserIn):
    with closing(connect()) as conn, conn:
        get_or_404(conn, user_id)
        conn.execute("UPDATE users SET name = ?, email = ? WHERE id = ?", (data.name, data.email, user_id))
        return get_or_404(conn, user_id)


@app.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: int):
    with closing(connect()) as conn, conn:
        get_or_404(conn, user_id)
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


@app.get("/health")
def health():
    return {"status": "ok"}
