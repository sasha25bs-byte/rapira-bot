import json
import os
import re
import asyncio
import random
import time
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

import io
import math
import aiohttp
from aiohttp import web
from PIL import Image, ImageDraw, ImageFont, ImageFilter

try:
    import pytesseract  # распознавание скорборда со скриншота (см. parse_scoreboard_ocr)
except ImportError:
    pytesseract = None
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, MenuButtonWebApp, WebAppInfo,
    ChatPermissions, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats, BotCommandScopeDefault,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ApplicationHandlerStop,
)
from telegram.constants import ParseMode

# ════════════════════════════════════════════════
#                    НАСТРОЙКИ
# ════════════════════════════════════════════════

import os as _os

BOT_TOKEN = _os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "Переменная окружения BOT_TOKEN не задана. "
        "Добавь её в Railway → Variables → BOT_TOKEN."
    )

# ── РОЛИ ──────────────────────────────────────────
# Создатель — доступ ко всем командам
CREATOR_ID       = 7979653269

def _parse_id_list(env_value: str) -> list:
    """Парсит строку вида '111,222, 333' из переменной окружения в список int ID."""
    result = []
    for part in env_value.split(","):
        part = part.strip()
        if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
            result.append(int(part))
    return result

# Админы — бан, мут, выдача эло за катки, создание матчей.
# Берутся из переменной окружения ADMIN_IDS (через запятую), создатель добавляется всегда.
ADMIN_IDS        = list({CREATOR_ID, *_parse_id_list(_os.environ.get("ADMIN_IDS", ""))})
# Модераторы — только мут и выдача каток (/win). Переменная окружения MODERATOR_IDS.
MODERATOR_IDS: list = _parse_id_list(_os.environ.get("MODERATOR_IDS", ""))
# Ютуберы/контент-мейкеры — получают верификационный бейдж в профиле.
# Переменная окружения YOUTUBER_IDS (через запятую).
YOUTUBER_IDS: list = _parse_id_list(_os.environ.get("YOUTUBER_IDS", ""))

WEBAPP_URL       = _os.environ.get("WEBAPP_URL", "")  # URL сайта на Railway
DATA_FILE        = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "faceit_db.json")
STORAGE_CHAT_ID  = int(_os.environ.get("STORAGE_CHAT_ID", "7979653269"))  # ID канала для хранения БД
ADMIN_GROUP_ID   = -1003700067489   # ID админ-конфы для уведомлений
TICKETS_THREAD_ID = 3               # ID темы "Тикеты" в админ-конфе (супергруппа с темами)

# ── СПИСОК ОСКОРБЛЕНИЙ ────────────────────────────
# Бот удалит сообщение, предупредит пользователя и уведомит админ-конфу
# Разделены на 2 категории, чтобы в предупреждении было видно ЗА ЧТО именно:
#  • INSULT_WORDS    — прямые оскорбления личности
#  • PROFANITY_WORDS — нецензурная лексика / мат (не всегда направлен на человека)


def is_creator(uid: int) -> bool:
    return uid == CREATOR_ID

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS or is_creator(uid)

def is_moderator(uid: int) -> bool:
    return uid in MODERATOR_IDS or is_admin(uid)

def is_youtuber(uid: int) -> bool:
    return uid in YOUTUBER_IDS

# ── БЕСЕДА / СЕЗОН ────────────────────────────────
BESEDA_LINK     = "https://t.me/faceitggvp"   # ссылка на беседу (там играются матчи)
BESEDA_USERNAME = "@faceitggvp"               # username беседы для проверки подписки
SEASON_NAME     = "Test Season"
SEASON_END      = "20.07.2026"

MAPS_LIST      = ["Dust 2"]
LOBBY_5V5_SIZE = 10
LOBBY_2V2_SIZE = 4
PICK_TIMEOUT   = 90
BAN_TIMEOUT    = 90

# ── КАЛИБРОВКА ────────────────────────────────────
# Сколько матчей нужно сыграть, прежде чем игроку показывается реальный
# уровень/ранг. До этого — в /top, /stats и на карточках вместо уровня
# показывается прогресс калибровки, а сам игрок не попадает в топ.
CALIBRATION_GAMES = 5

ELO_WIN_PC       = 15
ELO_LOSS_PC      = 30
ELO_WIN_MOBILE   = 25
ELO_LOSS_MOBILE  = 20
ELO_MIN      = 100
BOT_ID_START = -100000

# ── СТАРТОВЫЙ РАНГ ПОСЛЕ КАЛИБРОВКИ ──────────────────────────────────
# После CALIBRATION_GAMES матчей боту нужно выдать игроку стартовый ELO.
# Раньше формула растягивала винрейт на весь диапазон 100–2000, из-за чего
# всего 4 победы и 1 поражение (80%) сразу давали ~1600 ELO — это слишком
# много для пяти матчей и ломало баланс топа. Теперь используем умеренную
# базу: средний игрок (50% побед) стартует около CALIBRATION_BASE_ELO,
# а винрейт лишь немного сдвигает его в ту или иную сторону
# (± CALIBRATION_SWING / 2 при 100%/0% побед).
CALIBRATION_BASE_ELO  = 1000
CALIBRATION_SWING     = 800


def elo_deltas_for(platform: str) -> tuple:
    """Возвращает (плюс_за_победу, минус_за_поражение) в зависимости от платформы."""
    if platform == "mobile":
        return ELO_WIN_MOBILE, ELO_LOSS_MOBILE
    return ELO_WIN_PC, ELO_LOSS_PC

NOT_REGISTERED_MSG = (
    "❌ <b>Вы не зарегистрированы!</b>\n\n"
    "Для регистрации введите:\n"
    "<code>/reg GAME_ID Никнейм Платформа</code>\n\n"
    "Примеры:\n"
    "<code>/reg 6888 Londyyy pc</code>\n"
    "<code>/reg 6888 Londyyy mobile</code>\n\n"
    "⚠️ <b>За обман платформы вы получаете бан от администрации Faceit!</b>"
)

BOT_NAMES = [
    "Zeus","Simple","KennyS","Device","Guardian","Cold",
    "ElectroNic","Perfecto","B1T","Monesy","JL","Zywoo",
    "Faker","NaVi_Bot","Twistzz","Ropz","NAF","sh1ro","Ax1Le"
]

# ════════════════════════════════════════════════
#                   ДАТАКЛАСС
# ════════════════════════════════════════════════

@dataclass
class Player:
    user_id:       int
    nickname:      str
    external_id:   str   = ""
    elo:           int   = 0
    elo_5v5:       int   = 0
    elo_2v2:       int   = 0
    wins:          int   = 0
    losses:        int   = 0
    wins_5v5:      int   = 0
    losses_5v5:    int   = 0
    wins_2v2:      int   = 0
    losses_2v2:    int   = 0
    avg:           float = 0.0
    avg_5v5:       float = 0.0
    avg_2v2:       float = 0.0
    is_bot:        bool  = False
    total_kills:   int   = 0
    total_deaths:  int   = 0
    platform:      str   = "pc"   # "pc" или "mobile" — влияет на начисление ELO за /win
    registered_ts: float = 0.0    # unix-время реальной регистрации (0 = неизвестно, старая запись)

    def lvl_icon(self) -> str:
        if not self.is_calibrated:
            return f"🔄 Калибровка {self.total_games}/{CALIBRATION_GAMES}"
        return self._lvl_for(self.elo)

    def lvl_icon_5v5(self) -> str:
        if not self.is_calibrated:
            return f"🔄 Калибровка {self.total_games}/{CALIBRATION_GAMES}"
        return self._lvl_for(self.elo)

    def lvl_icon_2v2(self) -> str:
        if not self.is_calibrated:
            return f"🔄 Калибровка {self.total_games}/{CALIBRATION_GAMES}"
        return self._lvl_for(self.elo)

    @property
    def total_games(self) -> int:
        return self.wins + self.losses

    @property
    def is_calibrated(self) -> bool:
        return self.total_games >= CALIBRATION_GAMES

    def _lvl_for(self, elo: int) -> str:
        if elo >= 2001: return "🏆 LVL 10"
        if elo >= 1751: return "🔴 LVL 9"
        if elo >= 1531: return "🔴 LVL 8"
        if elo >= 1351: return "🟠 LVL 7"
        if elo >= 1201: return "🟠 LVL 6"
        if elo >= 1051: return "🟡 LVL 5"
        if elo >= 901:  return "🟡 LVL 4"
        if elo >= 751:  return "🟢 LVL 3"
        if elo >= 501:  return "🟢 LVL 2"
        return "⚪ LVL 1"

    def tg_link(self) -> str:
        if self.is_bot:
            return f"🤖 <b>{self.nickname}</b>"
        return f'<a href="tg://user?id={self.user_id}">{self.nickname}</a>'

# ════════════════════════════════════════════════
#                  БАЗА ДАННЫХ
# ════════════════════════════════════════════════

# Глобальная ссылка на приложение (нужна для Telegram-синхронизации)
_app_ref = None
_sync_task: Optional[asyncio.Task] = None
_db_cache: Optional[Dict[str, Any]] = None  # кеш в памяти для быстрого доступа


def load_db() -> Dict[str, Any]:
    global _db_cache
    if _db_cache is not None:
        return _db_cache
    default: Dict[str, Any] = {
        "players": {}, "match_counter": 0, "active_matches": {},
        "queue_5v5": [], "queue_2v2": [], "lobby_5v5": {}, "lobby_2v2": {}, "muted": {}, "banned": {}, "bot_counter": 0,
        "tickets": {}, "ticket_counter": 0, "user_open_ticket": {},
        "pending_ocr": {},      # m_id -> распознанный ботом результат матча, ждёт подтверждения админа
        "unresolved_results": {},  # m_id -> скрин прислали, но OCR не распознал — нужна ручная проверка
        "dm_result_wait": {},   # str(uid) -> m_id: игрок нажал «Отправить результат» в ЛС и должен прислать скрин
    }
    if not os.path.exists(DATA_FILE):
        _db_cache = default
        return _db_cache
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in default.items():
            data.setdefault(k, v)
        _db_cache = data
        return _db_cache
    except Exception:
        _db_cache = default
        return _db_cache


def save_db(db: Dict[str, Any]) -> None:
    global _db_cache
    _db_cache = db  # обновляем кеш сразу
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False)
    # Планируем отложенную синхронизацию в Telegram (через 10 сек)
    _schedule_tg_sync()


def _schedule_tg_sync():
    """Планирует синхронизацию БД в Telegram (дебаунс 10 сек)."""
    global _sync_task
    if not STORAGE_CHAT_ID or _app_ref is None:
        return
    try:
        asyncio.get_running_loop()  # проверяем что loop запущен
        if _sync_task and not _sync_task.done():
            _sync_task.cancel()
        _sync_task = asyncio.ensure_future(_delayed_sync())
    except RuntimeError:
        # Нет запущенного event loop — пропускаем синхронизацию
        pass
    except Exception:
        pass


async def _delayed_sync():
    """Ждёт 10 секунд, потом синхронизирует БД в Telegram."""
    try:
        await asyncio.sleep(30)
        await _sync_db_to_telegram()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[sync] Ошибка: {e}")


async def _sync_db_to_telegram():
    """Загружает файл БД в Telegram-канал и закрепляет сообщение."""
    if not STORAGE_CHAT_ID or _app_ref is None:
        return
    try:
        import io
        db   = load_db()
        data = json.dumps(db, indent=4, ensure_ascii=False).encode("utf-8")
        buf  = io.BytesIO(data)
        buf.name = "faceit_db.json"
        msg = await _app_ref.bot.send_document(
            chat_id=STORAGE_CHAT_ID,
            document=buf,
            caption="📦 FACEIT DB backup",
        )
        try:
            await _app_ref.bot.pin_chat_message(
                chat_id=STORAGE_CHAT_ID,
                message_id=msg.message_id,
                disable_notification=True,
            )
        except Exception:
            pass
        print(f"✅ БД синхронизирована в Telegram (msg_id={msg.message_id})")
    except Exception as e:
        print(f"⚠️ Ошибка синхронизации БД: {e}")


async def _restore_db_from_telegram(bot):
    """При старте бота восстанавливает БД из закреплённого сообщения в канале."""
    if not STORAGE_CHAT_ID:
        print("ℹ️ STORAGE_CHAT_ID не задан — хранение только локально")
        return
    try:
        import io
        chat = await bot.get_chat(STORAGE_CHAT_ID)
        if chat.pinned_message and chat.pinned_message.document:
            tg_file = await bot.get_file(chat.pinned_message.document.file_id)
            buf = io.BytesIO()
            await tg_file.download_to_memory(buf)
            buf.seek(0)
            data = json.loads(buf.read().decode("utf-8"))
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            global _db_cache
            _db_cache = None  # сбрасываем кеш — следующий load_db прочитает свежие данные
            print("✅ БД восстановлена из Telegram!")
        else:
            print("ℹ️ В канале нет сохранённой БД — начинаем с чистого листа")
            # Сразу делаем первый бэкап
            await _sync_db_to_telegram()
    except Exception as e:
        print(f"⚠️ Не удалось восстановить БД из Telegram: {e}")


def _safe_num(d: dict, key: str, default):
    """Достаёт число из словаря, безопасно подставляя default, если поле
    отсутствует, равно None или повреждено (не число). Нужно там, где
    арифметика (например max()) происходит ДО создания Player — там, где
    .setdefault() не спасает от уже существующих None-значений."""
    v = d.get(key, default)
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return default
    return v


_PLAYER_NUMERIC_DEFAULTS = {
    "elo": 0, "elo_5v5": 0, "elo_2v2": 0,
    "wins": 0, "losses": 0, "wins_5v5": 0, "losses_5v5": 0,
    "wins_2v2": 0, "losses_2v2": 0,
    "avg": 0.0, "avg_5v5": 0.0, "avg_2v2": 0.0,
    "total_kills": 0, "total_deaths": 0,
    "registered_ts": 0.0,
}
_PLAYER_STRING_DEFAULTS = {"external_id": "", "nickname": "?", "platform": "pc"}
_PLAYER_BOOL_DEFAULTS   = {"is_bot": False}


def _make_player(d: dict) -> "Player":
    """Создаёт Player из словаря, игнорируя неизвестные поля.
    Дополнительно чистит None/битые значения в числовых, строковых и булевых
    полях — это защищает /top, /stats и PNG-карточки от падения из-за старых
    или повреждённых записей в БД (например, elo_5v5=None у игрока, который
    никогда не играл 5v5). .setdefault() в вызывающем коде НЕ спасает от этого,
    так как срабатывает только когда ключ отсутствует, а не когда он None."""
    import dataclasses
    known = {f.name for f in dataclasses.fields(Player)}
    clean = {k: v for k, v in d.items() if k in known}
    for field, default in _PLAYER_NUMERIC_DEFAULTS.items():
        v = clean.get(field, default)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            v = default
        clean[field] = v
    for field, default in _PLAYER_STRING_DEFAULTS.items():
        v = clean.get(field, default)
        clean[field] = v if isinstance(v, str) else default
    for field, default in _PLAYER_BOOL_DEFAULTS.items():
        v = clean.get(field, default)
        clean[field] = v if isinstance(v, bool) else default
    return Player(**clean)


def get_player(uid: int, name: str = "Player") -> Player:
    db = load_db()
    s  = str(uid)
    if s not in db["players"]:
        db["players"][s] = asdict(Player(uid, name))
        save_db(db)
    d = db["players"][s]
    for field, val in [("wins",0),("losses",0),("avg",0.0),
                       ("elo",0),("elo_5v5",0),("elo_2v2",0),
                       ("wins_5v5",0),("losses_5v5",0),
                       ("wins_2v2",0),("losses_2v2",0),
                       ("avg_5v5",0.0),("avg_2v2",0.0),
                       ("external_id",""),("is_bot",False),
                       ("total_kills",0),("total_deaths",0),
                       ("platform","pc")]:
        d.setdefault(field, val)
    return _make_player(d)

# ════════════════════════════════════════════════
#             ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ════════════════════════════════════════════════

def check_banned(uid: int) -> bool:
    db = load_db()
    until = db["banned"].get(str(uid))
    return bool(until and datetime.now().timestamp() < until)


def db_ban_until(uid: int) -> float:
    """Возвращает unix-timestamp окончания бана из БД (0, если не забанен)."""
    db = load_db()
    return db["banned"].get(str(uid), 0) or 0


def check_muted(uid: int) -> bool:
    db = load_db()
    until = db["muted"].get(str(uid))
    return bool(until and datetime.now().timestamp() < until)


def db_mute_until(uid: int) -> float:
    """Возвращает unix-timestamp окончания мута из БД (0, если не в муте)."""
    db = load_db()
    return db["muted"].get(str(uid), 0) or 0


async def _notify_punishment_dm(context: ContextTypes.DEFAULT_TYPE, target: int,
                                 kind: str, duration_label: str, reason: str = "") -> None:
    """
    Уведомляет игрока в личных сообщениях о выданном наказании (мут/бан).
    Если пользователь не запускал бота в ЛС (не нажимал /start) — Telegram
    не даст отправить ему сообщение, поэтому ошибки тут просто гасим.
    """
    if kind == "mute":
        title = "🔇 <b>Вам выдан МУТ</b>"
    else:
        title = "🚫 <b>Вы ЗАБАНЕНЫ</b>"

    text = (
        f"{title}\n\n"
        f"⏳ Срок: <b>{duration_label}</b>\n"
    )
    if reason:
        text += f"📌 Причина: {reason}\n"
    if kind == "mute":
        text += (
            "\n❗️ Пока действует мут, вы не можете писать в чат, "
            "вставать в очередь, выбирать игроков или взаимодействовать с ботом."
        )
    else:
        text += "\n❗️ Вы исключены из беседы и не сможете в неё вернуться до окончания срока."

    try:
        await context.bot.send_message(chat_id=target, text=text, parse_mode=ParseMode.HTML)
    except Exception as e:
        print(f"[punishment_dm] не удалось отправить ЛС uid={target}: {e}")


