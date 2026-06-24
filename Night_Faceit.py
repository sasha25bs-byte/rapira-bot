import json
import os
import re
import asyncio
import random
import time
import io
import math
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

import aiohttp
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, MenuButtonWebApp, WebAppInfo, ChatPermissions
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

BOT_TOKEN = "8771277676:AAER0fIck_J_YBAzW4Or9vtrFI7We6rQICk"

# ── РОЛИ ──────────────────────────────────────────
# Создатель — доступ ко всем командам
CREATOR_ID       = 7979653269
# Админы — бан, мут, выдача эло за катки, создание матчей
ADMIN_IDS        = [CREATOR_ID]
# Модераторы — только мут и выдача каток (/win)
MODERATOR_IDS: list = []   # добавляй сюда ID модераторов

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
INSULT_WORDS = [
    "пидор", "пидар", "пидрила", "пидорас", "пидараст",
    "хуеплет", "хуесос", "еблан", "мудак", "мудила",
    "шлюха", "шалава", "давалка", "блядь",
    "ублюдок", "мразь", "тварь", "чмо", "залупа",
]

PROFANITY_WORDS = [
    "нахуй", "нахер", "твою мать", "твою маму",
    "маму твою", "маму ебал", "мать ебал",
    "ёб твою", "еб твою", "пошел нахуй", "иди нахуй",
]


def is_creator(uid: int) -> bool:
    return uid == CREATOR_ID

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS or is_creator(uid)

def is_moderator(uid: int) -> bool:
    return uid in MODERATOR_IDS or is_admin(uid)

MAPS_LIST      = ["Seaside"]
LOBBY_5V5_SIZE = 10
LOBBY_2V2_SIZE = 4
PICK_TIMEOUT   = 90
BAN_TIMEOUT    = 90

ELO_WIN_PC       = 15
ELO_LOSS_PC      = 30
ELO_WIN_MOBILE   = 25
ELO_LOSS_MOBILE  = 20
ELO_MIN      = 100
BOT_ID_START = -100000


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
    elo:           int   = 1000
    elo_5v5:       int   = 1000
    elo_2v2:       int   = 1000
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

    def lvl_icon(self) -> str:
        return self._lvl_for(self.elo)

    def lvl_icon_5v5(self) -> str:
        return self._lvl_for(self.elo)

    def lvl_icon_2v2(self) -> str:
        return self._lvl_for(self.elo)

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


