"""Генерация TELEGRAM_STRING_SESSION. Запускать ЛОКАЛЬНО, один раз.

    pip install telethon
    python gen_session.py

Понадобятся api_id/api_hash с https://my.telegram.org -> API development tools
и код подтверждения из Telegram.
"""
import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> None:
    api_id = int(input("api_id: ").strip())
    api_hash = input("api_hash: ").strip()
    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        print("\nTELEGRAM_STRING_SESSION=")
        print(client.session.save())
        print("\nПоследние диалоги (ищите нужный канал):")
        async for d in client.iter_dialogs(limit=40):
            if d.is_channel:
                print(f"  {d.id}\t{d.name}")


if __name__ == "__main__":
    asyncio.run(main())