async def _schedule_mute_expiry(bot, target: int, until_ts: float) -> None:
    """
    Спит до момента until_ts, затем — если мут всё ещё актуален именно с
    этим сроком (не был снят раньше через /unmute и не перевыдан с другим
    сроком) — снимает запись из БД и шлёт игроку ЛС, что мут истёк.
    Если срок уже сдвинулся (новый /mute) — этот таймер просто завершается,
    свежий таймер сам пришлёт уведомление позже.
    """
    delay = until_ts - datetime.now().timestamp()
    if delay > 0:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

    db = load_db()
    current = db.get("muted", {}).get(str(target))
    if not current or abs(current - until_ts) > 1:
        return  # сняли вручную раньше или перевыдали с другим сроком
    if current > datetime.now().timestamp():
        return  # ещё не истёк (на всякий случай)

    db["muted"].pop(str(target), None)
    save_db(db)
    try:
        await bot.send_message(
            chat_id=target,
            text=(
                "🔊 <b>Мут истёк!</b>\n\n"
                "Наказание снято, можете снова писать в чат и играть.\n"
                "Не нарушайте правила 🙂"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        print(f"[mute_expiry] не удалось уведомить uid={target}: {e}")


async def _schedule_ban_expiry(bot, target: int, until_ts: float) -> None:
    """Аналог _schedule_mute_expiry, но для бана. Перманентные баны
    (until_ts >= 9_999_999_999) сюда не передаются — они не истекают сами."""
    if until_ts >= 9_999_999_999:
        return

    delay = until_ts - datetime.now().timestamp()
    if delay > 0:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

    db = load_db()
    current = db.get("banned", {}).get(str(target))
    if not current or current >= 9_999_999_999 or abs(current - until_ts) > 1:
        return  # сняли вручную раньше, перевыдали с другим сроком, или стал перманентным
    if current > datetime.now().timestamp():
        return

    db["banned"].pop(str(target), None)
    save_db(db)
    try:
        await bot.send_message(
            chat_id=target,
            text=(
                "✅ <b>Бан истёк!</b>\n\n"
                "Вы можете вернуться в беседу и продолжить играть.\n"
                "Не нарушайте правила 🙂"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        print(f"[ban_expiry] не удалось уведомить uid={target}: {e}")


def is_registered(uid: int) -> bool:
    db = load_db()
    s  = str(uid)
    return s in db["players"] and bool(db["players"][s].get("external_id"))


def parse_duration(s: str) -> Optional[int]:
    # Поддержка латинских и кириллических суффиксов: m/м, h/ч, d/д
    units = {"m": 60, "h": 3600, "d": 86400, "м": 60, "ч": 3600, "д": 86400}
    if s and s[-1] in units:
        try:
            return int(s[:-1]) * units[s[-1]]
        except ValueError:
            pass
    try:
        return int(s) * 60
    except ValueError:
        return None


def _is_bot_uid(uid: int) -> bool:
    return uid < 0


def get_reply_target(update: Update, args: list) -> Optional[int]:
    """
    Возвращает target user_id:
    - Если ответил на сообщение — берём ID из того сообщения
    - Если передан аргумент — парсим его как user_id
    - Иначе None
    """
    if update.message and update.message.reply_to_message:
        return update.message.reply_to_message.from_user.id
    if args:
        try:
            return int(args[0])
        except ValueError:
            pass
    return None


async def is_subscribed_beseda(bot, uid: int) -> bool:
    """Проверяет, состоит ли пользователь в беседе (по username)."""
    try:
        member = await bot.get_chat_member(BESEDA_USERNAME, uid)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False


def _sub_gate_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➡️ Перейти в беседу", url=BESEDA_LINK)],
        [InlineKeyboardButton("🔄 Я подписался", callback_data="reg_check_sub")],
    ])


async def dm_buttons_only(update: Update) -> bool:
    """В ЛС для обычных игроков доступна только команда /start — всё
    остальное делается через кнопки меню. Возвращает True, если апдейт
    нужно заблокировать (и уже отправлено пояснение)."""
    msg = update.message
    if not msg or msg.chat.type != "private":
        return False
    uid = update.effective_user.id
    if is_admin(uid) or is_moderator(uid):
        return False
    await msg.reply_text(
        "ℹ️ В личных сообщениях боту доступна только команда /start.\n"
        "Нажми /start и пользуйся кнопками меню 👇"
    )
    return True


async def gate(update: Update, need_reg: bool = True, need_unmute: bool = False) -> bool:
    """Единая проверка. True = заблокировать. Админы всегда проходят.
    ВАЖНО: муты теперь блокируют АБСОЛЮТНО ЛЮБУЮ команду бота (не только
    постановку в очередь), поэтому check_muted проверяется всегда,
    параметр need_unmute оставлен только для обратной совместимости вызовов."""
    if not update.message:
        return False
    uid = update.effective_user.id
    if is_admin(uid):
        return False
    if check_banned(uid):
        try:
            await update.message.delete()
        except Exception:
            pass
        return True
    if check_muted(uid):
        until = db_mute_until(uid)
        left  = max(0, int(until - datetime.now().timestamp()))
        mins, secs = divmod(left, 60)
        await update.message.reply_text(
            f"🔇 Вы в муте ещё {mins} мин. {secs} сек. — бот не выполняет ваши команды."
        )
        try:
            await update.message.delete()
        except Exception:
            pass
        return True
    if need_reg and not is_registered(uid):
        await update.message.reply_text(NOT_REGISTERED_MSG, parse_mode=ParseMode.HTML)
        return True
    return False

# ════════════════════════════════════════════════
#               УТИЛИТЫ ЛОББИ
# ════════════════════════════════════════════════

def lobby_text(mode: str, queue: List[int]) -> str:
    size   = LOBBY_5V5_SIZE if mode == "5v5" else LOBBY_2V2_SIZE
    emoji  = "🎮" if mode == "5v5" else "⚡"
    filled = len(queue)
    bar    = "🟩" * filled + "⬜" * (size - filled)
    pct    = int(filled / size * 100)

    lines = [
        f"╔══════════════╗",
        f"║ {emoji} <b>ЛОББИ {mode.upper()}</b> {emoji} ║",
        f"╚══════════════╝",
        f"",
        f"👥 Игроков: <b>{filled}/{size}</b>  •  <b>{pct}%</b>",
        f"<code>[{bar}]</code>",
        f"",
    ]

    medals = ["🥇", "🥈", "🥉"]

    if queue:
        lines.append("┌─ <b>Игроки в очереди</b>")
        for i, uid in enumerate(queue, 1):
            p   = get_player(uid)
            num = medals[i - 1] if i <= 3 else f"<b>{i}.</b>"
            lines.append(
                f"│ {num} {p.lvl_icon()} {p.tg_link()}\n"
                f"│  <code>[{p.external_id or '???'}]</code> · <b>{p.elo}</b> ELO"
            )
        lines.append("└──────────────")
    else:
        lines.append("┌──────────────")
        lines.append("│ <i>Очередь пока пуста...</i>")
        lines.append("│ <i>Нажми кнопку и заходи! 👇</i>")
        lines.append("└──────────────")

    return "\n".join(lines)


def lobby_kb(mode: str, uid: int, queue: List[int]) -> InlineKeyboardMarkup:
    btn_join  = InlineKeyboardButton("✅ Присоединиться", callback_data=f"join_{mode}")
    btn_leave = InlineKeyboardButton("🚪 Выйти",          callback_data=f"leave_{mode}")
    return InlineKeyboardMarkup([[btn_join], [btn_leave]])

# ════════════════════════════════════════════════
#              МАТЧ — СОЗДАНИЕ И АВТО-БОТ
# ════════════════════════════════════════════════

def _pick_buttons(m_id: str, pool: List[int]) -> List[List[InlineKeyboardButton]]:
    return [
        [InlineKeyboardButton(
            f"{get_player(u).lvl_icon()} {get_player(u).nickname} "
            f"[{get_player(u).external_id or '?'}] | {get_player(u).avg:.1f}%",
            callback_data=f"pk_{m_id}_{u}"
        )] for u in pool
    ]


def _pline(uid: int) -> str:
    p = get_player(uid)
    return f"  • {p.tg_link()} <code>[{p.external_id or '?'}]</code>"


async def _bot_auto_pick(m_id: str, context: ContextTypes.DEFAULT_TYPE, chat_id: int, thread_id: Optional[int] = None):
    await asyncio.sleep(2)
    db = load_db()
    m  = db["active_matches"].get(m_id)
    if not m:
        return

    turn = m["turn"]
    if not _is_bot_uid(turn):
        return

    ct_cap = m["ct"][0]
    t_cap  = m["t"][0]
    phase  = m.get("phase", "pick")

    if phase == "pick" and m["pool"]:
        chosen = random.choice(m["pool"])
        (m["ct"] if turn == ct_cap else m["t"]).append(chosen)
        m["pool"].remove(chosen)

        if len(m["pool"]) == 1:
            last = m["pool"].pop(0)
            (m["ct"] if len(m["ct"]) <= len(m["t"]) else m["t"]).append(last)

        bot_p = get_player(turn)

        if m["pool"]:
            m["turn"]  = t_cap if turn == ct_cap else ct_cap
            cur_side   = "🔵 CT" if m["turn"] == ct_cap else "🔴 T"
            txt = (
                f"🤖 <b>{bot_p.nickname}</b> выбрал <b>{get_player(chosen).nickname}</b>\n\n"
                f"🎯 <b>Пик | Матч #{m_id} [{m['mode'].upper()}]</b>\n"
                f"CT: {len(m['ct'])} | T: {len(m['t'])}\n"
                f"Ход: {cur_side}"
            )
            save_db(db)
            try:
                await context.bot.send_message(
                    chat_id=chat_id, message_thread_id=thread_id, text=txt,
                    reply_markup=InlineKeyboardMarkup(_pick_buttons(m_id, m["pool"])),
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
            if _is_bot_uid(m["turn"]):
                await _bot_auto_pick(m_id, context, chat_id, thread_id)
        else:
            # Пик игроков завершён
            task = _pick_timer_tasks.pop(m_id, None)
            if task:
                task.cancel()
            try:
                await context.bot.send_message(
                    chat_id=chat_id, message_thread_id=thread_id,
                    text=f"🤖 <b>{bot_p.nickname}</b> выбрал <b>{get_player(chosen).nickname}</b>",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

            if len(m["maps"]) > 1:
                # Карт больше одной — начинаем бан карт. Первым банит CT-капитан.
                m["phase"] = "ban"
                m["turn"] = ct_cap
                m["ban_start_time"] = time.time()
                save_db(db)
                ban_btns = [
                    [InlineKeyboardButton(f"🚫 {mn}", callback_data=f"bn_{m_id}_{mn}")]
                    for mn in m["maps"]
                ]
                ban_txt = _ban_status_text(m_id, m, m.get("ban_timeout", BAN_TIMEOUT))
                try:
                    sent = await context.bot.send_message(
                        chat_id=chat_id, message_thread_id=thread_id,
                        text=ban_txt,
                        reply_markup=InlineKeyboardMarkup(ban_btns),
                        parse_mode=ParseMode.HTML
                    )
                    m["ban_msg_id"] = sent.message_id
                    save_db(db)
                except Exception:
                    pass
                ban_task = asyncio.create_task(_ban_timer(m_id, context, chat_id))
                _ban_timer_tasks[m_id] = ban_task
                if _is_bot_uid(m["turn"]):
                    await _bot_auto_ban(m_id, context, chat_id, thread_id)
                return

            host_uid  = m.get("host_uid", ct_cap)
            host_p    = get_player(host_uid)
            host_side = "🔵 CT" if host_uid == ct_cap else "🔴 T"
            final_map = m["maps"][0] if m["maps"] else "Seaside"
            m["phase"] = "done"
            save_db(db)
            await _announce_lobby_ready(context, chat_id, thread_id, m_id, m, host_p, host_side, final_map)
    elif phase == "ban":
        await _bot_auto_ban(m_id, context, chat_id, thread_id)


async def _bot_auto_ban(m_id: str, context: ContextTypes.DEFAULT_TYPE, chat_id: int, thread_id: Optional[int] = None):
    await asyncio.sleep(2)
    db = load_db()
    m  = db["active_matches"].get(m_id)
    if not m or not m.get("maps"):
        return

    turn = m["turn"]
    if not _is_bot_uid(turn):
        return

    ct_cap   = m["ct"][0]
    t_cap    = m["t"][0]
    map_name = random.choice(m["maps"])
    bot_p    = get_player(turn)
    m["maps"].remove(map_name)
    m["banned_maps"].append(map_name)

    if len(m["maps"]) == 1:
        final_map  = m["maps"][0]
        banned_str = ", ".join(m["banned_maps"])
        host_uid  = m.get("host_uid", ct_cap)
        host_p    = get_player(host_uid)
        host_side = "🔵 CT" if host_uid == ct_cap else "🔴 T"
        save_db(db)
        try:
            await context.bot.send_message(
                chat_id=chat_id, message_thread_id=thread_id,
                text=f"🤖 <b>{bot_p.nickname}</b> забанил <b>{map_name}</b>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
        await _announce_lobby_ready(context, chat_id, thread_id, m_id, m, host_p, host_side, final_map, banned_str)
        return

    m["turn"] = t_cap if turn == ct_cap else ct_cap
    cur_side  = "🔵 CT" if m["turn"] == ct_cap else "🔴 T"
    ban_btns  = [
        [InlineKeyboardButton(f"🚫 {mn}", callback_data=f"bn_{m_id}_{mn}")]
        for mn in m["maps"]
    ]
    txt = (
        f"🤖 <b>{bot_p.nickname}</b> забанил <b>{map_name}</b>\n\n"
        f"🗺 <b>Баны карт | Матч #{m_id}</b>\n"
        f"Осталось: {len(m['maps'])} карт | Ход: {cur_side}"
    )
    save_db(db)
    try:
        await context.bot.send_message(
                    chat_id=chat_id, message_thread_id=thread_id, text=txt,
            reply_markup=InlineKeyboardMarkup(ban_btns),
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass
    if _is_bot_uid(m["turn"]):
        await _bot_auto_ban(m_id, context, chat_id, thread_id)


# Глобальный словарь задач таймера пика
_pick_timer_tasks: Dict[str, asyncio.Task] = {}
# Глобальный словарь задач таймера бана карт
_ban_timer_tasks: Dict[str, asyncio.Task] = {}


async def _pick_timer(m_id: str, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Фоновая задача: каждые 10 секунд обновляет таймер в сообщении пика."""
    while True:
        await asyncio.sleep(10)
        try:
            db = load_db()
            m  = db["active_matches"].get(m_id)
            if not m or m.get("phase") != "pick":
                break

            elapsed   = time.time() - m["pick_start_time"]
            remaining = max(0, int(m["pick_timeout"] - elapsed))

            msg_id = m.get("pick_msg_id")
            if not msg_id:
                break

            if remaining <= 0:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=msg_id,
                        text="⏰ <b>Время на пик вышло! Матч отменён.</b>",
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass
                db["active_matches"].pop(m_id, None)
                save_db(db)
                break

            txt = _pick_status_text(m_id, m, remaining)
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=msg_id,
                    text=txt,
                    reply_markup=InlineKeyboardMarkup(_pick_buttons(m_id, m["pool"])),
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
        except asyncio.CancelledError:
            break
        except Exception:
            pass


async def _ban_timer(m_id: str, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Фоновая задача: каждые 10 секунд обновляет таймер бана карт.
    При истечении — рандомно банит карту за опоздавшего капитана."""
    while True:
        await asyncio.sleep(10)
        try:
            db = load_db()
            m  = db["active_matches"].get(m_id)
            if not m or m.get("phase") != "ban":
                break

            elapsed   = time.time() - m.get("ban_start_time", time.time())
            remaining = max(0, int(m.get("ban_timeout", BAN_TIMEOUT) - elapsed))

            msg_id = m.get("ban_msg_id")

            if remaining <= 0:
                # Время на бан вышло — отменяем матч
                cancel_txt = (
                    f"⏰ <b>Время на бан карт вышло! Матч #{m_id} отменён.</b>\n\n"
                    f"Капитан не успел забанить карту вовремя."
                )
                if msg_id:
                    try:
                        await context.bot.edit_message_text(
                            chat_id=chat_id, message_id=msg_id,
                            text=cancel_txt, parse_mode=ParseMode.HTML
                        )
                    except Exception:
                        try:
                            await context.bot.send_message(
                                chat_id=chat_id, text=cancel_txt, parse_mode=ParseMode.HTML
                            )
                        except Exception:
                            pass
                db["active_matches"].pop(m_id, None)
                save_db(db)
                break

            # Обновляем сообщение с таймером
            if msg_id and m.get("maps"):
                ban_btns = [
                    [InlineKeyboardButton(f"🚫 {mn}", callback_data=f"bn_{m_id}_{mn}")]
                    for mn in m["maps"]
                ]
                txt = _ban_status_text(m_id, m, remaining)
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=msg_id,
                        text=txt,
                        reply_markup=InlineKeyboardMarkup(ban_btns),
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass
        except asyncio.CancelledError:
            break
        except Exception:
            pass


def _ban_status_text(m_id: str, m: Dict, remaining: int) -> str:
    """Формирует текст бана карт с таймером."""
    ct_cap   = m["ct"][0]
    cur_side = "🔵 CT" if m["turn"] == ct_cap else "🔴 T"
    banned   = ", ".join(m["banned_maps"]) if m["banned_maps"] else "нет"
    return (
        f"🗺 <b>Баны карт | Матч #{m_id} [{m['mode'].upper()}]</b>\n"
        f"⏳ Осталось: <b>{remaining} сек</b> | Ход: {cur_side}\n"
        f"🚫 Уже забанены: {banned}\n"
        f"Осталось карт: {len(m['maps'])}"
    )


def _pick_status_text(m_id: str, m: Dict, remaining: int) -> str:
    """Формирует текст пика с полным составом команд и пула."""
    ct_cap   = m["ct"][0]
    cur_side = "🔵 CT" if m["turn"] == ct_cap else "🔴 T"

    ct_list  = "\n".join(f"  • {get_player(u).tg_link()}" for u in m["ct"])
    t_list   = "\n".join(f"  • {get_player(u).tg_link()}" for u in m["t"])
    pool_list = "\n".join(
        f"  {i+1}. {get_player(u).tg_link()} <code>[{get_player(u).external_id or '?'}]</code>"
        for i, u in enumerate(m["pool"])
    )

    return (
        f"🎯 <b>Пик | Матч #{m_id} [{m['mode'].upper()}]</b>\n"
        f"⏳ Осталось: <b>{remaining} сек</b> | Ход: {cur_side}\n\n"
        f"🔵 CT ({len(m['ct'])}):\n{ct_list}\n\n"
        f"🔴 T ({len(m['t'])}):\n{t_list}\n\n"
        f"👥 Пул:\n{pool_list}"
    )


_KEYCAP_DIGITS = {
    "0": "0️⃣", "1": "1️⃣", "2": "2️⃣", "3": "3️⃣", "4": "4️⃣",
    "5": "5️⃣", "6": "6️⃣", "7": "7️⃣", "8": "8️⃣", "9": "9️⃣",
}


def _big_match_number(m_id: str) -> str:
    """Превращает номер матча в крупные emoji-цифры, чтобы он бросался в глаза."""
    return "".join(_KEYCAP_DIGITS.get(ch, ch) for ch in str(m_id))


async def _announce_lobby_ready(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    thread_id: Optional[int],
    m_id: str,
    m: Dict,
    host_p,
    host_side: str,
    final_map: str,
    banned_str: Optional[str] = None,
):
    """
    Отправляет ОДНО сообщение о том, что лобби собрано и пики завершены,
    плюс отдельное личное сообщение хосту с просьбой создать лобби.
    """
    ct_list = "\n".join(_pline(u) for u in m["ct"])
    t_list  = "\n".join(_pline(u) for u in m["t"])
    banned_line = f"🚫 Забанены: {banned_str}\n" if banned_str else ""

    text = (
        "✅ <b>ЛОББИ СОБРАНО — ВСЁ ГОТОВО!</b>\n"
        f"📌 Матч <b>#{m_id}</b> | Режим: <b>{m['mode'].upper()}</b>\n\n"
        f"🔵 <b>CT:</b>\n{ct_list}\n\n"
        f"🔴 <b>T:</b>\n{t_list}\n\n"
        f"🖥 Создаёт лобби: {host_p.tg_link()} ({host_side})\n"
        f"📨 Хост — не забудь скинуть код от лобби в чат!\n\n"
        f"🗺 Карта: <b>{final_map}</b>\n"
        f"{banned_line}"
        f"⚠️ Результат отправляйте в тему «результат игр», указав номер матча <b>#{m_id}</b>.\n\n"
        f"🎙 Каждая сторона договаривается между собой и заходит в свой войс 👇"
    )

    voice_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔵 Войс CT", url="https://link.parallelchat.app/?redirect=https://parallel.go.link/30FUc"),
            InlineKeyboardButton("🔴 Войс T",  url="https://link.parallelchat.app/?redirect=https://parallel.go.link/30FUc"),
        ]
    ])

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=voice_kb,
        )
    except Exception as e:
        print(f"[lobby_ready] не удалось отправить баннер матча #{m_id}: {e}")

    # Личное уведомление хосту — он не всегда замечает тег в общем чате
    if not _is_bot_uid(host_p.user_id):
        try:
            await context.bot.send_message(
                chat_id=host_p.user_id,
                text=(
                    f"🖥 <b>Ты хост в матче #{m_id}!</b>\n\n"
                    f"🗺 Карта: <b>{final_map}</b>\n"
                    f"🎯 Сторона: <b>{host_side}</b>\n\n"
                    f"📨 Создай лобби и отправь код в общий чат."
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            print(f"[lobby_ready] не удалось отправить ЛС хосту uid={host_p.user_id}: {e}")

    # ── ЛС каждому реальному игроку матча — кнопка «Отправить результат» ────
    # После окончания катки игрок жмёт кнопку и присылает скрин прямо в ЛС
    # боту — бот сам понимает, за какой это матч (см. sendres_/dm_result_wait
    # и result_dm_photo_handler).
    for member_uid in (m["ct"] + m["t"]):
        if _is_bot_uid(member_uid):
            continue
        side_tag = "🔵 CT" if member_uid in m["ct"] else "🔴 T"
        try:
            await context.bot.send_message(
                chat_id=member_uid,
                text=(
                    f"🎮 <b>Матч #{m_id} собран!</b>\n\n"
                    f"Твоя сторона: {side_tag}\n"
                    f"🗺 Карта: <b>{final_map}</b>\n"
                    f"{banned_line}"
                    f"\n🔵 <b>CT:</b>\n{ct_list}\n\n"
                    f"🔴 <b>T:</b>\n{t_list}\n\n"
                    f"🖥 Хост лобби: {host_p.tg_link()} ({host_side})\n\n"
                    f"Когда катка закончится — нажми кнопку ниже и пришли "
                    f"скриншот результата прямо в этот чат."
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📤 Отправить результат", callback_data=f"sendres_{m_id}")
                ]]),
            )
        except Exception as e:
            print(f"[lobby_ready] не удалось отправить ЛС игроку uid={member_uid}: {e}")


async def start_match(players: List[int], mode: str, db: Dict,
                      context: ContextTypes.DEFAULT_TYPE, chat_id: int,
                      thread_id: Optional[int] = None):
    db["match_counter"] += 1
    m_id   = str(db["match_counter"])
    random.shuffle(players)
    ct_cap = players[0]
    t_cap  = players[1]
    pool   = players[2:]

    # Рандомно выбираем хоста из двух капитанов
    host_uid = random.choice([ct_cap, t_cap])

    db["active_matches"][m_id] = {
        "mode": mode, "ct": [ct_cap], "t": [t_cap], "pool": pool,
        "turn": ct_cap, "phase": "pick", "maps": MAPS_LIST.copy(),
        "banned_maps": [], "pick_start_time": time.time(),
        "pick_timeout": PICK_TIMEOUT, "ban_timeout": BAN_TIMEOUT,
        "chat_id": chat_id,
        "thread_id": thread_id, "host_uid": host_uid,
    }
    save_db(db)

    ct_p      = get_player(ct_cap)
    t_p       = get_player(t_cap)
    host_p    = get_player(host_uid)
    host_side = "🔵 CT" if host_uid == ct_cap else "🔴 T"

    # ── ГРОМКИЙ ТЕГ ВСЕХ ИГРОКОВ ЛОББИ ───────────────────────────────────────
    # tg_link() рендерит <a href="tg://user?id=...">Ник</a> — Telegram
    # засчитывает это как настоящий тег (text_mention) и присылает игроку
    # пуш-уведомление, даже если у него нет username. Шлём это ОТДЕЛЬНЫМ
    # сообщением перед самим пиком, чтобы все 100% увидели, что их тегнули.
    real_players = [u for u in players if not _is_bot_uid(u)]
    tag_line = " ".join(get_player(u).tg_link() for u in real_players) or "—"
    tag_txt = (
        "🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔\n"
        "📣 <b>ЛОББИ СОБРАНО — ПОРА ПИКАТЬ!</b> 📣\n"
        f"🎮 Матч #{m_id}\n"
        "🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔\n\n"
        f"{tag_line}\n\n"
        "👇 Капитаны, переходите к выбору игроков ниже 👇"
    )
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text=tag_txt,
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        print(f"[start_match] не удалось отправить тег-сообщение матча #{m_id}: {e}")

    map_line = (
        f"🗺 Карта: <b>{MAPS_LIST[0]}</b>\n\n"
        if len(MAPS_LIST) == 1 else
        f"🗺 Карты в пуле: <b>{', '.join(MAPS_LIST)}</b> (после пика — бан карт)\n\n"
    )
    txt = (
        f"🆕 <b>Матч #{m_id} [{mode.upper()}]</b>\n\n"
        f"🔵 CT капитан: {ct_p.tg_link()} <code>[{ct_p.external_id or '?'}]</code>\n"
        f"🔴 T  капитан: {t_p.tg_link()} <code>[{t_p.external_id or '?'}]</code>\n\n"
        f"🖥 Создает лобби: {host_p.tg_link()} ({host_side})\n📨 Не забудь отправить в чат код от лобби\n\n"
        f"{map_line}"
        f"👥 В пуле: {len(pool)} игроков\n"
        f"⏳ На пик: <b>{PICK_TIMEOUT} сек</b>\n\n"
        f"Ход: 🔵 CT — выбирает первого игрока"
    )
    btns = _pick_buttons(m_id, pool)
    sent = await context.bot.send_message(
        chat_id=chat_id,
        message_thread_id=thread_id,
        text=txt,
        reply_markup=InlineKeyboardMarkup(btns) if btns else None,
        parse_mode=ParseMode.HTML
    )
    # Сохраняем message_id для таймера
    db["active_matches"][m_id]["pick_msg_id"] = sent.message_id
    save_db(db)
    # Запускаем фоновый таймер (обновление каждые 10 сек)
    task = asyncio.create_task(_pick_timer(m_id, context, chat_id))
    _pick_timer_tasks[m_id] = task
    if _is_bot_uid(ct_cap):
        await _bot_auto_pick(m_id, context, chat_id, thread_id)


def _create_fake_bot(db: Dict) -> int:
    db["bot_counter"] += 1
    bot_uid  = BOT_ID_START - db["bot_counter"]
    wins     = random.randint(0, 60)
    losses   = random.randint(0, 60)
    avg      = round(wins / (wins + losses) * 100, 1) if (wins + losses) else 0.0
    db["players"][str(bot_uid)] = asdict(Player(
        user_id=bot_uid,
        nickname=random.choice(BOT_NAMES) + f"#{db['bot_counter']}",
        external_id=f"bot_{db['bot_counter']}",
        elo=random.randint(800, 1800),
        wins=wins, losses=losses, avg=avg, is_bot=True
    ))
    return bot_uid

# ════════════════════════════════════════════════
#              ПУБЛИЧНЫЕ КОМАНДЫ
# ════════════════════════════════════════════════

def main_menu_kb(uid: int, reg: bool) -> InlineKeyboardMarkup:
    """Главное меню в ЛС — только кнопки, только то, что реально есть в боте."""
    keyboard = []
    if WEBAPP_URL:
        keyboard.append([InlineKeyboardButton(
            "🌐 Открыть Night Faceit Stats",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )])
    keyboard.append([InlineKeyboardButton("🔍 Найти матч", callback_data="cmd_play")])
    if reg:
        keyboard.append([InlineKeyboardButton("📊 Мой профиль", callback_data="cmd_stats")])
    else:
        keyboard.append([InlineKeyboardButton("📝 Регистрация", callback_data="cmd_reg")])
    keyboard.append([
        InlineKeyboardButton("🏆 Топ",   callback_data="cmd_top"),
        InlineKeyboardButton("✨ Сезон", callback_data="cmd_season"),
    ])
    keyboard.append([
        InlineKeyboardButton("📜 Правила",   callback_data="cmd_rules"),
        InlineKeyboardButton("🆘 Поддержка", callback_data="cmd_support"),
    ])
    if is_moderator(uid):
        keyboard.append([InlineKeyboardButton("🌙 Команда Faceit", callback_data="cmd_admins")])
        keyboard.append([InlineKeyboardButton("🧾 Результаты матчей", callback_data="cmd_pending_list")])
    return InlineKeyboardMarkup(keyboard)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start — приветствие."""
    uid  = update.effective_user.id
    name = update.effective_user.first_name or "игрок"
    db   = load_db()
    s    = str(uid)
    reg  = bool(s in db["players"] and db["players"][s].get("external_id"))

    # ── Группа/беседа: тут всё по полным командам, кнопочное меню не нужно ──
    if update.message and update.message.chat.type != "private":
        text = (
            f"🌙 <b>Night Faceit</b>\n\n"
            f"<b>Команды:</b>\n"
            f"/reg — Регистрация\n"
            f"/5v5 — Лобби 5v5\n"
            f"/2v2 — Лобби 2v2\n"
            f"/stats — Твоя статистика\n"
            f"/top — Топ игроков\n"
            f"/admins — Список команд по ролям\n"
            f"/rules — Правила"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    # ── ЛС: только кнопки, без личных данных вроде ID/баланса ──
    text = (
        f"👋 <b>Привет, {name}!</b>\n\n"
        f"🌙 <b>Night Faceit</b> — твоя персональная лига\n\n"
        f"{'✅ Ты зарегистрирован' if reg else '❌ Ты не зарегистрирован'}\n\n"
        f"👇 Выбери действие:"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(uid, reg)
    )


async def reg_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await dm_buttons_only(update): return
    if await gate(update, need_reg=False): return
    uid = update.effective_user.id
    s   = str(uid)
    db  = load_db()

    if s in db["players"] and db["players"][s].get("external_id"):
        await update.message.reply_text(
            "🚫 Вы уже зарегистрированы.\n"
            "Для смены данных обратитесь к администратору."
        )
        return

    if len(context.args) < 3:
        await update.message.reply_text(
            "📝 <b>Формат регистрации:</b>\n"
            "<code>/reg GAME_ID Никнейм Платформа</code>\n\n"
            "🖥 Платформы: <code>pc</code> или <code>mobile</code>\n\n"
            "Примеры:\n"
            "<code>/reg 6888 Londyyy pc</code>\n"
            "<code>/reg 6888 Londyyy mobile</code>\n\n"
            "⚠️ <b>За обман платформы вы получаете бан от администрации Faceit!</b>",
            parse_mode=ParseMode.HTML
        )
        return

    game_id  = context.args[0]
    platform = context.args[-1].lower()

    if platform not in ("pc", "mobile"):
        await update.message.reply_text(
            "🚫 <b>Неверная платформа!</b>\n\n"
            "Укажи <code>pc</code> или <code>mobile</code> в конце:\n"
            "<code>/reg GAME_ID Никнейм pc</code>\n"
            "<code>/reg GAME_ID Никнейм mobile</code>\n\n"
            "⚠️ <b>За обман платформы вы получаете бан от администрации Faceit!</b>",
            parse_mode=ParseMode.HTML
        )
        return

    nickname = " ".join(context.args[1:-1])

    if not game_id.isdigit():
        await update.message.reply_text(
            "🚫 <b>GAME ID должен содержать только цифры!</b>\n\n"
            "Пример: <code>/reg 6888 Londyyy pc</code>",
            parse_mode=ParseMode.HTML
        )
        return

    if len(nickname) > 32:
        await update.message.reply_text("🚫 Никнейм слишком длинный (максимум 32 символа).")
        return

    if not nickname:
        await update.message.reply_text(
            "🚫 Не указан никнейм!\n\n"
            "Пример: <code>/reg 6888 Londyyy pc</code>",
            parse_mode=ParseMode.HTML
        )
        return

    for d in db["players"].values():
        if d.get("external_id") == game_id and not d.get("is_bot"):
            await update.message.reply_text("🚫 Этот GAME ID уже зарегистрирован.")
            return

    player_data = asdict(Player(uid, nickname, game_id))
    player_data["platform"] = platform
    player_data["registered_ts"] = time.time()
    db["players"][s] = player_data
    save_db(db)

    platform_label = "📱 Мобильный" if platform == "mobile" else "🖥 ПК"
    win_d, loss_d  = elo_deltas_for(platform)

    await update.message.reply_text(
        f"✅ <b>Зарегистрирован!</b>\n\n"
        f"👤 Никнейм: <b>{nickname}</b>\n"
        f"🆔 GAME ID: <code>{game_id}</code>\n"
        f"🎮 Платформа: <b>{platform_label}</b>\n"
        f"📊 ELO за победу: <b>+{win_d}</b> | за поражение: <b>-{loss_d}</b>\n\n"
        f"Вставай в очередь: /5v5 или /2v2\n\n"
        f"━━━━━━━━━━━━━━\n"
        f"⚠️ <b>За обман платформы вы получаете бан от администрации Faceit!</b>",
        parse_mode=ParseMode.HTML
    )


async def platform_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/platform pc|mobile [user_id] — выбор платформы. Админ может менять другим игрокам."""
    uid     = update.effective_user.id
    db      = load_db()
    admin   = is_admin(uid)

    # ── Определяем цель: reply, user_id-аргумент или сам пользователь ──
    target_uid = None
    target_from_reply = False

    if (update.message.reply_to_message and
            update.message.reply_to_message.from_user and
            not update.message.reply_to_message.forum_topic_created and
            not update.message.reply_to_message.forum_topic_edited):
        target_uid        = update.message.reply_to_message.from_user.id
        target_from_reply = True

    # Разбираем аргументы: /platform pc|mobile [user_id]  или  /platform [user_id] pc|mobile
    choice     = None
    arg_uid    = None
    for arg in (context.args or []):
        if arg.lower() in ("pc", "mobile"):
            choice = arg.lower()
        else:
            try:
                arg_uid = int(arg)
            except ValueError:
                pass

    if arg_uid and not target_from_reply:
        target_uid = arg_uid

    # Если цель не задана — это сам пользователь
    if target_uid is None:
        target_uid = uid

    changing_other = (target_uid != uid)

    # Только админ может менять чужую платформу
    if changing_other and not admin:
        await update.message.reply_text("🚫 Только администратор может менять платформу другим игрокам.")
        return

    s = str(target_uid)
    if s not in db["players"] or not db["players"][s].get("external_id"):
        await update.message.reply_text(
            "❌ Игрок не зарегистрирован." if changing_other else NOT_REGISTERED_MSG,
            parse_mode=ParseMode.HTML
        )
        return

    # Если платформа не указана — показываем текущую
    if choice is None:
        cur       = db["players"][s].get("platform", "pc")
        cur_label = "📱 Мобильный" if cur == "mobile" else "🖥 ПК"
        target_p  = get_player(target_uid)
        if changing_other:
            await update.message.reply_text(
                f"👤 Игрок: <b>{target_p.nickname}</b>\n"
                f"🎮 Текущая платформа: <b>{cur_label}</b>\n\n"
                f"Изменить: <code>/platform pc {target_uid}</code> или <code>/platform mobile {target_uid}</code>",
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                f"Текущая платформа: <b>{cur_label}</b>\n\n"
                f"Изменить: <code>/platform pc</code> или <code>/platform mobile</code>",
                parse_mode=ParseMode.HTML
            )
        return

    db["players"][s]["platform"] = choice
    save_db(db)
    label          = "📱 Мобильный" if choice == "mobile" else "🖥 ПК"
    win_d, loss_d  = elo_deltas_for(choice)

    if changing_other:
        target_p = get_player(target_uid)
        await update.message.reply_text(
            f"✅ <b>Платформа изменена!</b>\n\n"
            f"👤 Игрок: <b>{target_p.nickname}</b> (<code>{target_uid}</code>)\n"
            f"🎮 Новая платформа: <b>{label}</b>\n"
            f"📊 За победу: <b>+{win_d} ELO</b> | За поражение: <b>-{loss_d} ELO</b>",
            parse_mode=ParseMode.HTML
        )
        # Уведомляем самого игрока
        try:
            await update.get_bot().send_message(
                chat_id=target_uid,
                text=(
                    f"⚙️ <b>Администратор изменил вашу платформу</b>\n\n"
                    f"🎮 Новая платформа: <b>{label}</b>\n"
                    f"📊 За победу: <b>+{win_d} ELO</b> | За поражение: <b>-{loss_d} ELO</b>"
                ),
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
    else:
        await update.message.reply_text(
            f"✅ Платформа изменена на: <b>{label}</b>\n"
            f"Теперь за победу: <b>+{win_d} ELO</b>, за поражение: <b>-{loss_d} ELO</b>",
            parse_mode=ParseMode.HTML
        )


def _elo_progress(elo: int):
    """Возвращает (уровень, % прогресса до след. уровня, ELO до след. уровня или None на макс. уровне)."""
    bounds = [
        (0, 500), (501, 750), (751, 900), (901, 1050), (1051, 1200),
        (1201, 1350), (1351, 1530), (1531, 1750), (1751, 2000), (2001, 10**9),
    ]
    for i, (lo, hi) in enumerate(bounds):
        if lo <= elo <= hi:
            level = i + 1
            if level == 10:
                return level, 100, None
            span = hi - lo + 1
            pct = int(((elo - lo) / span) * 100)
            to_next = hi + 1 - elo
            return level, pct, to_next
    return 1, 0, 501


def _progress_bar(pct: int, length: int = 10) -> str:
    filled = max(0, min(length, round(pct / 100 * length)))
    return "▰" * filled + "▱" * (length - filled)


def build_stats_text(target: int, looking_at_self: bool, private_chat: bool = True) -> tuple:
    """Возвращает (text, keyboard) для профиля игрока target. Переиспользуется
    и командой /stats (в беседе), и кнопкой «📊 Мой профиль» в ЛС.

    private_chat: web_app-кнопки Telegram разрешает ТОЛЬКО в личных чатах с ботом —
    в группах это вызывает ошибку BUTTON_TYPE_INVALID. Поэтому кнопку добавляем
    только если сейчас действительно ЛС."""
    db = load_db()
    s  = str(target)

    if s not in db["players"] or not db["players"][s].get("external_id"):
        if looking_at_self:
            return (
                "❌ Вы не зарегистрированы!\n\n"
                "Нажмите «📝 Регистрация» в главном меню — /start.",
                None
            )
        return ("❌ Этот пользователь не зарегистрирован.", None)

    d = db["players"][s]
    for field, val in [("wins",0),("losses",0),("avg",0.0),("elo",0),
                       ("elo_5v5",0),("elo_2v2",0),
                       ("wins_5v5",0),("losses_5v5",0),
                       ("wins_2v2",0),("losses_2v2",0),
                       ("avg_5v5",0.0),("avg_2v2",0.0),
                       ("external_id",""),("is_bot",False),("nickname","?"),("user_id",target),
                       ("total_kills",0),("total_deaths",0),
                       ("platform","pc")]:
        d.setdefault(field, val)

    p = _make_player(d)

    if p.is_bot:
        return ("🤖 Это тестовый бот — статистики нет.", None)

    total_games = p.wins + p.losses
    total_wr    = f"{p.avg:.1f}%" if total_games else "—"
    unified_elo = p.elo

    if is_creator(target):
        role_line = "👑 <b>Создатель</b>\n"
    elif is_admin(target):
        role_line = "🛡 <b>Администратор</b>\n"
    elif is_moderator(target):
        role_line = "🔰 <b>Модератор</b>\n"
    else:
        role_line = ""

    def lvl_icon_for(elo: int) -> str:
        if elo >= 2001: return "🏆 LVL 10"
        if elo >= 1751: return "🔴 LVL 9"
        if elo >= 1531: return "🔴 LVL 8"
        if elo >= 1351: return "🟠 LVL 7"
        if elo >= 1201: return "🟠 LVL 6"
        if elo >= 1051: return "🟡 LVL 5"
        if elo >= 901:  return "🟡 LVL 4"
        if elo >= 751:  return "🟢 LVL 3"
        if elo >= 501:  return "🟢 LVL 2"
        return "⚪ LVL 1"

    platform_label = "📱 Мобильный" if p.platform == "mobile" else "🖥 ПК"
    kd_label = f"{round(p.total_kills / p.total_deaths, 2)}" if p.total_deaths else f"{p.total_kills}"

    if p.is_calibrated:
        level, pct, to_next = _elo_progress(unified_elo)
        bar = _progress_bar(pct)
        next_line = f"до LVL {level+1}: <b>{to_next}</b> ELO" if to_next is not None else "🏆 максимальный уровень"
        rank_block = (
            f"{lvl_icon_for(unified_elo)}   <b>{unified_elo}</b> ELO\n"
            f"{bar}  {pct}%\n"
            f"{next_line}\n"
        )
    else:
        cal_bar = _progress_bar(int(total_games / CALIBRATION_GAMES * 100))
        rank_block = (
            f"🔄 <b>Калибровка</b>\n"
            f"{cal_bar}  {total_games}/{CALIBRATION_GAMES} матчей\n"
            f"Сыграйте ещё <b>{max(0, CALIBRATION_GAMES - total_games)}</b> матч(ей), чтобы получить ранг\n"
        )

    text = (
        f"✦ {p.tg_link()} ✦\n"
        f"🆔 <code>{p.external_id or 'не указан'}</code>   {platform_label}\n"
        f"{role_line}"
        f"━━━━━━━━━━━━━━\n"
        f"{rank_block}"
        f"━━━━━━━━━━━━━━\n"
        f"🏆 Побед: <b>{p.wins}</b>      💀 Поражений: <b>{p.losses}</b>\n"
        f"📈 Winrate: <b>{total_wr}</b>      🎮 Матчей: <b>{total_games}</b>\n"
        f"🔫 K/D: <b>{kd_label}</b>  <i>({p.total_kills}/{p.total_deaths})</i>\n"
        f"━━━━━━━━━━━━━━"
    )

    kb = None
    if looking_at_self and WEBAPP_URL and private_chat:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            "📊 Подробная статистика и история матчей",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )]])

    return (text, kb)


async def _fetch_avatar_path(bot, user_id: int) -> Optional[str]:
    """Скачивает текущую аватарку пользователя из его Telegram-профиля во
    временный файл и возвращает путь к нему. Возвращает None, если у
    пользователя нет фото профиля или скачать не получилось — в этом случае
    карточка нарисует прежний плейсхолдер вместо аватарки."""
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if not photos or photos.total_count == 0:
            return None
        file_id = photos.photos[0][-1].file_id
        tg_file = await bot.get_file(file_id)
        import io
        buf = io.BytesIO()
        await tg_file.download_to_memory(buf)
        buf.seek(0)
        avatar_path = _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)),
            f"_avatar_{user_id}.jpg"
        )
        with open(avatar_path, "wb") as f:
            f.write(buf.read())
        return avatar_path
    except Exception as e:
        print(f"⚠️ Не удалось скачать аватар Telegram user_id={user_id}: {e!r}")
        return None


async def _fetch_avatar_paths(bot, user_ids: List[int]) -> Dict[int, str]:
    """Скачивает аватарки сразу нескольких игроков (для карточки топа).
    Возвращает {user_id: путь_к_файлу} только для тех, у кого получилось
    скачать фото профиля; остальные останутся с плейсхолдером."""
    result: Dict[int, str] = {}
    for uid in user_ids:
        path = await _fetch_avatar_path(bot, uid)
        if path:
            result[uid] = path
    return result


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await dm_buttons_only(update): return
    try:
        uid    = update.effective_user.id
        target = uid

        # В topics reply_to_message указывает на заголовок темы — игнорируем
        if (update.message.reply_to_message and
                update.message.reply_to_message.from_user and
                update.message.reply_to_message.from_user.id != update.effective_user.id and
                not update.message.reply_to_message.forum_topic_created and
                not update.message.reply_to_message.forum_topic_edited):
            target = update.message.reply_to_message.from_user.id
        elif context.args:
            try:
                target = int(context.args[0])
            except ValueError:
                await update.message.reply_text("Формат: /stats [user_id]"); return

        looking_at_self = (target == uid)
        private_chat = (update.effective_chat.type == "private")
        text, kb = build_stats_text(target, looking_at_self, private_chat)

        # Если игрок не зарегистрирован / это бот — build_stats_text уже вернул
        # понятное сообщение об этом, картинку в таком случае не рисуем.
        card_path = _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)),
            f"_stats_card_{target}_{update.effective_chat.id}.png"
        )
        avatar_path = await _fetch_avatar_path(context.bot, target)
        result = None
        try:
            result = render_stats_card(target, card_path, avatar_path=avatar_path)
        except Exception as e:
            print(f"⚠️ Не удалось сгенерировать карточку профиля uid={target}: {e!r}")
            result = None

        if not result:
            if avatar_path:
                try: os.remove(avatar_path)
                except OSError: pass
            await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            return

        try:
            with open(result, "rb") as f:
                await update.message.reply_photo(photo=f, reply_markup=kb)
        finally:
            try:
                os.remove(result)
            except OSError:
                pass
            if avatar_path:
                try: os.remove(avatar_path)
                except OSError: pass
    except Exception as e:
        import traceback
        print(f"[stats_cmd ERROR] uid={update.effective_user.id} error={e}\n{traceback.format_exc()}")
        await update.message.reply_text(f"❌ Ошибка stats: {e}")


async def listdb_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    db    = load_db()
    lines = ["📋 <b>Все игроки в БД:</b>\n"]
    for uid_str, d in db["players"].items():
        if d.get("is_bot"): continue
        lines.append(
            f"ID: <code>{uid_str}</code> | <b>{d.get('nickname','?')}</b> | GAME ID: <code>{d.get('external_id','нет')}</code>"
        )
    if len(lines) == 1:
        lines.append("Нет игроков")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ════════════════════════════════════════════════
#              КАРТОЧКА ТОП-10 (PNG)
# ════════════════════════════════════════════════

_CARD_W          = 1200
_CARD_HEADER_H   = 230
_CARD_ROW_H      = 108
_CARD_ROW_GAP    = 14
_CARD_PAD_BOTTOM = 30

_CARD_BG          = (7, 7, 13)
_CARD_PURPLE      = (124, 92, 255)
_CARD_PURPLE_LIT  = (170, 148, 255)
_CARD_WHITE       = (238, 238, 244)
_CARD_GRAY        = (126, 133, 150)
_CARD_ROW_BG      = (13, 13, 22)
_CARD_ROW_BORDER  = (26, 25, 40)
_CARD_LOGO_BG     = (17, 16, 28)
_CARD_LOGO_BORDER = (58, 50, 90)

_FONT_CANDIDATES = [
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "fonts"),  # шрифты рядом со скриптом (в репо)
    "/usr/share/fonts/truetype/dejavu",   # Debian/Ubuntu
    "/usr/share/fonts/dejavu",            # некоторые другие дистрибутивы
]


def _find_font_path(name: str) -> Optional[str]:
    for d in _FONT_CANDIDATES:
        p = _os.path.join(d, name)
        if _os.path.exists(p):
            return p
    return None


def _card_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    path = _find_font_path(name)
    if path:
        return ImageFont.truetype(path, size)
    # Крайний случай — шрифт не найден нигде. Используем встроенный шрифт PIL
    # (без кириллицы, но хотя бы не роняет всю карточку).
    print(f"⚠️ Шрифт {name} не найден ни в одном из путей: {_FONT_CANDIDATES}")
    return ImageFont.load_default()


def _card_draw_logo_icon(d: ImageDraw.ImageDraw, cx: float, cy: float, size: float,
                          color=_CARD_PURPLE_LIT, bg=_CARD_LOGO_BG, border=_CARD_LOGO_BORDER, bw: int = 2):
    r = size / 2
    d.rounded_rectangle([cx - r, cy - r, cx + r, cy + r], radius=r * 0.32, fill=bg, outline=border, width=bw)
    w = max(3, int(size * 0.09))
    d.line([(cx - r * 0.42, cy - r * 0.48), (cx - r * 0.02, cy + r * 0.42), (cx + r * 0.12, cy - r * 0.02)],
           fill=color, width=w, joint="curve")
    d.line([(cx + r * 0.12, cy - r * 0.02), (cx + r * 0.42, cy - r * 0.48)], fill=color, width=w, joint="curve")


def _card_draw_hex(d: ImageDraw.ImageDraw, cx: float, cy: float, size: float, number: int, color, font):
    pts = [(cx + size * math.cos(math.pi / 6 + i * math.pi / 3),
            cy + size * math.sin(math.pi / 6 + i * math.pi / 3)) for i in range(6)]
    d.polygon(pts, outline=color, width=3)
    txt = str(number)
    bbox = d.textbbox((0, 0), txt, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text((cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]), txt, font=font, fill=color)


