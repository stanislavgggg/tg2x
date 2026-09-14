"""Конфигурация из переменных окружения."""
import os


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    v = os.getenv(name, "").strip()
    try:
        return int(v)
    except ValueError:
        return default


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# --- Telegram источник (юзер-сессия, не бот) ---
TELEGRAM_API_ID = _int("TELEGRAM_API_ID", 0)
TELEGRAM_API_HASH = _str("TELEGRAM_API_HASH")
TELEGRAM_STRING_SESSION = _str("TELEGRAM_STRING_SESSION")
# ID канала-источника (-100...) или @username
SOURCE_CHAT = _str("SOURCE_CHAT")

# --- X (Twitter), OAuth 1.0a ---
X_ENABLED = _bool("X_ENABLED", True)
X_API_KEY = _str("X_API_KEY")
X_API_SECRET = _str("X_API_SECRET")
X_ACCESS_TOKEN = _str("X_ACCESS_TOKEN")
X_ACCESS_SECRET = _str("X_ACCESS_SECRET")
X_TEXT_LIMIT = _int("X_TEXT_LIMIT", 280)
# Ссылка, которая дописывается в конец твита. Пусто = без ссылки.
X_LINK = _str("X_LINK")
# Хештеги в конец. Формат любой: "#crypto #casino" или "crypto, casino".
X_HASHTAGS = _str("X_HASHTAGS")

# --- LLM-переписывание ---
LLM_PROVIDER = _str("LLM_PROVIDER", "openai").lower()   # openai | anthropic | openrouter
LLM_API_KEY = _str("LLM_API_KEY")
LLM_MODEL = _str("LLM_MODEL", "gpt-4.1-mini")
LLM_TIMEOUT = _int("LLM_TIMEOUT", 60)
# Доп. правила стиля — дописываются в промпт как есть
BRAND_VOICE = _str(
    "BRAND_VOICE",
    "Coinplay is a crypto casino and sportsbook. Tone: confident, sharp, no hype words, no exclamation marks.",
)

# --- Ревью через Telegram-бота ---
REVIEW_ENABLED = _bool("REVIEW_ENABLED", True)
REVIEW_BOT_TOKEN = _str("REVIEW_BOT_TOKEN")
REVIEW_CHAT_ID = _str("REVIEW_CHAT_ID")
# Автопубликация, если за это время никто не нажал кнопку. 0 = ждать вечно.
REVIEW_TIMEOUT_SECONDS = _int("REVIEW_TIMEOUT_SECONDS", 0)

# --- Фильтры источника ---
# Публиковать только посты с картинкой
REQUIRE_MEDIA = _bool("REQUIRE_MEDIA", True)
# Минимальная длина текста поста, чтобы его вообще брать в работу
MIN_TEXT_LENGTH = _int("MIN_TEXT_LENGTH", 40)
# Пропускать посты, содержащие любую из этих подстрок (через запятую, регистр не важен)
SKIP_IF_CONTAINS = _str("SKIP_IF_CONTAINS")
# Брать в работу только посты с одной из подстрок. Пусто = все.
ONLY_IF_CONTAINS = _str("ONLY_IF_CONTAINS")

# --- Тайминги ---
# Сколько ждать, чтобы собрать альбом (медиагруппу) целиком
GROUP_WAIT_SECONDS = _int("GROUP_WAIT_SECONDS", 20)
WORKER_INTERVAL_SECONDS = _int("WORKER_INTERVAL_SECONDS", 10)
MAX_ATTEMPTS = _int("MAX_ATTEMPTS", 5)
# Задержка перед публикацией в X после одобрения, секунды (антипаттерн «одновременно везде»)
POST_DELAY_SECONDS = _int("POST_DELAY_SECONDS", 0)

# --- Сервис ---
DATA_DIR = _str("DATA_DIR", "/data")
PORT = _int("PORT", 8000)
ADMIN_TOKEN = _str("ADMIN_TOKEN")
# Не публиковать никуда, только писать в лог и в /status
DRY_RUN = _bool("DRY_RUN", False)

MEDIA_DIR = os.path.join(DATA_DIR, "media")
DB_PATH = os.path.join(DATA_DIR, "tg2x.db")


def validate() -> list[str]:
    """Возвращает список проблем конфигурации."""
    errs = []
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        errs.append("TELEGRAM_API_ID / TELEGRAM_API_HASH не заданы")
    if not TELEGRAM_STRING_SESSION:
        errs.append("TELEGRAM_STRING_SESSION не задан (сгенерируйте локально: python gen_session.py)")
    if not SOURCE_CHAT:
        errs.append("SOURCE_CHAT не задан")
    if not LLM_API_KEY:
        errs.append("LLM_API_KEY не задан")
    if X_ENABLED and not DRY_RUN:
        if not all([X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET]):
            errs.append("X_* ключи заданы не полностью (нужен блок OAuth 1.0a)")
    if REVIEW_ENABLED:
        if not REVIEW_BOT_TOKEN:
            errs.append("REVIEW_ENABLED=true, но REVIEW_BOT_TOKEN не задан")
        if not REVIEW_CHAT_ID:
            errs.append("REVIEW_ENABLED=true, но REVIEW_CHAT_ID не задан")
    return errs
