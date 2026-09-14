"""Точка входа: FastAPI + слушатель Telegram + воркер + ревью-бот."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

import config
import db
import review
import telegram_source
import worker
import x_api

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("main")

_tasks: list[asyncio.Task] = []


def _check_token(token: str) -> None:
    if not config.ADMIN_TOKEN or token != config.ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="bad token")


@asynccontextmanager
async def lifespan(app: FastAPI):
    problems = config.validate()
    for p in problems:
        log.error("КОНФИГ: %s", p)
    if problems:
        raise RuntimeError("конфигурация неполная, смотрите логи выше")

    db.init()
    log.info("БД готова: %s", config.DB_PATH)

    if config.X_ENABLED and not config.DRY_RUN:
        try:
            log.info("X-аккаунт: @%s", await asyncio.to_thread(x_api.whoami))
        except Exception as e:  # noqa: BLE001
            log.error("X: не смог проверить аккаунт — %s", e)

    await telegram_source.start()
    _tasks.append(asyncio.create_task(worker.loop()))
    if config.REVIEW_ENABLED:
        _tasks.append(asyncio.create_task(review.poll_loop()))

    try:
        yield
    finally:
        for t in _tasks:
            t.cancel()
        await telegram_source.stop()


app = FastAPI(title="tg2x — Telegram → X", lifespan=lifespan)


@app.get("/")
async def root():
    return {"ok": True, "service": "tg2x"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/status")
async def status():
    return {
        "source": config.SOURCE_CHAT,
        "review_enabled": config.REVIEW_ENABLED,
        "dry_run": config.DRY_RUN,
        "counts": db.stats(),
        "recent": db.recent(20),
    }


@app.get("/admin/dialogs")
async def dialogs(token: str = Query("")):
    _check_token(token)
    return {"dialogs": await telegram_source.list_dialogs()}


@app.post("/admin/backfill")
async def backfill(token: str = Query(""), limit: int = Query(3, ge=1, le=20)):
    """Забрать последние N постов канала в очередь — удобно для первой проверки."""
    _check_token(token)
    return {"added": await telegram_source.backfill(limit)}


@app.post("/admin/requeue/{pid}")
async def requeue(pid: str, token: str = Query("")):
    """Вернуть пост в обработку (перепишется заново)."""
    _check_token(token)
    post = db.get_post(pid)
    if not post:
        raise HTTPException(status_code=404, detail="not found")
    db.update_post(pid, status="pending", tweet_text=None, review_msg_id=None,
                   attempts=0, error=None, publish_after=0)
    return {"ok": True, "id": pid, "was": post["status"]}


@app.post("/admin/skip/{pid}")
async def skip(pid: str, token: str = Query("")):
    _check_token(token)
    if not db.get_post(pid):
        raise HTTPException(status_code=404, detail="not found")
    db.update_post(pid, status="skipped")
    return {"ok": True, "id": pid}