def _card_level_color(lvl: int):
    if lvl >= 8:
        return (255, 104, 0)
    if lvl >= 4:
        return (255, 184, 0)
    return (150, 200, 130)


def _lvl_number(elo: int) -> int:
    if elo >= 2001: return 10
    if elo >= 1751: return 9
    if elo >= 1531: return 8
    if elo >= 1351: return 7
    if elo >= 1201: return 6
    if elo >= 1051: return 5
    if elo >= 901:  return 4
    if elo >= 751:  return 3
    if elo >= 501:  return 2
    return 1


def build_top_players(limit: int = 10) -> List[Player]:
    """Возвращает калиброванных игроков (сыграно >= CALIBRATION_GAMES матчей),
    отсортированных по ELO. Используется и текстом, и карточкой /top.
    Некалиброванные игроки в топ не попадают — как и в настоящем FACEIT,
    у них ещё нет подтверждённого ранга."""
    db      = load_db()
    players = []
    for d in db["players"].values():
        try:
            if not d.get("external_id") or d.get("is_bot"): continue
            for field, val in [("wins", 0), ("losses", 0), ("avg", 0.0), ("elo", 0),
                                ("elo_5v5", 0), ("elo_2v2", 0),
                                ("wins_5v5", 0), ("losses_5v5", 0),
                                ("wins_2v2", 0), ("losses_2v2", 0),
                                ("avg_5v5", 0.0), ("avg_2v2", 0.0),
                                ("external_id", ""), ("is_bot", False),
                                ("total_kills", 0), ("total_deaths", 0),
                                ("platform", "pc")]:
                d.setdefault(field, val)
            d["elo"] = max(_safe_num(d, "elo", 0), _safe_num(d, "elo_5v5", 0), _safe_num(d, "elo_2v2", 0))
            p = _make_player(d)
            if not p.is_calibrated:
                continue
            players.append(p)
        except Exception as e:
            print(f"⚠️ Пропускаю повреждённую запись игрока в /top: {e!r}")
            continue
    players.sort(key=lambda p: p.elo, reverse=True)
    return players[:limit]


def render_top_card(out_path: str, group_name: str = "NightFaceit", limit: int = 10,
                     avatar_paths: Optional[Dict[int, str]] = None) -> Optional[str]:
    """Рисует PNG-карточку топ-игроков (актуальные данные из БД) и сохраняет в out_path.
    Возвращает out_path, либо None если рейтинг пуст."""
    players = build_top_players(limit)
    if not players:
        return None

    f_title = _card_font(48)
    f_logo  = _card_font(24)
    f_head  = _card_font(19, bold=False)
    f_rank  = _card_font(26)
    f_name  = _card_font(27)
    f_stat  = _card_font(25)
    f_hex   = _card_font(19)

    n = len(players)
    H = _CARD_HEADER_H + n * (_CARD_ROW_H + _CARD_ROW_GAP) + _CARD_PAD_BOTTOM
    img = Image.new("RGB", (_CARD_W, H), _CARD_BG)
    d = ImageDraw.Draw(img)

    d.text((40, 32), "ТОП", font=f_title, fill=_CARD_PURPLE_LIT)
    tw = d.textbbox((0, 0), "ТОП ", font=f_title)[2]
    d.text((40 + tw, 32), "ИГРОКИ", font=f_title, fill=_CARD_WHITE)

    d.rounded_rectangle([40, 100, 300, 158], radius=15, fill=(13, 12, 22), outline=(36, 32, 54), width=1)
    _card_draw_logo_icon(d, 70, 129, 34)
    d.text((98, 118), group_name, font=f_logo, fill=_CARD_WHITE)

    headers = [("Место", 40), ("Игрок", 175), ("Матчи", 600), ("% побед", 730), ("Очки", 880), ("K/D", 1075)]
    hy = 195
    for text, x in headers:
        d.text((x, hy), text, font=f_head, fill=_CARD_GRAY)
    d.line([(40, hy + 33), (_CARD_W - 40, hy + 33)], fill=_CARD_ROW_BORDER, width=1)

    y = hy + 48
    for i, p in enumerate(players, start=1):
        d.rounded_rectangle([40, y, _CARD_W - 40, y + _CARD_ROW_H], radius=16,
                             fill=_CARD_ROW_BG, outline=_CARD_ROW_BORDER, width=1)
        cy = y + _CARD_ROW_H // 2
        d.text((40 + 24, cy - 16), str(i), font=f_rank, fill=_CARD_WHITE)
        row_avatar = (avatar_paths or {}).get(p.user_id)
        _card_draw_avatar(img, d, 175 + 38, cy, 29, row_avatar, border=(150, 90, 160))

        name = p.nickname if len(p.nickname) <= 16 else p.nickname[:15] + "…"
        d.text((175 + 90, cy - 15), name, font=f_name, fill=_CARD_WHITE)

        total = p.wins + p.losses
        wr    = f"{p.avg:.0f}%" if total else "—"
        kd    = (p.total_kills / p.total_deaths) if p.total_deaths else 0.0

        d.text((600, cy - 14), str(total), font=f_stat, fill=_CARD_WHITE)
        d.text((730, cy - 14), wr, font=f_stat, fill=_CARD_WHITE)

        lvl = _lvl_number(p.elo)
        hc  = _card_level_color(lvl)
        _card_draw_hex(d, 880 + 20, cy, 21, lvl, hc, f_hex)
        d.text((880 + 55, cy - 14), str(p.elo), font=f_stat, fill=_CARD_WHITE)
        d.text((1075, cy - 14), f"{kd:.2f}", font=f_stat, fill=_CARD_WHITE)

        y += _CARD_ROW_H + _CARD_ROW_GAP

    img.save(out_path)
    return out_path


# ════════════════════════════════════════════════
#           КАРТОЧКА /stats (PNG, личный профиль)
# ════════════════════════════════════════════════

def compute_player_map_stats(uid: int) -> Dict[str, Dict[str, int]]:
    """Считает победы/поражения/киллы/смерти игрока в разрезе карт,
    на основе реально сыгранных и завершённых матчей (finished_matches).
    Никаких выдуманных чисел — только то, что реально записано в БД."""
    db = load_db()
    finished = db.get("finished_matches", {})
    stats: Dict[str, Dict[str, int]] = {}
    s_uid = str(uid)
    for m in finished.values():
        winners = m.get("winners", []) or []
        losers  = m.get("losers", [])  or []
        if uid not in winners and uid not in losers:
            continue
        map_name = m.get("map") or "—"
        won = uid in winners
        kd_by_uid = m.get("kd_by_uid", {}) or {}
        kills, deaths = kd_by_uid.get(s_uid, [0, 0])
        row = stats.setdefault(map_name, {"wins": 0, "losses": 0, "kills": 0, "deaths": 0})
        if won:
            row["wins"] += 1
        else:
            row["losses"] += 1
        row["kills"]  += int(kills)
        row["deaths"] += int(deaths)
    return stats


def compute_recent_matches(uid: int, limit: int = 5) -> List[Dict[str, Any]]:
    """Список последних завершённых матчей игрока (реальные записи из БД),
    отсортированный от новых к старым."""
    db = load_db()
    finished = db.get("finished_matches", {})
    rows = []
    s_uid = str(uid)
    for m_id, m in finished.items():
        winners = m.get("winners", []) or []
        losers  = m.get("losers", [])  or []
        if uid not in winners and uid not in losers:
            continue
        won = uid in winners
        elo_snap = m.get("elo_snapshot", {}) or {}
        raw_delta = elo_snap.get(s_uid)
        if isinstance(raw_delta, dict):
            delta = raw_delta.get("elo_after", 0) - raw_delta.get("elo_before", 0)
        else:
            delta = raw_delta if (raw_delta is None or won) else -raw_delta
        rows.append({
            "match_id": m_id,
            "mode": m.get("mode", "?"),
            "map": m.get("map") or "—",
            "won": won,
            "elo_delta": delta,
            "ts": m.get("finished_ts", 0),
        })
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows[:limit]


def _card_draw_donut(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float,
                      pct: float, label: str, sub_lines: List[str],
                      font_big, font_small, ring_color=_CARD_PURPLE_LIT, track_color=(40, 38, 56)):
    """Рисует кольцевую диаграмму (донат) с числом в центре и подписями снизу."""
    bbox = [cx - r, cy - r, cx + r, cy + r]
    d.arc(bbox, 0, 360, fill=track_color, width=8)
    pct = max(0.0, min(1.0, pct))
    if pct > 0:
        d.arc(bbox, -90, -90 + 360 * pct, fill=ring_color, width=8)
    # Максимальная ширина текста внутри кольца — с запасом от внутреннего края,
    # иначе длинные значения вроде "100%" вылезают за пределы кружка.
    max_label_w = r * 1.5
    base_size = getattr(font_big, "size", 30)
    fit_font = _card_fit_font(d, label, max_label_w, base_size, min_size=12,
                               bold=("Bold" in getattr(font_big, "path", "DejaVuSans-Bold.ttf")))
    tb = d.textbbox((0, 0), label, font=fit_font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    d.text((cx - tw / 2 - tb[0], cy - th / 2 - tb[1]), label, font=fit_font, fill=_CARD_WHITE)
    ly = cy + r + 14
    for line in sub_lines:
        lb = d.textbbox((0, 0), line, font=font_small)
        lw = lb[2] - lb[0]
        d.text((cx - lw / 2 - lb[0], ly), line, font=font_small, fill=_CARD_GRAY)
        ly += 24


def _make_gradient(w: int, h: int, c1: tuple, c2: tuple, steps: int = 48) -> Image.Image:
    """Диагональный градиент c1 (верх-лево) → c2 (низ-право), размером w×h.
    Чистый PIL/Python, без numpy — строим маленькую сетку steps×steps и
    растягиваем её билинейно, это быстро и не тянет лишних зависимостей
    (numpy не всегда стоит на проде — ровно так же ломался Pillow раньше)."""
    small = Image.new("RGB", (steps, steps))
    px = small.load()
    denom = max(1, 2 * (steps - 1))
    for j in range(steps):
        for i in range(steps):
            t = (i + j) / denom
            r = int(c1[0] + (c2[0] - c1[0]) * t)
            g = int(c1[1] + (c2[1] - c1[1]) * t)
            b = int(c1[2] + (c2[2] - c1[2]) * t)
            px[i, j] = (r, g, b)
    return small.resize((w, h), Image.BILINEAR)


def _card_section_title(d: ImageDraw.ImageDraw, x: int, y: int, text: str, font,
                         accent=(150, 110, 255)):
    """Заголовок секции с цветным акцентным маркером слева (замена emoji-иконкам)."""
    d.rounded_rectangle([x, y + 6, x + 6, y + 26], radius=3, fill=accent)
    d.text((x + 18, y), text, font=font, fill=_CARD_WHITE)


# ── ХЕЛПЕРЫ ДЛЯ НОВОЙ ВЁРСТКИ /stats ──────────────────────────────────────

def _card_fit_font(d: ImageDraw.ImageDraw, text: str, max_w: float, base_size: int,
                    min_size: int = 12, bold: bool = True):
    """Подбирает самый крупный размер шрифта (от base_size вниз до min_size),
    при котором text не шире max_w. Не даёт длинным значениям («Мобильный»,
    «100%» и т.п.) вылезать за края своих блоков/кружков."""
    size = base_size
    while size > min_size:
        f = _card_font(size, bold=bold)
        tb = d.textbbox((0, 0), text, font=f)
        if (tb[2] - tb[0]) <= max_w:
            return f
        size -= 1
    return _card_font(min_size, bold=bold)


def _card_center_text(d: ImageDraw.ImageDraw, cx: float, cy: float, text: str, font, fill):
    """Рисует текст, центрированный по (cx, cy). Возвращает (ширина, высота)."""
    tb = d.textbbox((0, 0), text, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    d.text((cx - tw / 2 - tb[0], cy - th / 2 - tb[1]), text, font=font, fill=fill)
    return tw, th


def _card_right_text(d: ImageDraw.ImageDraw, right_x: float, y: float, text: str, font, fill):
    """Рисует текст, прижатый правым краем к right_x. Возвращает ширину текста."""
    tb = d.textbbox((0, 0), text, font=font)
    tw = tb[2] - tb[0]
    d.text((right_x - tw - tb[0], y), text, font=font, fill=fill)
    return tw


def _card_wrap_text(d: ImageDraw.ImageDraw, text: str, font, max_w: float) -> List[str]:
    """Простой word-wrap по ширине текста для многострочных подсказок на карточке."""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        tb = d.textbbox((0, 0), trial, font=font)
        if tb[2] - tb[0] <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _card_divider(d: ImageDraw.ImageDraw, x0: float, x1: float, y: float, color=_CARD_ROW_BORDER):
    d.line([(x0, y), (x1, y)], fill=color, width=1)


def _card_stat_col(d: ImageDraw.ImageDraw, x: float, w: float, y: float, label: str, value: str,
                    f_lb, f_val, val_color=_CARD_WHITE):
    """Одна колонка в 3-колоночной строке статистики (label сверху, value снизу, по центру)."""
    cx = x + w / 2
    _card_center_text(d, cx, y, label, f_lb, _CARD_GRAY)
    base_size = getattr(f_val, "size", 21)
    fit_val_font = _card_fit_font(d, value, w - 10, base_size, min_size=12)
    _card_center_text(d, cx, y + 22, value, fit_val_font, val_color)


def _card_leaderboard_row(d: ImageDraw.ImageDraw, x: float, w: float, y: float,
                           rank_txt: str, name: str, value_txt: str, highlight: bool, f_lb):
    """Строка таблицы лиги: место, мини-аватар, ник, ELO (справа)."""
    col = _CARD_PURPLE_LIT if highlight else _CARD_GRAY
    name_col = _CARD_PURPLE_LIT if highlight else _CARD_WHITE
    d.text((x, y), rank_txt, font=f_lb, fill=col)
    _card_draw_logo_icon(d, x + 46, y + 11, 24, color=name_col, bg=_CARD_LOGO_BG, border=_CARD_LOGO_BORDER, bw=1)
    nm = name if len(name) <= 15 else name[:14] + "…"
    d.text((x + 66, y), nm, font=f_lb, fill=name_col)
    _card_right_text(d, x + w, y, value_txt, f_lb, _CARD_PURPLE_LIT if highlight else _CARD_GRAY)


def _card_recent_row(d: ImageDraw.ImageDraw, x: float, w: float, y: float, h: float,
                      row: Dict[str, Any], date_txt: str, f_name, f_sub):
    """Строка списка «Последние матчи»: W/L-иконка, карта+режим, дельта ELO, дата."""
    won = row["won"]
    fill_c = (20, 40, 30) if won else (45, 20, 20)
    edge_c = (90, 220, 140) if won else (230, 100, 100)
    sq = 34
    sy = y + (h - sq) / 2
    d.rounded_rectangle([x, sy, x + sq, sy + sq], radius=9, fill=fill_c, outline=edge_c, width=2)
    _card_center_text(d, x + sq / 2, sy + sq / 2, "W" if won else "L", f_sub, edge_c)

    tx = x + sq + 14
    d.text((tx, y + 4), row.get("map", "—"), font=f_name, fill=_CARD_WHITE)
    d.text((tx, y + h - 22), row.get("mode", ""), font=f_sub, fill=_CARD_GRAY)

    delta = row.get("elo_delta")
    if delta is not None:
        delta_txt = f"+{delta}" if delta >= 0 else str(delta)
        delta_col = (110, 220, 150) if delta >= 0 else (230, 110, 110)
        _card_right_text(d, x + w, y + 4, delta_txt, f_sub, delta_col)
    _card_right_text(d, x + w, y + h - 22, date_txt, f_sub, _CARD_GRAY)


def _card_find_map_image(map_name: str) -> Optional[str]:
    """Ищет локальную картинку карты — сначала в папке maps/ рядом со скриптом
    (maps/dust2.jpg), а если её там нет — прямо в корне репозитория, рядом
    с самим Night_Faceit.py (dust2.jpg). Если нигде не нашли — вызывающий код
    рисует плейсхолдер, карточка не ломается."""
    slug = re.sub(r"[^a-z0-9]+", "", map_name.lower())
    script_dir = _os.path.dirname(_os.path.abspath(__file__))
    search_dirs = [_os.path.join(script_dir, "maps"), script_dir]
    exts = (".jpg", ".jpeg", ".png", ".webp", ".JPG", ".JPEG", ".PNG", ".WEBP")

    for base in search_dirs:
        for ext in exts:
            p = _os.path.join(base, slug + ext)
            if _os.path.exists(p):
                return p

    # Ничего не нашли — печатаем, что именно искали, чтобы было видно в логах
    # (Railway/консоль), почему на карточке плейсхолдер вместо фото карты.
    print(f"⚠️ Картинка карты не найдена: искал '{slug}.(jpg|jpeg|png|webp)' в {search_dirs}")
    try:
        print(f"   Содержимое папки со скриптом ({script_dir}): {_os.listdir(script_dir)}")
    except Exception as e:
        print(f"   Не удалось прочитать папку со скриптом: {e!r}")
    return None


def _card_cover_crop(im: Image.Image, w: int, h: int) -> Image.Image:
    """Ресайз+кроп картинки под размер w×h с сохранением пропорций (аналог CSS background-size: cover)."""
    sw, sh = im.size
    scale = max(w / sw, h / sh)
    nw, nh = int(math.ceil(sw * scale)), int(math.ceil(sh * scale))
    im = im.resize((nw, nh), Image.LANCZOS)
    x0 = (nw - w) // 2
    y0 = (nh - h) // 2
    return im.crop((x0, y0, x0 + w, y0 + h))


def _card_draw_avatar(img: Image.Image, d: ImageDraw.ImageDraw, cx: float, cy: float, r: float,
                       avatar_path: Optional[str], border=(210, 190, 255)) -> None:
    """Рисует круглую аватарку игрока (реальное фото профиля из Telegram) в
    шапке карточки. Если путь не передан или картинку не удалось открыть —
    рисует прежний плейсхолдер (иконку-лого), карточка не ломается."""
    size = int(r * 2)
    photo = None
    if avatar_path:
        try:
            photo = _card_cover_crop(Image.open(avatar_path).convert("RGB"), size, size).convert("RGBA")
        except Exception as e:
            print(f"⚠️ Не удалось открыть аватар {avatar_path}: {e!r}")
            photo = None
    if photo is None:
        _card_draw_logo_icon(d, cx, cy, size, bg=(18, 14, 30), border=border)
        return
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
    x0, y0 = int(cx - r), int(cy - r)
    img.paste(photo.convert("RGB"), (x0, y0), mask)
    d.ellipse([x0, y0, x0 + size, y0 + size], outline=border, width=2)


def _card_map_panel(img: Image.Image, d: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float,
                     map_name: str):
    """Рисует панель карты в блоке «Статистика по карте»: реальное фото карты,
    если оно положено в maps/<слаг>.jpg|png рядом со скриптом, иначе —
    аккуратный процедурный плейсхолдер (градиент + водяной знак с инициалами
    карты), плюс подпись названия карты внизу панели."""
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    w, h = x1 - x0, y1 - y0
    radius = 14

    path = _card_find_map_image(map_name)
    photo = None
    if path:
        try:
            photo = _card_cover_crop(Image.open(path).convert("RGB"), w, h).convert("RGBA")
        except Exception as e:
            print(f"⚠️ Нашёл файл карты {path}, но не смог его открыть/обработать: {e!r}")
            photo = None
    if photo is None:
        photo = _make_gradient(w, h, (26, 19, 42), (54, 30, 66)).convert("RGBA")
        wm = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        wd = ImageDraw.Draw(wm)
        initials = "".join(word[0] for word in map_name.split()[:2]).upper() or "?"
        f_wm = _card_font(int(h * 0.66))
        tb = wd.textbbox((0, 0), initials, font=f_wm)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        wd.text((w / 2 - tw / 2 - tb[0], h / 2 - th / 2 - tb[1]), initials, font=f_wm, fill=(255, 255, 255, 24))
        photo = Image.alpha_composite(photo, wm)

    # Затемнение снизу — чтобы название карты читалось поверх любой картинки
    scrim = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    scrim_h = min(64, h)
    for i in range(scrim_h):
        a = int(175 * (i / scrim_h))
        y = h - scrim_h + i
        sd.line([(0, y), (w, y)], fill=(4, 3, 8, a))
    photo = Image.alpha_composite(photo, scrim)

    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    img.paste(photo.convert("RGB"), (x0, y0), mask)
    d.rounded_rectangle([x0, y0, x1 - 1, y1 - 1], radius=radius, outline=_CARD_ROW_BORDER, width=1)

    # Только название карты внизу панели — без отдельной иконки с буквами.
    name_x = x0 + 14
    f_name = _card_font(21)
    d.text((name_x, y1 - 14 - 22), map_name.upper(), font=f_name, fill=_CARD_WHITE)


def compute_player_rank(target: int) -> Optional[tuple]:
    """Возвращает (место, всего в рейтинге) для калиброванного игрока по реальному
    ELO-рейтингу, либо None, если игрок ещё не прошёл калибровку / не в рейтинге."""
    ranked = build_top_players(limit=1_000_000)
    for i, p in enumerate(ranked, start=1):
        if p.user_id == target:
            return i, len(ranked)
    return None


def _elo_bounds(elo: int) -> tuple:
    """Возвращает (уровень, нижняя_граница, верхняя_граница) для текущего ELO."""
    bounds = [
        (0, 500), (501, 750), (751, 900), (901, 1050), (1051, 1200),
        (1201, 1350), (1351, 1530), (1531, 1750), (1751, 2000), (2001, 2001),
    ]
    for i, (lo, hi) in enumerate(bounds):
        if lo <= elo <= hi or (i == 9 and elo >= lo):
            return i + 1, lo, (hi if i < 9 else elo)
    return 1, 0, 500


def render_stats_card(target: int, out_path: str, group_name: str = "NightFaceit",
                       avatar_path: Optional[str] = None) -> Optional[str]:
    """Рисует PNG-карточку личного профиля игрока (двухколоночная вёрстка) с
    актуальными данными из БД. Возвращает out_path, либо None если игрок не
    зарегистрирован / бот. Никаких выдуманных метрик (Rating/Impact/MVP/HS%/KPR
    и т.п.) — только то, что бот реально считает."""
    db = load_db()
    s = str(target)
    if s not in db["players"] or not db["players"][s].get("external_id"):
        return None

    d_raw = db["players"][s]
    for field, val in [("wins", 0), ("losses", 0), ("avg", 0.0), ("elo", 0),
                        ("elo_5v5", 0), ("elo_2v2", 0),
                        ("wins_5v5", 0), ("losses_5v5", 0),
                        ("wins_2v2", 0), ("losses_2v2", 0),
                        ("avg_5v5", 0.0), ("avg_2v2", 0.0),
                        ("external_id", ""), ("is_bot", False),
                        ("nickname", "?"), ("user_id", target),
                        ("total_kills", 0), ("total_deaths", 0),
                        ("platform", "pc"), ("registered_ts", 0.0)]:
        d_raw.setdefault(field, val)
    d_raw["elo"] = max(_safe_num(d_raw, "elo", 0), _safe_num(d_raw, "elo_5v5", 0), _safe_num(d_raw, "elo_2v2", 0))

    try:
        p = _make_player(d_raw)
    except Exception:
        return None
    if p.is_bot:
        return None

    # Реальные бейджи роли/статуса игрока (владелец/админ/модератор + блогер)
    role_badge = None
    if is_creator(target):
        role_badge = ("ВЛАДЕЛЕЦ", (255, 200, 90))
    elif is_admin(target):
        role_badge = ("АДМИН", (230, 110, 110))
    elif is_moderator(target):
        role_badge = ("МОДЕРАТОР", (110, 180, 230))
    blogger_badge = ("БЛОГЕР", (110, 210, 200)) if is_youtuber(target) else None
    badges = [b for b in (role_badge, blogger_badge) if b]

    MARGIN  = 24
    GAP     = 20
    W       = 1200
    LEFT_W  = 740
    RIGHT_X = MARGIN + LEFT_W + GAP
    RIGHT_W = W - RIGHT_X - MARGIN
    HEADER_H = 214 if badges else 158
    BG = _CARD_BG

    total_games = p.total_games
    kd = (p.total_kills / p.total_deaths) if p.total_deaths else float(p.total_kills)

    f_title    = _card_font(34)
    f_rank_tag = _card_font(19)
    f_id       = _card_font(19, bold=False)
    f_logo     = _card_font(22)
    f_badge    = _card_font(16)
    f_sect     = _card_font(24)
    f_box_lb   = _card_font(18, bold=False)
    f_box_val  = _card_font(29)
    f_donut    = _card_font(30)
    f_donut_lb = _card_font(20)
    f_small    = _card_font(16, bold=False)
    f_tiny     = _card_font(14, bold=False)
    f_hex      = _card_font(16)
    f_stat_lb  = _card_font(14, bold=False)
    f_stat_val = _card_font(21)
    f_row_name = _card_font(18)
    f_row_sub  = _card_font(13, bold=False)

    recent    = compute_recent_matches(target, limit=5)
    rank_info = compute_player_rank(target)              # (место, всего) либо None
    top3      = build_top_players(limit=3)
    map_name  = MAPS_LIST[0] if MAPS_LIST else "Dust 2"
    map_stats = compute_player_map_stats(target).get(map_name)
    has_map_data = bool(map_stats) and (map_stats["wins"] + map_stats["losses"]) > 0

    # Достаточно большой холст "с запасом" — реальную высоту обрежем в конце.
    CANVAS_H = 1700
    img = Image.new("RGB", (W, CANVAS_H), BG)
    d = ImageDraw.Draw(img)

    # ── ШАПКА (градиентный баннер) ──────────────────────────────────────
    header_box = [MARGIN, 24, W - MARGIN, HEADER_H]
    grad_w, grad_h = header_box[2] - header_box[0], header_box[3] - header_box[1]
    gradient = _make_gradient(grad_w, grad_h, (10, 8, 16), (40, 22, 52))
    mask = Image.new("L", (grad_w, grad_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, grad_w - 1, grad_h - 1], radius=18, fill=255)
    img.paste(gradient, (header_box[0], header_box[1]), mask)
    d.rounded_rectangle(header_box, radius=18, outline=(150, 90, 160), width=1)

    avatar_cx, avatar_cy, avatar_r = 90, 24 + (HEADER_H - 24) / 2, 42
    _card_draw_avatar(img, d, avatar_cx, avatar_cy, avatar_r, avatar_path, border=(210, 190, 255))
    dot_r = 9
    dot_cx, dot_cy = avatar_cx + avatar_r * 0.72, avatar_cy + avatar_r * 0.72
    d.ellipse([dot_cx - dot_r - 3, dot_cy - dot_r - 3, dot_cx + dot_r + 3, dot_cy + dot_r + 3], fill=(18, 14, 30))
    d.ellipse([dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r], fill=(90, 220, 140))

    name_x = 150
    cursor_y = 24 + 24
    if rank_info:
        d.text((name_x, cursor_y), f"#{rank_info[0]}", font=f_rank_tag, fill=_CARD_PURPLE_LIT)
        cursor_y += 26
    display_name = p.nickname if len(p.nickname) <= 20 else p.nickname[:19] + "…"
    d.text((name_x, cursor_y), display_name, font=f_title, fill=_CARD_WHITE)
    cursor_y += 46
    d.text((name_x, cursor_y), f"ID: {p.external_id or '—'}", font=f_id, fill=(200, 190, 215))
    cursor_y += 32

    # Бейджи роли/блогера — в один ряд, сколько есть (0/1/2)
    if badges:
        bx = name_x
        for btxt, bcol in badges:
            btb = d.textbbox((0, 0), btxt, font=f_badge)
            bw, bh = btb[2] - btb[0] + 26, btb[3] - btb[1] + 14
            d.rounded_rectangle([bx, cursor_y, bx + bw, cursor_y + bh], radius=bh / 2, outline=bcol, width=2)
            d.text((bx + 13, cursor_y + 7 - btb[1]), btxt, font=f_badge, fill=bcol)
            bx += bw + 10

    pill_w = 230
    glow_cx, glow_cy = W - MARGIN - pill_w + 32, 40 + 26
    glow_r = 60
    glow = Image.new("RGBA", (W, CANVAS_H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        [glow_cx - glow_r, glow_cy - glow_r, glow_cx + glow_r, glow_cy + glow_r],
        fill=(160, 110, 255, 110))
    glow = glow.filter(ImageFilter.GaussianBlur(22))
    img.paste(glow.convert("RGB"), (0, 0), glow)

    d.rounded_rectangle([W - MARGIN - pill_w, 40, W - MARGIN, 40 + 52], radius=14,
                         fill=(18, 14, 26), outline=(150, 90, 160), width=1)
    _card_draw_logo_icon(d, W - MARGIN - pill_w + 32, 40 + 26, 30)
    d.text((W - MARGIN - pill_w + 58, 40 + 14), group_name, font=f_logo, fill=_CARD_WHITE)

    y0 = HEADER_H + 24

    # ════════════════════════ ЛЕВАЯ КОЛОНКА ════════════════════════════
    yl = y0
    _card_section_title(d, MARGIN, yl, "Статистика", f_sect)
    yl += 44

    row_w  = (LEFT_W - GAP) / 2
    row1_h = 176
    for bx0 in (MARGIN, MARGIN + row_w + GAP):
        d.rounded_rectangle([bx0, yl, bx0 + row_w, yl + row1_h], radius=16,
                             fill=_CARD_ROW_BG, outline=_CARD_ROW_BORDER, width=1)

    # -- K/D (донат + подпись СПРАВА от кольца, а не под ним) --
    donut_r = 54
    donut_cx = MARGIN + 24 + donut_r
    donut_cy = yl + row1_h / 2
    if total_games == 0:
        _card_draw_donut(d, donut_cx, donut_cy, donut_r, 0.0, "?", [],
                          f_donut, f_small, ring_color=(70, 60, 90))
        kd_val_txt = "K = 0   D = 0"
    else:
        kd_fill = (p.total_kills / (p.total_kills + p.total_deaths)) if (p.total_kills + p.total_deaths) else 0.0
        _card_draw_donut(d, donut_cx, donut_cy, donut_r, kd_fill, f"{kd:.2f}", [],
                          f_donut, f_small, ring_color=(124, 92, 255))
        kd_val_txt = f"K = {p.total_kills:,}   D = {p.total_deaths:,}"
    side_x = donut_cx + donut_r + 22
    d.text((side_x, donut_cy - 22), "K/D", font=f_donut_lb, fill=_CARD_WHITE)
    d.text((side_x, donut_cy + 6), kd_val_txt, font=f_small, fill=_CARD_GRAY)

    # -- Уровень / Калибровка аккаунта --
    box2_x = MARGIN + row_w + GAP
    if p.is_calibrated:
        level, lo, hi = _elo_bounds(p.elo)
        lvl_color = _card_level_color(level)
        _card_draw_hex(d, box2_x + row_w - 40, yl + 30, 22, level, lvl_color, f_hex)
        d.text((box2_x + 20, yl + 20), "Уровень", font=f_box_lb, fill=_CARD_GRAY)
        bar_w = row_w - 40
        bar_x = box2_x + 20
        bar_y = yl + 96
        d.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + 12], radius=6, fill=(40, 38, 56))
        span = max(1, hi - lo)
        pct = max(0.0, min(1.0, (p.elo - lo) / span))
        fill_w = bar_w * pct
        if fill_w > 0:
            d.rounded_rectangle([bar_x, bar_y, bar_x + fill_w, bar_y + 12], radius=6, fill=lvl_color)
        d.text((bar_x, bar_y + 20), str(lo), font=f_tiny, fill=_CARD_GRAY)
        cur_txt = f"{p.elo:,}"
        _card_center_text(d, box2_x + row_w / 2, yl + 62, cur_txt, f_box_val, _CARD_WHITE)
        _card_right_text(d, bar_x + bar_w, bar_y + 20, str(hi), f_tiny, _CARD_GRAY)
    else:
        _card_draw_hex(d, box2_x + row_w - 40, yl + 30, 22, "?", (255, 184, 0), f_hex)
        d.text((box2_x + 20, yl + 18), "Калибровка аккаунта", font=f_box_lb, fill=_CARD_WHITE)
        desc = f"Сыграйте {CALIBRATION_GAMES} матчей, чтобы мы могли определить ваш уровень."
        lines = _card_wrap_text(d, desc, f_tiny, row_w - 40)
        ty = yl + 48
        for line in lines[:3]:
            d.text((box2_x + 20, ty), line, font=f_tiny, fill=_CARD_GRAY)
            ty += 19
        d.text((box2_x + 20, yl + row1_h - 34), f"Сыграно {total_games}/{CALIBRATION_GAMES} матчей",
               font=f_box_lb, fill=_CARD_WHITE)

    yl += row1_h + 16

    # -- 5v5 / 2v2 (реальная разбивка по режимам) --
    row2_h = 96
    for bx0 in (MARGIN, MARGIN + row_w + GAP):
        d.rounded_rectangle([bx0, yl, bx0 + row_w, yl + row2_h], radius=16,
                             fill=_CARD_ROW_BG, outline=_CARD_ROW_BORDER, width=1)
    wl_avail_w = row_w - 40
    d.text((MARGIN + 20, yl + 16), "5v5", font=f_box_lb, fill=_CARD_GRAY)
    wl5_txt = f"{p.wins_5v5}W / {p.losses_5v5}L"
    d.text((MARGIN + 20, yl + 44), wl5_txt, font=_card_fit_font(d, wl5_txt, wl_avail_w, 29), fill=_CARD_WHITE)
    bx2 = MARGIN + row_w + GAP
    d.text((bx2 + 20, yl + 16), "2v2", font=f_box_lb, fill=_CARD_GRAY)
    wl2_txt = f"{p.wins_2v2}W / {p.losses_2v2}L"
    d.text((bx2 + 20, yl + 44), wl2_txt, font=_card_fit_font(d, wl2_txt, wl_avail_w, 29), fill=_CARD_WHITE)

    yl += row2_h + 34

    # -- Статистика по карте (единый блок: фото карты + донат винрейта + 2 строки реальных чисел) --
    _card_section_title(d, MARGIN, yl, "Статистика по карте", f_sect)
    yl += 44

    row3_h = 270
    box3 = [MARGIN, yl, MARGIN + LEFT_W, yl + row3_h]
    d.rounded_rectangle(box3, radius=16, fill=_CARD_ROW_BG, outline=_CARD_ROW_BORDER, width=1)

    pad = 16
    panel_w = 232
    panel_x0, panel_y0 = MARGIN + pad, yl + pad
    panel_x1, panel_y1 = panel_x0 + panel_w, yl + row3_h - pad
    _card_map_panel(img, d, panel_x0, panel_y0, panel_x1, panel_y1, map_name)

    cont_x0 = panel_x1 + 20
    cont_x1 = MARGIN + LEFT_W - pad
    cont_w  = cont_x1 - cont_x0
    col_w   = cont_w / 3

    # Фиксированная сетка отступов — не зависит от ветки has_map_data,
    # поэтому обе ветки гарантированно укладываются в row3_h.
    donut_cy   = yl + 60
    div1_y     = yl + 112
    row_a_lb_y = yl + 130
    div2_y     = yl + 190
    row_b_lb_y = yl + 208

    if has_map_data:
        mw, ml = map_stats["wins"], map_stats["losses"]
        mwr = mw / (mw + ml) * 100 if (mw + ml) else 0.0
        mkd = (map_stats["kills"] / map_stats["deaths"]) if map_stats["deaths"] else float(map_stats["kills"])
        wr_cx = cont_x0 + 44
        _card_draw_donut(d, wr_cx, donut_cy, 40, mwr / 100, f"{mwr:.0f}%", [], f_donut, f_small, ring_color=(90, 220, 140))
        d.text((wr_cx + 40 + 20, donut_cy - 22), "Победы на карте", font=f_donut_lb, fill=_CARD_WHITE)
        d.text((wr_cx + 40 + 20, donut_cy + 6), f"W = {mw}   L = {ml}", font=f_small, fill=_CARD_GRAY)
        kd_txt, kills_txt, deaths_txt = f"{mkd:.2f}", f"{map_stats['kills']:,}", f"{map_stats['deaths']:,}"
        wins_txt, losses_txt, matches_txt = str(mw), str(ml), str(mw + ml)
        wins_col, losses_col = (110, 220, 150), (230, 110, 110)
    else:
        _card_draw_donut(d, cont_x0 + 44, donut_cy, 40, 0.0, "?", [], f_donut, f_small, ring_color=(70, 60, 90))
        msg = ("Статистика появится\nпосле калибровки" if not p.is_calibrated else "Нет матчей\nна этой карте")
        my = donut_cy - 20
        for line in msg.split("\n"):
            d.text((cont_x0 + 44 + 40 + 20, my), line, font=f_donut_lb, fill=_CARD_GRAY)
            my += 24
        kd_txt = kills_txt = deaths_txt = wins_txt = losses_txt = matches_txt = "—"
        wins_col = losses_col = _CARD_WHITE

    _card_divider(d, cont_x0, cont_x1, div1_y)
    _card_stat_col(d, cont_x0,             col_w, row_a_lb_y, "K/D",      kd_txt,     f_stat_lb, f_stat_val)
    _card_stat_col(d, cont_x0 + col_w,     col_w, row_a_lb_y, "Убийства", kills_txt,  f_stat_lb, f_stat_val)
    _card_stat_col(d, cont_x0 + 2 * col_w, col_w, row_a_lb_y, "Смерти",   deaths_txt, f_stat_lb, f_stat_val)

    _card_divider(d, cont_x0, cont_x1, div2_y)
    _card_stat_col(d, cont_x0,             col_w, row_b_lb_y, "Побед",     wins_txt,    f_stat_lb, f_stat_val, wins_col)
    _card_stat_col(d, cont_x0 + col_w,     col_w, row_b_lb_y, "Поражений", losses_txt,  f_stat_lb, f_stat_val, losses_col)
    _card_stat_col(d, cont_x0 + 2 * col_w, col_w, row_b_lb_y, "Матчей",    matches_txt, f_stat_lb, f_stat_val)

    yl += row3_h

    # ════════════════════════ ПРАВАЯ КОЛОНКА ════════════════════════════
    yr = y0
    info_h = 150
    d.rounded_rectangle([RIGHT_X, yr, RIGHT_X + RIGHT_W, yr + info_h], radius=16,
                         fill=_CARD_ROW_BG, outline=_CARD_ROW_BORDER, width=1)
    half_w = RIGHT_W / 2
    plat_label = "Мобильный" if p.platform == "mobile" else "ПК"

    def _tick(x, y, color=_CARD_PURPLE_LIT):
        d.rounded_rectangle([x, y + 3, x + 4, y + 15], radius=2, fill=color)

    # Ширина колонки минус отступ до следующей — long "Мобильный"/число матчей
    # больше не наезжают друг на друга.
    col_avail_w = half_w - 30

    _tick(RIGHT_X + 20, yr + 20)
    d.text((RIGHT_X + 32, yr + 18), "Устройство", font=f_box_lb, fill=_CARD_GRAY)
    plat_font = _card_fit_font(d, plat_label, col_avail_w, 29)
    d.text((RIGHT_X + 20, yr + 44), plat_label, font=plat_font, fill=_CARD_WHITE)

    _tick(RIGHT_X + half_w + 10, yr + 20)
    d.text((RIGHT_X + half_w + 22, yr + 18), "Матчей", font=f_box_lb, fill=_CARD_GRAY)
    matches_txt2 = f"{total_games:,}"
    matches_font = _card_fit_font(d, matches_txt2, col_avail_w, 29)
    d.text((RIGHT_X + half_w + 10, yr + 44), matches_txt2, font=matches_font, fill=_CARD_WHITE)

    reg_label = (datetime.fromtimestamp(p.registered_ts).strftime("%d.%m.%Y")
                 if getattr(p, "registered_ts", 0) else "—")
    _tick(RIGHT_X + 20, yr + 100)
    d.text((RIGHT_X + 32, yr + 98), "Регистрация", font=f_box_lb, fill=_CARD_GRAY)
    d.text((RIGHT_X + 20, yr + 122), reg_label, font=f_box_lb, fill=_CARD_WHITE)

    rank_label = f"#{rank_info[0]} из {rank_info[1]}" if rank_info else "—"
    _tick(RIGHT_X + half_w + 10, yr + 100)
    d.text((RIGHT_X + half_w + 22, yr + 98), "Место в топе", font=f_box_lb, fill=_CARD_GRAY)
    d.text((RIGHT_X + half_w + 10, yr + 122), rank_label, font=f_box_lb, fill=_CARD_WHITE)

    yr += info_h + 30

    _card_section_title(d, RIGHT_X, yr, "Лига", f_sect)
    yr += 44

    league_rows = list(top3)
    show_own_row = bool(rank_info and rank_info[0] > 3)
    n_rows = len(league_rows) + (1 if show_own_row else 0)

    if not p.is_calibrated:
        league_h = 118
        d.rounded_rectangle([RIGHT_X, yr, RIGHT_X + RIGHT_W, yr + league_h], radius=16,
                             fill=_CARD_ROW_BG, outline=_CARD_ROW_BORDER, width=1)
        _card_draw_hex(d, RIGHT_X + 40, yr + 40, 24, "?", (90, 84, 110), f_hex)
        d.text((RIGHT_X + 76, yr + 20), "Без ранга", font=f_box_val, fill=_CARD_WHITE)
        d.text((RIGHT_X + 76, yr + 54), "Пройдите калибровку", font=f_tiny, fill=_CARD_GRAY)
        _card_divider(d, RIGHT_X + 16, RIGHT_X + RIGHT_W - 16, yr + 86)
        d.text((RIGHT_X + 20, yr + 96), "Позиция появится после калибровки", font=f_tiny, fill=_CARD_GRAY)
    else:
        header_h = 78
        rows_h = n_rows * 42 + 12
        league_h = header_h + rows_h
        d.rounded_rectangle([RIGHT_X, yr, RIGHT_X + RIGHT_W, yr + league_h], radius=16,
                             fill=_CARD_ROW_BG, outline=_CARD_ROW_BORDER, width=1)

        level, lo, hi = _elo_bounds(p.elo)
        lvl_color = _card_level_color(level)
        _card_draw_hex(d, RIGHT_X + 38, yr + 39, 24, level, lvl_color, f_hex)
        d.text((RIGHT_X + 76, yr + 16), SEASON_NAME, font=f_box_lb, fill=_CARD_GRAY)
        d.text((RIGHT_X + 76, yr + 38), "Лига", font=f_box_val, fill=_CARD_WHITE)
        d.text((RIGHT_X + RIGHT_W - 16 - 60, yr + 16), "ELO", font=f_box_lb, fill=_CARD_GRAY)
        _card_right_text(d, RIGHT_X + RIGHT_W - 16, yr + 34, f"{p.elo:,}", f_box_val, _CARD_WHITE)

        _card_divider(d, RIGHT_X + 16, RIGHT_X + RIGHT_W - 16, yr + header_h)

        ry2 = yr + header_h + 10
        for i, rp in enumerate(league_rows, start=1):
            is_me = (rp.user_id == target)
            nm = rp.nickname if len(rp.nickname) <= 15 else rp.nickname[:14] + "…"
            _card_leaderboard_row(d, RIGHT_X + 16, RIGHT_W - 32, ry2, f"#{i}", nm, f"{rp.elo:,}", is_me, f_box_lb)
            ry2 += 42
        if show_own_row:
            nm = p.nickname if len(p.nickname) <= 15 else p.nickname[:14] + "…"
            _card_leaderboard_row(d, RIGHT_X + 16, RIGHT_W - 32, ry2, f"#{rank_info[0]}", nm, f"{p.elo:,}", True, f_box_lb)

    yr += league_h + 30

    _card_section_title(d, RIGHT_X, yr, "Последние матчи", f_sect)
    yr += 44

    row_h = 50
    n_show = max(1, len(recent))
    list_h = n_show * row_h
    d.rounded_rectangle([RIGHT_X, yr, RIGHT_X + RIGHT_W, yr + list_h + 16], radius=16,
                         fill=_CARD_ROW_BG, outline=_CARD_ROW_BORDER, width=1)
    if not recent:
        d.text((RIGHT_X + 20, yr + 20), "Матчей пока нет", font=f_box_lb, fill=_CARD_GRAY)
    else:
        ry3 = yr + 8
        for row in recent:
            date_txt = datetime.fromtimestamp(row["ts"]).strftime("%d.%m") if row.get("ts") else "—"
            _card_recent_row(d, RIGHT_X + 16, RIGHT_W - 32, ry3, row_h, row, date_txt, f_row_name, f_row_sub)
            ry3 += row_h
    yr += list_h + 16 + 30

    # ── FOOTER ───────────────────────────────────────────────────────────
    final_y = max(yl, yr) + 6
    footer = "NIGHT FACEIT"
    d.line([(MARGIN, final_y), (W - MARGIN, final_y)], fill=_CARD_ROW_BORDER, width=1)
    tb = d.textbbox((0, 0), footer, font=f_logo)
    fcx = W / 2
    _card_draw_logo_icon(d, fcx - (tb[2]-tb[0]) / 2 - 26, final_y + 34, 26)
    d.text((fcx - (tb[2]-tb[0]) / 2 + 4, final_y + 34 - 12), footer, font=f_logo, fill=_CARD_WHITE)
    final_y += 60

    img = img.crop((0, 0, W, min(CANVAS_H, final_y)))
    img.save(out_path)
    return out_path


def build_top_text() -> str:
    """Текст топ-10 игроков. Переиспользуется /top (как фолбэк) и кнопкой «🏆 Топ»."""
    players = build_top_players(10)

    if not players:
        return "🏆 Рейтинг пока пуст.\n\nНи один игрок ещё не прошёл калибровку (нужно сыграть 5 матчей)."

    medals = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    lines  = ["🏆 <b>Топ-10 — Night Faceit</b>\n━━━━━━━━━━━━━━"]
    for i, p in enumerate(players):
        total = p.wins + p.losses
        wr    = f"{p.avg:.1f}%" if total else "—"
        lines.append(
            f"{medals[i]} {p.lvl_icon()} {p.tg_link()}\n"
            f"    ELO: <b>{p.elo}</b> | WR: <b>{wr}</b> | Игр: <b>{total}</b>"
        )

    return "\n".join(lines)


async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await dm_buttons_only(update): return
    if await gate(update): return

    card_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), f"_top_card_{update.effective_chat.id}.png")
    top_players = build_top_players(10)
    avatar_paths = await _fetch_avatar_paths(context.bot, [p.user_id for p in top_players])
    try:
        result = render_top_card(card_path, avatar_paths=avatar_paths)
    except Exception as e:
        print(f"⚠️ Не удалось сгенерировать карточку топа: {e!r}")
        result = None

    if not result:
        for ap in avatar_paths.values():
            try: os.remove(ap)
            except OSError: pass
        await update.message.reply_text(build_top_text(), parse_mode=ParseMode.HTML)
        return

    try:
        with open(result, "rb") as f:
            await update.message.reply_photo(photo=f)
    finally:
        try:
            os.remove(result)
        except OSError:
            pass
        for ap in avatar_paths.values():
            try: os.remove(ap)
            except OSError: pass


