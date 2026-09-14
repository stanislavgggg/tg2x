"""Ревью черновиков через Telegram-бота: кнопки «Опубликовать / Переписать / Пропустить».

Свой текст: ответьте (reply) на сообщение с черновиком — ваш текст станет твитом
и уйдёт в публикацию.
"""
import asyncio
import json
import logging
import os

import httpx

import config
import db
from rewriter import x_len

log = logging.getLogger("review")

API = "https://api.telegram.org/bot{token}/{method}"
OFFSET_FILE = os.path.join(config.DATA_DIR, "tg_offset.json")


def _url(method: str) -> str:
    return API.format(token=config.REVIEW_BOT_TOKEN, method=method)


def _load_offset() -> int:
    try:
        with open(OFFSET_FILE) as f:
            return int(json.load(f).get("offset", 0))
    except Exception:  # noqa: BLE001
        return 0


def _save_offset(offset: int) -> None:
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(OFFSET_FILE, "w") as f:
            json.dump({"offset": offset}, f)
    except Exception as e:  # noqa: BLE001
        log.warning("не смог сохранить offset: %s", e)


def _keyboard(pid: str) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "✅ Опубликовать", "callback_data": f"ok:{pid}"},
            {"text": "🔄 Переписать", "callback_data": f"re:{pid}"},
            {"text": "🚫 Пропустить", "callback_data": f"no:{pid}"},
        ]]
    }


def _caption(post, tweet: str) -> str:
    return (
        f"Черновик твита · {x_len(tweet)}/{config.X_TEXT_LIMIT} симв.\n"
        f"Источник: msg {post['first_msg_id']}\n\n"
        f"{tweet}\n\n"
        f"Ответьте на это сообщение своим текстом, чтобы заменить черновик."
    )


async def send_draft(post, tweet: str) -> int | None:
    """Отправляет черновик в чат ревью, возвращает message_id."""
    data = {
        "chat_id": config.REVIEW_CHAT_ID,
        "reply_markup": json.dumps(_keyboard(post["id"])),
    }
    media_path = post["media_path"]
    async with httpx.AsyncClient(timeout=60) as client:
        if media_path and os.path.exists(media_path):
            data["caption"] = _caption(post, tweet)[:1024]
            with open(media_path, "rb") as f:
                r = await client.post(_url("sendPhoto"), data=data, files={"photo": f})
        else:
            data["text"] = _caption(post, tweet)[:4000]
            data["disable_web_page_preview"] = "true"
            r = await client.post(_url("sendMessage"), data=data)
    if r.status_code != 200:
        log.error("не смог отправить черновик: %s %s", r.status_code, r.text[:300])
        return None
    return r.json()["result"]["message_id"]


async def _answer_callback(cb_id: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=20) as client:
        await client.post(_url("answerCallbackQuery"), data={"callback_query_id": cb_id, "text": text})


async def _clear_buttons(message_id: int) -> None:
    async with httpx.AsyncClient(timeout=20) as client:
        await client.post(_url("editMessageReplyMarkup"), data={
            "chat_id": config.REVIEW_CHAT_ID,
            "message_id": message_id,
            "reply_markup": json.dumps({"inline_keyboard": []}),
        })


async def notify(text: str, reply_to: int | None = None) -> None:
    data = {"chat_id": config.REVIEW_CHAT_ID, "text": text[:4000], "disable_web_page_preview": "true"}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    async with httpx.AsyncClient(timeout=20) as client:
        await client.post(_url("sendMessage"), data=data)


async def _handle_callback(cb: dict) -> None:
    data = cb.get("data", "")
    cb_id = cb["id"]
    msg_id = cb.get("message", {}).get("message_id")
    action, _, pid = data.partition(":")
    post = db.get_post(pid)
    if not post:
        await _answer_callback(cb_id, "Пост не найден")
        return
    if post["status"] not in ("review", "failed"):
        await _answer_callback(cb_id, f"Уже в статусе {post['status']}")
        return

    if action == "ok":
        db.update_post(pid, status="approved", error=None)
        await _answer_callback(cb_id, "Отправляю в X")
    elif action == "re":
        db.update_post(pid, status="pending", tweet_text=None, review_msg_id=None)
        await _answer_callback(cb_id, "Переписываю")
    elif action == "no":
        db.update_post(pid, status="skipped")
        await _answer_callback(cb_id, "Пропущено")
    else:
        await _answer_callback(cb_id, "Неизвестная команда")
        return

    if msg_id:
        await _clear_buttons(msg_id)


async def _handle_message(msg: dict) -> None:
    """Свой текст в ответ на черновик."""
    reply = msg.get("reply_to_message")
    text = (msg.get("text") or "").strip()
    if not reply or not text:
        return
    post = db.get_post_by_review_msg(reply["message_id"])
    if not post or post["status"] != "review":
        return
    if x_len(text) > config.X_TEXT_LIMIT:
        await notify(
            f"Ваш текст {x_len(text)} симв. — больше лимита {config.X_TEXT_LIMIT}. Не принял.",
            reply_to=msg["message_id"],
        )
        return
    db.update_post(post["id"], tweet_text=text, status="approved", error=None)
    await _clear_buttons(reply["message_id"])
    await notify("Принял ваш текст, публикую.", reply_to=msg["message_id"])


async def poll_loop() -> None:
    """Long polling getUpdates."""
    offset = _load_offset()
    log.info("ревью-бот запущен, offset=%s", offset)
    while True:
        try:
            async with httpx.AsyncClient(timeout=40) as client:
                r = await client.get(_url("getUpdates"), params={
                    "offset": offset,
                    "timeout": 30,
                    "allowed_updates": json.dumps(["callback_query", "message"]),
                })
            if r.status_code != 200:
                log.warning("getUpdates: %s %s", r.status_code, r.text[:200])
                await asyncio.sleep(5)
                continue
            for upd in r.json().get("result", []):
                offset = upd["update_id"] + 1
                try:
                    if "callback_query" in upd:
                        await _handle_callback(upd["callback_query"])
                    elif "message" in upd:
                        await _handle_message(upd["message"])
                except Exception as e:  # noqa: BLE001
                    log.exception("ошибка обработки апдейта: %s", e)
            _save_offset(offset)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("ошибка polling: %s", e)
            await asyncio.sleep(5)
