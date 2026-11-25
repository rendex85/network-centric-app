import logging
import os
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, closing
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("order-service")

DB_PATH = os.getenv("DB_PATH", "orders.db")
USER_SERVICE_URL = os.getenv("USER_SERVICE_URL", "http://user-service:5000")
NOTIFICATION_SERVICE_URL = os.getenv("NOTIFICATION_SERVICE_URL", "http://notification-service:5001")


class CircuitBreaker:
    """После threshold отказов подряд запросы к сервису не отправляются reset_timeout секунд."""

    def __init__(self, name, threshold=2, reset_timeout=15.0):
        self.name = name
        self.threshold = threshold
        self.reset_timeout = reset_timeout
        self.failures = 0
        self.opened_at = None
        self.lock = threading.Lock()

    def allow(self):
        with self.lock:
            if self.opened_at is None:
                return True
            if time.monotonic() - self.opened_at >= self.reset_timeout:
                log.info("circuit %s: half-open, пробный запрос", self.name)
                return True
            return False

    def success(self):
        with self.lock:
            if self.opened_at is not None:
                log.info("circuit %s: closed", self.name)
            self.failures = 0
            self.opened_at = None

    def failure(self):
        with self.lock:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.monotonic()
                log.warning("circuit %s: open на %.0f с после %d отказов", self.name, self.reset_timeout, self.failures)


user_breaker = CircuitBreaker("user-service")
notify_breaker = CircuitBreaker("notification-service")


def log_retry(state):
    log.warning("повтор запроса %s, попытка %d", state.fn.__name__, state.attempt_number)


@retry(stop=stop_after_attempt(2), wait=wait_fixed(0.5), retry=retry_if_exception_type(httpx.RequestError),
       before_sleep=log_retry, reraise=True)
def fetch_user(user_id):
    return httpx.get(f"{USER_SERVICE_URL}/users/{user_id}", timeout=2.0)


# POST повторяется только при ошибке соединения: запрос гарантированно не дошёл, дубля уведомления не будет
@retry(stop=stop_after_attempt(2), wait=wait_fixed(0.5), retry=retry_if_exception_type(httpx.ConnectError),
       before_sleep=log_retry, reraise=True)
def send_notification(message):
    httpx.post(f"{NOTIFICATION_SERVICE_URL}/notify", json={"message": message}, timeout=2.0).raise_for_status()


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@asynccontextmanager
async def lifespan(app):
    with closing(connect()) as conn, conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS orders ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, item TEXT NOT NULL, "
            "status TEXT NOT NULL, notification_failed INTEGER NOT NULL DEFAULT 0)"
        )
    yield


app = FastAPI(lifespan=lifespan)


class OrderCreate(BaseModel):
    user_id: int
    item: str


class OrderStatus(BaseModel):
    status: Literal["created", "paid", "shipped", "cancelled"]


def get_or_404(conn, order_id):
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="order not found")
    order = dict(row)
    order["notification_failed"] = bool(order["notification_failed"])
    return order


@app.post("/orders", status_code=201)
def create_order(data: OrderCreate):
    if not user_breaker.allow():
        raise HTTPException(status_code=503, detail="user-service unavailable (circuit open)")
    try:
        resp = fetch_user(data.user_id)
    except httpx.RequestError as e:
        user_breaker.failure()
        log.error("user-service недоступен: %s", e)
        raise HTTPException(status_code=502, detail="user-service unavailable")
    if resp.status_code not in (200, 404):
        user_breaker.failure()
        raise HTTPException(status_code=502, detail="user-service error")
    user_breaker.success()
    if resp.status_code == 404:
        raise HTTPException(status_code=400, detail="user not found")

    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO orders (user_id, item, status) VALUES (?, ?, 'created')",
            (data.user_id, data.item),
        )
        order_id = cur.lastrowid
    log.info("заказ #%d создан", order_id)

    notified = False
    if notify_breaker.allow():
        try:
            send_notification(f"Заказ #{order_id} создан для пользователя {data.user_id}: {data.item}")
            notify_breaker.success()
            notified = True
        except httpx.HTTPError as e:
            notify_breaker.failure()
            log.error("уведомление по заказу #%d не отправлено: %s", order_id, e)
    else:
        log.warning("circuit notification-service open, уведомление по заказу #%d пропущено", order_id)

    with closing(connect()) as conn, conn:
        if not notified:
            conn.execute("UPDATE orders SET notification_failed = 1 WHERE id = ?", (order_id,))
        return get_or_404(conn, order_id)


@app.get("/orders")
def list_orders():
    with closing(connect()) as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM orders ORDER BY id")]
        return [get_or_404(conn, i) for i in ids]


@app.get("/orders/{order_id}")
def get_order(order_id: int):
    with closing(connect()) as conn:
        return get_or_404(conn, order_id)


@app.patch("/orders/{order_id}")
def update_order_status(order_id: int, data: OrderStatus):
    with closing(connect()) as conn, conn:
        get_or_404(conn, order_id)
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (data.status, order_id))
        return get_or_404(conn, order_id)


@app.delete("/orders/{order_id}", status_code=204)
def delete_order(order_id: int):
    with closing(connect()) as conn, conn:
        get_or_404(conn, order_id)
        conn.execute("DELETE FROM orders WHERE id = ?", (order_id,))


@app.get("/health")
def health():
    return {"status": "ok"}