DM_LOBBY_WARNING = (
    "⚠️ <b>Внимание</b>\n\n"
    "Лобби можно собрать прямо здесь, в ЛС. Но играется матч всё равно "
    "на сервере, а <b>итоги (скриншот со счётом) отправляются в беседу "
    f"нашей платформы</b> ({BESEDA_USERNAME}), в раздел <b>game scrin</b>, "
    "с указанием номера матча в подписи к фото."
)


async def _open_lobby_from_dm(update: Update, mode: str):
    """Показывает предупреждение и сразу лобби при вызове /5v5 или /2v2 из ЛС."""
    uid = update.effective_user.id
    if not is_registered(uid) and uid not in ADMIN_IDS:
        await update.message.reply_text(NOT_REGISTERED_MSG, parse_mode=ParseMode.HTML)
        return
    db = load_db()
    q_list = db.get(f"queue_{mode}", [])
    await update.message.reply_text(
        DM_LOBBY_WARNING + "\n\n" + lobby_text(mode, q_list),
        reply_markup=lobby_kb(mode, uid, q_list),
        parse_mode=ParseMode.HTML
    )


async def play5_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.chat.type == "private":
        await _open_lobby_from_dm(update, "5v5")
        return
    if await gate(update, need_unmute=True): return
    uid = update.effective_user.id
    db  = load_db()
    db["lobby_5v5"] = {
        "chat_id": update.message.chat_id,
        "thread_id": update.message.message_thread_id
    }
    save_db(db)
    q   = db.get("queue_5v5", [])
    await update.message.reply_text(
        lobby_text("5v5", q),
        reply_markup=lobby_kb("5v5", uid, q),
        parse_mode=ParseMode.HTML
    )


async def play2_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.chat.type == "private":
        await _open_lobby_from_dm(update, "2v2")
        return
    if await gate(update, need_unmute=True): return
    uid = update.effective_user.id
    db  = load_db()
    db["lobby_2v2"] = {
        "chat_id": update.message.chat_id,
        "thread_id": update.message.message_thread_id
    }
    save_db(db)
    q   = db.get("queue_2v2", [])
    await update.message.reply_text(
        lobby_text("2v2", q),
        reply_markup=lobby_kb("2v2", uid, q),
        parse_mode=ParseMode.HTML
    )


CMD_DESCRIPTIONS = {
    "mute":        "🔇 <code>/mute id [30m|2h|1d]</code> — мут в чате и боте (можно ответом на сообщение). Без срока — 30 мин.",
    "unmute":      "🔊 <code>/unmute id</code> — снять мут раньше срока.",
    "ban":         "🚫 <code>/ban id [30m|2h|1d|perm]</code> — бан + кик из чата. <code>perm</code> — навсегда.",
    "unban":       "✅ <code>/unban id</code> — снять бан, игрок сможет вернуться.",
    "win":         "🏆 <code>/win номер ct|t</code> — зафиксировать победу стороны и начислить ELO (дальше построчно: <code>ID 6888 — 2 убийства — 8 смертей.</code>).",
    "dropmatch":   "🗑 <code>/dropmatch номер</code> — закрыть активный матч в 0 (катка не состоялась). ELO не меняется.",
    "cancelwin":   "↩️ <code>/cancelwin номер</code> — отменить уже засчитанный матч, вернуть ELO всем участникам.",
    "setelo":      "📊 <code>/setelo id значение</code> — выставить ELO игроку вручную.",
    "rename":      "✏️ <code>/rename Новый_Ник</code> (ответом на сообщение) — сменить ник игроку.",
    "changeid":    "🆔 <code>/changeid НовыйID</code> (ответом на сообщение) — сменить GAME ID игроку.",
    "elo":         "🔍 <code>/elo id</code> — посмотреть ELO/статистику игрока.",
    "clearqueue":  "🧹 <code>/clearqueue</code> — очистить очередь лобби 5v5/2v2.",
    "matches":     "📋 <code>/matches</code> — список активных матчей.",
    "bots1":       "🤖 <code>/bots1</code> — тестовый матч 5v5 с ботами.",
    "bots2":       "🤖 <code>/bots2</code> — тестовый матч 2v2 с ботами.",
    "unreg":       "❌ <code>/unreg id</code> — снять регистрацию игрока.",
    "listdb":      "🗄 <code>/listdb</code> — выгрузка базы данных.",
    "addmod":      "➕ <code>/addmod id</code> — назначить модератора.",
    "removemod":   "➖ <code>/removemod id</code> — снять модератора.",
    "addadm":      "➕ <code>/addadm id</code> — назначить админа.",
    "removeadm":   "➖ <code>/removeadm id</code> — снять админа.",
    "resetdb":     "♻️ <code>/resetdb</code> — полный сброс базы данных.",
    "tickets":     "🎫 <code>/tickets</code> — список открытых тикетов поддержки.",
    "reply":       "💬 <code>/reply N текст</code> — ответить игроку в тикет №N (уйдёт ему в ЛС).",
    "closeticket": "🔒 <code>/closeticket N</code> — закрыть тикет №N.",
}

MOD_CMD_KEYS     = ["mute", "unmute", "win", "dropmatch", "rename", "changeid", "tickets", "reply", "closeticket"]
ADMIN_CMD_KEYS   = MOD_CMD_KEYS + ["cancelwin", "ban", "unban", "setelo", "elo", "clearqueue", "matches", "unreg", "listdb"]
CREATOR_CMD_KEYS = ADMIN_CMD_KEYS + ["bots1", "bots2", "addmod", "removemod", "addadm", "removeadm", "resetdb"]

DURATION_NOTE = (
    "⏱ <b>Срок для /mute и /ban:</b> число + буква — <code>m</code> минуты, "
    "<code>h</code> часы, <code>d</code> дни. Пример: <code>30m</code>, <code>2h</code>, <code>1d</code>. "
    "Просто число без буквы = минуты. Для бана ещё есть <code>perm</code> — навсегда."
)


def build_admins_text(uid: int) -> str:
    """Текст со списком стаффа и команд по роли. Переиспользуется /admins и кнопкой меню."""
    db  = load_db()

    def _get_link(user_id: int) -> str:
        s = str(user_id)
        if s in db["players"] and db["players"][s].get("nickname"):
            nick = db["players"][s]["nickname"]
        else:
            nick = f"id{user_id}"
        return f'<a href="tg://user?id={user_id}">{nick}</a>'

    staff_lines = []
    staff_lines.append(f"· {_get_link(CREATOR_ID)} <i>(создатель)</i>")
    for aid in ADMIN_IDS:
        if aid != CREATOR_ID:
            staff_lines.append(f"· {_get_link(aid)} <i>(админ)</i>")
    for mid in MODERATOR_IDS:
        staff_lines.append(f"· {_get_link(mid)} <i>(модер)</i>")
    for yid in YOUTUBER_IDS:
        if yid not in ADMIN_IDS and yid not in MODERATOR_IDS:
            staff_lines.append(f"· {_get_link(yid)} <i>(ютубер)</i>")

    staff_block = "\n".join(staff_lines) if staff_lines else "—"

    if is_creator(uid):
        my_role, cmd_keys = "👑 создатель", CREATOR_CMD_KEYS
    elif is_admin(uid):
        my_role, cmd_keys = "🛡 админ", ADMIN_CMD_KEYS
    elif is_moderator(uid):
        my_role, cmd_keys = "🔰 модератор", MOD_CMD_KEYS
    else:
        my_role, cmd_keys = None, None

    text = "🌙 <b>Команда Faceit</b>\n━━━━━━━━━━━━━━\n\n" + staff_block

    # Стаффу дополнительно показываем их роль и описание каждой команды
    if is_moderator(uid) and my_role and cmd_keys:
        cmds_block = "\n".join(CMD_DESCRIPTIONS[k] for k in cmd_keys)
        text += (
            f"\n\n━━━━━━━━━━━━━━\n"
            f"<i>Твоя роль: {my_role}</i>\n\n"
            f"{cmds_block}\n\n"
            f"{DURATION_NOTE}"
        )

    return text


async def admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /admins — список доступных команд по роли + состав стаффа"""
    if await dm_buttons_only(update): return
    await update.message.reply_text(build_admins_text(update.effective_user.id), parse_mode=ParseMode.HTML)



RULES_TEXT = (
    "🌙 <b>ПРАВИЛА NIGHT FACEIT</b>\n"
    "━━━━━━━━━━━━━━\n\n"
    "<b>👤 РЕГИСТРАЦИЯ</b>\n"
    "Без регистрации — в матч не попасть.\n"
    "Команда: /reg GAME_ID Никнейм Платформа\n"
    "├ ПК: /reg 6888 Londyyy pc\n"
    "└ Моб: /reg 6888 Londyyy mobile\n"
    "⚠️ Чужой ID или неверная платформа — <b>бан</b>.\n\n"
    "━━━━━━━━━━━━━━\n\n"
    "<b>📊 ЭЛО И УРОВНИ</b>\n"
    f"Первые {CALIBRATION_GAMES} матчей — калибровка (ранг скрыт, ЭЛО не меняется).\n"
    "По итогам калибровки бот сам выдаёт стартовый ранг по результатам этих игр.\n"
    "Минимум ЭЛО после калибровки: 100\n\n"
    "💻 ПК — победа <b>+15</b> / поражение <b>−30</b>\n"
    "📱 Мобайл — победа <b>+25</b> / поражение <b>−20</b>\n\n"
    "⚪ LVL 1 → до 500\n"
    "🟢 LVL 2 → 501 – 750\n"
    "🟢 LVL 3 → 751 – 900\n"
    "🟡 LVL 4 → 901 – 1050\n"
    "🟡 LVL 5 → 1051 – 1200\n"
    "🟠 LVL 6 → 1201 – 1350\n"
    "🟠 LVL 7 → 1351 – 1530\n"
    "🔴 LVL 8 → 1531 – 1750\n"
    "🔴 LVL 9 → 1751 – 2000\n"
    "🏆 LVL 10 → 2001+\n\n"
    "━━━━━━━━━━━━━━\n\n"
    "<b>🗣️ ПОВЕДЕНИЕ В ЧАТЕ</b>\n"
    "Бот следит за оскорблениями автоматически.\n"
    "Плохое слово = сообщение удаляется мгновенно.\n\n"
    "1-е нарушение → ⚠️ Предупреждение №1/2\n"
    "└ сбрасывается через 2 часа\n"
    "2-е нарушение → 🔇 Мут на 30 минут\n"
    "В муте → сообщения удаляются тихо, никто не видит\n\n"
    "🚫 <b>Запрещено:</b>\n"
    "├ Оскорбления, мат в адрес других игроков\n"
    "├ Угрозы, травля, преследование\n"
    "├ Спам, флуд, реклама\n"
    "└ Политика и разжигание конфликтов\n\n"
    "━━━━━━━━━━━━━━\n\n"
    "<b>🎮 ПРАВИЛА МАТЧЕЙ</b>\n"
    "├ Лив из матча = наказание в виде бана\n"
    "├ Код лобби — сразу в чат после создания\n"
    "├ Результат — скрин в тему «Результаты игр» с номером матча\n"
    "└ Без скрина ЭЛО не начисляется\n\n"
    "━━━━━━━━━━━━━━\n\n"
    "<b>🚨 ЧИТЕРСТВО</b>\n"
    "Запрещено абсолютно всё:\n"
    "├ Читы, аимботы, ESP, моды с преимуществом\n"
    "├ Договорняки и намеренный слив\n"
    "├ Чужой аккаунт / поддельный Game ID\n"
    "└ Ложная платформа ради большего ЭЛО\n\n"
    "☠️ Наказание — <b>перманентный бан без апелляций.</b>\n"
    "Жалоба на читера — в личку админу с доказательствами.\n\n"
    "━━━━━━━━━━━━━━\n\n"
    "<b>👑 АДМИНИСТРАЦИЯ</b>\n"
    "👑 Создатель\n"
    "🛡 Админ\n"
    "🔰 Модератор\n\n"
    "Споры с администрацией в общем чате — запрещены.\n"
    "Вопрос или жалоба — напишите боту в ЛС команду /ticket, "
    "опишите ситуацию, и администрация ответит прямо здесь.\n\n"
    "━━━━━━━━━━━━━━\n"
    "<i>Незнание правил не освобождает от ответственности.\n"
    "Играем честно — Night Faceit 🌙</i>"
)

SEASON_TEXT = (
    f"✨ <b>{SEASON_NAME}</b>\n\n"
    "Добро пожаловать в первый тестовый сезон Night Faceit.\n"
    f"{SEASON_NAME} — это запуск обновлённой соревновательной системы, "
    "механики ELO и лобби 5v5/2v2.\n\n"
    "Каждый матч влияет на твою позицию в топе.\n\n"
    f"✨ <i>Сезон заканчивается {SEASON_END}</i>"
)


async def rules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /rules — правила чата"""
    if await dm_buttons_only(update): return
    await update.message.reply_text(RULES_TEXT, parse_mode=ParseMode.HTML)


async def addmod_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Только создатель — добавить модератора"""
    if not is_creator(update.effective_user.id): return
    target = get_reply_target(update, context.args)
    if target is None:
        await update.message.reply_text("Формат: /addmod <user_id> или ответь на сообщение"); return
    if target not in MODERATOR_IDS:
        MODERATOR_IDS.append(target)
    await update.message.reply_text(f"✅ Пользователь <code>{target}</code> добавлен в модераторы.", parse_mode=ParseMode.HTML)


async def removemod_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Только создатель — убрать модератора"""
    if not is_creator(update.effective_user.id): return
    target = get_reply_target(update, context.args)
    if target is None:
        await update.message.reply_text("Формат: /removemod <user_id> или ответь на сообщение"); return
    if target in MODERATOR_IDS:
        MODERATOR_IDS.remove(target)
    await update.message.reply_text(f"✅ Пользователь <code>{target}</code> удалён из модераторов.", parse_mode=ParseMode.HTML)




async def resetdb_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Только создатель — полный сброс БД"""
    if not is_creator(update.effective_user.id):
        return
    global _db_cache
    empty = {
        "players": {}, "match_counter": 0, "active_matches": {},
        "queue_5v5": [], "queue_2v2": [], "lobby_5v5": {}, "lobby_2v2": {},
        "muted": {}, "banned": {}, "bot_counter": 0, "warns": {},
        "tickets": {}, "ticket_counter": 0, "user_open_ticket": {},
        "pending_ocr": {}, "dm_result_wait": {}, "unresolved_results": {},
    }
    _db_cache = empty
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        import json as _json
        _json.dump(empty, f, indent=4, ensure_ascii=False)
    # Сразу синхронизируем чистую БД в Telegram
    await _sync_db_to_telegram()
    await update.message.reply_text(
        "✅ <b>База данных полностью очищена.</b>\n"
        "Все игроки, матчи, ЭЛО, муты и баны удалены.",
        parse_mode=ParseMode.HTML
    )

async def newseason_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Только владелец — начинает новый сезон: обнуляет ВСЮ игровую статистику
    (победы/поражения по режимам, ЭЛО, средний винрейт, киллы/смерти) у всех
    зарегистрированных игроков, но НЕ удаляет самих игроков — их ID, ник,
    платформа и факт регистрации сохраняются, заново регистрироваться не
    нужно. Все игроки заново проходят калибровку.
    Для защиты от случайного запуска требует подтверждения: /newseason confirm"""
    if not is_creator(update.effective_user.id):
        return

    if not context.args or context.args[0].lower() != "confirm":
        await update.message.reply_text(
            "⚠️ <b>Это обнулит статистику ВСЕХ игроков</b> (победы, поражения, ЭЛО, K/D) "
            "и начнёт новый сезон. Регистрация игроков сохранится.\n\n"
            "Чтобы подтвердить, отправьте:\n<code>/newseason confirm</code>",
            parse_mode=ParseMode.HTML
        )
        return

    db = load_db()
    players = db.get("players", {})
    reset_fields = {
        "wins": 0, "losses": 0,
        "wins_5v5": 0, "losses_5v5": 0,
        "wins_2v2": 0, "losses_2v2": 0,
        "avg": 0.0, "avg_5v5": 0.0, "avg_2v2": 0.0,
        "elo": 0, "elo_5v5": 0, "elo_2v2": 0,
        "total_kills": 0, "total_deaths": 0,
    }
    reset_count = 0
    for s, pdata in players.items():
        if pdata.get("is_bot"):
            continue
        for field, val in reset_fields.items():
            pdata[field] = val
        reset_count += 1

    save_db(db)
    await _sync_db_to_telegram()
    await update.message.reply_text(
        f"🆕 <b>Новый сезон начат!</b>\n\n"
        f"Статистика обнулена у <b>{reset_count}</b> игроков: победы, поражения, "
        f"ЭЛО и K/D сброшены в 0.\n"
        f"Регистрация, никнеймы, ID и платформы сохранены — заново регистрироваться "
        f"НЕ нужно.\n"
        f"Все игроки снова проходят калибровку ({CALIBRATION_GAMES} матчей), "
        f"прежде чем получат новый ранг.",
        parse_mode=ParseMode.HTML
    )


async def addadm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Только создатель — добавить админа"""
    if not is_creator(update.effective_user.id): return
    target = get_reply_target(update, context.args)
    if target is None:
        await update.message.reply_text("Формат: /addadm <user_id> или ответь на сообщение"); return
    if target not in ADMIN_IDS:
        ADMIN_IDS.append(target)
    await update.message.reply_text(f"✅ Пользователь <code>{target}</code> добавлен в админы.", parse_mode=ParseMode.HTML)


async def removeadm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Только создатель — убрать админа"""
    if not is_creator(update.effective_user.id): return
    target = get_reply_target(update, context.args)
    if target is None:
        await update.message.reply_text("Формат: /removeadm <user_id> или ответь на сообщение"); return
    if target == CREATOR_ID:
        await update.message.reply_text("❌ Нельзя убрать создателя."); return
    if target in ADMIN_IDS:
        ADMIN_IDS.remove(target)
    await update.message.reply_text(f"✅ Пользователь <code>{target}</code> удалён из админов.", parse_mode=ParseMode.HTML)


# ════════════════════════════════════════════════
#                  СИСТЕМА ТИКЕТОВ
# ════════════════════════════════════════════════
#
# Вариант "тикеты через ЛС с ботом":
#   • Игрок пишет боту в личку /ticket — создаётся номер тикета.
#   • Дальше ЛЮБОЕ сообщение игрока боту в ЛС (текст или фото) автоматически
#     транслируется в тему "Тикеты" админ-конфы с префиксом [Тикет #N].
#   • Модератор/админ/создатель отвечает командой /reply N текст — ответ
#     уходит игроку в ЛС от бота.
#   • Закрыть тикет: игрок — /closeticket в ЛС, стафф — /closeticket N.
#   • Доступ к работе с тикетами (/reply, /closeticket N, /tickets) —
#     у ВСЕХ ролей стаффа: модератор, админ, создатель.


async def _send_to_tickets_topic(context: ContextTypes.DEFAULT_TYPE, text: str) -> Optional[str]:
    """Отправляет сообщение в тему «Тикеты» админ-конфы (или в саму конфу,
    если тема не настроена). Возвращает None при успехе или текст ошибки."""
    if not ADMIN_GROUP_ID:
        return "ADMIN_GROUP_ID не задан"
    try:
        kwargs: Dict[str, Any] = {"chat_id": ADMIN_GROUP_ID, "text": text, "parse_mode": ParseMode.HTML}
        if TICKETS_THREAD_ID:
            kwargs["message_thread_id"] = TICKETS_THREAD_ID
        await context.bot.send_message(**kwargs)
        return None
    except Exception as e:
        return str(e)


