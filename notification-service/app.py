import logging
import os
import sqlite3
from contextlib import asynccontextmanager, closing

from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("notification-service")

DB_PATH = os.getenv("DB_PATH", "notifications.db")


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@asynccontextmanager
async def lifespan(app):
    with closing(connect()) as conn, conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS notifications ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT NOT NULL, "
            "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    yield


app = FastAPI(lifespan=lifespan)


class Notify(BaseModel):
    message: str


@app.post("/notify")
def notify(data: Notify):
    log.info("уведомление: %s", data.message)
    with closing(connect()) as conn, conn:
        conn.execute("INSERT INTO notifications (message) VALUES (?)", (data.message,))
    return {"status": "sent"}


@app.get("/notifications")
def list_notifications():
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM notifications ORDER BY id")
        return {"notifications": [dict(r) for r in rows]}


@app.get("/health")
def health():
    return {"status": "ok"}
