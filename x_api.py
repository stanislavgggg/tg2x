"""Публикация в X (Twitter): media upload v1.1 + create_tweet v2, OAuth 1.0a."""
import logging
import os

import tweepy

import config

log = logging.getLogger("x_api")

_client: tweepy.Client | None = None
_api_v1: tweepy.API | None = None


def _clients() -> tuple[tweepy.Client, tweepy.API]:
    global _client, _api_v1
    if _client is None:
        _client = tweepy.Client(
            consumer_key=config.X_API_KEY,
            consumer_secret=config.X_API_SECRET,
            access_token=config.X_ACCESS_TOKEN,
            access_token_secret=config.X_ACCESS_SECRET,
        )
        auth = tweepy.OAuth1UserHandler(
            config.X_API_KEY,
            config.X_API_SECRET,
            config.X_ACCESS_TOKEN,
            config.X_ACCESS_SECRET,
        )
        _api_v1 = tweepy.API(auth)
    return _client, _api_v1


def whoami() -> str:
    client, _ = _clients()
    me = client.get_me()
    return me.data.username if me and me.data else "?"


def publish(text: str, media_path: str | None) -> str:
    """Публикует твит, возвращает tweet_id."""
    if config.DRY_RUN:
        log.info("DRY_RUN, твит не отправлен: %s", text.replace("\n", " | ")[:200])
        return "dry-run"

    client, api_v1 = _clients()
    media_ids = None
    if media_path and os.path.exists(media_path):
        media = api_v1.media_upload(filename=media_path)
        media_ids = [media.media_id_string]
    elif media_path:
        log.warning("файл медиа не найден на диске: %s", media_path)

    resp = client.create_tweet(text=text, media_ids=media_ids)
    tweet_id = str(resp.data["id"])
    log.info("опубликовано в X: %s", tweet_id)
    return tweet_id
