"""Слушатель канала-источника на Telethon (юзер-сессия)."""
import logging
import os

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaPhoto

import config
import db

log = logging.getLogger("tg_source")

_client: TelegramClient | None = None
_source_entity = None


def client() -> TelegramClient:
    global _client
    if _client is None:
        _client = TelegramClient(
            StringSession(config.TELEGRAM_STRING_SESSION),
            config.TELEGRAM_API_ID,
            config.TELEGRAM_API_HASH,
        )
    return _client


def _parse_source(value: str):
    v = value.strip()
    if v.startswith("@") or not (v.lstrip("-").isdigit()):
        return v
    return int(v)


async def _download_media(message) -> str | None:
    """Качает фото поста. Видео и документы пропускаем."""
    if not message.media:
        return None
    if not isinstance(message.media, MessageMediaPhoto):
        log.info("msg %s: медиа не фото (%s), пропускаю файл", message.id, type(message.media).__name__)
        return None
    os.makedirs(config.MEDIA_DIR, exist_ok=True)
    path = os.path.join(config.MEDIA_DIR, f"{message.chat_id}_{message.id}.jpg")
    try:
        saved = await message.download_media(file=path)
        return saved
    except Exception as e:  # noqa: BLE001
        log.warning("не смог скачать медиа msg %s: %s", message.id, e)
        return None


async def _handle(event) -> None:
    msg = event.message
    chat_id = str(msg.chat_id)
    group_key = str(msg.grouped_id) if msg.grouped_id else f"m{msg.id}"
    text = (msg.message or "").strip()
    media_path = await _download_media(msg)

    added = db.add_raw(chat_id, msg.id, group_key, text, media_path)
    if added:
        log.info("принял msg %s (group %s, текст %d симв., медиа=%s)",
                 msg.id, group_key, len(text), bool(media_path))


async def start() -> None:
    global _source_entity
    c = client()
    await c.start()
    me = await c.get_me()
    _source_entity = await c.get_entity(_parse_source(config.SOURCE_CHAT))
    title = getattr(_source_entity, "title", None) or getattr(_source_entity, "username", "?")
    log.info("Telegram: вошёл как @%s, слушаю «%s» (id %s)", me.username, title, _source_entity.id)

    c.add_event_handler(_handle, events.NewMessage(chats=_source_entity))


async def stop() -> None:
    if _client and _client.is_connected():
        await _client.disconnect()


async def list_dialogs(limit: int = 60) -> list[dict]:
    """Помогает найти id нужного канала."""
    c = client()
    out = []
    async for d in c.iter_dialogs(limit=limit):
        out.append({"id": d.id, "name": d.name, "is_channel": d.is_channel})
    return out


async def backfill(limit: int = 5) -> int:
    """Забирает последние N постов канала в очередь (для теста)."""
    c = client()
    entity = _source_entity or await c.get_entity(_parse_source(config.SOURCE_CHAT))
    n = 0
    async for msg in c.iter_messages(entity, limit=limit):
        chat_id = str(msg.chat_id)
        group_key = str(msg.grouped_id) if msg.grouped_id else f"m{msg.id}"
        text = (msg.message or "").strip()
        media_path = await _download_media(msg)
        if db.add_raw(chat_id, msg.id, group_key, text, media_path):
            n += 1
    log.info("backfill: добавлено %d сообщений", n)
    return n
