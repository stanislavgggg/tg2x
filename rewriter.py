"""Переписывание поста из Telegram в формат X (Twitter) через LLM."""
import logging
import re

import httpx

import config

log = logging.getLogger("rewriter")

URL_RE = re.compile(r"https?://\S+|\b[\w-]+\.(?:com|io|net|org|bet|casino|app|co)\b/?\S*", re.I)
# X считает любую ссылку как 23 символа (t.co)
URL_WEIGHT = 23


def x_len(text: str) -> int:
    """Длина твита по правилам X: ссылка = 23 символа."""
    stripped = URL_RE.sub("", text)
    n_urls = len(URL_RE.findall(text))
    return len(stripped) + n_urls * URL_WEIGHT


def normalize_hashtags(raw: str) -> list[str]:
    if not raw:
        return []
    parts = re.split(r"[,\s]+", raw.strip())
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        out.append(p if p.startswith("#") else "#" + p)
    return out


def assemble(body: str, link: str = "", hashtags: str = "", limit: int = 280) -> str:
    """Собирает финальный твит: текст + хештеги + ссылка, не вылезая за лимит."""
    body = body.strip()
    tags = [t for t in normalize_hashtags(hashtags) if t.lower() not in body.lower()]

    def build(b: str, used_tags: list[str]) -> str:
        parts = [b]
        if used_tags:
            parts.append(" ".join(used_tags))
        if link:
            parts.append(link)
        return "\n\n".join(p for p in parts if p)

    # отрезаем хештеги по одному, пока не влезет
    while True:
        candidate = build(body, tags)
        if x_len(candidate) <= limit:
            return candidate
        if tags:
            tags.pop()
            continue
        break

    # всё ещё длинно — режем тело по границе предложения/слова
    reserve = x_len(build("", [])) + 2
    budget = limit - reserve
    if budget < 20:
        budget = limit - (URL_WEIGHT + 2 if link else 0)
    cut = body[:budget]
    for sep in (". ", "! ", "? ", " — ", ", ", " "):
        idx = cut.rfind(sep)
        if idx > budget * 0.5:
            cut = cut[: idx + (1 if sep.startswith((".", "!", "?")) else 0)]
            break
    return build(cut.strip().rstrip(",;—-"), [])


SYSTEM_PROMPT = """You rewrite Telegram channel posts into single-tweet posts for X.

Rules:
- Output ONE tweet. No thread, no numbering, no options, no commentary.
- Hard limit: {budget} characters. Aim for 180-230.
- Keep every factual detail you use exactly as in the source: numbers, bonus amounts,
  team names, tournament names, dates, kickoff times, prize pools. Never invent or round them.
- Drop what does not fit rather than compressing it into something inaccurate.
- No hashtags. No links. No "link in bio". A CTA is added separately.
- At most one emoji, and only if the source uses one. Usually zero.
- No markdown, no quotes around the tweet, no line of dashes.
- Write in the same language as the source post.
- Style: {voice}

Return only the tweet text."""

USER_PROMPT = """Source Telegram post:
<post>
{text}
</post>

Rewrite it as one tweet."""


async def _call_anthropic(system: str, user: str) -> str:
    async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": config.LLM_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": config.LLM_MODEL,
                "max_tokens": 500,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        r.raise_for_status()
        data = r.json()
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


async def _call_openrouter(system: str, user: str) -> str:
    async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT) as client:
        r = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {config.LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": config.LLM_MODEL,
                "max_tokens": 500,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]


def _clean(raw: str) -> str:
    text = raw.strip()
    text = re.sub(r"^```[a-z]*\n?|```$", "", text).strip()
    # модель иногда оборачивает результат в кавычки
    if len(text) > 2 and text[0] in "\"'«" and text[-1] in "\"'»":
        text = text[1:-1].strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


async def rewrite(source_text: str) -> str:
    """Возвращает готовый твит: тело от LLM + хештеги + ссылка."""
    reserved = 0
    if config.X_LINK:
        reserved += URL_WEIGHT + 2
    reserved += len(" ".join(normalize_hashtags(config.X_HASHTAGS)))
    budget = max(80, config.X_TEXT_LIMIT - reserved - 4)

    system = SYSTEM_PROMPT.format(budget=budget, voice=config.BRAND_VOICE)
    user = USER_PROMPT.format(text=source_text.strip())

    if config.LLM_PROVIDER == "openrouter":
        raw = await _call_openrouter(system, user)
    else:
        raw = await _call_anthropic(system, user)

    body = _clean(raw)
    if not body:
        raise RuntimeError("LLM вернула пустой ответ")

    tweet = assemble(body, config.X_LINK, config.X_HASHTAGS, config.X_TEXT_LIMIT)
    log.info("переписано: %d символов (вес X: %d)", len(tweet), x_len(tweet))
    return tweet