def get_player(uid: int, name: str = "Player") -> Player:
    db = load_db()
    s  = str(uid)
    if s not in db["players"]:
        db["players"][s] = asdict(Player(uid, name))
        save_db(db)
    d = db["players"][s]
    for field, val in [("wins",0),("losses",0),("avg",0.0),
                       ("elo",1000),("elo_5v5",1000),("elo_2v2",1000),
                       ("wins_5v5",0),("losses_5v5",0),
                       ("wins_2v2",0),("losses_2v2",0),
                       ("avg_5v5",0.0),("avg_2v2",0.0),
                       ("external_id",""),("is_bot",False),
                       ("total_kills",0),("total_deaths",0)]:
        d.setdefault(field, val)
    return Player(**d)

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
        f"╔══════════════════════╗",
        f"║  {emoji}  <b>ЛОББИ {mode.upper()}</b>  {emoji}  ║",
        f"╚══════════════════════╝",
        f"",
        f"👥 Игроков: <b>{filled}/{size}</b>  •  <b>{pct}%</b>",
        f"<code>[{bar}]</code>",
        f"",
    ]

    medals = ["🥇", "🥈", "🥉"]

    if queue:
        lines.append("┌─ <b>Игроки в очереди</b> ──────")
        for i, uid in enumerate(queue, 1):
            p   = get_player(uid)
            num = medals[i - 1] if i <= 3 else f"<b>{i}.</b>"
            lines.append(
                f"│ {num} {p.lvl_icon()} {p.tg_link()}\n"
                f"│    <code>[{p.external_id or '???'}]</code>  ·  <b>{p.elo}</b> ELO"
            )
        lines.append("└───────────────────────────")
    else:
        lines.append("┌───────────────────────────")
        lines.append("│  <i>Очередь пока пуста...</i>")
        lines.append("│  <i>Нажми кнопку и заходи! 👇</i>")
        lines.append("└───────────────────────────")

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
            # Пик завершён — карта одна (Seaside), бан не нужен
            task = _pick_timer_tasks.pop(m_id, None)
            if task:
                task.cancel()
            host_uid  = m.get("host_uid", ct_cap)
            host_p    = get_player(host_uid)
            host_side = "🔵 CT" if host_uid == ct_cap else "🔴 T"
            final_map = m["maps"][0] if m["maps"] else "Seaside"
            m["phase"] = "done"
            save_db(db)
            try:
                await context.bot.send_message(
                    chat_id=chat_id, message_thread_id=thread_id,
                    text=f"🤖 <b>{bot_p.nickname}</b> выбрал <b>{get_player(chosen).nickname}</b>",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
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

    txt = (
        f"🆕 <b>Матч #{m_id} [{mode.upper()}]</b>\n\n"
        f"🔵 CT капитан: {ct_p.tg_link()} <code>[{ct_p.external_id or '?'}]</code>\n"
        f"🔴 T  капитан: {t_p.tg_link()} <code>[{t_p.external_id or '?'}]</code>\n\n"
        f"🖥 Создает лобби: {host_p.tg_link()} ({host_side})\n📨 Не забудь отправить в чат код от лобби\n\n"
        f"🗺 Карта: <b>{MAPS_LIST[0]}</b>\n\n"
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

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start — приветствие"""
    uid  = update.effective_user.id
    name = update.effective_user.first_name or "игрок"
    db   = load_db()
    s    = str(uid)
    reg  = s in db["players"] and db["players"][s].get("external_id")

    keyboard = []
    if WEBAPP_URL:
        keyboard.append([InlineKeyboardButton(
            "🌐 Открыть Night Faceit Stats",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )])
    keyboard.append([
        InlineKeyboardButton("📊 Мой профиль", callback_data="cmd_stats"),
        InlineKeyboardButton("🏆 Топ",         callback_data="cmd_top"),
    ])
    if not reg:
        keyboard.append([InlineKeyboardButton("📝 Регистрация", callback_data="cmd_reg")])

    text = (
        f"👋 <b>Привет, {name}!</b>\n\n"
        f"🌙 <b>Night Faceit</b> — твоя персональная лига\n\n"
        f"{'✅ Ты зарегистрирован' if reg else '❌ Ты не зарегистрирован'}\n\n"
        f"📝 <b>Регистрация:</b> <code>/reg GAME_ID Никнейм pc/mobile</code>\n"
        f"   Пример: <code>/reg 6888 Londyyy pc</code>\n\n"
        f"🎮 <b>Лобби</b> создаётся <b>только в беседе</b> — в ЛС не работает!\n\n"
        f"<b>Команды:</b>\n"
        f"/reg — Регистрация\n"
        f"/5v5 — Лобби 5v5\n"
        f"/2v2 — Лобби 2v2\n"
        f"/stats — Твоя статистика\n"
        f"/top — Топ игроков\n"
        f"/admins — Список команд по ролям"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def reg_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        f"━━━━━━━━━━━━━━━━━━━━━\n"
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


async def _send_profile_card(bot, chat_id: int, target_uid: int,
                             reply_to_message_id: int = None,
                             message_thread_id: int = None,
                             caption_extra: str = "") -> None:
    """
    Вспомогательная функция — генерирует и отправляет карточку профиля.
    Используется и в /stats, и в автообновлении после матча.
    """
    db = load_db()
    s  = str(target_uid)
    if s not in db["players"] or not db["players"][s].get("external_id"):
        return

    d = db["players"][s]
    for field, val in [
        ("wins", 0), ("losses", 0), ("avg", 0.0), ("elo", 1000),
        ("elo_5v5", 1000), ("elo_2v2", 1000),
        ("wins_5v5", 0), ("losses_5v5", 0),
        ("wins_2v2", 0), ("losses_2v2", 0),
        ("avg_5v5", 0.0), ("avg_2v2", 0.0),
        ("total_kills", 0), ("total_deaths", 0),
        ("external_id", ""), ("is_bot", False),
        ("nickname", "?"), ("user_id", target_uid),
    ]:
        d.setdefault(field, val)

    p = Player(**d)
    if p.is_bot:
        return

    # Скачиваем аватарку
    avatar_bytes = None
    try:
        photos = await bot.get_user_profile_photos(target_uid, limit=1)
        if photos.total_count > 0:
            file = await bot.get_file(photos.photos[0][-1].file_id)
            av_buf = io.BytesIO()
            await file.download_to_memory(av_buf)
            avatar_bytes = av_buf.getvalue()
    except Exception as e:
        print(f"[card] аватарка uid={target_uid}: {e}")

    try:
        card_bytes = await asyncio.get_event_loop().run_in_executor(
            None, _generate_profile_card, p, avatar_bytes
        )
    except Exception as e:
        print(f"[card] генерация uid={target_uid}: {e}")
        return

    caption = f"🌙 <b>{p.nickname}</b>  •  {p.elo} ELO"
    if caption_extra:
        caption += f"\n{caption_extra}"

    kwargs = dict(
        chat_id=chat_id,
        photo=io.BytesIO(card_bytes),
        caption=caption,
        parse_mode=ParseMode.HTML,
    )
    if reply_to_message_id:
        kwargs["reply_to_message_id"] = reply_to_message_id
    if message_thread_id:
        kwargs["message_thread_id"] = message_thread_id

    await bot.send_photo(**kwargs)


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    target = uid

    if (update.message.reply_to_message and
            update.message.reply_to_message.from_user and
            update.message.reply_to_message.from_user.id != uid and
            not update.message.reply_to_message.forum_topic_created and
            not update.message.reply_to_message.forum_topic_edited):
        target = update.message.reply_to_message.from_user.id
    elif context.args:
        try:
            target = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Формат: /stats [user_id]")
            return

    db = load_db()
    s  = str(target)

    if s not in db["players"] or not db["players"][s].get("external_id"):
        if target == uid:
            await update.message.reply_text(
                "❌ Вы не зарегистрированы!\n\nДля регистрации:\n<code>/reg GAME_ID Никнейм</code>",
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text("❌ Этот пользователь не зарегистрирован.")
        return

    d = db["players"][s]
    if d.get("is_bot"):
        await update.message.reply_text("🤖 Это тестовый бот — нет статистики.")
        return

    thread_id = getattr(update.message, "message_thread_id", None)
    await _send_profile_card(
        bot=context.bot,
        chat_id=update.effective_chat.id,
        target_uid=target,
        message_thread_id=thread_id,
    )


def _generate_profile_card(p, avatar_bytes=None):
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
    import io as _io, math as _math, os as _os

    W, H  = 1536, 1024
    BG    = (13,  13,  15)
    PANEL = (22,  22,  27)
    PAN2  = (32,  32,  38)
    LGRAY = (50,  50,  58)
    WHITE = (255, 255, 255)
    GRAY  = (160, 160, 180)
    DGRAY = (90,  90,  100)
    GREEN = (72,  199, 142)
    BLUE  = (100, 140, 255)
    YELL  = (255, 196,  0)

    def rr(draw, xy, r, fill=None, outline=None, ow=1):
        draw.rounded_rectangle(list(xy), radius=r, fill=fill, outline=outline, width=ow)

    def fnt(size, bold=False):
        suffix = "-Bold" if bold else ""
        for path in [
            f"/usr/share/fonts/truetype/dejavu/DejaVuSans{suffix}.ttf",
            f"/usr/share/fonts/truetype/liberation/LiberationSans{suffix}.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]:
            if _os.path.exists(path):
                return ImageFont.truetype(path, size)
        return ImageFont.load_default()

    def donut(img, cx, cy, R, thick, frac, col_fg, col_bg=None):
        if col_bg is None: col_bg = LGRAY
        draw = ImageDraw.Draw(img)
        for i in range(360):
            a  = _math.radians(i - 90)
            mx = cx + R * _math.cos(a)
            my = cy + R * _math.sin(a)
            c  = col_fg if i < frac * 360 else col_bg
            t  = thick // 2
            draw.ellipse([mx-t, my-t, mx+t, my+t], fill=c)

    def hbar(draw, x, y, w, h, frac, col=WHITE):
        rr(draw, [x, y, x+w, y+h], h//2, LGRAY)
        if frac > 0:
            rr(draw, [x, y, x+int(w*min(frac,1)), y+h], h//2, col)

    def moon_hex(draw, cx, cy, R):
        pts = [(cx + R*_math.cos(_math.radians(60*k-90)),
                cy + R*_math.sin(_math.radians(60*k-90))) for k in range(6)]
        draw.polygon(pts, fill=PAN2, outline=WHITE)
        r = int(R*0.52)
        draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=WHITE)
        draw.ellipse([cx-r+int(r*0.55), cy-r-2, cx+r+int(r*0.55), cy+r-2], fill=PAN2)

    elo    = getattr(p, "elo",          1000)
    wins   = getattr(p, "wins",         0)
    losses = getattr(p, "losses",       0)
    total  = wins + losses
    kills  = getattr(p, "total_kills",  0)
    deaths = getattr(p, "total_deaths", 0)
    kd     = round(kills / deaths, 2) if deaths else float(kills)
    wr_pct = round(wins / total * 100) if total else 0
    avg_f  = round(getattr(p, "avg", 0), 1)
    w5     = getattr(p, "wins_5v5",   0)
    l5     = getattr(p, "losses_5v5", 0)
    w2     = getattr(p, "wins_2v2",   0)
    l2     = getattr(p, "losses_2v2", 0)
    t5 = w5+l5; wr5 = round(w5/t5*100, 1) if t5 else 0.0

    def lvl(e):
        for thr,lv in [(2001,10),(1751,9),(1531,8),(1351,7),(1201,6),(1051,5),(901,4),(751,3),(501,2)]:
            if e >= thr: return lv
        return 1
    level = lvl(elo)

    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # HEADER
    rr(draw, [0, 0, W, 158], 0, PANEL)
    AX, AY, AR = 18, 14, 65
    if avatar_bytes:
        try:
            av = Image.open(_io.BytesIO(avatar_bytes)).convert("RGB")
            av = av.resize((AR*2, AR*2), Image.LANCZOS)
            mk = Image.new("L", (AR*2, AR*2), 0)
            ImageDraw.Draw(mk).ellipse([0,0,AR*2,AR*2], fill=255)
            img.paste(av, (AX, AY), mk)
        except Exception:
            avatar_bytes = None
    if not avatar_bytes:
        draw.ellipse([AX, AY, AX+AR*2, AY+AR*2], fill=PAN2)
        draw.text((AX+AR, AY+AR), "?", font=fnt(36, True), fill=GRAY, anchor="mm")
    draw.ellipse([AX-2, AY-2, AX+AR*2+2, AY+AR*2+2], outline=WHITE, width=2)
    draw.text((AX+AR*2+16, 24),  f"# : {p.external_id or '0'}", font=fnt(20), fill=GRAY)
    draw.text((AX+AR*2+16, 52),  p.nickname, font=fnt(48, bold=True), fill=WHITE)
    draw.text((AX+AR*2+16, 108), f"ID: {p.user_id}", font=fnt(20), fill=GRAY)
    LCX = W // 2
    moon_hex(draw, LCX, 76, 50)
    draw = ImageDraw.Draw(img)
    draw.text((LCX+60, 56),  "NIGHT",  font=fnt(26, bold=True), fill=WHITE)
    draw.text((LCX+60, 90),  "FACEIT", font=fnt(26, bold=True), fill=WHITE)
    draw.rectangle([0, 158, W, 161], fill=LGRAY)

    # LEFT — STATISTIC
    LX, LW = 12, 896
    rr(draw, [LX, 172, LX+LW, 568], 14, PANEL)
    moon_hex(draw, LX+30, 202, 16)
    draw = ImageDraw.Draw(img)
    draw.text((LX+52, 192), "Statistic", font=fnt(22, bold=True), fill=WHITE)
    KX, KY = LX+80, 330
    donut(img, KX, KY, 52, 12, min(kd/3, 1), WHITE)
    draw = ImageDraw.Draw(img)
    draw.text((KX, KY), f"{kd:.2f}", font=fnt(20, bold=True), fill=WHITE, anchor="mm")
    draw.text((KX+74, 268), "Kill/Deaths", font=fnt(18, bold=True), fill=WHITE)
    draw.text((KX+74, 302), f"K = {kills}", font=fnt(16), fill=GRAY)
    draw.text((KX+74, 328), f"D = {deaths}", font=fnt(16), fill=GRAY)
    LVX = LX + 340
    draw.text((LVX, 244), "Level", font=fnt(18), fill=GRAY)
    moon_hex(draw, LX+LW-42, 258, 26)
    draw = ImageDraw.Draw(img)
    draw.text((LVX, 272), "Calibration", font=fnt(16), fill=GRAY)
    hbar(draw, LVX, 308, LX+LW-LVX-58, 10, level/10)
    draw.text((LX+LW-52, 300), f"{level}/10", font=fnt(22, bold=True), fill=WHITE, anchor="ra")
    SW = (LW - 28) // 3
    stat6 = [
        ("Rating",  f"{wr_pct:.2f}", wr_pct/100),
        ("AVG",     f"{avg_f:.2f}",  min(avg_f/100, 1)),
        ("Impact",  f"{kd:.2f}",     min(kd/3, 1)),
        ("KPR",     f"{kills/(total or 1):.2f}", min(kills/(total or 1)/5, 1)),
        ("Assists",  "0.00",         0),
        ("SVR",      "0.00",         0),
    ]
    for i, (label, val, frac) in enumerate(stat6):
        sx = LX + 12 + (i%3)*(SW+8)
        sy = 380 + (i//3)*90
        rr(draw, [sx, sy, sx+SW-4, sy+80], 10, PAN2)
        draw.text((sx+10, sy+8),    label, font=fnt(14), fill=GRAY)
        draw.text((sx+SW-14, sy+6), val,   font=fnt(24, bold=True), fill=WHITE, anchor="ra")
        hbar(draw, sx+10, sy+54, SW-24, 6, frac)
        lbl = "-" if frac==0 else ("High" if frac>0.65 else ("Stable" if frac>0.35 else "Low"))
        draw.text((sx+10, sy+64), lbl, font=fnt(11), fill=DGRAY)

    # MAP STATISTIC
    rr(draw, [LX, 580, LX+LW, 1010], 14, PANEL)
    moon_hex(draw, LX+30, 610, 16)
    draw = ImageDraw.Draw(img)
    draw.text((LX+52, 600), "Map Statistic", font=fnt(22, bold=True), fill=WHITE)
    MX, MY = LX+76, 728
    donut(img, MX, MY, 54, 12, wr_pct/100, WHITE)
    draw = ImageDraw.Draw(img)
    draw.text((MX, MY), f"{wr_pct}%", font=fnt(20, bold=True), fill=WHITE, anchor="mm")
    draw.text((MX+78, 678), "Win Rate", font=fnt(17, bold=True), fill=WHITE)
    draw.text((MX+78, 710), f"W = {wins}", font=fnt(16), fill=GRAY)
    draw.text((MX+78, 736), f"L = {losses}", font=fnt(16), fill=GRAY)
    MPX, MPY, MPW, MPH = LX+268, 636, 336, 178
    map_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "map_seaside.jpg")
    rr(draw, [MPX, MPY, MPX+MPW, MPY+MPH], 12, PAN2)
    if _os.path.exists(map_path):
        try:
            mp = Image.open(map_path).convert("RGB")
            mp = ImageEnhance.Sharpness(mp).enhance(2.0)
            mp = mp.resize((MPW, MPH), Image.LANCZOS)
            mk = Image.new("L", (MPW, MPH), 0)
            ImageDraw.Draw(mk).rounded_rectangle([0,0,MPW,MPH], radius=12, fill=255)
            img.paste(mp, (MPX, MPY), mk)
            draw = ImageDraw.Draw(img)
        except Exception:
            pass
    SX = MPX + MPW + 20
    draw.text((SX, 648),  "Seaside",                font=fnt(24, bold=True), fill=WHITE)
    draw.text((SX, 696),  f"W = {w5}    L = {l5}", font=fnt(16), fill=GRAY)
    draw.text((SX, 730),  f"K/D = {kd:.2f}",       font=fnt(16), fill=GRAY)
    draw.text((SX, 756),  f"W/R = {wr5}%",         font=fnt(16), fill=GRAY)
    draw.text((LX+LW//2, 870), "NO OTHER MAPS", font=fnt(18, bold=True), fill=LGRAY, anchor="mm")
    draw.text((LX+LW//2, 898), "ONLY SEASIDE",  font=fnt(18, bold=True), fill=LGRAY, anchor="mm")

    # RIGHT COLUMN
    RX, RW = 920, 604
    rr(draw, [RX, 172, RX+RW, 308], 14, PANEL)
    moon_hex(draw, RX+26, 194, 14)
    draw = ImageDraw.Draw(img)
    draw.text((RX+46, 185), "Recent Matches", font=fnt(19, bold=True), fill=WHITE)
    recent = (["W"]*wins + ["L"]*losses)[-20:]
    recent += [""] * (20 - len(recent))
    BSZ, GAP = 46, 8
    for i, r in enumerate(recent):
        bx = RX + 12 + (i%5)*(BSZ+GAP)
        by = 214 + (i//5)*(BSZ+GAP)
        bc = (30,60,30) if r=="W" else ((60,25,25) if r=="L" else LGRAY)
        rr(draw, [bx, by, bx+BSZ, by+BSZ], 8, bc)
    rr(draw, [RX, 318, RX+RW, 444], 14, PANEL)
    for ix2, iy2, label, val in [
        (RX+18, 334,  "Playtime",  f"{total*3}h"),
        (RX+RW//2+10, 334, "Join Date", "—"),
        (RX+18, 388,  "Game",      str(total)),
        (RX+RW//2+10, 388, "MVP",  "0"),
    ]:
        draw.text((ix2, iy2),    label, font=fnt(14), fill=GRAY)
        draw.text((ix2, iy2+24), val,   font=fnt(22, bold=True), fill=WHITE)
    rr(draw, [RX, 454, RX+RW, 572], 14, PANEL)
    draw.text((RX+18, 466), "League",  font=fnt(14), fill=GRAY)
    draw.text((RX+18, 490), "Default", font=fnt(26, bold=True), fill=WHITE)
    moon_hex(draw, RX+RW-52, 519, 36)
    draw = ImageDraw.Draw(img)
    rr(draw, [RX, 582, RX+RW, 840], 14, PANEL)
    draw.text((RX+18, 594), "Places", font=fnt(14), fill=GRAY)
    for i in range(3):
        py = 622 + i*60
        rr(draw, [RX+14, py, RX+58, py+34], 8, PAN2, WHITE, 1)
        draw.text((RX+36, py+17), f"#{i+1}", font=fnt(13, bold=True), fill=WHITE, anchor="mm")
        draw.text((RX+70, py+6),  "—", font=fnt(17), fill=WHITE)
        if i < 2:
            draw.line([RX+14, py+46, RX+RW-14, py+46], fill=LGRAY, width=1)
    draw.line([RX+14, 814, RX+RW-14, 814], fill=LGRAY, width=1)
    moon_hex(draw, RX+30, 830, 14)
    draw = ImageDraw.Draw(img)
    draw.text((RX+50, 822), f"Calibration {level}/10", font=fnt(15), fill=GRAY)
    rr(draw, [RX, 848, RX+RW, 1010], 14, PANEL)
    for i in range(20):
        bx = RX + 12 + (i%5)*(BSZ+GAP)
        by = 860 + (i//5)*(BSZ+GAP)
        rr(draw, [bx, by, bx+BSZ, by+BSZ], 8, LGRAY)

    img = img.filter(ImageFilter.SHARPEN)
    buf = _io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.read()


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


async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await gate(update): return
    db      = load_db()
    players = []
    for d in db["players"].values():
        if not d.get("external_id") or d.get("is_bot"): continue
        for field, val in [("wins",0),("losses",0),("avg",0.0),("elo",1000),
                           ("elo_5v5",1000),("elo_2v2",1000),
                           ("wins_5v5",0),("losses_5v5",0),
                           ("wins_2v2",0),("losses_2v2",0),
                           ("avg_5v5",0.0),("avg_2v2",0.0),
                           ("external_id",""),("is_bot",False),
                           ("total_kills",0),("total_deaths",0)]:
            d.setdefault(field, val)
        # Синхронизируем единое elo из max(elo, elo_5v5, elo_2v2) при старых данных
        d["elo"] = max(d.get("elo", 1000), d.get("elo_5v5", 1000), d.get("elo_2v2", 1000))
        try:
            players.append(Player(**d))
        except Exception:
            continue

    if not players:
        await update.message.reply_text("🏆 Рейтинг пока пуст.")
        return

    players.sort(key=lambda p: p.elo, reverse=True)
    medals = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    lines  = ["🏆 <b>Топ-10 — Night Faceit</b>\n━━━━━━━━━━━━━━━"]
    for i, p in enumerate(players[:10]):
        total = p.wins + p.losses
        wr    = f"{p.avg:.1f}%" if total else "—"
        lines.append(
            f"{medals[i]} {p.lvl_icon()} {p.tg_link()}\n"
            f"    ELO: <b>{p.elo}</b> | WR: <b>{wr}</b> | Игр: <b>{total}</b>"
        )
    if len(players) > 10:
        lines.append(f"\n... и ещё {len(players)-10} в рейтинге")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def play5_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.chat.type == "private":
        await update.message.reply_text(
            "❌ <b>Лобби создаётся только в беседе!</b>\n\n"
            "Заходи в беседу и запускай там:\n"
            "👉 https://t.me/faceitggvp",
            parse_mode=ParseMode.HTML
        )
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
        await update.message.reply_text(
            "❌ <b>Лобби создаётся только в беседе!</b>\n\n"
            "Заходи в беседу и запускай там:\n"
            "👉 https://t.me/faceitggvp",
            parse_mode=ParseMode.HTML
        )
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


async def admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /admins — список доступных команд по роли + состав стаффа"""
    uid = update.effective_user.id
    db  = load_db()

    # Собираем стафф
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

    staff_block = "\n".join(staff_lines) if staff_lines else "—"

    if is_creator(uid):
        my_role, cmd_keys = "👑 создатель", CREATOR_CMD_KEYS
    elif is_admin(uid):
        my_role, cmd_keys = "🛡 админ", ADMIN_CMD_KEYS
    elif is_moderator(uid):
        my_role, cmd_keys = "🔰 модератор", MOD_CMD_KEYS
    else:
        my_role, cmd_keys = None, None

    text = "🌙 <b>Night Faceit — Стафф</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n" + staff_block

    # Стаффу дополнительно показываем их роль и описание каждой команды
    if is_moderator(uid) and my_role and cmd_keys:
        cmds_block = "\n".join(CMD_DESCRIPTIONS[k] for k in cmd_keys)
        text += (
            f"\n\n━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Твоя роль: {my_role}</i>\n\n"
            f"{cmds_block}\n\n"
            f"{DURATION_NOTE}"
        )

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)



RULES_TEXT = (
    "🌙 <b>ПРАВИЛА NIGHT FACEIT</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "<b>👤 РЕГИСТРАЦИЯ</b>\n"
    "Без регистрации — в матч не попасть.\n"
    "Команда: /reg GAME_ID Никнейм Платформа\n"
    "├ ПК: /reg 6888 Londyyy pc\n"
    "└ Моб: /reg 6888 Londyyy mobile\n"
    "⚠️ Чужой ID или неверная платформа — <b>бан</b>.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "<b>📊 ЭЛО И УРОВНИ</b>\n"
    "Старт: 1000 ЭЛО | Минимум: 100 ЭЛО\n\n"
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
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
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
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "<b>🎮 ПРАВИЛА МАТЧЕЙ</b>\n"
    "├ Лив из матча = наказание в виде бана\n"
    "├ Код лобби — сразу в чат после создания\n"
    "├ Результат — скрин в тему «Результаты игр» с номером матча\n"
    "└ Без скрина ЭЛО не начисляется\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "<b>🚨 ЧИТЕРСТВО</b>\n"
    "Запрещено абсолютно всё:\n"
    "├ Читы, аимботы, ESP, моды с преимуществом\n"
    "├ Договорняки и намеренный слив\n"
    "├ Чужой аккаунт / поддельный Game ID\n"
    "└ Ложная платформа ради большего ЭЛО\n\n"
    "☠️ Наказание — <b>перманентный бан без апелляций.</b>\n"
    "Жалоба на читера — в личку админу с доказательствами.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "<b>👑 АДМИНИСТРАЦИЯ</b>\n"
    "👑 Создатель\n"
    "🛡 Админ\n"
    "🔰 Модератор\n\n"
    "Споры с администрацией в общем чате — запрещены.\n"
    "Вопрос или жалоба — напишите боту в ЛС команду /ticket, "
    "опишите ситуацию, и администрация ответит прямо здесь.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "<i>Незнание правил не освобождает от ответственности.\n"
    "Играем честно — Night Faceit 🌙</i>"
)


async def rules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /rules — правила чата"""
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

    # ── TOP 2v2 ───────────────────────────────────────────────────────────────
    if cb == "top_2v2":
        await q.answer()
        db      = update.callback_query  # just to not shadow
        db      = load_db()
        players = []
        for d in db["players"].values():
            if not d.get("external_id") or d.get("is_bot"): continue
            for field, val in [("wins",0),("losses",0),("avg",0.0),("elo",1000),
                               ("elo_5v5",1000),("elo_2v2",1000),
                               ("wins_5v5",0),("losses_5v5",0),
                               ("wins_2v2",0),("losses_2v2",0),
                               ("avg_5v5",0.0),("avg_2v2",0.0),
                               ("external_id",""),("is_bot",False),
                               ("total_kills",0),("total_deaths",0)]:
                d.setdefault(field, val)
            try:
                players.append(Player(**d))
            except Exception:
                continue
        if not players:
            await q.message.reply_text("🏆 Рейтинг 2v2 пока пуст.")
            return
        players.sort(key=lambda p: p.elo_2v2, reverse=True)
        medals = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
        lines  = ["⚡ <b>Топ-10 игроков — 2v2</b>\n━━━━━━━━━━━━━━━"]
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
            lobby_info    = db.get(f"lobby_{mode}", {})
            lobby_chat    = lobby_info.get("chat_id") or q.message.chat_id
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
            # Пик завершён — карта одна (Seaside), бан не нужен
            task = _pick_timer_tasks.pop(m_id, None)
            if task:
                task.cancel()
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
            chat_id_for_banner = m.get("chat_id", q.message.chat_id)
            thread_id_for_banner = m.get("thread_id")
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

async def scoreboard_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Игрок присылает в группу скриншот результатов с подписью, где есть номер матча
    (например: фото + подпись "3" или "матч 3").
    Без ИИ: бот просто проверяет, что отправитель — участник этого матча,
    пересылает скрин в админ-конфу на ручную проверку и просит сдать
    результат командой /result.
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

    p = get_player(uid)
    await msg.reply_text(
        f"📸 Скриншот игры #{m_id} принят.\n\n"
        f"Ожидайте, когда администрация Night Faceit зарегает вам игру.\n\n"
        f"Спасибо что выбрали наш фейсит 🌙",
        parse_mode=ParseMode.HTML,
    )

    if ADMIN_GROUP_ID:
        try:
            await context.bot.forward_message(
                chat_id=ADMIN_GROUP_ID,
                from_chat_id=msg.chat_id,
                message_id=msg.message_id,
            )
            await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=(
                    f"📸 Скрин результатов матча #{m_id}\n"
                    f"От: <a href=\"tg://user?id={uid}\">{p.nickname}</a>\n"
                    f"Проверьте вручную и при необходимости скорректируйте /result / /elo."
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            print(f"[scoreboard] admin notify error: {e}")


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
    mode    = m.get("mode", "5v5")
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

    win_lines:  List[str] = []
    loss_lines: List[str] = []

    def _apply(target_uid: int, won: bool, lines: List[str]) -> None:
        if _is_bot_uid(target_uid):
            return
        s = str(target_uid)
        pdata = db["players"].get(s)
        if not pdata:
            return

        platform      = pdata.get("platform", "pc")
        win_d, loss_d = elo_deltas_for(platform)
        delta         = win_d if won else -loss_d

        for field in ("elo", f"elo_{mode}"):
            pdata[field] = max(ELO_MIN, pdata.get(field, 1000) + delta)

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

        # ── Киллы/смерти за матч → накопительный средний KD ────────────────
        kills, deaths = kd_by_uid.get(target_uid, (0, 0))
        pdata["total_kills"]  = pdata.get("total_kills", 0) + kills
        pdata["total_deaths"] = pdata.get("total_deaths", 0) + deaths
        total_kd = round(pdata["total_kills"] / pdata["total_deaths"], 2) if pdata["total_deaths"] else float(pdata["total_kills"])

        nick = pdata.get("nickname", "?")
        sign = "+" if won else "-"
        applied = win_d if won else loss_d
        lines.append(
            f"  • {nick}: {sign}{applied} ELO → <b>{pdata['elo']}</b> | "
            f"{kills}/{deaths} (KD матча {round(kills/deaths, 2) if deaths else kills}) | "
            f"общий KD: <b>{total_kd}</b>"
        )

    for uid in winners:
        _apply(uid, True, win_lines)
    for uid in losers:
        _apply(uid, False, loss_lines)

    # ── Сохраняем снапшот для возможной отмены (/cancelwin) ─────────────────
    kd_snapshot = {str(uid): list(kd_by_uid.get(uid, (0, 0))) for uid in all_uids}
    # Сохраняем фактически применённые дельты ELO для точного отката в /cancelwin
    elo_snapshot: Dict[str, int] = {}
    for uid in all_uids:
        s_snap = str(uid)
        pdata_snap = db["players"].get(s_snap, {})
        platform_snap = pdata_snap.get("platform", "pc")
        win_d_s, loss_d_s = elo_deltas_for(platform_snap)
        elo_snapshot[s_snap] = win_d_s if uid in winners else loss_d_s
    db.setdefault("finished_matches", {})[m_id] = {
        "mode":         mode,
        "winners":      winners,
        "losers":       losers,
        "kd_by_uid":    kd_snapshot,
        "elo_snapshot": elo_snapshot,
        "finished_ts":  datetime.now().timestamp(),
    }

    db["active_matches"].pop(m_id, None)
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

    # ── Авто-обновление карточек всех участников матча ──────────────────────
    # Отправляем карточку каждому реальному игроку в тот же чат / тему,
    # чтобы все сразу видели актуальное ELO без лишних команд.
    chat_id   = update.effective_chat.id
    thread_id = getattr(update.message, "message_thread_id", None)
    real_uids = [u for u in all_uids if not _is_bot_uid(u)]
    for p_uid in real_uids:
        try:
            p_d = db["players"].get(str(p_uid), {})
            won = p_uid in winners
            sign = "+" if won else "-"
            p_plat = p_d.get("platform", "pc")
            win_d, loss_d = elo_deltas_for(p_plat)
            applied = win_d if won else loss_d
            new_elo = p_d.get("elo", 1000)
            extra = f"{'✅ Победа' if won else '❌ Поражение'}  {sign}{applied} ELO → <b>{new_elo}</b>"
            await _send_profile_card(
                bot=context.bot,
                chat_id=chat_id,
                target_uid=p_uid,
                message_thread_id=thread_id,
                caption_extra=extra,
            )
        except Exception as e:
            print(f"[win auto-card] uid={p_uid}: {e}")


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

        # Берём точную дельту из снапшота; если старый матч без снапшота — считаем по платформе
        if s in elo_snapshot:
            delta = elo_snapshot[s]
        else:
            platform      = pdata.get("platform", "pc")
            win_d, loss_d = elo_deltas_for(platform)
            delta = win_d if won else loss_d

        # Откат ELO: победителям отнимаем, проигравшим добавляем
        for field in ("elo", f"elo_{mode}"):
            old_val = pdata.get(field, 1000)
            new_val = max(ELO_MIN, old_val - delta) if won else (old_val + delta)
            pdata[field] = new_val

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

        nick  = pdata.get("nickname", "?")
        sign  = "-" if won else "+"
        lines.append(f"  • {nick}: {sign}{delta} ELO → <b>{pdata['elo']}</b>")

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
            for field, val in [("elo_5v5",1000),("wins_5v5",0),("losses_5v5",0),("avg_5v5",0.0),
                               ("elo_2v2",1000),("wins_2v2",0),("losses_2v2",0),("avg_2v2",0.0),
                               ("total_kills",0),("total_deaths",0)]:
                d.setdefault(field, val)
            p     = Player(**d)
            total = p.wins_5v5 + p.losses_5v5
            wr    = f"{p.avg_5v5:.1f}%" if total else "—"
            rows.append((p.nickname, p.external_id, p.elo_5v5, wr, total, p.lvl_icon_5v5()))
        except Exception:
            continue
    if not rows:
        await update.message.reply_text("Нет зарегистрированных игроков."); return

    rows.sort(key=lambda x: x[2], reverse=True)
    lines = ["📊 <b>ELO таблица (5v5)</b>\n━━━━━━━━━━━━━━━━━━━━━"]
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

    db["players"][s]["external_id"] = ""
    db["players"][s]["nickname"]    = "Player"
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
        "elo":          d.get("elo", 1000),
        "elo_5v5":      d.get("elo_5v5", d.get("elo", 1000)),
        "elo_2v2":      d.get("elo_2v2", d.get("elo", 1000)),
        "elo_1v1":      d.get("elo_1v1", d.get("elo", 1000)),
        "wins":         d.get("wins", 0),
        "losses":       d.get("losses", 0),
        "wins_5v5":     d.get("wins_5v5", 0),
        "losses_5v5":   d.get("losses_5v5", 0),
        "wins_2v2":     d.get("wins_2v2", 0),
        "losses_2v2":   d.get("losses_2v2", 0),
        "wins_1v1":     d.get("wins_1v1", 0),
        "losses_1v1":   d.get("losses_1v1", 0),
        "avg":          d.get("avg", 0.0),
        "avg_5v5":      d.get("avg_5v5", 0.0),
        "avg_2v2":      d.get("avg_2v2", 0.0),
        "avg_1v1":      d.get("avg_1v1", 0.0),
        "total_kills":  d.get("total_kills", 0),
        "total_deaths": d.get("total_deaths", 0),
        "is_bot":       d.get("is_bot", False),
    }

CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}

# ════════════════════════════════════════════════
#         АВТО-УДАЛЕНИЕ + ДЕТЕКТОР ОСКОРБЛЕНИЙ
# ════════════════════════════════════════════════

def _contains_insult(text: str) -> Optional[tuple]:
    """Возвращает (слово, категория) или None.
    Категория: 'insult' — оскорбление личности, 'profanity' — нецензурная лексика."""
    t = text.lower()
    for word in INSULT_WORDS:
        if word in t:
            return (word, "insult")
    for word in PROFANITY_WORDS:
        if word in t:
            return (word, "profanity")
    return None


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
            # Не прерываем — продолжаем проверку оскорблений

    # ── 2. ОСКОРБЛЕНИЯ — сначала проверка (мгновенно), потом БД ───────────
    found = _contains_insult(msg.text)
    if not found:
        return

    bad_word, reason_kind = found
    reason_label = "оскорбление" if reason_kind == "insult" else "нецензурная лексика"

    print(f"[insult] uid={uid} слово={bad_word!r} категория={reason_kind}")

    # ── 3. ЕСЛИ ЧЕЛОВЕК В МУТЕ — просто тихо удаляем, без предов и уведомлений ──
    if check_muted(uid):
        try:
            await msg.delete()
        except Exception:
            pass
        print(f"[insult] uid={uid} в муте — сообщение удалено без предупреждения")
        return

    db   = load_db()
    s    = str(uid)
    nick = db["players"].get(s, {}).get("nickname") or name
    now  = datetime.now().timestamp()

    # ── 4. ОБЫЧНАЯ ЭСКАЛАЦИЯ: пред 1/2 → мут 30 мин ────────────────────────
    warns     = db.setdefault("warns", {})
    warn_data = warns.get(s, {"count": 0, "expires": 0})
    # Совместимость: если старый формат (просто число)
    if isinstance(warn_data, int):
        warn_data = {"count": warn_data, "expires": 0}

    warn_data["count"] += 1
    warn_count = warn_data["count"]

    if warn_count >= 4:
        # Мут 30 минут, сбрасываем счётчик предов
        mute_seconds = 1800
        mute_until   = now + mute_seconds
        db.setdefault("muted", {})[s] = mute_until
        warn_data = {"count": 0, "expires": 0}
        warns[s]  = warn_data
        warn_text = (
            f"🔇 <b>{nick}</b>, сообщение удалено.\n"
            f"Причина: <b>{reason_label}</b>.\n"
            f"Нарушение #{warn_count} — мут на <b>30 минут</b>."
        )
        admin_text = (
            f"🔇 <b>Автомут</b>\n"
            f"👤 <a href=\"tg://user?id={uid}\">{nick}</a>\n"
            f"📌 Причина: <b>{reason_label}</b>\n"
            f"💬 <i>«{msg.text[:120]}»</i>\n"
            f"📍 {msg.chat.title} | Нарушений: {warn_count} → мут 30 мин."
        )
        # Реальное ограничение на уровне Telegram (как и у команды /mute)
        until_dt = int(datetime.now().timestamp()) + mute_seconds
        asyncio.create_task(_tg_mute(context, msg.chat_id, uid, until_date=until_dt))
        # Уведомляем нарушителя в ЛС о наказании
        asyncio.create_task(_notify_punishment_dm(context, uid, "mute", _fmt_duration(mute_seconds), reason_label))
        # И ставим таймер на уведомление об истечении мута
        asyncio.create_task(_schedule_mute_expiry(context.bot, uid, mute_until))
    else:
        # Предупреждение — истекает через 2 часа
        warn_data["expires"] = now + 7200
        warns[s]  = warn_data
        warn_text = (
            f"⚠️ <b>{nick}</b>, сообщение удалено.\n"
            f"Причина: <b>{reason_label}</b>.\n"
            f"Предупреждение <b>#{warn_count}/4</b> — на 4-м даётся мут на 30 мин.\n"
            f"<i>Предупреждения снимутся автоматически через 2 часа.</i>"
        )
        admin_text = (
            f"⚠️ <b>Нарушение</b>\n"
            f"👤 <a href=\"tg://user?id={uid}\">{nick}</a>\n"
            f"📌 Причина: <b>{reason_label}</b>\n"
            f"💬 <i>«{msg.text[:120]}»</i>\n"
            f"📍 {msg.chat.title} | Предупреждений: {warn_count}/4"
        )
        # Запускаем авто-снятие предупреждения через 2 часа
        async def _auto_clear_warn(user_s: str, expire_ts: float):
            await asyncio.sleep(7200)
            db2 = load_db()
            w   = db2.get("warns", {}).get(user_s)
            if isinstance(w, dict) and w.get("count", 0) > 0 and w.get("expires", 0) <= expire_ts + 1:
                db2["warns"][user_s] = {"count": 0, "expires": 0}
                save_db(db2)
                print(f"[warn_expire] авто-снятие предупреждения uid={user_s}")
        asyncio.create_task(_auto_clear_warn(s, warn_data["expires"]))

    save_db(db)

    # Удаление сообщения-нарушения + отправка предупреждения — выполняем сразу,
    # НЕ дожидаясь автоудаления самого текста предупреждения (через 60 сек).
    async def _send_warn():
        try:
            sent = await context.bot.send_message(
                chat_id=msg.chat_id,
                message_thread_id=msg.message_thread_id,
                text=warn_text,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            print(f"[insult] warn error: {e}")
            return
        # Самоудаление текста предупреждения через 60 сек — в фоне
        async def _delete_later():
            await asyncio.sleep(60)
            try:
                await sent.delete()
            except Exception:
                pass
        asyncio.create_task(_delete_later())

    async def _notify_admin():
        if not ADMIN_GROUP_ID:
            return
        try:
            await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=admin_text,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            print(f"[insult] admin notify error: {e}")

    await asyncio.gather(
        msg.delete(),
        _send_warn(),
        _notify_admin(),
        return_exceptions=True,
    )




async def handle_options(request):
    return web.Response(headers=CORS_HEADERS)

async def api_top(request):
    """GET /api/top?mode=5v5"""
    mode = request.rel_url.query.get("mode", "5v5")
    if mode not in ("5v5", "2v2", "1v1"):
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
    await app.bot.set_my_commands([
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
    ])
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