async def ticket_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /ticket — открыть тикет в поддержку администрации. Работает только в
    личных сообщениях с ботом. После открытия игрок просто пишет сообщения
    боту в ЛС — каждое транслируется в тему "Тикеты" админ-конфы.
    """
    msg = update.message
    if not msg or msg.chat.type != "private":
        return
    uid = update.effective_user.id
    if check_banned(uid):
        await msg.reply_text("🚫 Вы забанены и не можете создавать тикеты.")
        return

    db = load_db()
    existing = db.get("user_open_ticket", {}).get(str(uid))
    if existing and db.get("tickets", {}).get(existing, {}).get("status") == "open":
        await msg.reply_text(
            f"🎫 У вас уже открыт тикет <b>#{existing}</b>.\n"
            f"Просто напишите сообщение сюда — оно уйдёт администрации.\n"
            f"Закрыть тикет: /closeticket",
            parse_mode=ParseMode.HTML,
        )
        return

    p    = get_player(uid, update.effective_user.first_name or "Игрок")
    nick = p.nickname if is_registered(uid) else (update.effective_user.first_name or "Игрок")

    db["ticket_counter"] = db.get("ticket_counter", 0) + 1
    tid = str(db["ticket_counter"])
    db.setdefault("tickets", {})[tid] = {
        "user_id":    uid,
        "nickname":   nick,
        "status":     "open",
        "created_ts": datetime.now().timestamp(),
    }
    db.setdefault("user_open_ticket", {})[str(uid)] = tid
    save_db(db)

    await msg.reply_text(
        f"🎫 <b>Тикет #{tid} открыт.</b>\n\n"
        f"Опишите проблему — каждое следующее сообщение (текст или фото) "
        f"будет передано администрации.\n"
        f"Закрыть тикет: /closeticket",
        parse_mode=ParseMode.HTML,
    )

    first_text = " ".join(context.args) if context.args else None
    intro = (
        f"🎫 <b>Новый тикет #{tid}</b>\n"
        f"👤 <a href=\"tg://user?id={uid}\">{nick}</a> (<code>{uid}</code>)"
    )
    if first_text:
        intro += f"\n\n💬 {first_text}"
    err = await _send_to_tickets_topic(context, intro)
    if err:
        print(f"[ticket] не удалось уведомить админ-конфу: {err}")


async def reg_dm_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Ловит текстовое сообщение в ЛС, когда пользователь находится в процессе
    кнопочной регистрации (после выбора платформы). Ожидаемый формат:
    "GAME_ID Никнейм". Если пользователь не в процессе регистрации — не
    трогает сообщение, чтобы его мог обработать следующий обработчик (тикеты).
    """
    msg = update.message
    if not msg or not msg.text:
        return
    uid = update.effective_user.id
    platform = context.user_data.get("reg_platform")
    if not platform:
        return  # не в процессе регистрации — пропускаем дальше

    if is_registered(uid):
        context.user_data.pop("reg_platform", None)
        return

    parts = msg.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        await msg.reply_text(
            "🚫 Отправь ID и никнейм одним сообщением, через пробел:\n"
            "<code>6888 Londyyy</code>",
            parse_mode=ParseMode.HTML
        )
        raise ApplicationHandlerStop()

    game_id, nickname = parts[0].strip(), parts[1].strip()

    if not game_id.isdigit():
        await msg.reply_text(
            "🚫 <b>GAME ID должен содержать только цифры!</b>\n\n"
            "Пример: <code>6888 Londyyy</code>",
            parse_mode=ParseMode.HTML
        )
        raise ApplicationHandlerStop()

    if len(nickname) > 32:
        await msg.reply_text("🚫 Никнейм слишком длинный (максимум 32 символа). Попробуй ещё раз.")
        raise ApplicationHandlerStop()

    db = load_db()
    for d in db["players"].values():
        if d.get("external_id") == game_id and not d.get("is_bot"):
            await msg.reply_text("🚫 Этот GAME ID уже зарегистрирован. Проверь ID и попробуй снова.")
            raise ApplicationHandlerStop()

    player_data = asdict(Player(uid, nickname, game_id))
    player_data["platform"] = platform
    player_data["registered_ts"] = time.time()
    db["players"][str(uid)] = player_data
    save_db(db)
    context.user_data.pop("reg_platform", None)

    platform_label = "📱 Мобильный" if platform == "mobile" else "🖥 ПК"
    win_d, loss_d  = elo_deltas_for(platform)

    await msg.reply_text(
        f"✅ <b>Зарегистрирован!</b>\n\n"
        f"👤 Никнейм: <b>{nickname}</b>\n"
        f"🆔 GAME ID: <code>{game_id}</code>\n"
        f"🎮 Платформа: <b>{platform_label}</b>\n"
        f"📊 ELO за победу: <b>+{win_d}</b> | за поражение: <b>-{loss_d}</b>\n\n"
        f"🔍 Найти матч можно только в беседе.\n\n"
        f"━━━━━━━━━━━━━━\n"
        f"⚠️ <b>За обман платформы вы получаете бан от администрации Faceit!</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➡️ Перейти в беседу", url=BESEDA_LINK)],
            [InlineKeyboardButton("⬅️ В главное меню", callback_data="cmd_menu")],
        ])
    )
    raise ApplicationHandlerStop()


