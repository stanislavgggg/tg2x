"""Фоновый воркер: собрать пост → переписать → (ревью) → опубликовать в X."""
import asyncio
import logging
import time

import config
import db
import rewriter
import review
import x_api

log = logging.getLogger("worker")


def _passes_filters(text: str, media_path: str | None) -> tuple[bool, str]:
    if config.REQUIRE_MEDIA and not media_path:
        return False, "нет картинки"
    if len(text) < config.MIN_TEXT_LENGTH:
        return False, f"текст короче {config.MIN_TEXT_LENGTH} символов"
    low = text.lower()
    if config.SKIP_IF_CONTAINS:
        for token in [t.strip().lower() for t in config.SKIP_IF_CONTAINS.split(",") if t.strip()]:
            if token in low:
                return False, f"стоп-слово «{token}»"
    if config.ONLY_IF_CONTAINS:
        tokens = [t.strip().lower() for t in config.ONLY_IF_CONTAINS.split(",") if t.strip()]
        if tokens and not any(t in low for t in tokens):
            return False, "нет ни одного обязательного слова"
    return True, ""


async def _collect_groups() -> None:
    for chat_id, group_key in db.ready_groups(config.GROUP_WAIT_SECONDS):
        rows = db.take_group(chat_id, group_key)
        if not rows:
            continue
        # текст берём самый длинный из пачки, картинку — первую доступную
        text = max((r["text"] or "" for r in rows), key=len).strip()
        media_path = next((r["media_path"] for r in rows if r["media_path"]), None)
        first_msg_id = rows[0]["msg_id"]

        ok, reason = _passes_filters(text, media_path)
        status = "pending" if ok else "skipped"
        pid = db.create_post(chat_id, group_key, first_msg_id, text, media_path, status=status)
        if pid is None:
            continue
        if ok:
            log.info("новый пост %s из msg %s", pid, first_msg_id)
        else:
            db.update_post(pid, error=reason)
            log.info("пропускаю msg %s: %s", first_msg_id, reason)


async def _rewrite_pending() -> None:
    for post in db.posts_by_status("pending"):
        pid = post["id"]
        try:
            tweet = await rewriter.rewrite(post["source_text"])
        except Exception as e:  # noqa: BLE001
            log.exception("переписывание %s упало: %s", pid, e)
            db.bump_attempt(pid, f"rewrite: {e}")
            continue

        if config.REVIEW_ENABLED:
            db.update_post(pid, tweet_text=tweet, status="review", error=None)
            fresh = db.get_post(pid)
            msg_id = await review.send_draft(fresh, tweet)
            if msg_id:
                db.update_post(pid, review_msg_id=msg_id)
            else:
                db.bump_attempt(pid, "не удалось отправить черновик в ревью")
                db.update_post(pid, status="pending")
        else:
            db.update_post(
                pid,
                tweet_text=tweet,
                status="approved",
                error=None,
                publish_after=time.time() + config.POST_DELAY_SECONDS,
            )


async def _auto_approve_timeouts() -> None:
    if not (config.REVIEW_ENABLED and config.REVIEW_TIMEOUT_SECONDS > 0):
        return
    cutoff = time.time() - config.REVIEW_TIMEOUT_SECONDS
    for post in db.posts_by_status("review", limit=50):
        if post["updated_at"] <= cutoff:
            db.update_post(post["id"], status="approved")
            log.info("пост %s одобрен по таймауту ревью", post["id"])


async def _publish_approved() -> None:
    now = time.time()
    for post in db.posts_by_status("approved"):
        if post["publish_after"] and post["publish_after"] > now:
            continue
        pid = post["id"]
        try:
            tweet_id = await asyncio.to_thread(x_api.publish, post["tweet_text"], post["media_path"])
        except Exception as e:  # noqa: BLE001
            log.exception("публикация %s упала: %s", pid, e)
            db.bump_attempt(pid, f"x: {e}")
            fresh = db.get_post(pid)
            if fresh and fresh["attempts"] >= config.MAX_ATTEMPTS:
                db.update_post(pid, status="failed")
                if config.REVIEW_ENABLED:
                    await review.notify(f"❌ Не смог опубликовать пост {pid}: {e}")
            continue

        db.update_post(pid, status="posted", tweet_id=tweet_id, error=None)
        log.info("пост %s опубликован: %s", pid, tweet_id)
        if config.REVIEW_ENABLED and tweet_id != "dry-run":
            await review.notify(f"✅ Опубликовано: https://x.com/i/status/{tweet_id}")


async def loop() -> None:
    log.info("воркер запущен, тик %d сек.", config.WORKER_INTERVAL_SECONDS)
    while True:
        try:
            await _collect_groups()
            await _rewrite_pending()
            await _auto_approve_timeouts()
            if config.X_ENABLED:
                await _publish_approved()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("ошибка тика воркера: %s", e)
        await asyncio.sleep(config.WORKER_INTERVAL_SECONDS)