async def ticket_dm_forward_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Транслятор переписки тикета: любое НЕ-командное сообщение (текст или
    фото) в ЛС боту от игрока с открытым тикетом пересылается в тему
    "Тикеты" админ-конфы с префиксом [Тикет #N].
    """
    msg = update.message
    if not msg:
        return
    uid = update.effective_user.id
    if is_moderator(uid):
        return  # у стаффа свои команды (/reply, /closeticket, /tickets)
    if check_banned(uid) or check_muted(uid):
        return  # уже обработано глобальными фильтрами наказаний

    db  = load_db()
    tid = db.get("user_open_ticket", {}).get(str(uid))
    ticket = db.get("tickets", {}).get(tid) if tid else None
    if not ticket or ticket.get("status") != "open":
        await msg.reply_text(
            "ℹ️ У вас нет открытого тикета. Чтобы написать администрации — /ticket"
        )
        return

    nick   = ticket.get("nickname", "Игрок")
    header = f"[Тикет #{tid}] <a href=\"tg://user?id={uid}\">{nick}</a>:"

    try:
        if msg.photo:
            caption = f"{header}\n{msg.caption or ''}".strip()
            kwargs: Dict[str, Any] = {
                "chat_id": ADMIN_GROUP_ID, "photo": msg.photo[-1].file_id,
                "caption": caption, "parse_mode": ParseMode.HTML,
            }
            if TICKETS_THREAD_ID:
                kwargs["message_thread_id"] = TICKETS_THREAD_ID
            await context.bot.send_photo(**kwargs)
        elif msg.text:
            kwargs = {
                "chat_id": ADMIN_GROUP_ID, "text": f"{header}\n{msg.text}",
                "parse_mode": ParseMode.HTML,
            }
            if TICKETS_THREAD_ID:
                kwargs["message_thread_id"] = TICKETS_THREAD_ID
            await context.bot.send_message(**kwargs)
        else:
            return
        await msg.reply_text("✅ Передано администрации.")
    except Exception as e:
        print(f"[ticket] ошибка пересылки: {e}")
        await msg.reply_text("⚠️ Не удалось передать сообщение, попробуйте позже.")


async def reply_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /reply <N> <текст> — ответить игроку в тикет №N. Доступно модератору,
    админу и создателю. Ответ уходит игроку в ЛС от имени бота.
    """
    if not is_moderator(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Формат: /reply <номер_тикета> <текст ответа>")
        return

    tid  = context.args[0]
    text = " ".join(context.args[1:])

    db     = load_db()
    ticket = db.get("tickets", {}).get(tid)
    if not ticket:
        await update.message.reply_text(f"❌ Тикет #{tid} не найден."); return
    if ticket.get("status") != "open":
        await update.message.reply_text(f"❌ Тикет #{tid} уже закрыт."); return

    target_uid = ticket["user_id"]
    try:
        await context.bot.send_message(
            chat_id=target_uid,
            text=f"💬 <b>Ответ администрации (Тикет #{tid}):</b>\n\n{text}",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Не удалось отправить игроку: {e}")
        return

    await update.message.reply_text(f"✅ Ответ отправлен в тикет #{tid}.")


async def closeticket_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /closeticket <N> — стафф (модератор/админ/создатель) закрывает любой
    тикет по номеру. /closeticket без аргумента в ЛС — игрок закрывает
    свой текущий открытый тикет.
    """
    uid = update.effective_user.id
    msg = update.message
    db  = load_db()

    if is_moderator(uid):
        if not context.args:
            await msg.reply_text("Формат: /closeticket <номер_тикета>")
            return
        tid    = context.args[0]
        ticket = db.get("tickets", {}).get(tid)
        if not ticket:
            await msg.reply_text(f"❌ Тикет #{tid} не найден."); return
        ticket["status"]    = "closed"
        ticket["closed_by"] = uid
        db.get("user_open_ticket", {}).pop(str(ticket["user_id"]), None)
        save_db(db)
        await msg.reply_text(f"🔒 Тикет #{tid} закрыт.")
        try:
            await context.bot.send_message(
                chat_id=ticket["user_id"],
                text=f"🔒 Ваш тикет #{tid} закрыт администрацией.\nЧтобы открыть новый — /ticket",
            )
        except Exception:
            pass
        return

    # Игрок закрывает свой собственный тикет — только в ЛС
    if not msg or msg.chat.type != "private":
        return
    tid    = db.get("user_open_ticket", {}).get(str(uid))
    ticket = db.get("tickets", {}).get(tid) if tid else None
    if not ticket or ticket.get("status") != "open":
        await msg.reply_text("ℹ️ У вас нет открытого тикета.")
        return
    ticket["status"]    = "closed"
    ticket["closed_by"] = uid
    db.get("user_open_ticket", {}).pop(str(uid), None)
    save_db(db)
    await msg.reply_text(f"🔒 Тикет #{tid} закрыт.")
    await _send_to_tickets_topic(context, f"🔒 Тикет #{tid} закрыт игроком.")


async def tickets_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/tickets — список открытых тикетов. Доступно модератору и выше."""
    if not is_moderator(update.effective_user.id):
        return
    db = load_db()
    open_tickets = [(tid, t) for tid, t in db.get("tickets", {}).items() if t.get("status") == "open"]
    if not open_tickets:
        await update.message.reply_text("✅ Открытых тикетов нет.")
        return
    open_tickets.sort(key=lambda x: float(x[0]))
    lines = []
    for tid, t in open_tickets:
        nick  = t.get("nickname", "?")
        uidp  = t.get("user_id")
        lines.append(f"🎫 #{tid} — <a href=\"tg://user?id={uidp}\">{nick}</a> (<code>{uidp}</code>)")
    await update.message.reply_text(
        "📋 <b>Открытые тикеты:</b>\n\n" + "\n".join(lines) +
        "\n\nОтветить: <code>/reply N текст</code>\nЗакрыть: <code>/closeticket N</code>",
        parse_mode=ParseMode.HTML,
    )


# ════════════════════════════════════════════════
#             CALLBACK — ЛОББИ / ПИК / БАН
# ════════════════════════════════════════════════


async def _menu_edit(q, text: str, kb: Optional[InlineKeyboardMarkup] = None) -> None:
    """Редактирует текущее сообщение бота вместо отправки нового —
    чтобы кнопки меню не спамили чат новыми сообщениями."""
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        try:
            await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass


async def _menu_send_photo(q, photo_path: str, kb: Optional[InlineKeyboardMarkup] = None) -> None:
    """Аналог _menu_edit, но для PNG-карточек. Telegram не даёт превратить
    текстовое сообщение в фото через edit_message_*, поэтому старое сообщение
    меню удаляется, а новая карточка отправляется отдельным сообщением."""
    chat = q.message.chat if q.message else None
    try:
        if q.message:
            await q.message.delete()
    except Exception:
        pass
    try:
        with open(photo_path, "rb") as f:
            if chat:
                await chat.send_photo(photo=f, reply_markup=kb)
            elif q.message:
                await q.message.reply_photo(photo=f, reply_markup=kb)
    except Exception as e:
        print(f"⚠️ Не удалось отправить фото-карточку меню: {e!r}")
    finally:
        try:
            os.remove(photo_path)
        except OSError:
            pass


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    uid = q.from_user.id
    cb  = q.data

    # ── ГЛОБАЛЬНАЯ ПРОВЕРКА БАНА/МУТА ──────────────────────────────────────
    # Мут и бан теперь блокируют АБСОЛЮТНО ЛЮБОЕ взаимодействие с ботом:
    # постановку в очередь, выбор игроков на пике, баны карт и т.д.
    if not is_admin(uid):
        if check_banned(uid):
            await q.answer("🚫 Вы забанены и исключены из беседы!", show_alert=True)
            return
        if check_muted(uid):
            await q.answer("🔇 Вы в муте — любые действия запрещены!", show_alert=True)
            return

    # ── ПОДТВЕРЖДЕНИЕ РАСПОЗНАННОГО РЕЗУЛЬТАТА МАТЧА (OCR) ────────────────────
    if cb.startswith("ocrwin_confirm:") or cb.startswith("ocrwin_manual:"):
        if not is_moderator(uid):
            await q.answer("❌ Недостаточно прав.", show_alert=True)
            return
        action, m_id = cb.split(":", 1)
        db = load_db()

        if action == "ocrwin_manual":
            db.get("pending_ocr", {}).pop(m_id, None)
            save_db(db)
            await q.answer()
            try:
                await q.edit_message_text(
                    (q.message.text or "") + f"\n\n✏️ Отклонено — введите результат командой /win {m_id} ...",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
            return

        # ocrwin_confirm
        pending = db.get("pending_ocr", {}).get(m_id)
        m = db.get("active_matches", {}).get(m_id)
        if not pending or not m:
            await q.answer("❌ Матч уже обработан или недоступен.", show_alert=True)
            return

        await q.answer()
        side = pending["side"]
        kd_by_uid = {int(k): tuple(v) for k, v in pending["kd_by_uid"].items()}

        win_lines, loss_lines, calib_notifications, mode, winners, losers = _finalize_match(
            db, m_id, m, side, kd_by_uid
        )
        db.get("pending_ocr", {}).pop(m_id, None)
        save_db(db)
        await _send_calibration_dms(context.bot, calib_notifications)

        win_side_label  = "🔵 CT" if side == "ct" else "🔴 T"
        lose_side_label = "🔴 T"  if side == "ct" else "🔵 CT"
        text = (
            f"🏆 <b>Матч #{m_id} [{mode.upper()}] завершён!</b> (подтверждено {q.from_user.first_name})\n\n"
            f"✅ Победила сторона: {win_side_label}\n"
            + ("\n".join(win_lines) if win_lines else "  (нет реальных игроков)") + "\n\n"
            f"❌ Проиграла сторона: {lose_side_label}\n"
            + ("\n".join(loss_lines) if loss_lines else "  (нет реальных игроков)")
        )
        try:
            await q.edit_message_text(text, parse_mode=ParseMode.HTML)
        except Exception:
            await context.bot.send_message(chat_id=q.message.chat_id, text=text, parse_mode=ParseMode.HTML)

        # ── Сразу показываем админу другие незакрытые матчи, если есть ──────
        remaining = db.get("pending_ocr", {})
        if remaining:
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=f"🧾 Есть ещё {len(remaining)} матч(ей), ожидающих подтверждения результата:",
                )
            except Exception:
                pass
            for next_id, next_pending in list(remaining.items())[:3]:
                next_m = db.get("active_matches", {}).get(next_id)
                if not next_m:
                    continue
                text2, kb2 = _build_ocr_confirm_card(next_id, next_m, next_pending)
                try:
                    await context.bot.send_message(chat_id=uid, text=text2, parse_mode=ParseMode.HTML, reply_markup=kb2)
                except Exception:
                    pass
        return

    # ── ИГРОК ЖМЁТ «ОТПРАВИТЬ РЕЗУЛЬТАТ» В ЛС ────────────────────────────────
    if cb.startswith("sendres_"):
        m_id = cb.split("_", 1)[1]
        db = load_db()
        m = db.get("active_matches", {}).get(m_id)
        if not m:
            await q.answer("❌ Матч уже закрыт.", show_alert=True)
            return
        all_players = [u for u in (m["ct"] + m["t"]) if not _is_bot_uid(u)]
        if uid not in all_players:
            await q.answer("❌ Вы не участник этого матча.", show_alert=True)
            return
        db.setdefault("dm_result_wait", {})[str(uid)] = m_id
        save_db(db)
        await q.answer()
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"📸 Пришлите скриншот результата матча #{m_id} следующим сообщением прямо сюда.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    # ── АДМИН/МОД: СПИСОК МАТЧЕЙ, ОЖИДАЮЩИХ ПОДТВЕРЖДЕНИЯ РЕЗУЛЬТАТА ─────────
    if cb == "cmd_pending_list":
        if not is_moderator(uid):
            await q.answer("❌ Недостаточно прав.", show_alert=True)
            return
        await q.answer()
        db = load_db()
        pending    = db.get("pending_ocr", {})
        unresolved = db.get("unresolved_results", {})
        if not pending and not unresolved:
            try:
                await context.bot.send_message(chat_id=uid, text="✅ Нет матчей, ожидающих подтверждения результата.")
            except Exception:
                pass
            return
        sent_any = False
        for m_id, p_data in pending.items():
            m = db.get("active_matches", {}).get(m_id)
            if not m:
                continue
            text, kb = _build_ocr_confirm_card(m_id, m, p_data)
            try:
                await context.bot.send_message(chat_id=uid, text=text, parse_mode=ParseMode.HTML, reply_markup=kb)
                sent_any = True
            except Exception:
                pass
        for m_id, u_data in unresolved.items():
            if m_id in pending:
                continue  # уже показали выше как распознанный
            m = db.get("active_matches", {}).get(m_id)
            if not m:
                continue
            reporter = get_player(u_data.get("reported_by")) if u_data.get("reported_by") else None
            reasons = u_data.get("reasons") or []
            text = (
                f"📸 <b>Матч #{m_id}</b> — прислан скрин, но бот <u>не смог распознать</u> результат.\n"
                + (f"Причина: {'; '.join(reasons)}\n" if reasons else "") +
                (f"\nОт: {reporter.tg_link()}" if reporter else "") +
                f"\n\nВведите результат вручную:\n<code>/win {m_id} ct|t ...</code>"
            )
            try:
                await context.bot.send_message(chat_id=uid, text=text, parse_mode=ParseMode.HTML)
                sent_any = True
            except Exception:
                pass
        if not sent_any:
            try:
                await context.bot.send_message(chat_id=uid, text="✅ Нет матчей, ожидающих подтверждения результата.")
            except Exception:
                pass
        return

    # ── ГЛАВНОЕ МЕНЮ (ЛС) ────────────────────────────────────────────────────
    if cb == "cmd_menu":
        await q.answer()
        db  = load_db()
        s   = str(uid)
        reg = bool(s in db["players"] and db["players"][s].get("external_id"))
        name = q.from_user.first_name or "игрок"
        text = (
            f"👋 <b>Привет, {name}!</b>\n\n"
            f"🌙 <b>Night Faceit</b> — твоя персональная лига\n\n"
            f"{'✅ Ты зарегистрирован' if reg else '❌ Ты не зарегистрирован'}\n\n"
            f"👇 Выбери действие:"
        )
        await _menu_edit(q, text, main_menu_kb(uid, reg))
        return

    # ── НАЙТИ МАТЧ (из ЛС — с предупреждением) ──────────────────────────────
    if cb == "cmd_play":
        await q.answer()
        if not is_registered(uid) and uid not in ADMIN_IDS:
            await _menu_edit(
                q,
                "❌ <b>Вы не зарегистрированы!</b>\n\n"
                "Нажмите «📝 Регистрация» в главном меню.",
                InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ В главное меню", callback_data="cmd_menu")]])
            )
            return
        await _menu_edit(
            q,
            DM_LOBBY_WARNING + "\n\nВыбери режим:",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🎮 Лобби 5v5", callback_data="dm_lobby_5v5")],
                [InlineKeyboardButton("⚡ Лобби 2v2", callback_data="dm_lobby_2v2")],
                [InlineKeyboardButton("➡️ Перейти в беседу", url=BESEDA_LINK)],
                [InlineKeyboardButton("⬅️ В главное меню", callback_data="cmd_menu")],
            ])
        )
        return

    # ── ЛОББИ ИЗ ЛС: показываем текущую очередь, кнопки join_/leave_ уже общие ──
    if cb in ("dm_lobby_5v5", "dm_lobby_2v2"):
        await q.answer()
        mode = cb.split("_")[-1]
        db   = load_db()
        q_list = db.get(f"queue_{mode}", [])
        try:
            await q.edit_message_text(
                lobby_text(mode, q_list),
                reply_markup=lobby_kb(mode, uid, q_list),
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
        return

    # ── МОЙ ПРОФИЛЬ ───────────────────────────────────────────────────────────
    if cb == "cmd_stats":
        await q.answer()
        private_chat = bool(q.message and q.message.chat.type == "private")
        text, kb = build_stats_text(uid, True, private_chat)
        rows = list(kb.inline_keyboard) if kb else []
        rows.append([InlineKeyboardButton("⬅️ В главное меню", callback_data="cmd_menu")])
        back_kb = InlineKeyboardMarkup(rows)

        card_path = _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)),
            f"_stats_card_{uid}_{q.message.chat.id if q.message else uid}.png"
        )
        avatar_path = await _fetch_avatar_path(context.bot, uid)
        result = None
        try:
            result = render_stats_card(uid, card_path, avatar_path=avatar_path)
        except Exception as e:
            print(f"⚠️ Не удалось сгенерировать карточку профиля (кнопка) uid={uid}: {e!r}")
            result = None

        if result:
            await _menu_send_photo(q, result, back_kb)
        else:
            await _menu_edit(q, text, back_kb)
        if avatar_path:
            try: os.remove(avatar_path)
            except OSError: pass
        return

    # ── ТОП ИГРОКОВ ───────────────────────────────────────────────────────────
    if cb == "cmd_top":
        await q.answer()
        back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ В главное меню", callback_data="cmd_menu")]])

        card_path = _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)),
            f"_top_card_{q.message.chat.id if q.message else uid}.png"
        )
        top_players = build_top_players(10)
        avatar_paths = await _fetch_avatar_paths(context.bot, [p.user_id for p in top_players])
        result = None
        try:
            result = render_top_card(card_path, avatar_paths=avatar_paths)
        except Exception as e:
            print(f"⚠️ Не удалось сгенерировать карточку топа (кнопка): {e!r}")
            result = None

        if result:
            await _menu_send_photo(q, result, back_kb)
        else:
            await _menu_edit(q, build_top_text(), back_kb)
        for ap in avatar_paths.values():
            try: os.remove(ap)
            except OSError: pass
        return

    # ── СЕЗОН ─────────────────────────────────────────────────────────────────
    if cb == "cmd_season":
        await q.answer()
        await _menu_edit(
            q, SEASON_TEXT,
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ В главное меню", callback_data="cmd_menu")]])
        )
        return

    # ── ПРАВИЛА ───────────────────────────────────────────────────────────────
    if cb == "cmd_rules":
        await q.answer()
        await _menu_edit(
            q, RULES_TEXT,
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ В главное меню", callback_data="cmd_menu")]])
        )
        return

    # ── КОМАНДЫ СТАФФА ────────────────────────────────────────────────────────
    if cb == "cmd_admins":
        await q.answer()
        await _menu_edit(
            q, build_admins_text(uid),
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ В главное меню", callback_data="cmd_menu")]])
        )
        return

    # ── ПОДДЕРЖКА (открыть тикет прямо из меню) ─────────────────────────────
    if cb == "cmd_support":
        await q.answer()
        if check_banned(uid):
            await _menu_edit(
                q, "🚫 Вы забанены и не можете создавать тикеты.",
                InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ В главное меню", callback_data="cmd_menu")]])
            )
            return
        db  = load_db()
        tid = db.get("user_open_ticket", {}).get(str(uid))
        ticket = db.get("tickets", {}).get(tid) if tid else None
        if ticket and ticket.get("status") == "open":
            await _menu_edit(
                q,
                f"🎫 У вас уже открыт тикет <b>#{tid}</b>.\n"
                f"Просто напишите сообщение сюда — оно уйдёт администрации.\n"
                f"Закрыть тикет: /closeticket",
                InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ В главное меню", callback_data="cmd_menu")]])
            )
            return
        p    = get_player(uid, q.from_user.first_name or "Игрок")
        nick = p.nickname if is_registered(uid) else (q.from_user.first_name or "Игрок")
        db["ticket_counter"] = db.get("ticket_counter", 0) + 1
        tid = str(db["ticket_counter"])
        db.setdefault("tickets", {})[tid] = {
            "user_id": uid, "nickname": nick, "status": "open",
            "created_ts": datetime.now().timestamp(),
        }
        db.setdefault("user_open_ticket", {})[str(uid)] = tid
        save_db(db)
        await _menu_edit(
            q,
            f"🎫 <b>Тикет #{tid} открыт.</b>\n\n"
            f"Опишите проблему — каждое следующее сообщение (текст или фото) "
            f"будет передано администрации.\n"
            f"Закрыть тикет: /closeticket",
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ В главное меню", callback_data="cmd_menu")]])
        )
        intro = (
            f"🎫 <b>Новый тикет #{tid}</b>\n"
            f"👤 <a href=\"tg://user?id={uid}\">{nick}</a> (<code>{uid}</code>)"
        )
        err = await _send_to_tickets_topic(context, intro)
        if err:
            print(f"[ticket] не удалось уведомить админ-конфу: {err}")
        return

    # ── РЕГИСТРАЦИЯ: сначала подписка на беседу ─────────────────────────────
    if cb == "cmd_reg":
        await q.answer()
        if is_registered(uid):
            await _menu_edit(
                q, "🚫 Вы уже зарегистрированы.\nДля смены данных обратитесь к администратору.",
                InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ В главное меню", callback_data="cmd_menu")]])
            )
            return
        if await is_subscribed_beseda(context.bot, uid):
            await _menu_edit(
                q, "📝 <b>Регистрация</b>\n\nВыбери свою платформу:",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("🖥 ПК", callback_data="reg_platform_pc"),
                     InlineKeyboardButton("📱 Мобильный", callback_data="reg_platform_mobile")],
                    [InlineKeyboardButton("⬅️ В главное меню", callback_data="cmd_menu")],
                ])
            )
        else:
            await _menu_edit(
                q,
                "🔒 <b>Доступ ограничен</b>\n\n"
                "Для регистрации сначала подпишись на нашу беседу — там играются матчи.",
                _sub_gate_kb()
            )
        return

    if cb == "reg_check_sub":
        if is_registered(uid):
            await q.answer("Вы уже зарегистрированы", show_alert=True)
            return
        if await is_subscribed_beseda(context.bot, uid):
            await q.answer("✅ Подписка подтверждена!")
            await _menu_edit(
                q, "📝 <b>Регистрация</b>\n\nВыбери свою платформу:",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("🖥 ПК", callback_data="reg_platform_pc"),
                     InlineKeyboardButton("📱 Мобильный", callback_data="reg_platform_mobile")],
                    [InlineKeyboardButton("⬅️ В главное меню", callback_data="cmd_menu")],
                ])
            )
        else:
            await q.answer("❌ Вы ещё не подписались на беседу!", show_alert=True)
        return

    if cb in ("reg_platform_pc", "reg_platform_mobile"):
        if is_registered(uid):
            await q.answer("Вы уже зарегистрированы", show_alert=True)
            return
        if not await is_subscribed_beseda(context.bot, uid):
            await q.answer("❌ Сначала подпишись на беседу!", show_alert=True)
            await _menu_edit(
                q, "🔒 <b>Доступ ограничен</b>\n\nСначала подпишись на беседу.",
                _sub_gate_kb()
            )
            return
        await q.answer()
        platform = "pc" if cb == "reg_platform_pc" else "mobile"
        context.user_data["reg_platform"] = platform
        platform_label = "🖥 ПК" if platform == "pc" else "📱 Мобильный"
        await _menu_edit(
            q,
            f"✅ Платформа: <b>{platform_label}</b>\n\n"
            f"Теперь отправь одним сообщением твой <b>игровой ID</b> и <b>никнейм</b>:\n"
            f"<code>6888 Londyyy</code>",
            None
        )
        return

    # ── TOP 2v2 ───────────────────────────────────────────────────────────────
    if cb == "top_2v2":
        await q.answer()
        db      = update.callback_query  # just to not shadow
        db      = load_db()
        players = []
        for d in db["players"].values():
            if not d.get("external_id") or d.get("is_bot"): continue
            for field, val in [("wins",0),("losses",0),("avg",0.0),("elo",0),
                               ("elo_5v5",0),("elo_2v2",0),
                               ("wins_5v5",0),("losses_5v5",0),
                               ("wins_2v2",0),("losses_2v2",0),
                               ("avg_5v5",0.0),("avg_2v2",0.0),
                               ("external_id",""),("is_bot",False),
                               ("total_kills",0),("total_deaths",0),
                               ("platform","pc")]:
                d.setdefault(field, val)
            try:
                players.append(_make_player(d))
            except Exception:
                continue
        if not players:
            await q.message.reply_text("🏆 Рейтинг 2v2 пока пуст.")
            return
        players.sort(key=lambda p: p.elo_2v2, reverse=True)
        medals = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
        lines  = ["⚡ <b>Топ-10 игроков — 2v2</b>\n━━━━━━━━━━━━━━"]
        for i, p in enumerate(players[:10]):
            wr = f"{p.avg_2v2:.1f}%" if (p.wins_2v2+p.losses_2v2) else "—"
            lines.append(
                f"{medals[i]} {p.lvl_icon_2v2()} {p.tg_link()} <code>[{p.external_id}]</code>\n"
                f"    ELO: <b>{p.elo_2v2}</b> | WR: <b>{wr}</b> | Игр: <b>{p.wins_2v2+p.losses_2v2}</b>"
            )
        if len(players) > 10:
            lines.append(f"\n... и ещё {len(players)-10} в рейтинге")
        await q.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return

    # ── JOIN / LEAVE ─────────────────────────────────────────────────────────
    if cb in ("join_5v5","leave_5v5","join_2v2","leave_2v2"):
        action, mode = cb.split("_", 1)

        if not is_registered(uid) and uid not in ADMIN_IDS:
            await q.answer("🚫 Сначала зарегистрируйтесь: /reg", show_alert=True)
            return

        db    = load_db()
        key   = f"queue_{mode}"
        okey  = "queue_2v2" if mode == "5v5" else "queue_5v5"
        queue = db.get(key, [])
        size  = LOBBY_5V5_SIZE if mode == "5v5" else LOBBY_2V2_SIZE

        if action == "join":
            if uid in queue:
                await q.answer(f"✅ Вы уже в очереди {mode.upper()} ({len(queue)}/{size})")
                try:
                    await q.edit_message_text(
                        lobby_text(mode, queue),
                        reply_markup=lobby_kb(mode, uid, queue),
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass
                return
            if uid in db.get(okey, []):
                await q.answer("Вы уже в другой очереди!", show_alert=True)
                return
            queue.append(uid)
            await q.answer(f"✅ Вы присоединились! {len(queue)}/{size}")
        else:
            if uid not in queue:
                # Кнопка устарела (после /clearqueue) — обновляем без ошибки
                await q.answer("Вы уже не в очереди")
                try:
                    await q.edit_message_text(
                        lobby_text(mode, queue),
                        reply_markup=lobby_kb(mode, uid, queue),
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass
                return
            queue.remove(uid)
            await q.answer(f"❌ Вы вышли из очереди {mode.upper()}")

            db[key] = queue
            save_db(db)

            # ── Выход из лобби в ЛС — сразу кидаем в главное меню ──
            if q.message and q.message.chat.type == "private":
                s    = str(uid)
                reg  = bool(s in db["players"] and db["players"][s].get("external_id"))
                name = q.from_user.first_name or "игрок"
                menu_text = (
                    f"👋 <b>Привет, {name}!</b>\n\n"
                    f"🌙 <b>Night Faceit</b> — твоя персональная лига\n\n"
                    f"{'✅ Ты зарегистрирован' if reg else '❌ Ты не зарегистрирован'}\n\n"
                    f"👇 Выбери действие:"
                )
                try:
                    await q.edit_message_text(
                        menu_text,
                        reply_markup=main_menu_kb(uid, reg),
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass
                return

            try:
                await q.edit_message_text(
                    lobby_text(mode, queue),
                    reply_markup=lobby_kb(mode, uid, queue),
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
            return

        db[key] = queue
        save_db(db)

        try:
            await q.edit_message_text(
                lobby_text(mode, queue),
                reply_markup=lobby_kb(mode, uid, queue),
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

        if len(queue) >= size:
            match_players = queue[:size]
            db[key]       = queue[size:]
            lobby_info   = db.get(f"lobby_{mode}", {})
            # Если лобби никто не открывал в беседе (например все зашли из ЛС),
            # матч всё равно стартует в беседе платформы, а не в личке —
            # там же потом принимаются скрины результатов (game scrin).
            if lobby_info.get("chat_id"):
                lobby_chat = lobby_info["chat_id"]
            elif q.message.chat.type != "private":
                lobby_chat = q.message.chat_id
            else:
                lobby_chat = BESEDA_USERNAME
            lobby_thread  = lobby_info.get("thread_id")
            save_db(db)
            try:
                await start_match(match_players, mode, db, context, lobby_chat, lobby_thread)
            except Exception as e:
                print(f"[ОШИБКА] start_match: {e}")
                try:
                    await context.bot.send_message(
                        chat_id=lobby_chat,
                        message_thread_id=lobby_thread,
                        text="⚠️ Матч начался но произошла ошибка. Обратитесь к администратору."
                    )
                except Exception:
                    pass
        return

    # Для пика и бана карт — единый answer без текста
    try:
        await q.answer()
    except Exception:
        return

    # ── PICK ─────────────────────────────────────────────────────────────────
    if cb.startswith("pk_"):
        parts = cb.split("_")
        if len(parts) != 3: return
        _, m_id, p_str = parts
        try:
            p_id = int(p_str)
        except ValueError:
            return

        db = load_db()
        m  = db["active_matches"].get(m_id)
        if not m:
            await q.answer("Матч уже завершён", show_alert=True); return

        ct_cap = m["ct"][0]
        t_cap  = m["t"][0]

        if uid not in (ct_cap, t_cap):
            await q.answer("🚫 Только капитан может выбирать игроков!", show_alert=True); return
        if uid != m["turn"]:
            await q.answer(f"Сейчас ход {get_player(m['turn']).nickname}!", show_alert=True); return
        if time.time() - m["pick_start_time"] > m["pick_timeout"]:
            try: await q.edit_message_text("⏰ Время на пик вышло! Матч отменён.")
            except Exception: pass
            db["active_matches"].pop(m_id, None)
            save_db(db); return
        if p_id not in m["pool"]:
            await q.answer("Этот игрок уже выбран!", show_alert=True); return

        (m["ct"] if uid == ct_cap else m["t"]).append(p_id)
        m["pool"].remove(p_id)

        if len(m["pool"]) == 1:
            last = m["pool"].pop(0)
            (m["ct"] if len(m["ct"]) <= len(m["t"]) else m["t"]).append(last)

        if m["pool"]:
            m["turn"]   = t_cap if uid == ct_cap else ct_cap
            elapsed     = time.time() - m["pick_start_time"]
            remaining   = max(0, int(m["pick_timeout"] - elapsed))
            txt = _pick_status_text(m_id, m, remaining)
            try:
                await q.edit_message_text(
                    txt,
                    reply_markup=InlineKeyboardMarkup(_pick_buttons(m_id, m["pool"])),
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
            save_db(db)
            if _is_bot_uid(m["turn"]):
                await _bot_auto_pick(m_id, context, m.get("chat_id", q.message.chat_id))
        else:
            # Пик игроков завершён
            task = _pick_timer_tasks.pop(m_id, None)
            if task:
                task.cancel()

            chat_id_for_banner = m.get("chat_id", q.message.chat_id)
            thread_id_for_banner = m.get("thread_id")

            if len(m["maps"]) > 1:
                # Карт больше одной — начинаем бан карт. Первым банит CT-капитан.
                m["phase"] = "ban"
                m["turn"] = ct_cap
                m["ban_start_time"] = time.time()
                try:
                    await q.edit_message_text(
                        f"✅ <b>Пик игроков завершён | Матч #{m_id}</b>",
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass
                save_db(db)
                ban_btns = [
                    [InlineKeyboardButton(f"🚫 {mn}", callback_data=f"bn_{m_id}_{mn}")]
                    for mn in m["maps"]
                ]
                ban_txt = _ban_status_text(m_id, m, m.get("ban_timeout", BAN_TIMEOUT))
                try:
                    sent = await context.bot.send_message(
                        chat_id=chat_id_for_banner, message_thread_id=thread_id_for_banner,
                        text=ban_txt,
                        reply_markup=InlineKeyboardMarkup(ban_btns),
                        parse_mode=ParseMode.HTML
                    )
                    m["ban_msg_id"] = sent.message_id
                    save_db(db)
                except Exception:
                    pass
                ban_task = asyncio.create_task(_ban_timer(m_id, context, chat_id_for_banner))
                _ban_timer_tasks[m_id] = ban_task
                if _is_bot_uid(m["turn"]):
                    await _bot_auto_ban(m_id, context, chat_id_for_banner, thread_id_for_banner)
                return

            # Карта одна — бан не нужен, сразу объявляем лобби
            host_uid  = m.get("host_uid", ct_cap)
            host_p    = get_player(host_uid)
            host_side = "🔵 CT" if host_uid == ct_cap else "🔴 T"
            final_map = m["maps"][0] if m["maps"] else "Seaside"
            m["phase"] = "done"
            try:
                await q.edit_message_text(
                    f"✅ <b>Пик завершён | Матч #{m_id}</b>",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
            save_db(db)
            await _announce_lobby_ready(
                context, chat_id_for_banner, thread_id_for_banner,
                m_id, m, host_p, host_side, final_map
            )
        return

    # ── BAN MAP ───────────────────────────────────────────────────────────────
    if cb.startswith("bn_"):
        parts = cb.split("_", 2)
        if len(parts) != 3: return
        _, m_id, map_name = parts

        db = load_db()
        m  = db["active_matches"].get(m_id)
        if not m:
            await q.answer("Матч не найден", show_alert=True); return

        ct_cap = m["ct"][0]
        t_cap  = m["t"][0]

        if uid not in (ct_cap, t_cap):
            await q.answer("🚫 Только капитан может банить карты!", show_alert=True); return
        if uid != m["turn"]:
            await q.answer(f"Сейчас ход {get_player(m['turn']).nickname}!", show_alert=True); return
        if map_name not in m.get("maps", []):
            await q.answer("Карта уже забанена", show_alert=True); return

        m["maps"].remove(map_name)
        m["banned_maps"].append(map_name)

        if len(m["maps"]) == 1:
            final_map  = m["maps"][0]
            banned_str = ", ".join(m["banned_maps"])
            host_uid  = m.get("host_uid", ct_cap)
            host_p    = get_player(host_uid)
            host_side = "🔵 CT" if host_uid == ct_cap else "🔴 T"
            # Отменяем таймер бана
            ban_task = _ban_timer_tasks.pop(m_id, None)
            if ban_task:
                ban_task.cancel()
            try:
                await q.edit_message_text(
                    f"✅ <b>Баны карт завершены | Матч #{m_id}</b>",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
            save_db(db)
            chat_id_for_banner = m.get("chat_id", q.message.chat_id)
            thread_id_for_banner = m.get("thread_id")
            await _announce_lobby_ready(
                context, chat_id_for_banner, thread_id_for_banner,
                m_id, m, host_p, host_side, final_map, banned_str
            )
            return

        m["turn"] = t_cap if uid == ct_cap else ct_cap
        m["ban_start_time"] = time.time()   # сбрасываем таймер для следующего хода
        cur_side  = "🔵 CT" if m["turn"] == ct_cap else "🔴 T"
        ban_btns  = [
            [InlineKeyboardButton(f"🚫 {mn}", callback_data=f"bn_{m_id}_{mn}")]
            for mn in m["maps"]
        ]
        txt = _ban_status_text(m_id, m, BAN_TIMEOUT)
        try:
            await q.edit_message_text(
                txt,
                reply_markup=InlineKeyboardMarkup(ban_btns),
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
        save_db(db)
        if _is_bot_uid(m["turn"]):
            await _bot_auto_ban(m_id, context, m.get("chat_id", q.message.chat_id))

# ════════════════════════════════════════════════
#              АДМИН-КОМАНДЫ
# ════════════════════════════════════════════════

# ════════════════════════════════════════════════
#         ОБРАБОТКА СКРИНОВ РЕЗУЛЬТАТОВ (РУЧНАЯ)
# ════════════════════════════════════════════════

def _apply_win_to_player(
    db: Dict[str, Any],
    target_uid: int,
    won: bool,
    mode: str,
    kd_by_uid: Dict[int, tuple],
    lines: List[str],
    elo_snapshot: Dict[str, dict],
    calib_notifications: List[tuple],
) -> None:
    """
    Начисляет/списывает ELO одному игроку, обновляет W/L, винрейт, накопительный
    KD и (если только что закончилась калибровка) выдаёт стартовый ранг.
    Вынесено в отдельную функцию, чтобы одинаково применялось что из /win
    (ручной ввод), что из авто-подтверждения результата, распознанного ботом
    со скриншота (см. parse_scoreboard_ocr + callback ocrwin_confirm).
    """
    if _is_bot_uid(target_uid):
        return
    s = str(target_uid)
    pdata = db["players"].get(s)
    if not pdata:
        return

    platform      = pdata.get("platform", "pc")
    win_d, loss_d = elo_deltas_for(platform)

    elo_before      = pdata.get("elo", 0)
    elo_mode_before = pdata.get(f"elo_{mode}", 0)
    games_before     = pdata.get("wins", 0) + pdata.get("losses", 0)
    was_calibrated   = games_before >= CALIBRATION_GAMES

    if was_calibrated:
        delta = win_d if won else -loss_d
        for field, before in (("elo", elo_before), (f"elo_{mode}", elo_mode_before)):
            pdata[field] = max(ELO_MIN, before + delta)

    if won:
        pdata["wins"]            = pdata.get("wins", 0) + 1
        pdata[f"wins_{mode}"]    = pdata.get(f"wins_{mode}", 0) + 1
    else:
        pdata["losses"]          = pdata.get("losses", 0) + 1
        pdata[f"losses_{mode}"]  = pdata.get(f"losses_{mode}", 0) + 1

    w, l   = pdata.get("wins", 0), pdata.get("losses", 0)
    wm, lm = pdata.get(f"wins_{mode}", 0), pdata.get(f"losses_{mode}", 0)
    pdata["avg"]          = round(w / (w + l) * 100, 1) if (w + l) else 0.0
    pdata[f"avg_{mode}"]  = round(wm / (wm + lm) * 100, 1) if (wm + lm) else 0.0

    games_after    = w + l
    calib_done_now = (not was_calibrated) and games_after >= CALIBRATION_GAMES
    start_elo      = None
    if calib_done_now:
        win_rate  = (w / games_after) if games_after else 0.0
        start_elo = int(round(CALIBRATION_BASE_ELO + (win_rate - 0.5) * CALIBRATION_SWING))
        start_elo = max(ELO_MIN, min(2000, start_elo))
        pdata["elo"]         = start_elo
        pdata[f"elo_{mode}"] = start_elo
        calib_notifications.append((target_uid, start_elo, _lvl_number(start_elo)))

    kills, deaths = kd_by_uid.get(target_uid, (0, 0))
    pdata["total_kills"]  = pdata.get("total_kills", 0) + kills
    pdata["total_deaths"] = pdata.get("total_deaths", 0) + deaths
    total_kd = round(pdata["total_kills"] / pdata["total_deaths"], 2) if pdata["total_deaths"] else float(pdata["total_kills"])

    nick   = pdata.get("nickname", "?")
    kd_txt = (
        f"{kills}/{deaths} (KD матча {round(kills/deaths, 2) if deaths else kills}) | "
        f"общий KD: <b>{total_kd}</b>"
    )

    if calib_done_now:
        lines.append(
            f"  • {nick}: 🔄→✅ <b>калибровка завершена!</b> Стартовый ранг: <b>{start_elo}</b> ELO | {kd_txt}"
        )
    elif was_calibrated:
        sign    = "+" if won else "-"
        applied = win_d if won else loss_d
        lines.append(
            f"  • {nick}: {sign}{applied} ELO → <b>{pdata['elo']}</b> | {kd_txt}"
        )
    else:
        lines.append(
            f"  • {nick}: 🔄 калибровочный матч ({games_after}/{CALIBRATION_GAMES}), ранг ещё не присвоен | {kd_txt}"
        )

    elo_snapshot[s] = {
        "elo_before":      elo_before,
        "elo_mode_before": elo_mode_before,
        "elo_after":       pdata["elo"],
        "elo_mode_after":  pdata[f"elo_{mode}"],
    }


async def _send_calibration_dms(bot, calib_notifications: List[tuple]) -> None:
    """ЛС-уведомление игрокам, которые только что прошли калибровку."""
    for cal_uid, cal_elo, cal_lvl in calib_notifications:
        try:
            await bot.send_message(
                chat_id=cal_uid,
                text=(
                    f"✅ <b>Вы прошли калибровку!</b>\n\n"
                    f"Сыграно {CALIBRATION_GAMES} калибровочных матчей — "
                    f"бот определил ваш стартовый уровень.\n\n"
                    f"🏆 Уровень: <b>{cal_lvl}</b>\n"
                    f"📊 ELO: <b>{cal_elo}</b>\n\n"
                    f"Дальше ЭЛО меняется за каждую победу/поражение — удачи!"
                ),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            print(f"⚠️ Не удалось отправить ЛС о калибровке uid={cal_uid}: {e!r}")


def _finalize_match(db: Dict[str, Any], m_id: str, m: dict, side: str, kd_by_uid: Dict[int, tuple]):
    """
    Общее ядро завершения матча: начисляет результат обеим сторонам,
    сохраняет снапшот в finished_matches (для /cancelwin), убирает матч
    из active_matches. НЕ вызывает save_db и не шлёт сообщений — это
    делает вызывающий код (win_cmd / ocrwin_confirm), т.к. им нужно
    показать разный текст пользователю.
    Возвращает (win_lines, loss_lines, calib_notifications, mode, winners, losers).
    """
    winners = m.get("ct", []) if side == "ct" else m.get("t", [])
    losers  = m.get("t", [])  if side == "ct" else m.get("ct", [])
    mode    = m.get("mode", "5v5")
    all_uids = [u for u in (winners + losers) if not _is_bot_uid(u)]

    win_lines: List[str] = []
    loss_lines: List[str] = []
    elo_snapshot: Dict[str, dict] = {}
    calib_notifications: List[tuple] = []

    for uid in winners:
        _apply_win_to_player(db, uid, True, mode, kd_by_uid, win_lines, elo_snapshot, calib_notifications)
    for uid in losers:
        _apply_win_to_player(db, uid, False, mode, kd_by_uid, loss_lines, elo_snapshot, calib_notifications)

    kd_snapshot = {str(uid): list(kd_by_uid.get(uid, (0, 0))) for uid in all_uids}
    db.setdefault("finished_matches", {})[m_id] = {
        "mode":         mode,
        "map":          m["maps"][0] if m.get("maps") else None,
        "winners":      winners,
        "losers":       losers,
        "kd_by_uid":    kd_snapshot,
        "elo_snapshot": elo_snapshot,
        "finished_ts":  datetime.now().timestamp(),
    }
    db["active_matches"].pop(m_id, None)
    db.get("unresolved_results", {}).pop(m_id, None)

    return win_lines, loss_lines, calib_notifications, mode, winners, losers


# ════════════════════════════════════════════════
#     РАСПОЗНАВАНИЕ СКОРБОРДА (OCR, Tesseract)
# ════════════════════════════════════════════════
#
# Бот пытается сам прочитать финальный скорборд со скриншота: ники, игровые
# ID, киллы/смерти и счёт команд — чтобы не заставлять админа набирать всё
# руками. Это ЭВРИСТИКА поверх OCR, а не идеальное распознавание: разные
# разрешения экрана, HUD-элементы поверх таблицы (прицел, руки с ножом и т.п.),
# засветы и обрезанные скрины будут иногда путать бота.
#
# Поэтому результат распознавания НИКОГДА не применяется сам по себе — бот
# присылает админ-группе карточку "вот что я увидел" с кнопками
# "✅ Подтвердить" / "✏️ Ввести вручную". Только нажатие кнопки применяет
# результат. Если бот вообще не смог уверенно распознать таблицу — он прямо
# так и скажет, и попросит ввести /win руками, как раньше.

_OCR_RUS_LANG_CHECKED = False
_OCR_RUS_LANG_OK = False


def _ocr_available() -> bool:
    """
    True только если И pytesseract подключён, И у tesseract реально
    установлен русский языковой пакет (tesseract-ocr-rus). Без него
    lang="rus+eng" НЕ падает с ошибкой — Tesseract просто распознаёт
    кириллицу как случайную латиницу/мусор ("ПОБЕДА" → "MOBEA «"), и весь
    скрипт ниже молча не находит ни одного заголовка таблицы. Это самая
    частая причина, почему бот пишет "не нашёл таблицу на скрине" на
    абсолютно нормальных, чётких скриншотах — поэтому проверяем языковой
    пакет явно один раз при первом обращении и кэшируем результат.
    """
    global _OCR_RUS_LANG_CHECKED, _OCR_RUS_LANG_OK
    if pytesseract is None:
        return False
    if _OCR_RUS_LANG_CHECKED:
        return _OCR_RUS_LANG_OK
    _OCR_RUS_LANG_CHECKED = True
    try:
        langs = pytesseract.get_languages(config="")
        _OCR_RUS_LANG_OK = "rus" in langs
        if not _OCR_RUS_LANG_OK:
            print(
                "[ocr] ⚠️ ВНИМАНИЕ: языковой пакет 'rus' НЕ установлен в tesseract "
                f"(доступны только: {langs}). Распознавание русских скриншотов "
                "работать НЕ будет — Tesseract читает кириллицу как мусор без "
                "явной ошибки. Добавь 'tesseract-ocr-rus' в Aptfile проекта на "
                "Railway и передеплой."
            )
    except Exception as e:
        print(f"[ocr] не удалось проверить список языков tesseract: {e!r}")
        _OCR_RUS_LANG_OK = False
    return _OCR_RUS_LANG_OK


def parse_scoreboard_ocr(image_bytes: bytes) -> Optional[dict]:
    """
    Пытается разобрать скриншот результата матча. Игроки присылают ОДИН из
    двух разных экранов игры, и оба нужно понимать одинаково хорошо:

      Формат A — "в игре" (freeze-frame HUD после раунда/матча):
        колонки подписаны полными словами "УБИЙСТВ" / "СМЕРТЕЙ", сверху есть
        HUD-счёт команд (два числа рядом с таймером), и/или зелёный баннер
        "ПОБЕДА"/"ПОРАЖЕНИЕ" поверх части строк. Игровой ID стоит СПРАВА от
        ника (номер после имени).

      Формат B — финальный экран "В МЕНЮ" (табло по окончании матча):
        две колонки-таблицы бок о бок (Спецназ | Террористы), у каждой свои
        заголовки "У" (убийств) и "С" (смертей) вместо полных слов, общий
        счёт указан один раз сверху по центру каждой половины экрана
        ("СПЕЦНАЗ ... <b>8</b> | <b>6</b> ... ТЕРРОРИСТЫ"). Игровой ID тоже
        справа от ника.

    Алгоритм:
      1) Прогоняем изображение через Tesseract (рус+eng) → слова с координатами.
      2) Пытаемся найти заголовки полными словами (формат A). Если не нашли —
         ищем короткие заголовки "У"/"С" рядом друг с другом дважды (по разу
         на каждую половину экрана формата B) и работаем с двумя независимыми
         наборами колонок вместо одного.
      3) Группируем найденные числа в строки по Y-координате.
      4) В каждой строке: под "У"-колонкой → киллы, под "С"-колонкой → смерти,
         число СПРАВА от ника (или слева, если справа не нашли) → игровой ID.
      5) Цвет пикселя рядом с ником (синий/оранжевый) определяет команду —
         подстраховка на случай формата B, где команды и так разнесены по
         половинам экрана.
      6) Счёт команд: либо два числа над таблицей по центру (формат A), либо
         два числа в шапке "СПЕЦНАЗ X | Y ТЕРРОРИСТЫ" (формат B). Сторона с
         большим числом — победитель. Плюс запасной сигнал — баннер "ПОБЕДА".

    Возвращает None, если не удалось найти вообще ни одной таблицы (нет
    заголовков) или строк меньше одной — тогда вызывающий код должен
    откатиться на ручной ввод через /win.
    """
    if not _ocr_available():
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:
        return None

    w, h = img.size
    try:
        data = pytesseract.image_to_data(img, lang="rus+eng", output_type=pytesseract.Output.DICT)
    except Exception as e:
        print(f"[ocr] tesseract error: {e!r}")
        return None

    words = []
    for i in range(len(data.get("text", []))):
        txt = (data["text"][i] or "").strip()
        if not txt:
            continue
        try:
            conf = int(float(data["conf"][i]))
        except (ValueError, TypeError):
            conf = -1
        if conf < 25:
            continue
        words.append({
            "text": txt,
            "x": data["left"][i], "y": data["top"][i],
            "w": data["width"][i], "h": data["height"][i],
        })

    # ── Ищем колонки-заголовки. Может быть НЕСКОЛЬКО пар (формат B — по
    # паре на каждую половину экрана), поэтому всегда собираем список пар,
    # а не одну пару, как раньше. ────────────────────────────────────────
    col_pairs: List[dict] = []  # [{"kills_x":.., "deaths_x":.., "header_y":..}]

    # Формат A: полные слова "УБИЙСТВ" / "СМЕРТЕЙ" — обычно ровно одна пара
    # на весь экран (общая таблица на двоих).
    full_kills = [wd for wd in words if "УБИЙСТ" in wd["text"].upper()]
    full_deaths = [wd for wd in words if "СМЕРТ" in wd["text"].upper()]
    if full_kills and full_deaths:
        kw = full_kills[0]
        dw = min(full_deaths, key=lambda wd: abs(wd["y"] - kw["y"]))
        col_pairs.append({
            "kills_x":  kw["x"] + kw["w"] // 2,
            "deaths_x": dw["x"] + dw["w"] // 2,
            "header_y": min(kw["y"], dw["y"]),
        })

    # Формат B: короткие одиночные заголовки "У" и "С" — на каждой половине
    # экрана своя пара, соседствующая по Y и с "С" правее "У".
    if not col_pairs:
        short_u = [wd for wd in words if wd["text"].upper() in ("У", "K", "K/", "УБ") and wd["w"] < w * 0.06]
        short_s = [wd for wd in words if wd["text"].upper() in ("С", "D", "СМ") and wd["w"] < w * 0.06]
        for uw in short_u:
            # Ищем "С" на той же строке (близкий Y) и правее по X.
            same_row = [
                swd for swd in short_s
                if abs(swd["y"] - uw["y"]) < max(10, uw["h"])
                and swd["x"] > uw["x"]
            ]
            if not same_row:
                continue
            sw = min(same_row, key=lambda wd: wd["x"] - uw["x"])
            col_pairs.append({
                "kills_x":  uw["x"] + uw["w"] // 2,
                "deaths_x": sw["x"] + sw["w"] // 2,
                "header_y": min(uw["y"], sw["y"]),
            })

    if not col_pairs:
        return None  # не нашли ни одной шапки таблицы — не похоже на наш скорборд

    col_tol = max(45, w // 18)
    row_tol = max(16, h // 55)

    rows: List[dict] = []
    for pair in col_pairs:
        kills_x, deaths_x, header_y = pair["kills_x"], pair["deaths_x"], pair["header_y"]

        number_words = [wd for wd in words if wd["text"].isdigit() and wd["y"] > header_y]

        rows_y: List[int] = []
        for wd in sorted(number_words, key=lambda x: x["y"]):
            if not any(abs(wd["y"] - ry) < row_tol for ry in rows_y):
                rows_y.append(wd["y"])

        for ry in rows_y:
            row_nums = [wd for wd in number_words if abs(wd["y"] - ry) < row_tol]

            def _closest(target_x, exclude=()):
                cands = [wd for wd in row_nums if abs(wd["x"] - target_x) < col_tol and wd not in exclude]
                return min(cands, key=lambda wd: abs(wd["x"] - target_x)) if cands else None

            kills_cell  = _closest(kills_x)
            deaths_cell = _closest(deaths_x, exclude=[kills_cell] if kills_cell else [])
            if not kills_cell or not deaths_cell or kills_cell is deaths_cell:
                continue

            # Игровой ID — число в этой же строке, НЕ являющееся киллами/смертями.
            # Ищем СНАЧАЛА справа от ника (форматы A и B кладут ID сразу после
            # ника), затем слева — на случай другой раскладки экрана.
            id_candidates = [
                wd for wd in row_nums
                if wd is not kills_cell and wd is not deaths_cell
            ]
            if not id_candidates:
                continue
            # ID — самое левое число из тех, что расположены заметно левее
            # колонки "убийств" (т.е. явно не часть статы, а подпись у ника).
            id_pool = [wd for wd in id_candidates if wd["x"] < kills_x - col_tol]
            if not id_pool:
                continue
            id_cell = min(id_pool, key=lambda wd: wd["x"])

            # Ник — весь нечисловой текст в строке. Может быть как слева от
            # ID (формат A: "Ник ID"), так и всё равно слева (формат B тоже
            # кладёт ник первым, потом ID) — в обоих случаях берём слова
            # левее ID-ячейки.
            nick_words = [
                wd for wd in words
                if not wd["text"].isdigit()
                and abs(wd["y"] - ry) < row_tol
                and wd["x"] < id_cell["x"] - 5
                and wd["x"] < kills_x
            ]
            nick_words.sort(key=lambda wd: wd["x"])
            nickname = " ".join(wd["text"] for wd in nick_words) if nick_words else None
            if not nickname:
                continue

            sample_x = max(0, min(w - 1, nick_words[0]["x"] + 5))
            sample_y = max(0, min(h - 1, ry + max(4, nick_words[0]["h"] // 2)))
            try:
                r, g, b = img.getpixel((sample_x, sample_y))[:3]
            except Exception:
                r, g, b = (0, 0, 0)
            # Доп. сигнал команды: какая половина экрана (актуально для
            # формата B, где команды физически разнесены влево/вправо).
            side_hint = "left" if (kills_x < w / 2) else "right"
            team = "blue" if b > r + 12 else ("orange" if r > b + 12 else "unknown")

            rows.append({
                "external_id": id_cell["text"],
                "nickname":    nickname,
                "kills":       int(kills_cell["text"]),
                "deaths":      int(deaths_cell["text"]),
                "team":        team,
                "side_hint":   side_hint,
            })

    # Дедупликация — если формат A и B случайно дали пересекающиеся строки
    # (не должно происходить, т.к. col_pairs формируются взаимоисключающе,
    # но на всякий случай подстраховываемся по external_id).
    seen_ids = set()
    dedup_rows = []
    for row in rows:
        if row["external_id"] in seen_ids:
            continue
        seen_ids.add(row["external_id"])
        dedup_rows.append(row)
    rows = dedup_rows

    if len(rows) < 1:
        return None

    header_y_min = min(p["header_y"] for p in col_pairs)

    # ── Счёт команд, вариант 1: два числа над таблицей по центру экрана
    # (формат A — HUD-счёт рядом с таймером). ────────────────────────────
    score_candidates = [
        wd for wd in words
        if wd["text"].isdigit() and wd["y"] < header_y_min and len(wd["text"]) <= 2
        and w * 0.30 < wd["x"] < w * 0.70
    ]
    score_candidates.sort(key=lambda wd: wd["x"])
    blue_score = orange_score = None
    if len(score_candidates) >= 2:
        try:
            blue_score   = int(score_candidates[0]["text"])
            orange_score = int(score_candidates[1]["text"])
        except ValueError:
            blue_score = orange_score = None

    # ── Счёт команд, вариант 2: формат B кладёт счёт как отдельные числа
    # слева и справа от заголовка результата ("СПЕЦНАЗ  8   6  ТЕРРОРИСТЫ"),
    # выше всех колонок-заголовков "У"/"С". Берём два "крупных" одиночных
    # числа выше header_y_min, ближе к горизонтальному центру, но шире зоны
    # варианта 1 (т.к. на широких экранах счёт может быть не строго по
    # центру половины таблицы). ──────────────────────────────────────────
    if blue_score is None or orange_score is None:
        top_numbers = [
            wd for wd in words
            if wd["text"].isdigit() and wd["y"] < header_y_min and len(wd["text"]) <= 2
        ]
        top_numbers.sort(key=lambda wd: wd["x"])
        if len(top_numbers) >= 2:
            left_half  = [wd for wd in top_numbers if wd["x"] < w / 2]
            right_half = [wd for wd in top_numbers if wd["x"] >= w / 2]
            if left_half and right_half:
                try:
                    blue_score   = int(max(left_half,  key=lambda wd: wd["x"])["text"])
                    orange_score = int(min(right_half, key=lambda wd: wd["x"])["text"])
                except ValueError:
                    blue_score = orange_score = None

    # ── Запасной сигнал: баннер "ПОБЕДА"/"ПОРАЖЕНИЕ" ────────────────────
    # Слово само по себе не говорит, ЧЬЯ это победа — это интерпретируется
    # в _match_ocr_to_roster относительно того, кто прислал скриншот (он
    # точно один из игроков этого матча).
    result_word = None
    for wd in words:
        t = wd["text"].upper()
        if "ПОБЕД" in t:
            result_word = "victory"
            break
        if "ПОРАЖЕН" in t:
            result_word = "defeat"
            break

    return {
        "rows": rows,
        "blue_score": blue_score,
        "orange_score": orange_score,
        "result_word": result_word,
    }


def _match_ocr_to_roster(m: dict, ocr: dict, reporter_uid: Optional[int] = None,
                          prior_kd_by_uid: Optional[Dict[int, tuple]] = None,
                          prior_side_win: Optional[str] = None) -> tuple:
    """
    Сопоставляет распознанные строки (по external_id) с реальными игроками
    матча m (ct/t списки uid). Возвращает (kd_by_uid, side_win, unmatched, missing).
      kd_by_uid  — {uid: (kills, deaths)} — НАКОПЛЕННЫЙ результат: если матчу
                   уже присылали скрин раньше (prior_kd_by_uid), новые строки
                   ДОПОЛНЯЮТ его, а не затирают целиком. Это нужно потому что
                   разные игроки часто шлют разные скрины (каждый видит крупно
                   свою половину экрана) — из них двух вместе может получиться
                   полный ростер, даже если ни один скрин по отдельности не
                   содержал всех.
      side_win   — "ct"/"t"/None (None — не смогли определить победителя).
                   Если новый скрин не даёт счёта, используется prior_side_win.
      unmatched  — распознанные строки, чей external_id не принадлежит ни одному
                   игроку этого матча (мусор/OCR-ошибка)
      missing    — реальные игроки матча, для которых ДО СИХ ПОР нет строки
                   ни на одном присланном скрине
    """
    all_uids = [u for u in (m.get("ct", []) + m.get("t", [])) if not _is_bot_uid(u)]
    ext_to_uid = {}
    for uid in all_uids:
        p = get_player(uid)
        if p.external_id:
            ext_to_uid[str(p.external_id)] = uid

    kd_by_uid: Dict[int, tuple] = dict(prior_kd_by_uid or {})
    unmatched: List[str] = []
    matched_uids = set(kd_by_uid.keys())
    row_team_of_uid: Dict[int, str] = {}

    for row in ocr["rows"]:
        gid = row["external_id"]
        uid = ext_to_uid.get(gid)
        if uid is None:
            unmatched.append(f"{row.get('nickname') or '?'} [ID {gid}]")
            continue
        kd_by_uid[uid] = (row["kills"], row["deaths"])
        matched_uids.add(uid)
        row_team_of_uid[uid] = row["team"]

    missing = [get_player(u).nickname for u in all_uids if u not in matched_uids]

    # ── Определяем победившую сторону ────────────────────────────────
    winning_color = None
    if ocr.get("blue_score") is not None and ocr.get("orange_score") is not None:
        winning_color = "blue" if ocr["blue_score"] > ocr["orange_score"] else (
            "orange" if ocr["orange_score"] > ocr["blue_score"] else None
        )

    if winning_color is None and ocr.get("result_word") and reporter_uid is not None:
        reporter_color = row_team_of_uid.get(reporter_uid)
        if reporter_color in ("blue", "orange"):
            other_color = "orange" if reporter_color == "blue" else "blue"
            if ocr["result_word"] == "victory":
                winning_color = reporter_color
            elif ocr["result_word"] == "defeat":
                winning_color = other_color

    side_win = None
    if winning_color:
            # Какая физическая сторона (ct/t в базе) соответствует этому цвету?
            # Смотрим, к какой db-стороне принадлежит большинство игроков с этим цветом.
            ct_set = set(m.get("ct", []))
            color_uids = [u for u, c in row_team_of_uid.items() if c == winning_color]
            if color_uids:
                ct_hits = sum(1 for u in color_uids if u in ct_set)
                side_win = "ct" if ct_hits >= len(color_uids) / 2 else "t"

    if side_win is None:
        side_win = prior_side_win

    return kd_by_uid, side_win, unmatched, missing


def _fill_missing_players_as_losers(m: dict, kd_by_uid: Dict[int, tuple], side_win: Optional[str]) -> List[str]:
    """
    Для игроков матча, которых бот НИ РАЗУ не нашёл ни на одном присланном
    скрине (не зашёл в катку, вылетел, скрин не поймал его строку и т.п.) —
    автоматически проставляет им 0 убийств / 8 смертей и засчитывает их в
    ПРОИГРАВШУЮ сторону. Это соответствует логике «отсутствие результата —
    это не сыгранный раунд», а не техническая победа отсутствующего игрока.
    Возвращает список ников, которых пришлось доставить таким образом
    (используется в тексте карточки подтверждения, чтобы админ это видел).
    """
    if side_win not in ("ct", "t"):
        return []
    losing_side = "t" if side_win == "ct" else "ct"
    losing_uids = [u for u in m.get(losing_side, []) if not _is_bot_uid(u)]

    filled: List[str] = []
    for uid in losing_uids:
        if uid not in kd_by_uid:
            kd_by_uid[uid] = (0, 8)
            filled.append(get_player(uid).nickname)

    # Игроков победившей стороны без строки НЕ автозаполняем нулём — если
    # команда выиграла, но бот не нашёл кого-то из победителей на скринах,
    # это обычно ошибка распознавания ника/ID, а не реальный 0/0 победителя.
    # Такие случаи остаются в missing и уходят на ручную проверку админом.
    return filled


def _build_ocr_confirm_card(m_id: str, m: dict, pending: dict) -> tuple:
    """
    Строит (текст, клавиатура) карточки подтверждения распознанного
    результата матча. Используется и в общем чате, и в ЛС игроку/админу —
    чтобы карточка выглядела одинаково независимо от источника скрина.
    """
    side_win = pending["side"]
    kd_by_uid = {int(k): tuple(v) for k, v in pending["kd_by_uid"].items()}
    reporter_uid = pending.get("reported_by")
    filled_auto = set(pending.get("filled_auto", []))

    win_label = "🔵 CT" if side_win == "ct" else "🔴 T"
    lines = []
    for u, (k, d) in kd_by_uid.items():
        pl = get_player(u)
        side_tag = "🏆" if (u in m.get(side_win, [])) else "❌"
        auto_tag = " ⚠️<i>(не найден на скрине — авто 0/8)</i>" if pl.nickname in filled_auto else ""
        lines.append(f"  {side_tag} {pl.nickname}: {k}/{d}{auto_tag}")

    reporter_line = ""
    if reporter_uid:
        reporter_line = f"\n\nОт: {get_player(reporter_uid).tg_link()}"

    screenshots_line = ""
    n_shots = pending.get("screenshots_count", 1)
    if n_shots > 1:
        screenshots_line = f"\n📸 Собрано из {n_shots} присланных скринов."

    text = (
        f"🤖 <b>Матч #{m_id}</b> — распознанный результат по скрину:\n\n"
        f"Победила сторона: {win_label}\n" + "\n".join(lines) +
        reporter_line + screenshots_line +
        f"\n\nПроверьте и подтвердите, либо введите вручную через /win."
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Подтвердить и применить", callback_data=f"ocrwin_confirm:{m_id}"),
        InlineKeyboardButton("✏️ Ввести вручную",         callback_data=f"ocrwin_manual:{m_id}"),
    ]])
    return text, kb


async def _process_result_screenshot(m_id: str, m: dict, uid: int, msg, context: ContextTypes.DEFAULT_TYPE, db: Dict[str, Any]):
    """
    Общее ядро обработки присланного скрина результата: пробует распознать
    итог через OCR и либо кладёт карточку подтверждения в pending_ocr
    (плюс шлёт её в админ-конфу), либо просит администрацию проверить вручную.
    Используется и при скрине в группе (по подписи с номером матча),
    и при скрине, присланном игроком в ЛС боту.
    НЕ отвечает игроку — это делает вызывающий код (там разный текст).

    Игроки часто присылают РАЗНЫЕ скрины одного и того же матча (в игре
    видно крупно только свою половину экрана, а после матча — общий
    финальный экран), поэтому результат НАКАПЛИВАЕТСЯ: если по матчу уже
    приходил скрин раньше — новые распознанные строки достраивают прошлый
    результат, а не затирают его. Как только сторона-победитель известна
    (из счёта или баннера «ПОБЕДА»/«ПОРАЖЕНИЕ» на любом из скринов), а
    кого-то из проигравшей стороны так и не нашли ни на одном скрине —
    бот сам проставляет ему 0 убийств / 8 смертей, чтобы не блокировать
    подтверждение результата из-за одного не зашедшего в катку игрока.
    """
    p = get_player(uid)
    try:
        ocr_result = None
        unmatched: List[str] = []
        missing: List[str] = []

        prior = db.get("pending_ocr", {}).get(m_id)
        prior_kd_by_uid: Dict[int, tuple] = {}
        prior_side_win: Optional[str] = None
        prior_shots = 0
        if prior:
            prior_kd_by_uid = {int(k): tuple(v) for k, v in prior.get("kd_by_uid", {}).items()}
            prior_side_win  = prior.get("side")
            prior_shots     = prior.get("screenshots_count", 1)

        kd_by_uid: Dict[int, tuple] = dict(prior_kd_by_uid)
        side_win = prior_side_win

        try:
            photo_file = await msg.photo[-1].get_file()
            photo_bytes = bytes(await photo_file.download_as_bytearray())
            ocr_result = parse_scoreboard_ocr(photo_bytes)
            if ocr_result:
                kd_by_uid, side_win, unmatched, missing = _match_ocr_to_roster(
                    m, ocr_result, reporter_uid=uid,
                    prior_kd_by_uid=prior_kd_by_uid, prior_side_win=prior_side_win,
                )
            else:
                # Этот конкретный скрин не распознался, но у нас уже могли
                # быть накопленные данные от предыдущих скринов — считаем missing
                # относительно них, а не сбрасываем всё в ноль.
                all_uids = [u for u in (m.get("ct", []) + m.get("t", [])) if not _is_bot_uid(u)]
                missing = [get_player(u).nickname for u in all_uids if u not in kd_by_uid]
        except Exception as e:
            print(f"[result] ocr error: {e!r}")
            ocr_result = None

        if ADMIN_GROUP_ID:
            try:
                await context.bot.forward_message(
                    chat_id=ADMIN_GROUP_ID,
                    from_chat_id=msg.chat_id,
                    message_id=msg.message_id,
                )
            except Exception as e:
                print(f"[result] admin forward error: {e!r}")

        shots_count = prior_shots + 1

        # ── Автодоставка отсутствующих игроков ──────────────────────────
        # Победитель уже известен и остаётся кто-то без строки — считаем,
        # что это игроки проигравшей стороны, которые не отыграли катку
        # (не зашли/дисконнект), и проставляем им 0/8 автоматически.
        filled_auto: List[str] = []
        if side_win in ("ct", "t") and missing:
            filled_auto = _fill_missing_players_as_losers(m, kd_by_uid, side_win)
            all_uids = [u for u in (m.get("ct", []) + m.get("t", [])) if not _is_bot_uid(u)]
            missing = [get_player(u).nickname for u in all_uids if u not in kd_by_uid]

        ocr_ok = (bool(ocr_result) or bool(prior)) and side_win is not None and not unmatched and not missing and len(kd_by_uid) >= 2

        if not ocr_ok:
            reasons = []
            if not _ocr_available():
                if pytesseract is None:
                    reasons.append("на сервере не установлен pytesseract/tesseract (проверь requirements.txt и Aptfile на Railway)")
                else:
                    reasons.append("на сервере не установлен русский языковой пакет tesseract-ocr-rus (добавь его в Aptfile и передеплой)")
            elif not ocr_result and not prior:
                reasons.append("не нашёл таблицу на скрине (плохой ракурс/качество/не тот интерфейс)")
            else:
                if side_win is None:
                    reasons.append("не разобрал счёт команд")
                if unmatched:
                    reasons.append("есть нераспознанные/чужие ID: " + ", ".join(unmatched))
                if missing:
                    reasons.append("не нашёл строку для: " + ", ".join(missing))

            # Сохраняем накопленный прогресс — если это не первый скрин по
            # матчу, следующий присланный скрин (от другого игрока или того
            # же) продолжит достраивать этот же результат, а не начнёт с нуля.
            if kd_by_uid or side_win:
                db.setdefault("pending_ocr", {})[m_id] = {
                    "side":               side_win,
                    "kd_by_uid":          {str(k): list(v) for k, v in kd_by_uid.items()},
                    "reported_by":        uid,
                    "screenshots_count":  shots_count,
                    "filled_auto":        filled_auto,
                }

            # Помечаем матч как «есть скрин, но не распознан полностью» —
            # это увидит любой админ/мод через кнопку «🧾 Результаты матчей»
            # в ЛС, даже если он не сидит в админ-конфе.
            db.setdefault("unresolved_results", {})[m_id] = {
                "reported_by": uid,
                "reasons":     reasons,
                "ts":          time.time(),
            }
            save_db(db)

            progress_line = ""
            if kd_by_uid:
                progress_line = (
                    f"\nУже есть данные по {len(kd_by_uid)} из "
                    f"{len([u for u in (m.get('ct', []) + m.get('t', [])) if not _is_bot_uid(u)])} игроков "
                    f"(из {shots_count} скрин(ов)) — ждём остальные скрины или ручной ввод."
                )

            if ADMIN_GROUP_ID:
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=(
                        f"📸 Скрин результатов матча #{m_id}\n"
                        f"От: {p.tg_link()}\n\n"
                        f"⚠️ Бот не смог уверенно распознать результат автоматически "
                        f"({'; '.join(reasons) if reasons else 'неизвестная причина'})."
                        f"{progress_line}\n"
                        f"Проверьте вручную и введите <code>/win {m_id} ct|t ...</code>, "
                        f"либо дождитесь ещё одного скрина от другого игрока матча."
                    ),
                    parse_mode=ParseMode.HTML,
                )
            return

        # Успешно распознали — если раньше матч висел как «нераспознанный», убираем отметку
        db.get("unresolved_results", {}).pop(m_id, None)

        # ── Уверенно распознали — кладём в очередь на подтверждение админом ──
        db.setdefault("pending_ocr", {})[m_id] = {
            "side":               side_win,
            "kd_by_uid":          {str(k): list(v) for k, v in kd_by_uid.items()},
            "reported_by":        uid,
            "screenshots_count":  shots_count,
            "filled_auto":        filled_auto,
        }
        save_db(db)

        text, kb = _build_ocr_confirm_card(m_id, m, db["pending_ocr"][m_id])
        if ADMIN_GROUP_ID:
            await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID, text=text, parse_mode=ParseMode.HTML, reply_markup=kb,
            )
    except Exception as e:
        # Подстраховка: если где-то в блоке выше что-то пошло не так и
        # не было поймано локально — не даём ошибке пройти полностью молча.
        # Печатаем traceback в лог Railway И пытаемся всё равно уведомить
        # админ-группу, чтобы результат не потерялся без следа.
        import traceback
        traceback.print_exc()
        if ADMIN_GROUP_ID:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=(
                        f"⚠️ Ошибка при обработке скрина матча #{m_id}: <code>{e!r}</code>\n"
                        f"Проверьте вручную и введите <code>/win {m_id} ct|t ...</code>."
                    ),
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e2:
                print(f"[result] даже фолбэк-уведомление не отправилось: {e2!r}")


async def scoreboard_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Игрок присылает в группу скриншот результатов с подписью, где есть номер матча
    (например: фото + подпись "3" или "матч 3").
    Бот проверяет, что отправитель — участник этого матча, пробует
    распознать результат через OCR (см. _process_result_screenshot) и
    пересылает скрин в админ-конфу на проверку.
    """
    msg = update.message
    if not msg or not msg.photo:
        return
    if msg.chat.type not in ("group", "supergroup"):
        return

    caption = msg.caption or ""
    m_found = re.search(r"\d+", caption)
    if not m_found:
        return  # в подписи нет номера матча — не трогаем фото
    m_id = m_found.group(0)

    uid = msg.from_user.id
    db  = load_db()
    m   = db["active_matches"].get(m_id)
    if not m:
        await msg.reply_text(f"❌ Матч #{m_id} не найден или уже закрыт.")
        return

    all_players = [u for u in (m["ct"] + m["t"]) if not _is_bot_uid(u)]
    if uid not in all_players:
        await msg.reply_text("❌ Вы не участник этого матча — скрин не принят.")
        return

    await msg.reply_text(
        f"📸 Скриншот игры #{m_id} принят.\n\n"
        f"Ожидайте, когда администрация Night Faceit зарегает вам игру.\n\n"
        f"Спасибо что выбрали наш фейсит 🌙",
        parse_mode=ParseMode.HTML,
    )

    await _process_result_screenshot(m_id, m, uid, msg, context, db)


async def result_dm_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Скриншот результата, присланный игроком боту в ЛС.

    Номер матча бот определяет сам, без ввода от игрока:
      1. Если игрок недавно нажал кнопку «📤 Отправить результат» под
         сообщением о собранном лобби — берём матч оттуда (dm_result_wait).
      2. Иначе, если в подписи к фото есть число — считаем его номером матча.
      3. Иначе смотрим, в скольких активных матчах участвует этот игрок:
         если ровно в одном — берём его; если в нескольких — просим
         прислать скрин ещё раз с номером матча в подписи.
    Если ни один из вариантов не сработал (игрок вообще не в активных
    матчах) — не трогаем сообщение, чтобы не мешать транслятору тикетов.
    """
    msg = update.message
    if not msg or not msg.photo:
        return

    uid = update.effective_user.id
    db  = load_db()

    m_id = db.get("dm_result_wait", {}).pop(str(uid), None)
    if m_id is not None:
        save_db(db)

    caption = msg.caption or ""
    cap_num = re.search(r"\d+", caption)
    if not m_id and cap_num:
        m_id = cap_num.group(0)

    if not m_id:
        candidates = [
            mid for mid, mm in db.get("active_matches", {}).items()
            if uid in (mm.get("ct", []) + mm.get("t", []))
        ]
        if len(candidates) == 1:
            m_id = candidates[0]
        elif len(candidates) > 1:
            await msg.reply_text(
                "❓ Вы участвуете в нескольких матчах одновременно.\n"
                "Пришлите скрин ещё раз с подписью — номером нужного матча, например: <code>14</code>.",
                parse_mode=ParseMode.HTML,
            )
            raise ApplicationHandlerStop
        else:
            # Игрок не участник ни одного активного матча — не наш кейс,
            # пусть фото уйдёт в обычный транслятор тикетов.
            return

    m = db.get("active_matches", {}).get(m_id)
    if not m:
        await msg.reply_text(f"❌ Матч #{m_id} не найден или уже закрыт.")
        raise ApplicationHandlerStop

    all_players = [u for u in (m["ct"] + m["t"]) if not _is_bot_uid(u)]
    if uid not in all_players:
        await msg.reply_text("❌ Вы не участник этого матча — скрин не принят.")
        raise ApplicationHandlerStop

    await msg.reply_text(
        f"📸 Скриншот матча #{m_id} принят.\n\n"
        f"Ожидайте, когда администрация Night Faceit зарегает вам игру.\n\n"
        f"Спасибо что выбрали наш фейсит 🌙",
        parse_mode=ParseMode.HTML,
    )

    await _process_result_screenshot(m_id, m, uid, msg, context, db)
    raise ApplicationHandlerStop


# ════════════════════════════════════════════════
#                  МУТ / БАН (Telegram-уровень)
# ════════════════════════════════════════════════

_MUTE_PERMISSIONS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False,
)

_UNMUTE_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_invite_users=True,
)


async def _tg_mute(context: ContextTypes.DEFAULT_TYPE, chat_id: int, target: int, until_date=None) -> Optional[str]:
    """
    Настоящий мут на уровне Telegram: через restrict_chat_member отбирает у
    пользователя право писать/слать что-либо в чат до until_date.
    В отличие от простого удаления сообщений постфактум, это полностью
    блокирует возможность написать вообще что-либо — Telegram сам не даст
    отправить сообщение. Возвращает None при успехе или текст ошибки.
    """
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=target,
            permissions=_MUTE_PERMISSIONS,
            until_date=until_date,
        )
        return None
    except Exception as e:
        return str(e)


async def _tg_unmute(context: ContextTypes.DEFAULT_TYPE, chat_id: int, target: int) -> Optional[str]:
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=target,
            permissions=_UNMUTE_PERMISSIONS,
        )
        return None
    except Exception as e:
        return str(e)


def _fmt_duration(seconds: int) -> str:
    if seconds < 3600:
        return f"{seconds // 60} мин."
    if seconds < 86400:
        return f"{seconds // 3600} ч."
    return f"{seconds // 86400} д."


async def mute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Полный мут: пользователь полностью теряет возможность что-либо делать —
    писать сообщения, вставать в очередь, выбирать игроков на пике, банить
    карты, использовать любые команды бота. Реализовано в два слоя:
      1) Telegram-уровень — restrict_chat_member физически запрещает писать;
      2) Уровень бота — флаг в БД (check_muted), который проверяется во
         ВСЕХ командах (gate()) и во всех callback-кнопках (callback_handler),
         плюс глобальный фильтр сообщений на случай, если бот лишён прав
         администратора в чате.
    """
    if not is_moderator(update.effective_user.id): return

    target = get_reply_target(update, context.args)
    if target is None:
        await update.message.reply_text(
            "Формат: /mute <user_id> [30m|2h|1d]\n"
            "Или ответьте на сообщение пользователя.\n"
            "Без указания срока — мут на 30 минут."
        ); return

    if is_admin(target):
        await update.message.reply_text("❌ Нельзя замьютить администратора."); return

    args_offset = 0 if update.message.reply_to_message else 1
    dur_str  = context.args[args_offset] if len(context.args) > args_offset else None
    duration = parse_duration(dur_str) if dur_str else 1800
    if duration is None:
        await update.message.reply_text("Неверный формат. Примеры: 30m 2h 1d"); return

    db = load_db()
    p  = get_player(target)
    chat_id = update.effective_chat.id

    until_ts = int(datetime.now().timestamp()) + duration
    db.setdefault("muted", {})[str(target)] = until_ts
    save_db(db)
    asyncio.create_task(_schedule_mute_expiry(context.bot, target, float(until_ts)))

    tg_err = await _tg_mute(context, chat_id, target, until_date=until_ts)

    dur_label = _fmt_duration(duration)
    text = f"🔇 <b>{p.nickname}</b> замьючен на {dur_label}.\nВ это время он не может ничего писать/делать."
    if tg_err:
        text += (
            f"\n⚠️ Не удалось ограничить в Telegram ({tg_err}).\n"
            f"Проверьте, что бот — администратор чата с правом «Ограничение участников». "
            f"Сообщения пользователя будут удаляться автоматически как подстраховка."
        )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    if not _is_bot_uid(target):
        await _notify_punishment_dm(context, target, "mute", dur_label)


async def unmute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_moderator(update.effective_user.id): return

    target = get_reply_target(update, context.args)
    if target is None:
        await update.message.reply_text(
            "Формат: /unmute <user_id>\n"
            "Или ответьте на сообщение пользователя."
        ); return

    db = load_db()
    db["muted"].pop(str(target), None)
    save_db(db)

    tg_err = await _tg_unmute(context, update.effective_chat.id, target)
    p = get_player(target)
    text = f"🔊 Мут снят с <b>{p.nickname}</b>"
    if tg_err:
        text += f"\n⚠️ Telegram: {tg_err}"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def _tg_ban(context: ContextTypes.DEFAULT_TYPE, chat_id: int, target: int, until_date=None) -> Optional[str]:
    """
    Настоящий бан на уровне Telegram: исключает пользователя из чата и не даёт
    зайти обратно до until_date (None = навсегда).
    Возвращает None при успехе или текст ошибки при неудаче.
    """
    try:
        await context.bot.ban_chat_member(
            chat_id=chat_id,
            user_id=target,
            until_date=until_date,
        )
        return None
    except Exception as e:
        return str(e)


async def _tg_unban(context: ContextTypes.DEFAULT_TYPE, chat_id: int, target: int) -> Optional[str]:
    try:
        await context.bot.unban_chat_member(
            chat_id=chat_id,
            user_id=target,
            only_if_banned=True,
        )
        return None
    except Exception as e:
        return str(e)


async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return

    target = get_reply_target(update, context.args)
    if target is None:
        await update.message.reply_text(
            "Формат: /ban <user_id> [30m|2h|1d|perm]\n"
            "Или ответьте на сообщение пользователя."
        ); return

    if is_admin(target):
        await update.message.reply_text("❌ Нельзя забанить администратора."); return

    args_offset = 0 if update.message.reply_to_message else 1
    dur_str = context.args[args_offset] if len(context.args) > args_offset else None

    db = load_db()
    p  = get_player(target)
    chat_id = update.effective_chat.id

    if dur_str and dur_str.lower() == "perm":
        db["banned"][str(target)] = 9_999_999_999
        save_db(db)
        tg_err = await _tg_ban(context, chat_id, target, until_date=None)
        text = f"🚫 <b>{p.nickname}</b> перманентно забанен и исключён из беседы."
        if tg_err:
            text += (
                f"\n⚠️ Не удалось забанить в Telegram ({tg_err}).\n"
                f"Проверьте, что бот — администратор чата с правом «Блокировать участников». "
                f"Сообщения пользователя будут удаляться автоматически."
            )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        if not _is_bot_uid(target):
            await _notify_punishment_dm(context, target, "ban", "навсегда")
        return

    duration = parse_duration(dur_str) if dur_str else 86400
    if duration is None:
        await update.message.reply_text("Неверный формат. Примеры: 30m 2h 1d perm"); return

    until_ts = int(datetime.now().timestamp()) + duration
    db["banned"][str(target)] = until_ts
    save_db(db)
    asyncio.create_task(_schedule_ban_expiry(context.bot, target, float(until_ts)))

    tg_err = await _tg_ban(context, chat_id, target, until_date=until_ts)

    dur_label = _fmt_duration(duration)

    text = f"🚫 <b>{p.nickname}</b> забанен на {dur_label} и исключён из беседы."
    if tg_err:
        text += (
            f"\n⚠️ Не удалось забанить в Telegram ({tg_err}).\n"
            f"Проверьте, что бот — администратор чата с правом «Блокировать участников». "
            f"Сообщения пользователя будут удаляться автоматически."
        )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    if not _is_bot_uid(target):
        await _notify_punishment_dm(context, target, "ban", dur_label)


async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return

    target = get_reply_target(update, context.args)
    if target is None:
        await update.message.reply_text(
            "Формат: /unban <user_id>\n"
            "Или ответьте на сообщение пользователя."
        ); return

    db = load_db()
    db["banned"].pop(str(target), None)
    save_db(db)

    tg_err = await _tg_unban(context, update.effective_chat.id, target)
    p = get_player(target)
    text = f"✅ Бан снят с <b>{p.nickname}</b>"
    if tg_err:
        text += f"\n⚠️ Telegram: {tg_err}"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def win_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /win <номер_матча> <ct|t>
    ID <gameid> — <киллы> убийства — <смерти> смертей.
    ID <gameid> — <киллы> убийства — <смерти> смертей.
    ...

    Фиксирует результат матча: указанная в первой строке сторона считается
    победившей. Дальше — построчно, по одной строке на игрока, в формате:
        ID <GAME_ID> — <киллы> убийства/убийств — <смерти> смерть/смерти/смертей.
    Заголовки "КТ" / "Т" можно вставлять для читаемости — бот их игнорирует
    и просто ищет "ID <число>" и два числа в каждой строке. Порядок строк
    не важен, состав CT/T бот уже знает из самого матча.

    Всем реальным игрокам победившей стороны начисляется ELO за победу,
    проигравшей — списывается ELO за поражение (величина зависит от
    платформы каждого игрока — pc/mobile), обновляются общая и режимная
    (5v5/2v2) статистики побед/поражений, винрейт и средний KD.

    Доступно админам и модераторам.
    """
    if not is_moderator(update.effective_user.id): return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Формат:\n"
            "<code>/win 14 ct\n"
            "ID 6888 — 2 убийства — 8 смертей.\n"
            "ID 7842 — 2 убийства — 8 смертей.\n"
            "ID 7643 — 5 убийств — 2 смерти.\n"
            "ID 1175 — 2 убийства — 1 смерть.</code>",
            parse_mode=ParseMode.HTML
        ); return

    m_id = context.args[0]
    side = context.args[1].lower()
    if side not in ("ct", "t"):
        await update.message.reply_text(
            "Сторона должна быть <code>ct</code> или <code>t</code>.", parse_mode=ParseMode.HTML
        ); return

    db = load_db()
    m  = db.get("active_matches", {}).get(m_id)
    if not m:
        await update.message.reply_text(f"❌ Матч #{m_id} не найден или уже завершён.")
        return

    winners = m.get("ct", []) if side == "ct" else m.get("t", [])
    losers  = m.get("t", [])  if side == "ct" else m.get("ct", [])
    all_uids = [u for u in (winners + losers) if not _is_bot_uid(u)]

    # ── Парсим строки вида "ID 6888 — 2 убийства — 8 смертей." ─────────────
    # Берём текст сообщения целиком (не context.args!), чтобы сохранить
    # переносы строк, и ищем построчно "ID <число> ... <число> ... <число>".
    raw_text = update.message.text or ""
    body_lines = raw_text.split("\n")[1:]  # пропускаем первую строку (саму команду)

    line_re = re.compile(
        r"id\s*(\d+).*?(\d+)\s*убийств\w*.*?(\d+)\s*смерт\w*",
        re.IGNORECASE,
    )

    kd_by_gameid: Dict[str, tuple] = {}
    bad_lines: List[str] = []
    for raw_line in body_lines:
        line = raw_line.strip()
        if not line:
            continue
        match = line_re.search(line)
        if not match:
            # Строки-заголовки типа "КТ" / "Т" без ID — пропускаем молча
            if re.search(r"\bid\b", line, re.IGNORECASE):
                bad_lines.append(line)
            continue
        gid, k_str, d_str = match.groups()
        kd_by_gameid[gid] = (int(k_str), int(d_str))

    if bad_lines:
        await update.message.reply_text(
            "❌ Не получилось распознать строки:\n" + "\n".join(f"  • {x}" for x in bad_lines) +
            "\n\nФормат: <code>ID 6888 — 2 убийства — 8 смертей.</code>",
            parse_mode=ParseMode.HTML
        ); return

    if not kd_by_gameid:
        await update.message.reply_text(
            "❌ Не нашёл ни одной строки со статистикой.\n"
            "Формат:\n"
            "<code>ID 6888 — 2 убийства — 8 смертей.</code>",
            parse_mode=ParseMode.HTML
        ); return

    # Сопоставляем gameid → uid и проверяем, что все реальные игроки матча покрыты
    kd_by_uid: Dict[int, tuple] = {}
    missing_players: List[str] = []
    for uid in all_uids:
        p = get_player(uid)
        gid = p.external_id
        if gid and gid in kd_by_gameid:
            kd_by_uid[uid] = kd_by_gameid[gid]
        else:
            missing_players.append(f"{p.nickname} [ID {gid or '?'}]")

    if missing_players:
        await update.message.reply_text(
            "❌ Не указана статистика для:\n" + "\n".join(f"  • {x}" for x in missing_players) +
            "\n\nУкажи строку по каждому игроку матча.",
            parse_mode=ParseMode.HTML
        ); return

    unknown_gids = set(kd_by_gameid) - {get_player(u).external_id for u in all_uids}
    if unknown_gids:
        await update.message.reply_text(
            "⚠️ Эти ID не относятся к игрокам матча и были проигнорированы: "
            + ", ".join(f"<code>{g}</code>" for g in unknown_gids),
            parse_mode=ParseMode.HTML
        )

    win_lines, loss_lines, calib_notifications, mode, winners, losers = _finalize_match(
        db, m_id, m, side, kd_by_uid
    )
    await _send_calibration_dms(context.bot, calib_notifications)
    save_db(db)

    win_side_label  = "🔵 CT" if side == "ct" else "🔴 T"
    lose_side_label = "🔴 T"  if side == "ct" else "🔵 CT"

    text = (
        f"🏆 <b>Матч #{m_id} [{mode.upper()}] завершён!</b>\n\n"
        f"✅ Победила сторона: {win_side_label}\n"
        + ("\n".join(win_lines) if win_lines else "  (нет реальных игроков)") + "\n\n"
        f"❌ Проиграла сторона: {lose_side_label}\n"
        + ("\n".join(loss_lines) if loss_lines else "  (нет реальных игроков)")
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    # ── Если есть другие матчи, ожидающие подтверждения результата, —
    # сразу подсказываем об этом админу, закрывшему катку.
    remaining = db.get("pending_ocr", {})
    if remaining:
        try:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text=(
                    f"🧾 Есть ещё {len(remaining)} матч(ей), ожидающих подтверждения результата.\n"
                    f"Открой кнопку «🧾 Результаты матчей» в /start, чтобы их разобрать."
                ),
            )
        except Exception:
            pass


async def cancelwin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /cancelwin <номер_матча>
    Отменяет результат уже завершённого матча: возвращает ELO, статистику
    побед/поражений и KD к значениям ДО этой катки.
    Работает только если результат матча был сохранён через /win и матч
    находится в архиве (поле "finished_matches" в БД).
    Доступно модераторам, администраторам и создателю.
    """
    if not is_moderator(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "Формат: <code>/cancelwin &lt;номер_матча&gt;</code>\n"
            "Пример: <code>/cancelwin 14</code>\n\n"
            "⚠️ Команда отменяет результат матча и возвращает ELO всем участникам.",
            parse_mode=ParseMode.HTML
        )
        return

    m_id = context.args[0]
    db = load_db()
    finished = db.get("finished_matches", {})

    if m_id not in finished:
        await update.message.reply_text(
            f"❌ Матч <b>#{m_id}</b> не найден в архиве.\n"
            f"Убедитесь, что номер верный. Архив хранит только матчи, завершённые через /win.",
            parse_mode=ParseMode.HTML
        )
        return

    snapshot = finished[m_id]
    mode     = snapshot.get("mode", "5v5")
    winners  = snapshot.get("winners", [])
    losers   = snapshot.get("losers",  [])
    kd_by_uid_snap = snapshot.get("kd_by_uid", {})  # {str(uid): [kills, deaths]}

    elo_snapshot = snapshot.get("elo_snapshot", {})  # {str(uid): точная дельта из /win}

    lines = []

    def _revert(target_uid: int, won: bool) -> None:
        s = str(target_uid)
        if _is_bot_uid(target_uid):
            return
        pdata = db["players"].get(s)
        if not pdata:
            return

        snap = elo_snapshot.get(s)

        if isinstance(snap, dict):
            # Новый формат — просто восстанавливаем точные значения ЭЛО
            # «до матча», сохранённые в /win. Корректно откатывает как
            # обычные катки, так и матч, завершивший калибровку.
            elo_after  = snap.get("elo_after",  pdata.get("elo", 0))
            elo_before = snap.get("elo_before", pdata.get("elo", 0))
            pdata["elo"]         = elo_before
            pdata[f"elo_{mode}"] = snap.get("elo_mode_before", pdata.get(f"elo_{mode}", 0))
            delta_display = elo_after - elo_before
        else:
            # Старый формат (матчи, сыгранные до внедрения калибровки «с нуля»)
            if snap is not None:
                delta = snap
            else:
                platform      = pdata.get("platform", "pc")
                win_d, loss_d = elo_deltas_for(platform)
                delta = win_d if won else loss_d
            for field in ("elo", f"elo_{mode}"):
                old_val = pdata.get(field, 0)
                pdata[field] = max(ELO_MIN, old_val - delta) if won else (old_val + delta)
            delta_display = -delta if won else delta

        # Откат побед/поражений
        if won:
            pdata["wins"]           = max(0, pdata.get("wins", 0) - 1)
            pdata[f"wins_{mode}"]   = max(0, pdata.get(f"wins_{mode}", 0) - 1)
        else:
            pdata["losses"]         = max(0, pdata.get("losses", 0) - 1)
            pdata[f"losses_{mode}"] = max(0, pdata.get(f"losses_{mode}", 0) - 1)

        # Пересчёт винрейта
        w, l   = pdata.get("wins", 0), pdata.get("losses", 0)
        wm, lm = pdata.get(f"wins_{mode}", 0), pdata.get(f"losses_{mode}", 0)
        pdata["avg"]         = round(w / (w + l) * 100, 1) if (w + l) else 0.0
        pdata[f"avg_{mode}"] = round(wm / (wm + lm) * 100, 1) if (wm + lm) else 0.0

        # Откат KD
        kills, deaths = kd_by_uid_snap.get(s, [0, 0])
        pdata["total_kills"]  = max(0, pdata.get("total_kills", 0) - kills)
        pdata["total_deaths"] = max(0, pdata.get("total_deaths", 0) - deaths)

        nick = pdata.get("nickname", "?")
        sign = "+" if delta_display >= 0 else "-"
        lines.append(f"  • {nick}: {sign}{abs(delta_display)} ELO → <b>{pdata['elo']}</b>")

    for uid in winners:
        _revert(uid, True)
    for uid in losers:
        _revert(uid, False)

    # Удаляем из архива, восстанавливаем матч как активный для возможной переигровки
    del finished[m_id]
    db["finished_matches"] = finished
    save_db(db)

    text = (
        f"↩️ <b>Матч #{m_id} [{mode.upper()}] отменён!</b>\n\n"
        f"ELO и статистика возвращены:\n"
        + ("\n".join(lines) if lines else "  (нет реальных игроков)")
        + "\n\n✅ Можно провести матч заново через /win."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def rename_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /rename Новый_Ник — сменить ник игроку (ответом на его сообщение).
    Доступно модераторам, админам и создателю.
    """
    if not is_moderator(update.effective_user.id):
        return

    # Цель — только через ответ на сообщение
    if not (update.message.reply_to_message and
            update.message.reply_to_message.from_user and
            not update.message.reply_to_message.forum_topic_created and
            not update.message.reply_to_message.forum_topic_edited):
        await update.message.reply_text(
            "❌ Ответьте на сообщение игрока, которому хотите сменить ник.\n"
            "Формат: <code>/rename НовыйНик</code>",
            parse_mode=ParseMode.HTML
        )
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Укажите новый ник.\n"
            "Формат: <code>/rename НовыйНик</code>",
            parse_mode=ParseMode.HTML
        )
        return

    new_nick = " ".join(context.args)
    if len(new_nick) > 32:
        await update.message.reply_text("❌ Ник слишком длинный (максимум 32 символа).")
        return

    target = update.message.reply_to_message.from_user.id
    db = load_db()
    s  = str(target)

    if s not in db["players"] or not db["players"][s].get("external_id"):
        await update.message.reply_text("❌ Этот игрок не зарегистрирован.")
        return

    old_nick = db["players"][s].get("nickname", "?")
    db["players"][s]["nickname"] = new_nick
    save_db(db)

    await update.message.reply_text(
        f"✏️ Ник изменён:\n"
        f"<b>{old_nick}</b> → <b>{new_nick}</b>",
        parse_mode=ParseMode.HTML
    )
    try:
        await context.bot.send_message(
            chat_id=target,
            text=(
                f"✏️ <b>Администрация изменила ваш ник</b>\n\n"
                f"Было: <b>{old_nick}</b>\n"
                f"Стало: <b>{new_nick}</b>"
            ),
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass


async def changeid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /changeid НовыйID — сменить GAME ID игроку (ответом на его сообщение).
    Доступно модераторам, админам и создателю.
    """
    if not is_moderator(update.effective_user.id):
        return

    # Цель — только через ответ на сообщение
    if not (update.message.reply_to_message and
            update.message.reply_to_message.from_user and
            not update.message.reply_to_message.forum_topic_created and
            not update.message.reply_to_message.forum_topic_edited):
        await update.message.reply_text(
            "❌ Ответьте на сообщение игрока, которому хотите сменить GAME ID.\n"
            "Формат: <code>/changeid НовыйID</code>",
            parse_mode=ParseMode.HTML
        )
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Укажите новый GAME ID.\n"
            "Формат: <code>/changeid НовыйID</code>",
            parse_mode=ParseMode.HTML
        )
        return

    new_id = context.args[0]
    if not new_id.isdigit():
        await update.message.reply_text("❌ GAME ID должен содержать только цифры.")
        return

    target = update.message.reply_to_message.from_user.id
    db = load_db()
    s  = str(target)

    if s not in db["players"] or not db["players"][s].get("external_id"):
        await update.message.reply_text("❌ Этот игрок не зарегистрирован.")
        return

    # Проверяем что новый ID не занят другим игроком
    for uid_str, pdata in db["players"].items():
        if uid_str != s and pdata.get("external_id") == new_id and not pdata.get("is_bot"):
            await update.message.reply_text(
                f"❌ GAME ID <code>{new_id}</code> уже занят другим игроком.",
                parse_mode=ParseMode.HTML
            )
            return

    old_id = db["players"][s].get("external_id", "?")
    db["players"][s]["external_id"] = new_id
    save_db(db)

    nick = db["players"][s].get("nickname", "?")
    await update.message.reply_text(
        f"🆔 GAME ID изменён для <b>{nick}</b>:\n"
        f"<code>{old_id}</code> → <code>{new_id}</code>",
        parse_mode=ParseMode.HTML
    )
    try:
        await context.bot.send_message(
            chat_id=target,
            text=(
                f"🆔 <b>Администрация изменила ваш GAME ID</b>\n\n"
                f"Было: <code>{old_id}</code>\n"
                f"Стало: <code>{new_id}</code>"
            ),
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass


async def dropmatch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /dropmatch <номер_матча>
    Закрывает активный матч в «0» — без начисления и списания ELO, без записи
    в статистику. Используй когда катка не состоялась: хост сбросил лобби,
    игроки не собрались, матч завис на пике и т.п.
    Матч просто удаляется из списка активных, все участники остаются с тем
    же ELO что было до начала. Доступно модераторам и выше.
    """
    if not is_moderator(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "Формат: <code>/dropmatch &lt;номер_матча&gt;</code>\n"
            "Пример: <code>/dropmatch 14</code>\n\n"
            "⚠️ Матч закроется без начисления ELO и изменения статистики.\n"
            "Используй когда катка не состоялась (не собрались, хост сбросил лобби и т.п.).",
            parse_mode=ParseMode.HTML
        )
        return

    m_id = context.args[0]
    db   = load_db()
    m    = db.get("active_matches", {}).get(m_id)

    if not m:
        await update.message.reply_text(
            f"❌ Активный матч <b>#{m_id}</b> не найден.\n"
            f"Возможно он уже завершён или закрыт. Используй /matches чтобы увидеть актуальный список.",
            parse_mode=ParseMode.HTML
        )
        return

    mode     = m.get("mode", "5v5")
    ct_uids  = m.get("ct", [])
    t_uids   = m.get("t",  [])
    all_real = [u for u in (ct_uids + t_uids) if not _is_bot_uid(u)]

    # Отменяем фоновый таймер пика если он висит
    task = _pick_timer_tasks.pop(m_id, None)
    if task and not task.done():
        task.cancel()

    db["active_matches"].pop(m_id, None)
    save_db(db)

    # Собираем список игроков для отчёта
    player_lines = []
    for uid in all_real:
        p = get_player(uid)
        player_lines.append(f"  • {p.tg_link()} <code>[{p.external_id or '?'}]</code> — {p.elo} ELO (без изменений)")

    text = (
        f"🗑 <b>Матч #{m_id} [{mode.upper()}] закрыт в 0</b>\n\n"
        f"ELO и статистика <b>не изменены</b>.\n\n"
        f"👥 Участники:\n"
        + ("\n".join(player_lines) if player_lines else "  (нет реальных игроков)")
        + "\n\n<i>Если нужно отменить уже засчитанный матч — используй /cancelwin.</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def setelo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /setelo <user_id> <elo> — жёстко устанавливает ELO игроку.

    ВАЖНО: профиль (/stats) показывает unified_elo = max(elo, elo_5v5, elo_2v2),
    а топы/таблицы (/top, /elo) показывают elo_5v5 / elo_2v2 отдельно.
    Раньше команда меняла только общее поле "elo", из-за чего на экране
    ничего не менялось, если elo_5v5/elo_2v2 были выше нового значения.
    Теперь команда выставляет ОДНО И ТО ЖЕ значение сразу во все три поля
    (elo, elo_5v5, elo_2v2), поэтому изменение гарантированно видно везде.
    """
    if not is_admin(update.effective_user.id): return

    target = get_reply_target(update, context.args)
    if target is None:
        await update.message.reply_text(
            "Формат: /setelo <user_id> <elo>\n"
            "Или ответьте на сообщение и напишите /setelo <elo>"
        ); return

    args_offset = 0 if update.message.reply_to_message else 1
    if len(context.args) <= args_offset:
        await update.message.reply_text("Укажите новое ELO"); return
    try:
        new_elo = int(context.args[args_offset])
    except ValueError:
        await update.message.reply_text("ELO должно быть числом"); return

    db = load_db()
    s  = str(target)
    if s not in db["players"]:
        await update.message.reply_text("Игрок не найден"); return

    final_elo = max(ELO_MIN, new_elo)
    db["players"][s]["elo"]     = final_elo
    db["players"][s]["elo_5v5"] = final_elo
    db["players"][s]["elo_2v2"] = final_elo
    save_db(db)
    p = get_player(target)
    await update.message.reply_text(
        f"✅ ELO игрока <b>{p.nickname}</b> → <b>{final_elo}</b>\n"
        f"(обновлено общее, 5v5 и 2v2 ELO)",
        parse_mode=ParseMode.HTML
    )


async def elo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    db   = load_db()
    rows = []
    for d in db["players"].values():
        if not d.get("external_id") or d.get("is_bot"): continue
        try:
            for field, val in [("elo_5v5",0),("wins_5v5",0),("losses_5v5",0),("avg_5v5",0.0),
                               ("elo_2v2",0),("wins_2v2",0),("losses_2v2",0),("avg_2v2",0.0),
                               ("total_kills",0),("total_deaths",0),
                               ("wins",0),("losses",0),("avg",0.0),("elo",0),
                               ("platform","pc")]:
                d.setdefault(field, val)
            p     = _make_player(d)
            total = p.wins_5v5 + p.losses_5v5
            wr    = f"{p.avg_5v5:.1f}%" if total else "—"
            rows.append((p.nickname, p.external_id, p.elo_5v5, wr, total, p.lvl_icon_5v5()))
        except Exception:
            continue
    if not rows:
        await update.message.reply_text("Нет зарегистрированных игроков."); return

    rows.sort(key=lambda x: x[2], reverse=True)
    lines = ["📊 <b>ELO таблица (5v5)</b>\n━━━━━━━━━━━━━━"]
    for i, (nick, ext_id, elo, wr, games, icon) in enumerate(rows[:30], 1):
        lines.append(
            f"{i:2}. {icon} {nick} <code>[{ext_id}]</code>\n"
            f"    ELO: <b>{elo}</b> | WR: {wr} | Игр: {games}"
        )
    if len(rows) > 30:
        lines.append(f"\n... и ещё {len(rows)-30} игроков")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def clearqueue_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    db    = load_db()
    which = context.args[0].lower() if context.args else "all"
    keys  = (["queue_5v5"] if which == "5v5" else
             ["queue_2v2"] if which == "2v2" else
             ["queue_5v5","queue_2v2"])
    for q_key in keys:
        for uid in db.get(q_key, []):
            if uid < 0:
                db["players"].pop(str(uid), None)
        db[q_key] = []
    save_db(db)
    await update.message.reply_text(f"🗑 Очередь [{which}] очищена.")


async def matches_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    db      = load_db()
    matches = db.get("active_matches", {})
    if not matches:
        await update.message.reply_text("Нет активных матчей."); return
    lines = [f"📋 <b>Активные матчи ({len(matches)})</b>"]
    for m_id, m in matches.items():
        ct_n  = get_player(m["ct"][0]).nickname if m["ct"] else "?"
        t_n   = get_player(m["t"][0]).nickname  if m["t"]  else "?"
        lines.append(
            f"#{m_id} [{m.get('mode','?').upper()}] "
            f"{ct_n} vs {t_n} | {m.get('phase','?')} | пул: {len(m['pool'])}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def bots1_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Секретная. /bots1 — тест 5v5: ты + 9 ботов."""
    if not is_admin(update.effective_user.id): return
    db  = load_db()
    uid = update.effective_user.id
    players = [uid] + [_create_fake_bot(db) for _ in range(LOBBY_5V5_SIZE - 1)]
    save_db(db)
    await update.message.reply_text(
        f"🤖 Тестовый матч 5v5!\n👤 Реальных: 1 | 🤖 Ботов: {LOBBY_5V5_SIZE-1}"
    )
    await start_match(players, "5v5", db, context, update.message.chat_id, update.message.message_thread_id)


async def bots2_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Секретная. /bots2 — тест 2v2: ты + 3 бота."""
    if not is_admin(update.effective_user.id): return
    db  = load_db()
    uid = update.effective_user.id
    players = [uid] + [_create_fake_bot(db) for _ in range(LOBBY_2V2_SIZE - 1)]
    save_db(db)
    await update.message.reply_text(
        f"🤖 Тестовый матч 2v2!\n👤 Реальных: 1 | 🤖 Ботов: {LOBBY_2V2_SIZE-1}"
    )
    await start_match(players, "2v2", db, context, update.message.chat_id, update.message.message_thread_id)


async def unreg_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return

    target = get_reply_target(update, context.args)
    if target is None:
        await update.message.reply_text(
            "Формат: /unreg <user_id>\n"
            "Или ответьте на сообщение пользователя."
        ); return

    db = load_db()
    s  = str(target)

    if s not in db["players"] or not db["players"][s].get("external_id"):
        await update.message.reply_text("❌ Этот пользователь не зарегистрирован."); return

    nick = db["players"][s].get("nickname", "?")

    for q_key in ("queue_5v5", "queue_2v2"):
        if target in db.get(q_key, []):
            db[q_key].remove(target)

    # Полный сброс — при отмене регистрации стираются АБСОЛЮТНО все данные
    # игрока (ЭЛО, победы/поражения, K/D, платформа, дата регистрации и т.д.),
    # а не только ник и ID. Профиль в БД удаляется целиком: если игрок
    # зарегистрируется заново, он начнёт с чистого листа, как новый игрок.
    del db["players"][s]
    save_db(db)

    await update.message.reply_text(
        f"✅ Регистрация игрока <b>{nick}</b> сброшена.\n"
        f"Теперь он может зарегистрироваться заново через /reg",
        parse_mode=ParseMode.HTML
    )


# ════════════════════════════════════════════════
#                   ВЕБ API
# ════════════════════════════════════════════════

def _normalize_player(d: dict) -> dict:
    """Нормализует поля игрока — заполняет дефолтами."""
    return {
        "user_id":      d.get("user_id", 0),
        "nickname":     d.get("nickname", "?"),
        "external_id":  d.get("external_id", ""),
        "elo":          d.get("elo", 0),
        "elo_5v5":      d.get("elo_5v5", d.get("elo", 0)),
        "elo_2v2":      d.get("elo_2v2", d.get("elo", 0)),
        "wins":         d.get("wins", 0),
        "losses":       d.get("losses", 0),
        "wins_5v5":     d.get("wins_5v5", 0),
        "losses_5v5":   d.get("losses_5v5", 0),
        "wins_2v2":     d.get("wins_2v2", 0),
        "losses_2v2":   d.get("losses_2v2", 0),
        "avg":          d.get("avg", 0.0),
        "avg_5v5":      d.get("avg_5v5", 0.0),
        "avg_2v2":      d.get("avg_2v2", 0.0),
        "total_kills":  d.get("total_kills", 0),
        "total_deaths": d.get("total_deaths", 0),
        "platform":     d.get("platform", "pc"),
        "is_bot":       d.get("is_bot", False),
    }

CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}

# ════════════════════════════════════════════════
#              ДЕТЕКТОР КОДА ЛОББИ
# ════════════════════════════════════════════════

def _detect_lobby_code(text: str) -> Optional[str]:
    """
    Определяет код лобби в сообщении.
    Коды состоят ровно из 6 цифр.
    """
    # Ищем ровно 6 цифр подряд (не часть большего числа)
    m = re.search(r'(?<![\d])(\d{6})(?![\d])', text)
    if m:
        return m.group(1)
    return None


def _find_match_for_host(uid: int, db: Dict[str, Any]) -> Optional[tuple]:
    """
    Ищет активный матч, где uid является хостом (host_uid).
    Возвращает (match_id, match_data) или None.
    """
    for m_id, m in db.get("active_matches", {}).items():
        if m.get("host_uid") == uid:
            return (m_id, m)
    return None


async def _send_lobby_code_notification(
    bot,
    chat_id: int,
    thread_id: Optional[int],
    m_id: str,
    match: Dict[str, Any],
    host_player,
    lobby_code: str,
):
    """Отправляет уведомление со всеми тегами игроков и кодом лобби (1 клик — скопировать)."""
    all_uids = match.get("ct", []) + match.get("t", [])

    # Собираем теги всех живых (не-бот) игроков
    mentions = []
    for u in all_uids:
        if not _is_bot_uid(u):
            p = get_player(u)
            mentions.append(f'<a href="tg://user?id={u}">{p.nickname}</a>')

    mentions_str = " ".join(mentions) if mentions else "Все игроки"
    mode = match.get("mode", "").upper()

    text = (
        f"🔑 <b>Хост скинул код лобби!</b>\n"
        f"🎮 Матч #{m_id} [{mode}]\n\n"
        f"📢 {mentions_str}\n\n"
        f"<code>{lobby_code}</code>"
    )

    try:
        await bot.send_message(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )
        print(f"[lobby_code] Матч #{m_id} | хост={host_player.user_id} | код={lobby_code}")
    except Exception as e:
        print(f"[lobby_code] Ошибка отправки: {e}")


async def global_ban_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Глобальный фильтр бана — регистрируется с group=-2, то есть срабатывает
    РАНЬШЕ вообще всех остальных обработчиков (раньше мута, раньше команд) и
    ловит АБСОЛЮТНО ЛЮБОЙ тип сообщения от забаненного пользователя (текст,
    фото, стикеры, голосовые, видео и т.д.), а не только текст.

    После удаления сообщения дальнейшая обработка update'а полностью
    останавливается (ApplicationHandlerStop) — забаненный не может
    выполнить вообще ни одну команду бота и фактически исключён из беседы
    (плюс настоящий кик через ban_chat_member в самой команде /ban).
    """
    msg  = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if is_admin(user.id):
        return
    if not check_banned(user.id):
        return

    try:
        await msg.delete()
    except Exception as e:
        # Чаще всего сюда попадаем, если у бота нет прав администратора
        # в группе (нет права "Удаление сообщений") — без них бан не
        # сможет удалять чужие сообщения физически.
        print(f"[ban] не удалось удалить сообщение uid={user.id}: {e}")

    raise ApplicationHandlerStop


async def global_mute_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Глобальный фильтр мута — регистрируется с group=-1 (сразу после фильтра
    бана). Полностью запрещает замьюченному пользователю что-либо делать:

    • В группе — удаляет ЛЮБОЕ его сообщение (текст, фото, стикер, голосовое
      и т.д.) и останавливает дальнейшую обработку update'а, так что ни одна
      команда бота (/win, /5v5, /stats и т.д.) не выполнится. Это подстраховка
      на случай, если у бота нет прав ограничивать участников — основное же
      ограничение накладывается на уровне Telegram через restrict_chat_member
      в /mute, что физически не даёт пользователю отправить сообщение.
    • В личных сообщениях с ботом — отвечает, что пользователь в муте, и
      также останавливает обработку, чтобы ни одна команда боту в ЛС не
      прошла.
    """
    msg  = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if is_admin(user.id):
        return
    if not check_muted(user.id):
        return

    if msg.chat.type in ("group", "supergroup"):
        try:
            await msg.delete()
        except Exception as e:
            print(f"[mute] не удалось удалить сообщение uid={user.id}: {e}")
    else:
        until = db_mute_until(user.id)
        left  = max(0, int(until - datetime.now().timestamp()))
        mins, secs = divmod(left, 60)
        try:
            await msg.reply_text(f"🔇 Вы в муте ещё {mins} мин. {secs} сек. — бот вас не слушает.")
        except Exception:
            pass

    raise ApplicationHandlerStop


async def message_filter_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    if msg.chat.type not in ("group", "supergroup"):
        return

    uid  = msg.from_user.id
    name = msg.from_user.first_name or "Игрок"

    if is_moderator(uid):
        # Модераторы не проходят фильтр оскорблений, но код лобби всё равно ловим
        lobby_code = _detect_lobby_code(msg.text)
        if lobby_code:
            db = load_db()
            match_info = _find_match_for_host(uid, db)
            if match_info:
                m_id, match = match_info
                host_p = get_player(uid)
                await _send_lobby_code_notification(
                    context.bot,
                    msg.chat_id,
                    msg.message_thread_id,
                    m_id, match, host_p, lobby_code
                )
        return

    # ── 1. БАН ─────────────────────────────────────────────────────────────
    if check_banned(uid):
        try:
            await msg.delete()
        except Exception as e:
            print(f"[ban] не удалось удалить сообщение uid={uid}: {e}")
        # Подстраховка: если по какой-то причине Telegram-бан не сработал
        # (бот не был админом в момент /ban и т.п.) — пробуем забанить сейчас.
        try:
            until = db_ban_until(uid)
            await context.bot.ban_chat_member(
                chat_id=msg.chat_id,
                user_id=uid,
                until_date=None if until == 9_999_999_999 else datetime.fromtimestamp(until),
            )
        except Exception as e:
            print(f"[ban] не удалось забанить в Telegram uid={uid}: {e}")
        return

    # ── 1.5. ДЕТЕКТОР КОДА ЛОББИ ────────────────────────────────────────────
    lobby_code = _detect_lobby_code(msg.text)
    if lobby_code:
        db = load_db()
        match_info = _find_match_for_host(uid, db)
        if match_info:
            m_id, match = match_info
            host_p = get_player(uid)
            await _send_lobby_code_notification(
                context.bot,
                msg.chat_id,
                msg.message_thread_id,
                m_id, match, host_p, lobby_code
            )




async def handle_options(request):
    return web.Response(headers=CORS_HEADERS)

async def api_top(request):
    """GET /api/top?mode=5v5"""
    mode = request.rel_url.query.get("mode", "5v5")
    if mode not in ("5v5", "2v2"):
        mode = "5v5"
    db = load_db()
    players = []
    for d in db["players"].values():
        if not d.get("external_id"):
            continue
        p = _normalize_player(d)
        players.append({
            "nickname":    p["nickname"],
            "external_id": p["external_id"],
            "elo":         p[f"elo_{mode}"],
            "wins":        p[f"wins_{mode}"],
            "losses":      p[f"losses_{mode}"],
            "is_bot":      p["is_bot"],
        })
    players.sort(key=lambda x: x["elo"], reverse=True)
    return web.json_response(players[:30], headers=CORS_HEADERS)

async def api_players(request):
    """GET /api/players"""
    db = load_db()
    result = []
    for d in db["players"].values():
        if not d.get("external_id") or d.get("is_bot"):
            continue
        result.append(_normalize_player(d))
    result.sort(key=lambda x: x["elo"], reverse=True)
    return web.json_response(result, headers=CORS_HEADERS)

async def api_player(request):
    """GET /api/player/{ext_id}"""
    ext_id = request.match_info.get("ext_id", "")
    db = load_db()
    for d in db["players"].values():
        if str(d.get("external_id", "")) == ext_id:
            return web.json_response(_normalize_player(d), headers=CORS_HEADERS)
    return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)

async def api_stats(request):
    """GET /api/stats"""
    db = load_db()
    real = [d for d in db["players"].values()
            if d.get("external_id") and not d.get("is_bot")]
    return web.json_response({
        "total_players":  len(real),
        "total_matches":  db.get("match_counter", 0),
        "active_matches": len(db.get("active_matches", {})),
    }, headers=CORS_HEADERS)

async def api_player_by_tg(request):
    """GET /api/player_by_tg/{tg_id} — поиск игрока по Telegram user_id (для Mini App)."""
    tg_id_raw = request.match_info.get("tg_id", "")
    db = load_db()
    try:
        tg_id = int(tg_id_raw)
    except ValueError:
        return web.json_response({"error": "bad id"}, status=400, headers=CORS_HEADERS)
    d = db["players"].get(str(tg_id))
    if not d:
        return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)
    p = _normalize_player(d)
    all_real = [pl for pl in db["players"].values() if pl.get("external_id") and not pl.get("is_bot")]
    all_real.sort(key=lambda x: x.get("elo", 0), reverse=True)
    rank = 0
    for i, pl in enumerate(all_real, start=1):
        if str(pl.get("user_id")) == str(tg_id):
            rank = i
            break
    p["rank"] = rank
    p["total_players"] = len(all_real)
    return web.json_response(p, headers=CORS_HEADERS)

async def api_match_history(request):
    """GET /api/match_history/{tg_id} — последние завершённые матчи игрока (для Mini App)."""
    tg_id_raw = request.match_info.get("tg_id", "")
    db = load_db()
    try:
        tg_id = int(tg_id_raw)
    except ValueError:
        return web.json_response({"error": "bad id"}, status=400, headers=CORS_HEADERS)

    finished = db.get("finished_matches", {})
    players  = db.get("players", {})

    def _nick(uid) -> str:
        d = players.get(str(uid))
        return d.get("nickname", "?") if d else "?"

    rows = []
    for m_id, m in finished.items():
        winners = m.get("winners", [])
        losers  = m.get("losers", [])
        if tg_id not in winners and tg_id not in losers:
            continue
        won = tg_id in winners
        kd  = m.get("kd_by_uid", {}).get(str(tg_id), [0, 0])
        raw_snap = m.get("elo_snapshot", {}).get(str(tg_id))
        if isinstance(raw_snap, dict):
            # Новый формат — знак и величина уже учитывают калибровку
            # (0 во время калибровки, скачок на матче, где она завершилась).
            elo_delta = raw_snap.get("elo_after", 0) - raw_snap.get("elo_before", 0)
        elif raw_snap is not None:
            elo_delta = raw_snap if won else -raw_snap
        else:
            # Старые матчи (сыграны до того, как стал сохраняться elo_snapshot)
            # не хранят точную применённую дельту — считаем её по текущей
            # формуле начисления ELO, вместо того чтобы показывать +0 ELO.
            platform_fallback = players.get(str(tg_id), {}).get("platform", "pc")
            win_d_fb, loss_d_fb = elo_deltas_for(platform_fallback)
            elo_delta = win_d_fb if won else -loss_d_fb
        rows.append({
            "match_id":    m_id,
            "mode":        m.get("mode", "5v5"),
            "map":         m.get("map"),
            "won":         won,
            "kills":       kd[0] if len(kd) > 0 else 0,
            "deaths":      kd[1] if len(kd) > 1 else 0,
            "elo_delta":   elo_delta,
            "finished_ts": m.get("finished_ts", 0),
            "teammates":   [_nick(u) for u in (winners if won else losers) if u != tg_id],
            "opponents":   [_nick(u) for u in (losers if won else winners)],
        })

    rows.sort(key=lambda r: r["finished_ts"], reverse=True)
    return web.json_response(rows[:20], headers=CORS_HEADERS)

async def api_match_details(request):
    """
    GET /api/match_details/{match_id}
    Отдаёт полный состав обеих команд завершённого матча с никами и
    статистикой (киллы/смерти) каждого игрока — используется во
    всплывающей карточке истории матчей в веб-приложении.
    """
    m_id = request.match_info.get("match_id", "")
    db = load_db()
    finished = db.get("finished_matches", {})
    m = finished.get(m_id)
    if not m:
        return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)

    players   = db.get("players", {})
    kd_by_uid = m.get("kd_by_uid", {})

    def _team(uids: List[int], won: bool) -> Dict[str, Any]:
        roster = []
        for uid in uids:
            pdata = players.get(str(uid), {})
            kd = kd_by_uid.get(str(uid), [0, 0])
            roster.append({
                "nickname":    pdata.get("nickname", "?"),
                "external_id": pdata.get("external_id", ""),
                "kills":       kd[0] if len(kd) > 0 else 0,
                "deaths":      kd[1] if len(kd) > 1 else 0,
            })
        return {
            "name":    "Победители" if won else "Проигравшие",
            "won":     won,
            "players": roster,
        }

    data = {
        "match_id":    m_id,
        "mode":        m.get("mode", "5v5"),
        "map":         m.get("map"),
        "finished_ts": m.get("finished_ts", 0),
        "teams": [
            _team(m.get("winners", []), True),
            _team(m.get("losers", []),  False),
        ],
    }
    return web.json_response(data, headers=CORS_HEADERS)

async def api_health(request):
    return web.json_response({"status": "ok"}, headers=CORS_HEADERS)

async def serve_webapp(request):
    """Отдаёт webapp.html по корневому URL /"""
    import pathlib
    html_path = pathlib.Path(__file__).parent / "webapp.html"
    if html_path.exists():
        return web.FileResponse(html_path)
    return web.Response(text="webapp.html not found", status=404)

async def start_web_server():
    app = web.Application()
    app.router.add_route("OPTIONS", "/{path_info:.*}", handle_options)
    app.router.add_get("/",                    serve_webapp)
    app.router.add_get("/api/top",             api_top)
    app.router.add_get("/api/players",         api_players)
    app.router.add_get("/api/player/{ext_id}", api_player)
    app.router.add_get("/api/player_by_tg/{tg_id}", api_player_by_tg)
    app.router.add_get("/api/match_history/{tg_id}", api_match_history)
    app.router.add_get("/api/match_details/{match_id}", api_match_details)
    app.router.add_get("/api/stats",           api_stats)
    app.router.add_get("/health",              api_health)
    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Сервер запущен на порту {port}")

# ════════════════════════════════════════════════
#              МЕНЮ КОМАНД
# ════════════════════════════════════════════════

async def _reschedule_punishment_expiries(bot) -> None:
    """
    Вызывается при старте бота. На Railway процесс может перезапускаться
    (редеплой), а все asyncio-таймеры (_schedule_mute_expiry/_schedule_ban_expiry)
    при этом теряются. Тут заново ставим таймер на каждый активный мут/бан из БД —
    если срок уже прошёл, пока бот был офлайн, уведомление улетит сразу.
    """
    db = load_db()
    for uid_s, until in list(db.get("muted", {}).items()):
        try:
            target = int(uid_s)
        except ValueError:
            continue
        asyncio.create_task(_schedule_mute_expiry(bot, target, float(until)))
    for uid_s, until in list(db.get("banned", {}).items()):
        try:
            target = int(uid_s)
        except ValueError:
            continue
        asyncio.create_task(_schedule_ban_expiry(bot, target, float(until)))


async def set_commands(app: Application):
    global _app_ref
    _app_ref = app
    await _restore_db_from_telegram(app.bot)
    await _reschedule_punishment_expiries(app.bot)

    # ── ЛС: в списке команд (по "/") виден только /start — всё остальное
    # делается через кнопки меню.
    await app.bot.set_my_commands(
        [BotCommand("start", "Главное меню")],
        scope=BotCommandScopeAllPrivateChats()
    )

    # ── Беседа/группы: полный список команд, как раньше.
    group_commands = [
        BotCommand("start",   "Главное меню"),
        BotCommand("reg",     "Регистрация"),
        BotCommand("platform","Выбор платформы ПК/мобила"),
        BotCommand("5v5",    "Лобби 5v5"),
        BotCommand("2v2",    "Лобби 2v2"),
        BotCommand("stats",   "Мой профиль"),
        BotCommand("top",     "Топ игроков"),
        BotCommand("ticket",  "Написать в поддержку"),
        BotCommand("admins",  "Команды по ролям"),
        BotCommand("rules",   "Правила чата"),
    ]
    await app.bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())
    # Дефолтный scope — на случай супергрупп/каналов, не покрытых явными scope'ами.
    await app.bot.set_my_commands(group_commands, scope=BotCommandScopeDefault())

    if WEBAPP_URL:
        await app.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="🌙 Night Faceit",
                web_app=WebAppInfo(url=WEBAPP_URL)
            )
        )
        print(f"✅ Кнопка WebApp установлена: {WEBAPP_URL}")

# ════════════════════════════════════════════════
#                    ЗАПУСК
# ════════════════════════════════════════════════

async def run_bot():
    """Запуск через asyncio.run() — совместим с Python 3.13 + PTB 21.x"""
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",  start_cmd))
    app.add_handler(CommandHandler("reg",    reg_cmd))
    app.add_handler(CommandHandler("platform", platform_cmd))
    app.add_handler(CommandHandler("stats",  stats_cmd))
    app.add_handler(CommandHandler("top",    top_cmd))
    app.add_handler(CommandHandler("5v5",   play5_cmd))
    app.add_handler(CommandHandler("2v2",   play2_cmd))
    app.add_handler(CommandHandler("admins", admins_cmd))
    app.add_handler(CommandHandler("rules",  rules_cmd))

    app.add_handler(CommandHandler("win",        win_cmd))
    app.add_handler(CommandHandler("cancelwin",  cancelwin_cmd))
    app.add_handler(CommandHandler("dropmatch",  dropmatch_cmd))
    app.add_handler(CommandHandler("mute",       mute_cmd))
    app.add_handler(CommandHandler("unmute",     unmute_cmd))
    app.add_handler(CommandHandler("ban",        ban_cmd))
    app.add_handler(CommandHandler("unban",      unban_cmd))
    app.add_handler(CommandHandler("elo",        elo_cmd))
    app.add_handler(CommandHandler("setelo",     setelo_cmd))
    app.add_handler(CommandHandler("rename",     rename_cmd))
    app.add_handler(CommandHandler("changeid",   changeid_cmd))
    app.add_handler(CommandHandler("clearqueue", clearqueue_cmd))
    app.add_handler(CommandHandler("matches",    matches_cmd))
    app.add_handler(CommandHandler("bots1",      bots1_cmd))
    app.add_handler(CommandHandler("bots2",      bots2_cmd))
    app.add_handler(CommandHandler("unreg",      unreg_cmd))
    app.add_handler(CommandHandler("listdb",     listdb_cmd))
    app.add_handler(CommandHandler("addmod",     addmod_cmd))
    app.add_handler(CommandHandler("removemod",  removemod_cmd))
    app.add_handler(CommandHandler("addadm",     addadm_cmd))
    app.add_handler(CommandHandler("removeadm",  removeadm_cmd))
    app.add_handler(CommandHandler("resetdb",    resetdb_cmd))
    app.add_handler(CommandHandler("newseason",  newseason_cmd))

    app.add_handler(CommandHandler("ticket",      ticket_cmd))
    app.add_handler(CommandHandler("reply",       reply_cmd))
    app.add_handler(CommandHandler("closeticket", closeticket_cmd))
    app.add_handler(CommandHandler("tickets",     tickets_list_cmd))

    app.add_handler(CallbackQueryHandler(callback_handler))

    # Глобальные фильтры наказаний — срабатывают РАНЬШЕ команд и обычного
    # фильтра сообщений, ловят вообще любой тип апдейта (текст/фото/стикеры/
    # голосовые и т.д.). group=-2 (бан) проверяется раньше group=-1 (мут).
    _punish_filter = filters.TEXT | filters.PHOTO | filters.COMMAND | filters.Sticker.ALL | filters.VOICE | filters.VIDEO
    app.add_handler(MessageHandler(_punish_filter & filters.ChatType.GROUPS, global_ban_filter),  group=-2)
    app.add_handler(MessageHandler(_punish_filter & filters.ChatType.GROUPS, global_mute_filter), group=-1)

    # Фильтр сообщений группы: удаление у забаненных + детектор оскорблений
    # (только групповые чаты — личка сюда не попадает, см. ChatType.GROUPS).
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.GROUPS, scoreboard_photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, message_filter_handler))

    # Обработчик кнопочной регистрации в ЛС: ловит "ID Никнейм" после выбора
    # платформы, до того как сообщение попадёт в транслятор тикетов.
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
        reg_dm_text_handler,
    ), group=-1)

    # Скрины результатов матчей, присланные игроками в ЛС боту — до
    # транслятора тикетов, чтобы такие фото не улетали в тему поддержки.
    app.add_handler(MessageHandler(
        filters.PHOTO & filters.ChatType.PRIVATE,
        result_dm_photo_handler,
    ), group=-1)

    # Транслятор тикетов: НЕ-командные текст/фото в личке боту —
    # пересылаются в тему "Тикеты" админ-конфы, если у игрока открыт тикет.
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO) & filters.ChatType.PRIVATE & ~filters.COMMAND,
        ticket_dm_forward_handler,
    ))

    print("🌙 Night Faceit запускается... [v9 - Night Edition]")

    async with app:
        await set_commands(app)
        await start_web_server()
        await app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            poll_interval=0.5,
            timeout=10,
        )
        await app.start()
        print("✅ Бот запущен. Нажмите Ctrl+C для остановки.")
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            print("🛑 Остановка бота...")
            await app.updater.stop()
            await app.stop()

    print("✅ Бот остановлен.")


def main():
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
