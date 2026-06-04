import asyncio, logging, random, os
import aiosqlite
from datetime import date
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, LabeledPrice, PreCheckoutQuery, BotCommand
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "0").split(",")]
GRAND_CASE_PHOTO     = os.environ.get("GRAND_CASE_PHOTO_ID", "")
OUJI_CASE_PHOTO      = os.environ.get("OUJI_CASE_PHOTO_ID", "")
REDEAGLE_CASE_PHOTO  = os.environ.get("REDEAGLE_CASE_PHOTO_ID", "")
ALMERO_CASE_PHOTO    = os.environ.get("ALMERO_CASE_PHOTO_ID", "")
PALACE_CASE_PHOTO    = os.environ.get("PALACE_CASE_PHOTO_ID", "")
MIRAMASH_CASE_PHOTO  = os.environ.get("MIRAMASH_CASE_PHOTO_ID", "")
NEOKZZLESS_CASE_PHOTO = os.environ.get("NEOKZZLESS_CASE_PHOTO_ID", "")
DB_PATH = "/tmp/rapira.db"
START_BONUS = 300
DAILY_BONUS = 150
GOLD_ADMIN = "@tonkramzzz"
WITHDRAW_ADMIN = "@tonkramzzz"
MAINTENANCE = False  # тех.работы — включается командой /maintenance

CASES = {
    "base": {"name": "🟢 Базовый кейс", "price": 100, "desc": "Стартовый кейс для новичков.", "photo": None, "items": [
        {"name": "Скин AK-47 | Пустыня",       "rarity": "common",    "emoji": "⚪", "value": 30},
        {"name": "Скин M4A4 | Лесной",          "rarity": "common",    "emoji": "⚪", "value": 30},
        {"name": "Скин Desert Eagle | Огонь",   "rarity": "rare",      "emoji": "🔵", "value": 70},
        {"name": "Нож | Базовый",               "rarity": "epic",      "emoji": "🟣", "value": 200},
        {"name": "Агент | Призрак",             "rarity": "legendary", "emoji": "🟡", "value": 500},
    ], "weights": [60, 30, 8, 1.5, 0.5]},
    "tactical": {"name": "🔵 Тактический кейс", "price": 300, "desc": "Тактическое снаряжение для опытных бойцов.", "photo": None, "items": [
        {"name": "Скин AWP | Охотник",          "rarity": "common",    "emoji": "⚪", "value": 80},
        {"name": "Скин M4A1 | Хром",            "rarity": "rare",      "emoji": "🔵", "value": 180},
        {"name": "Нож-бабочка | Синий",         "rarity": "epic",      "emoji": "🟣", "value": 500},
        {"name": "Агент | Командир",            "rarity": "epic",      "emoji": "🟣", "value": 450},
        {"name": "Нож-бабочка | Золотой",       "rarity": "legendary", "emoji": "🟡", "value": 1200},
    ], "weights": [55, 33, 7, 4, 1]},
    "elite": {"name": "🟣 Элитный кейс", "price": 700, "desc": "Элитное снаряжение для лучших игроков.", "photo": None, "items": [
        {"name": "Скин AK-47 | Дракон",         "rarity": "rare",      "emoji": "🔵", "value": 400},
        {"name": "Нож | Тигровый",              "rarity": "epic",      "emoji": "🟣", "value": 900},
        {"name": "Агент | Элита",               "rarity": "epic",      "emoji": "🟣", "value": 850},
        {"name": "Нож | Рубиновый",             "rarity": "legendary", "emoji": "🟡", "value": 2000},
        {"name": "Агент | Легенда Rapira",      "rarity": "legendary", "emoji": "🟡", "value": 3000},
    ], "weights": [50, 30, 15, 3.5, 1.5]},
    "grand": {"name": "💜 Grand Case", "price": 700, "desc": "💜 <b>Эксклюзивный кейс от спонсора Grand News</b>\n\n📢 Следи за новостями спонсора: @GrandNewsQ", "photo": GRAND_CASE_PHOTO, "items": [
        {"name": "Скин AK-47 | Призма",         "rarity": "rare",      "emoji": "🔵", "value": 350},
        {"name": "Нож | Фиолетовый",            "rarity": "epic",      "emoji": "🟣", "value": 800},
        {"name": "Агент | Grand Elite",         "rarity": "epic",      "emoji": "🟣", "value": 900},
        {"name": "Нож | Аметист",               "rarity": "legendary", "emoji": "🟡", "value": 2200},
        {"name": "Агент | Grand Legend",        "rarity": "legendary", "emoji": "🟡", "value": 3500},
    ], "weights": [50, 28, 17, 3.5, 1.5]},
    "ouji": {"name": "🔴 Оуджи", "price": 500, "desc": "Кейс агента Оуджи.", "photo": OUJI_CASE_PHOTO, "items": [
        {"name": "Агент | Оуджи Рядовой",       "rarity": "common",    "emoji": "⚪", "value": 150},
        {"name": "Скин AK-47 | Красный",        "rarity": "rare",      "emoji": "🔵", "value": 350},
        {"name": "Агент | Оуджи Элитный",       "rarity": "epic",      "emoji": "🟣", "value": 800},
        {"name": "Нож | Алый",                  "rarity": "epic",      "emoji": "🟣", "value": 900},
        {"name": "Агент | Оуджи Легенда",       "rarity": "legendary", "emoji": "🟡", "value": 2500},
    ], "weights": [50, 30, 13, 5, 2]},
    "redeagle": {"name": "🦅 Красный Орёл", "price": 500, "desc": "Кейс агента Красный Орёл.", "photo": REDEAGLE_CASE_PHOTO, "items": [
        {"name": "Скин USP | Красная маска",    "rarity": "common",    "emoji": "⚪", "value": 150},
        {"name": "Скин M4A4 | Орёл",            "rarity": "rare",      "emoji": "🔵", "value": 350},
        {"name": "Агент | Красный Орёл",        "rarity": "epic",      "emoji": "🟣", "value": 850},
        {"name": "Нож | Маска черепа",          "rarity": "epic",      "emoji": "🟣", "value": 950},
        {"name": "Агент | Красный Орёл Элита",  "rarity": "legendary", "emoji": "🟡", "value": 2500},
    ], "weights": [50, 30, 13, 5, 2]},
    "almero": {"name": "🏖️ Almero", "price": 600, "desc": "Кейс карты Almero — пляж и океан.", "photo": ALMERO_CASE_PHOTO, "items": [
        {"name": "Скин AWP | Пляжный",          "rarity": "common",    "emoji": "⚪", "value": 180},
        {"name": "Скин Desert Eagle | Лагуна",  "rarity": "rare",      "emoji": "🔵", "value": 400},
        {"name": "Нож | Волна",                 "rarity": "epic",      "emoji": "🟣", "value": 900},
        {"name": "Агент | Almero Разведчик",    "rarity": "epic",      "emoji": "🟣", "value": 950},
        {"name": "Нож | Коралловый",            "rarity": "legendary", "emoji": "🟡", "value": 2800},
    ], "weights": [50, 29, 13, 5.5, 2.5]},
    "palace": {"name": "🏛️ Palace", "price": 800, "desc": "Кейс карты Palace — золото пустыни.", "photo": PALACE_CASE_PHOTO, "items": [
        {"name": "Скин AK-47 | Золото пустыни", "rarity": "common",    "emoji": "⚪", "value": 200},
        {"name": "Скин M4A1 | Дворец",          "rarity": "rare",      "emoji": "🔵", "value": 450},
        {"name": "Нож | Золотой клинок",        "rarity": "epic",      "emoji": "🟣", "value": 1000},
        {"name": "Агент | Страж Дворца",        "rarity": "epic",      "emoji": "🟣", "value": 1100},
        {"name": "Нож | Скимитар Легенда",      "rarity": "legendary", "emoji": "🟡", "value": 3200},
    ], "weights": [50, 29, 13, 5.5, 2.5]},
    "miramash": {"name": "🟢 Miramash", "price": 400, "desc": "Кейс карты Miramash — хаос и зелень.", "photo": MIRAMASH_CASE_PHOTO, "items": [
        {"name": "Скин Glock | Зелёный",        "rarity": "common",    "emoji": "⚪", "value": 120},
        {"name": "Скин AK-47 | Miramash",       "rarity": "rare",      "emoji": "🔵", "value": 300},
        {"name": "Нож | Зелёный хром",          "rarity": "epic",      "emoji": "🟣", "value": 750},
        {"name": "Агент | Miramash Ветеран",    "rarity": "epic",      "emoji": "🟣", "value": 800},
        {"name": "Нож | Изумруд",               "rarity": "legendary", "emoji": "🟡", "value": 2200},
    ], "weights": [52, 30, 12, 4.5, 1.5]},
    "neokzzless": {"name": "🟡 Neokzzless", "price": 700, "desc": "🟡 <b>Эксклюзивный кейс от спонсора Neokzzless</b>\n\n📢 Канал спонсора: @Neokzzless999", "photo": NEOKZZLESS_CASE_PHOTO, "items": [
        {"name": "Перчатки | Розовые DLN",      "rarity": "rare",      "emoji": "🔵", "value": 400},
        {"name": "Перчатки | Neokzzless Pro",   "rarity": "epic",      "emoji": "🟣", "value": 900},
        {"name": "Агент | Neokzzless Elite",    "rarity": "epic",      "emoji": "🟣", "value": 950},
        {"name": "Перчатки | Золотые DLN",      "rarity": "legendary", "emoji": "🟡", "value": 2500},
        {"name": "Агент | Neokzzless Legend",   "rarity": "legendary", "emoji": "🟡", "value": 3500},
    ], "weights": [50, 28, 17, 3.5, 1.5]},
}
STARS = {
    "small":  {"stars": 50,  "coins": 500,  "label": "500 монет"},
    "medium": {"stars": 150, "coins": 1700, "label": "1700 монет"},
    "large":  {"stars": 300, "coins": 3600, "label": "3600 монет"},
}
GOLD_PACKAGES = {
    "gold_small":  {"label": "💛 500 монет за Gold (500 Gold)", "coins": 500},
    "gold_medium": {"label": "💛 1500 монет за Gold (1500 Gold)", "coins": 1500},
    "gold_large":  {"label": "💛 3000 монет за Gold (3000 Gold)", "coins": 3000},
}
RARITY = {"common": "Обычный", "rare": "Редкий", "epic": "Эпический", "legendary": "Легендарный"}

DAILY_TASKS = [
    {"id": "open_cases_5",   "desc": "📦 Открыть 5 кейсов за день",              "goal": 5,    "reward": 40,  "type": "open_cases"},
    {"id": "open_cases_20",  "desc": "📦 Открыть 20 кейсов за день",             "goal": 20,   "reward": 80,  "type": "open_cases"},
    {"id": "open_tactical",  "desc": "🔵 Открыть 3 Тактических кейса",           "goal": 3,    "reward": 60,  "type": "open_tactical"},
    {"id": "open_elite_3",   "desc": "🟣 Открыть 3 Элитных кейса",              "goal": 3,    "reward": 100, "type": "open_elite"},
    {"id": "spend_1000",     "desc": "💸 Потратить 1000 монет на кейсы",         "goal": 1000, "reward": 50,  "type": "spend_coins"},
    {"id": "spend_3000",     "desc": "💸 Потратить 3000 монет на кейсы",         "goal": 3000, "reward": 100, "type": "spend_coins"},
    {"id": "sell_3",         "desc": "💰 Продать 3 предмета из инвентаря",       "goal": 3,    "reward": 30,  "type": "sell_item"},
    {"id": "get_rare_3",     "desc": "🔵 Получить 3 редких (или выше) предмета", "goal": 3,    "reward": 60,  "type": "get_rare"},
    {"id": "get_epic",       "desc": "🟣 Получить эпический предмет",            "goal": 1,    "reward": 80,  "type": "get_epic"},
    {"id": "get_legendary",  "desc": "🟡 Получить легендарный предмет",          "goal": 1,    "reward": 200, "type": "get_legendary"},
]

ACHIEVEMENTS = [
    {"id": "open_10",    "name": "🎯 Новичок",        "desc": "Открыть 10 кейсов",        "type": "total_cases", "goal": 10,   "reward": 100},
    {"id": "open_50",    "name": "🏅 Охотник",         "desc": "Открыть 50 кейсов",        "type": "total_cases", "goal": 50,   "reward": 300},
    {"id": "open_100",   "name": "🏆 Ветеран",         "desc": "Открыть 100 кейсов",       "type": "total_cases", "goal": 100,  "reward": 700},
    {"id": "legendary1", "name": "✨ Удачливый",       "desc": "Получить легендарный дроп", "type": "legendaries", "goal": 1,    "reward": 200},
    {"id": "legendary5", "name": "👑 Избранный",       "desc": "5 легендарных дропов",      "type": "legendaries", "goal": 5,    "reward": 800},
    {"id": "streak7",    "name": "📅 Неделя стрика",   "desc": "7 дней подряд в боте",      "type": "streak",      "goal": 7,    "reward": 500},
    {"id": "rich",       "name": "💰 Богач",            "desc": "Накопить 10 000 монет",     "type": "coins",       "goal": 10000,"reward": 1000},
]

# ───────────────────────────── DB ─────────────────────────────

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, coins INTEGER DEFAULT 0,
            last_daily TEXT, total_cases INTEGER DEFAULT 0, total_legendaries INTEGER DEFAULT 0,
            total_spent INTEGER DEFAULT 0, streak INTEGER DEFAULT 0, last_streak TEXT,
            referred_by INTEGER DEFAULT NULL, x2_used_date TEXT DEFAULT NULL
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, item_name TEXT,
            rarity TEXT, emoji TEXT, value INTEGER DEFAULT 0,
            obtained_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT,
            photo_id TEXT, reward INTEGER DEFAULT 0, status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS achievements (
            user_id INTEGER, achievement_id TEXT, earned_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, achievement_id)
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS daily_tasks (
            user_id INTEGER, task_id TEXT, progress INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0, task_date TEXT,
            PRIMARY KEY (user_id, task_id, task_date)
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS referrals (
            user_id INTEGER PRIMARY KEY, ref_code TEXT UNIQUE, total_refs INTEGER DEFAULT 0
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY, coins INTEGER DEFAULT 0,
            max_uses INTEGER DEFAULT 1, used_count INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            bonus_percent INTEGER DEFAULT 0,
            youtuber_id INTEGER DEFAULT NULL,
            youtuber_percent INTEGER DEFAULT 0
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS promo_uses (
            user_id INTEGER, code TEXT, used_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, code)
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS youtuber_earnings (
            youtuber_id INTEGER PRIMARY KEY,
            youtuber_username TEXT,
            promo_code TEXT,
            total_earned INTEGER DEFAULT 0,
            pending INTEGER DEFAULT 0,
            withdrawn INTEGER DEFAULT 0
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS user_active_promo (
            user_id INTEGER PRIMARY KEY,
            promo_code TEXT,
            youtuber_id INTEGER,
            youtuber_percent INTEGER
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT,
            item_id INTEGER, item_name TEXT, item_value INTEGER,
            trade_link TEXT DEFAULT '', status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS deposit_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            case_key TEXT NOT NULL,
            min_deposit INTEGER NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS deposit_case_grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            deposit_case_id INTEGER NOT NULL,
            deposit_amount INTEGER NOT NULL,
            granted_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS free_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            case_key TEXT NOT NULL,
            source TEXT DEFAULT 'deposit',
            obtained_at TEXT DEFAULT CURRENT_TIMESTAMP,
            used INTEGER DEFAULT 0
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS case_photos (
            case_key TEXT PRIMARY KEY, photo_id TEXT
        )""")
        await db.commit()

    # Загружаем фото кейсов из БД в память
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT case_key, photo_id FROM case_photos") as c:
            rows = await c.fetchall()
        for row in rows:
            if row[0] in CASES:
                CASES[row[0]]["photo"] = row[1]

async def get_user(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id=?", (uid,)) as c:
            return await c.fetchone()

async def reg_user(uid, uname, bonus, referred_by=None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id,username,coins,referred_by) VALUES (?,?,?,?)",
            (uid, uname, bonus, referred_by)
        )
        await db.commit()

async def add_coins(uid, amt):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (amt, uid))
        await db.commit()

async def spend_coins(uid, amt):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT coins FROM users WHERE user_id=?", (uid,)) as c:
            row = await c.fetchone()
        if not row or row[0] < amt:
            return False
        await db.execute("UPDATE users SET coins=coins-?,total_spent=total_spent+? WHERE user_id=?", (amt, amt, uid))
        await db.commit()
        return True

async def claim_daily(uid, bonus):
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT last_daily,streak,last_streak FROM users WHERE user_id=?", (uid,)) as c:
            row = await c.fetchone()
        if row and row[0] == today:
            return False
        from datetime import date as d_, timedelta
        yesterday = (d_.today() - timedelta(days=1)).isoformat()
        new_streak = (row[1] or 0) + 1 if row and row[2] == yesterday else 1
        await db.execute(
            "UPDATE users SET coins=coins+?,last_daily=?,streak=?,last_streak=? WHERE user_id=?",
            (bonus, today, new_streak, today, uid)
        )
        await db.commit()
        return True

async def get_top():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT username,coins,total_cases FROM users ORDER BY coins DESC LIMIT 10") as c:
            return await c.fetchall()

async def add_inv(uid, item):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO inventory (user_id,item_name,rarity,emoji,value) VALUES (?,?,?,?,?)",
            (uid, item["name"], item["rarity"], item["emoji"], item.get("value", 0))
        )
        await db.execute("UPDATE users SET total_cases=total_cases+1 WHERE user_id=?", (uid,))
        if item["rarity"] == "legendary":
            await db.execute("UPDATE users SET total_legendaries=total_legendaries+1 WHERE user_id=?", (uid,))
        await db.commit()

async def get_inv(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM inventory WHERE user_id=? ORDER BY obtained_at DESC", (uid,)) as c:
            return await c.fetchall()

async def sell_inv_item(uid, item_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM inventory WHERE id=? AND user_id=?", (item_id, uid)) as c:
            item = await c.fetchone()
        if not item:
            return None
        value = item["value"]
        item_name = item["item_name"]
        emoji = item["emoji"]
        rarity = item["rarity"]
        await db.execute("DELETE FROM inventory WHERE id=?", (item_id,))
        await db.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (value, uid))
        await db.commit()
        return {"value": value, "item_name": item_name, "emoji": emoji, "rarity": rarity}

async def create_sub(uid, uname, photo_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO submissions (user_id,username,photo_id) VALUES (?,?,?)", (uid, uname, photo_id))
        await db.commit()

async def last_sub_id():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT MAX(id) FROM submissions") as c:
            row = await c.fetchone()
            return row[0] or 0

async def approve_sub(sid, reward):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM submissions WHERE id=?", (sid,)) as c:
            row = await c.fetchone()
        if not row:
            return None
        await db.execute("UPDATE submissions SET status='approved',reward=? WHERE id=?", (reward, sid))
        await db.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (reward, row[0]))
        await db.commit()
        return row[0]

async def reject_sub(sid, reason=""):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM submissions WHERE id=?", (sid,)) as c:
            row = await c.fetchone()
        if not row:
            return None
        await db.execute("UPDATE submissions SET status='rejected' WHERE id=?", (sid,))
        await db.commit()
        return row[0]

async def get_admin_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            users = (await c.fetchone())[0]
        async with db.execute("SELECT COALESCE(SUM(total_cases),0) FROM users") as c:
            cases = (await c.fetchone())[0]
        async with db.execute("SELECT username,total_cases FROM users ORDER BY total_cases DESC LIMIT 5") as c:
            top = await c.fetchall()
        async with db.execute("SELECT COUNT(*) FROM submissions WHERE status='pending'") as c:
            pending = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'") as c:
            pending_wd = (await c.fetchone())[0]
        async with db.execute("SELECT COALESCE(SUM(coins),0) FROM users") as c:
            total_coins = (await c.fetchone())[0]
        async with db.execute("SELECT COALESCE(SUM(total_spent),0) FROM users") as c:
            total_spent = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM promo_codes WHERE is_active=1") as c:
            active_promos = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM free_cases WHERE used=0") as c:
            free_cases_pending = (await c.fetchone())[0]
        async with db.execute("SELECT COALESCE(SUM(pending),0) FROM youtuber_earnings") as c:
            yt_pending = (await c.fetchone())[0]
    return {
        "users": users, "cases": cases, "top": top,
        "pending_subs": pending, "pending_wd": pending_wd,
        "total_coins": total_coins, "total_spent": total_spent,
        "active_promos": active_promos,
        "free_cases_pending": free_cases_pending,
        "yt_pending": yt_pending,
    }

# ─────────────── Achievements & Daily Tasks ───────────────

async def check_achievements(uid, bot: Bot = None) -> list:
    """Проверяет и засчитывает достижения. Возвращает список новых достижений (без отправки сообщений)."""
    u = await get_user(uid)
    if not u:
        return []
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT achievement_id FROM achievements WHERE user_id=?", (uid,)) as c:
            earned = {r[0] for r in await c.fetchall()}
    earned_new = []
    for ach in ACHIEVEMENTS:
        if ach["id"] in earned:
            continue
        val = 0
        t = ach["type"]
        if t == "total_cases":    val = u["total_cases"]
        elif t == "legendaries":  val = u["total_legendaries"]
        elif t == "streak":       val = u["streak"]
        elif t == "coins":        val = u["coins"]
        if val >= ach["goal"]:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT OR IGNORE INTO achievements (user_id,achievement_id) VALUES (?,?)", (uid, ach["id"]))
                await db.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (ach["reward"], uid))
                await db.commit()
            earned_new.append(ach)
    # Вместо отправки сообщений — сохраняем счётчик непросмотренных достижений в памяти
    # (используется для бейджа в меню результата кейса)
    return earned_new

async def get_today_tasks(uid):
    today = date.today().isoformat()
    today_ids = [t["id"] for t in DAILY_TASKS]
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT task_id,progress,completed FROM daily_tasks WHERE user_id=? AND task_date=?",
            (uid, today)
        ) as c:
            rows = {r[0]: {"progress": r[1], "completed": r[2]} for r in await c.fetchall()}
        for tid in today_ids:
            if tid not in rows:
                await db.execute(
                    "INSERT OR IGNORE INTO daily_tasks (user_id,task_id,progress,completed,task_date) VALUES (?,?,0,0,?)",
                    (uid, tid, today)
                )
        await db.commit()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT task_id,progress,completed FROM daily_tasks WHERE user_id=? AND task_date=?",
            (uid, today)
        ) as c:
            rows = {r[0]: {"progress": r[1], "completed": r[2]} for r in await c.fetchall()}
    return rows

async def update_task_progress(uid, task_type, amount=1, bot: Bot = None):
    today = date.today().isoformat()
    newly_completed = []
    async with aiosqlite.connect(DB_PATH) as db:
        for task in DAILY_TASKS:
            if task["type"] != task_type:
                continue
            async with db.execute(
                "SELECT progress,completed FROM daily_tasks WHERE user_id=? AND task_id=? AND task_date=?",
                (uid, task["id"], today)
            ) as c:
                row = await c.fetchone()
            if not row:
                await db.execute(
                    "INSERT OR IGNORE INTO daily_tasks (user_id,task_id,progress,completed,task_date) VALUES (?,?,0,0,?)",
                    (uid, task["id"], today)
                )
                row = (0, 0)
            prog, done = row
            if done:
                continue
            new_prog = prog + amount
            completed = 1 if new_prog >= task["goal"] else 0
            await db.execute(
                "UPDATE daily_tasks SET progress=?,completed=? WHERE user_id=? AND task_id=? AND task_date=?",
                (new_prog, completed, uid, task["id"], today)
            )
            if completed:
                await db.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (task["reward"], uid))
                newly_completed.append(task)
        await db.commit()
    # Отправляем ОДНО сообщение со всеми выполненными заданиями
    if newly_completed and bot:
        lines = [f"• {t['desc']} <b>+{t['reward']} 🪙</b>" for t in newly_completed]
        try:
            await bot.send_message(
                uid,
                "✅ <b>Задания выполнены!</b>\n\n" + "\n".join(lines)
            )
        except Exception:
            pass

# ─────────────── Referral ───────────────

async def get_or_create_ref(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT ref_code FROM referrals WHERE user_id=?", (uid,)) as c:
            row = await c.fetchone()
        if row:
            return row[0]
        code = f"ref_{uid}"
        await db.execute("INSERT OR IGNORE INTO referrals (user_id,ref_code) VALUES (?,?)", (uid, code))
        await db.commit()
        return code

async def apply_referral(new_uid, code):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM referrals WHERE ref_code=?", (code,)) as c:
            row = await c.fetchone()
        if not row or row[0] == new_uid:
            return None
        ref_uid = row[0]
        async with db.execute("SELECT referred_by FROM users WHERE user_id=?", (new_uid,)) as c:
            u = await c.fetchone()
        if u and u[0]:
            return None  # already referred
        await db.execute("UPDATE users SET referred_by=?,coins=coins+50 WHERE user_id=?", (ref_uid, new_uid))
        await db.execute("UPDATE users SET coins=coins+50 WHERE user_id=?", (ref_uid,))
        await db.execute("UPDATE referrals SET total_refs=total_refs+1 WHERE user_id=?", (ref_uid,))
        await db.commit()
        return ref_uid

# ─────────────── X2 Mode ───────────────

async def can_use_x2(uid):
    today = date.today().isoformat()
    u = await get_user(uid)
    return u and u["x2_used_date"] != today

async def mark_x2_used(uid):
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET x2_used_date=? WHERE user_id=?", (today, uid))
        await db.commit()

# ─────────────── Промокоды ───────────────

async def create_promo(code, coins, max_uses, bonus_percent=0, youtuber_id=None, youtuber_percent=0, youtuber_username=None):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO promo_codes (code,coins,max_uses,bonus_percent,youtuber_id,youtuber_percent) VALUES (?,?,?,?,?,?)",
                (code.upper(), coins, max_uses, bonus_percent, youtuber_id, youtuber_percent)
            )
            # Если привязан ютубер — создаём/обновляем его запись
            if youtuber_id:
                await db.execute(
                    """INSERT OR IGNORE INTO youtuber_earnings (youtuber_id,youtuber_username,promo_code)
                       VALUES (?,?,?)""",
                    (youtuber_id, youtuber_username or str(youtuber_id), code.upper())
                )
                # Обновим username и promo если уже существует
                await db.execute(
                    "UPDATE youtuber_earnings SET youtuber_username=?,promo_code=? WHERE youtuber_id=?",
                    (youtuber_username or str(youtuber_id), code.upper(), youtuber_id)
                )
            await db.commit()
            return True
        except Exception:
            return False

async def use_promo(uid, code):
    """
    Активация промокода.
    Возвращает: (coins_given, status_msg, deposit_bonus_pct, youtuber_id)
      coins_given       — монеты начисленные сразу (0 если нет)
      status_msg        — "ok" или текст ошибки
      deposit_bonus_pct — % бонуса к каждому депозиту юзера (0 если нет)
      youtuber_id       — ID ютубера (None если нет)
    """
    code = code.upper()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT coins,max_uses,used_count,is_active,bonus_percent,youtuber_id,youtuber_percent FROM promo_codes WHERE code=?", (code,)
        ) as c:
            promo = await c.fetchone()
        if not promo:
            return None, "❌ Промокод не найден", 0, None
        coins, max_uses, used_count, is_active, deposit_bonus_pct, youtuber_id, youtuber_deposit_pct = promo
        if not is_active:
            return None, "❌ Промокод уже неактивен", 0, None
        if used_count >= max_uses:
            return None, "❌ Промокод исчерпан", 0, None
        async with db.execute("SELECT 1 FROM promo_uses WHERE user_id=? AND code=?", (uid, code)) as c:
            already = await c.fetchone()
        if already:
            return None, "❌ Ты уже использовал этот промокод", 0, None

        await db.execute("INSERT INTO promo_uses (user_id,code) VALUES (?,?)", (uid, code))
        await db.execute("UPDATE promo_codes SET used_count=used_count+1 WHERE code=?", (code,))
        if used_count + 1 >= max_uses:
            await db.execute("UPDATE promo_codes SET is_active=0 WHERE code=?", (code,))

        # Начисляем стартовые монеты (если заданы)
        if coins > 0:
            await db.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (coins, uid))

        # Привязываем промо к юзеру — % бонуса к депозиту действует на все его пополнения
        if deposit_bonus_pct > 0 or (youtuber_id and youtuber_deposit_pct > 0):
            await db.execute(
                """INSERT OR REPLACE INTO user_active_promo
                   (user_id, promo_code, youtuber_id, youtuber_percent)
                   VALUES (?,?,?,?)""",
                (uid, code, youtuber_id, youtuber_deposit_pct or 0)
            )

        await db.commit()
        return coins, "ok", deposit_bonus_pct, youtuber_id

async def process_deposit_bonuses(uid: int, base_coins: int) -> int:
    """
    Вызывается после каждого пополнения баланса.
    1. Начисляет юзеру +X% бонус от суммы депозита (если у него привязан промокод).
    2. Начисляет ютуберу Y% от суммы депозита.
    Возвращает количество бонусных монет, которые получил юзер.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT promo_code, youtuber_id, youtuber_percent FROM user_active_promo WHERE user_id=?", (uid,)
        ) as c:
            row = await c.fetchone()
        if not row:
            return 0
        promo_code, youtuber_id, youtuber_deposit_pct = row

        async with db.execute(
            "SELECT bonus_percent FROM promo_codes WHERE code=?", (promo_code,)
        ) as c:
            pr = await c.fetchone()
        deposit_bonus_pct = pr[0] if pr else 0

        user_bonus = int(base_coins * deposit_bonus_pct / 100)
        youtuber_earn = int(base_coins * youtuber_deposit_pct / 100)

        if user_bonus > 0:
            await db.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (user_bonus, uid))

        if youtuber_id and youtuber_earn > 0:
            await db.execute(
                "UPDATE youtuber_earnings SET total_earned=total_earned+?,pending=pending+? WHERE youtuber_id=?",
                (youtuber_earn, youtuber_earn, youtuber_id)
            )

        await db.commit()
    return user_bonus

async def get_youtuber_stats(youtuber_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM youtuber_earnings WHERE youtuber_id=?", (youtuber_id,)
        ) as c:
            return await c.fetchone()

async def youtuber_withdraw(youtuber_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT pending FROM youtuber_earnings WHERE youtuber_id=?", (youtuber_id,)
        ) as c:
            row = await c.fetchone()
        if not row or row[0] <= 0:
            return 0
        amount = row[0]
        await db.execute(
            "UPDATE youtuber_earnings SET pending=0,withdrawn=withdrawn+? WHERE youtuber_id=?",
            (amount, youtuber_id)
        )
        await db.commit()
        return amount

async def list_youtubers():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM youtuber_earnings ORDER BY total_earned DESC"
        ) as c:
            return await c.fetchall()

# ─────────────── Депозит-кейсы ───────────────

async def create_deposit_case(name: str, case_key: str, min_deposit: int) -> bool:
    """Создать новую акцию: при депозите от min_deposit монет — бесплатный кейс case_key."""
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO deposit_cases (name, case_key, min_deposit) VALUES (?,?,?)",
                (name, case_key, min_deposit)
            )
            await db.commit()
            return True
        except Exception:
            return False

async def delete_deposit_case(dc_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE deposit_cases SET is_active=0 WHERE id=?", (dc_id,))
        await db.commit()

async def list_deposit_cases(active_only=True):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        q = "SELECT * FROM deposit_cases"
        if active_only:
            q += " WHERE is_active=1"
        q += " ORDER BY min_deposit ASC"
        async with db.execute(q) as c:
            return await c.fetchall()

async def get_deposit_case(dc_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM deposit_cases WHERE id=?", (dc_id,)) as c:
            return await c.fetchone()

async def grant_deposit_cases(uid: int, deposit_amount: int, bot=None) -> list:
    """
    Проверяет все активные депозит-кейсы и выдаёт юзеру те,
    где deposit_amount >= min_deposit. Возвращает список выданных акций.
    """
    active = await list_deposit_cases(active_only=True)
    granted = []
    async with aiosqlite.connect(DB_PATH) as db:
        for dc in active:
            if deposit_amount >= dc["min_deposit"]:
                # Добавляем бесплатный кейс в free_cases
                await db.execute(
                    "INSERT INTO free_cases (user_id, case_key, source) VALUES (?,?,?)",
                    (uid, dc["case_key"], f"deposit_{dc['id']}")
                )
                await db.execute(
                    "INSERT INTO deposit_case_grants (user_id, deposit_case_id, deposit_amount) VALUES (?,?,?)",
                    (uid, dc["id"], deposit_amount)
                )
                granted.append(dc)
        await db.commit()
    return granted

async def get_free_cases(uid: int):
    """Получить неиспользованные бесплатные кейсы юзера."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM free_cases WHERE user_id=? AND used=0 ORDER BY obtained_at DESC",
            (uid,)
        ) as c:
            return await c.fetchall()

async def use_free_case(fc_id: int, uid: int) -> str | None:
    """Пометить бесплатный кейс как использованный. Возвращает case_key или None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM free_cases WHERE id=? AND user_id=? AND used=0", (fc_id, uid)
        ) as c:
            fc = await c.fetchone()
        if not fc:
            return None
        await db.execute("UPDATE free_cases SET used=1 WHERE id=?", (fc_id,))
        await db.commit()
        return fc["case_key"]

async def get_deposit_case_stats():
    """Статистика для админа: сколько кейсов выдано по каждой акции."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT dc.id, dc.name, dc.case_key, dc.min_deposit, dc.is_active,
                   COUNT(dcg.id) as total_grants
            FROM deposit_cases dc
            LEFT JOIN deposit_case_grants dcg ON dc.id = dcg.deposit_case_id
            GROUP BY dc.id
            ORDER BY dc.min_deposit ASC
        """) as c:
            return await c.fetchall()

async def delete_promo(code):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE promo_codes SET is_active=0 WHERE code=?", (code.upper(),))
        await db.commit()

async def list_promos():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT code,coins,max_uses,used_count,is_active,bonus_percent,youtuber_id,youtuber_percent FROM promo_codes ORDER BY created_at DESC LIMIT 20"
        ) as c:
            return await c.fetchall()

# ─────────────── Вывод скинов ───────────────

async def create_withdrawal(uid, uname, item_id, item_name, item_value, trade_link):
    async with aiosqlite.connect(DB_PATH) as db:
        # Удаляем предмет из инвентаря сразу (он заморожен на время вывода)
        async with db.execute("SELECT * FROM inventory WHERE id=? AND user_id=?", (item_id, uid)) as c:
            item = await c.fetchone()
        if not item:
            return None
        await db.execute("DELETE FROM inventory WHERE id=?", (item_id,))
        await db.execute(
            "INSERT INTO withdrawals (user_id,username,item_id,item_name,item_value,trade_link) VALUES (?,?,?,?,?,?)",
            (uid, uname, item_id, item_name, item_value, trade_link)
        )
        await db.commit()
        async with db.execute("SELECT MAX(id) FROM withdrawals") as c:
            row = await c.fetchone()
            return row[0]

async def get_pending_withdrawals():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM withdrawals WHERE status='pending' ORDER BY created_at ASC"
        ) as c:
            return await c.fetchall()

async def complete_withdrawal(wid):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM withdrawals WHERE id=?", (wid,)) as c:
            row = await c.fetchone()
        if not row:
            return None
        await db.execute("UPDATE withdrawals SET status='completed' WHERE id=?", (wid,))
        await db.commit()
        return row[0]

async def cancel_withdrawal(wid, reason=""):
    """Отменяем вывод — возвращаем предмет в инвентарь."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM withdrawals WHERE id=?", (wid,)) as c:
            w = await c.fetchone()
        if not w:
            return None, None
        await db.execute("UPDATE withdrawals SET status='cancelled' WHERE id=?", (wid,))
        # Возвращаем предмет
        await db.execute(
            "INSERT INTO inventory (user_id,item_name,rarity,emoji,value) VALUES (?,?,?,?,?)",
            (w["user_id"], w["item_name"], "epic", "🟣", w["item_value"])
        )
        await db.commit()
        return w["user_id"], w["item_name"]

# ═══════════════════════════ ROUTER ═══════════════════════════

router = Router()

class SS(StatesGroup):
    photo = State()
    reject_reason = State()
    broadcast_msg = State()
    give_coins = State()
    promo_input = State()
    withdraw_trade_link = State()
    withdraw_item_id = State()
    gold_custom_amount = State()

def mmk():
    b = InlineKeyboardBuilder()
    b.button(text="📦 Кейсы",          callback_data="menu_cases")
    b.button(text="💳 Купить монеты",   callback_data="menu_buy_coins")
    b.button(text="🎒 Инвентарь",       callback_data="menu_inventory")
    b.button(text="🎁 Ежедн. бонус",    callback_data="daily")
    b.button(text="📋 Задания дня",     callback_data="menu_daily_tasks")
    b.button(text="🏅 Достижения",      callback_data="menu_achievements")
    b.button(text="👥 Реферальная",     callback_data="menu_ref")
    b.button(text="📤 Вывод скина",     callback_data="menu_withdraw")
    b.button(text="🎟️ Промокод",        callback_data="menu_promo")
    b.button(text="🎰 Бонусные кейсы",  callback_data="menu_free_cases")
    b.adjust(2, 2, 2, 2, 2)
    return b.as_markup()

def back_btn():
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Назад", callback_data="back_main")
    return b.as_markup()

# ─── /start ───

@router.message(CommandStart())
async def start(m: Message, bot: Bot):
    global MAINTENANCE
    args = m.text.split()
    ref_code = args[1] if len(args) > 1 else None
    # Тех.работы — только для не-админов
    if MAINTENANCE and m.from_user.id not in ADMIN_IDS:
        await m.answer("🔧 <b>Технические работы</b>\n\nБот временно недоступен. Скоро вернёмся! ⏳")
        return
    ref_code = args[1] if len(args) > 1 else None
    u = await get_user(m.from_user.id)
    if not u:
        await reg_user(m.from_user.id, m.from_user.username or m.from_user.first_name, START_BONUS)
        if ref_code:
            ref_uid = await apply_referral(m.from_user.id, ref_code)
            if ref_uid:
                try:
                    await bot.send_message(ref_uid, f"🎉 По твоей ссылке зарегистрировался новый игрок!\n💰 +<b>50 монет</b> тебе и ему!")
                except Exception:
                    pass
        txt = (f"👋 Добро пожаловать в <b>Rapira Case Bot</b>!\n\n"
               f"🎁 Стартовый бонус: <b>{START_BONUS} монет</b>!\n\n"
               f"Открывай кейсы, выполняй задания и собирай скины 🏆")
    else:
        txt = f"👋 С возвращением, <b>{m.from_user.first_name}</b>!\n\n💰 Баланс: <b>{u['coins']} монет</b>"
    await m.answer(txt, reply_markup=mmk())

# ─── Команды ───

@router.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer(
        "📋 <b>Команды бота</b>\n\n"
        "/start — 🏠 Главное меню\n"
        "/case — 📦 Открыть кейс\n"
        "/buy — 💳 Купить монеты (Stars или Gold)\n"
        "/coin — 💰 Узнать баланс монет\n"
        "/ref — 👥 Реферальная ссылка\n"
        "/tasks — 📋 Ежедневные задания\n"
        "/achievements — 🏅 Мои достижения\n"
        "/help — ❓ Список команд"
    )

@router.message(Command("case"))
async def cmd_case(m: Message):
    u = await get_user(m.from_user.id)
    coins = u["coins"] if u else 0
    b = InlineKeyboardBuilder()
    for k, c in CASES.items():
        enough = "✅" if coins >= c["price"] else "❌"
        b.button(text=f"{enough} {c['name']} — {c['price']} монет", callback_data=f"open_card_{k}")
    b.button(text="🔙 Меню", callback_data="back_main")
    b.adjust(1)
    await m.answer(
        f"📦 <b>Выбери кейс</b>\n"
        f"💰 Твой баланс: <b>{coins} монет</b>\n\n"
        f"⚪ Обычный  🔵 Редкий  🟣 Эпический  🟡 Легендарный\n"
        f"✅ — хватает монет  ❌ — недостаточно",
        reply_markup=b.as_markup()
    )

@router.message(Command("coin"))
async def cmd_coin(m: Message):
    u = await get_user(m.from_user.id)
    coins = u["coins"] if u else 0
    await m.answer(f"💰 Твой баланс: <b>{coins} монет</b>")

@router.message(Command("buy"))
async def cmd_buy(m: Message):
    b = InlineKeyboardBuilder()
    b.button(text="⭐ Купить за Telegram Stars", callback_data="menu_stars")
    b.button(text="💛 Купить за Gold (Rapira)",  callback_data="menu_gold")
    b.button(text="🔙 Меню", callback_data="back_main")
    b.adjust(1)
    await m.answer("💳 <b>Купить монеты</b>\n\nВыбери способ:", reply_markup=b.as_markup())

@router.message(Command("ref"))
async def cmd_ref(m: Message, bot: Bot):
    code = await get_or_create_ref(m.from_user.id)
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={code}"
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT total_refs FROM referrals WHERE user_id=?", (m.from_user.id,)) as c:
            row = await c.fetchone()
    total = row[0] if row else 0
    await m.answer(
        f"👥 <b>Реферальная программа</b>\n\n"
        f"Приглашай друзей и получай <b>+50 монет</b> за каждого!\n"
        f"Друг тоже получит <b>+50 монет</b> 🎁\n\n"
        f"🔗 Твоя ссылка:\n<code>{link}</code>\n\n"
        f"👫 Приглашено друзей: <b>{total}</b>"
    )

@router.message(Command("tasks"))
async def cmd_tasks(m: Message):
    await show_daily_tasks(m.from_user.id, m)

@router.message(Command("achievements"))
async def cmd_achievements(m: Message):
    await show_achievements(m.from_user.id, m)

# ─── Admin Commands ───

@router.message(Command("broadcast"))
async def broadcast_start(m: Message, state: FSMContext):
    if m.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(SS.broadcast_msg)
    await m.answer("📢 Введи текст рассылки:")

@router.message(SS.broadcast_msg)
async def broadcast_send(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as c:
            uids = [r[0] for r in await c.fetchall()]
    ok = 0
    for uid in uids:
        try:
            await bot.send_message(uid, f"📢 <b>Объявление</b>\n\n{m.text}")
            ok += 1
        except Exception:
            pass
    await m.answer(f"✅ Рассылка отправлена: {ok}/{len(uids)} пользователей")

@router.message(Command("give"))
async def give_cmd(m: Message, bot: Bot):
    if m.from_user.id not in ADMIN_IDS:
        return
    parts = m.text.split()
    if len(parts) < 3:
        await m.answer("Формат: /give @username 500")
        return
    target_un = parts[1].lstrip("@")
    try:
        amount = int(parts[2])
    except ValueError:
        await m.answer("❌ Неверная сумма")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE username=?", (target_un,)) as c:
            row = await c.fetchone()
    if not row:
        await m.answer("❌ Пользователь не найден")
        return
    await add_coins(row[0], amount)
    try:
        await bot.send_message(row[0], f"🎁 Администратор начислил тебе <b>{amount} монет</b>!")
    except Exception:
        pass
    await m.answer(f"✅ @{target_un} начислено {amount} монет")

def admin_main_kb(maint: bool) -> object:
    b = InlineKeyboardBuilder()
    b.button(text="📊 Статистика",        callback_data="adm_stats")
    b.button(text="👥 Пользователи",      callback_data="adm_users")
    b.button(text="📋 Заявки скриншоты",  callback_data="adm_subs")
    b.button(text="📤 Заявки на вывод",   callback_data="adm_withdrawals")
    b.button(text="🎟️ Промокоды",         callback_data="adm_promos")
    b.button(text="🎰 Депозит-акции",     callback_data="adm_deposit_cases")
    b.button(text="📢 Рассылка",          callback_data="adm_broadcast")
    b.button(text="💰 Выдать монеты",     callback_data="adm_give")
    b.button(text="👤 Найти юзера",       callback_data="adm_find_user")
    b.button(text="📊 Ютуберы",           callback_data="adm_youtubers")
    maint_text = "🟢 Вкл тех.работы" if not maint else "🔴 Выкл тех.работы"
    b.button(text=maint_text,             callback_data="adm_toggle_maint")
    b.button(text="📖 Все команды",       callback_data="adm_commands")
    b.adjust(2, 2, 2, 2, 1, 1, 1)
    return b.as_markup()

async def admin_main_text():
    s = await get_admin_stats()
    maint = "🔴 Включены" if MAINTENANCE else "🟢 Выключены"
    alerts = []
    if s["pending_subs"] > 0:
        alerts.append(f"⚠️ Заявок на скриншоты: <b>{s['pending_subs']}</b>")
    if s["pending_wd"] > 0:
        alerts.append(f"⚠️ Заявок на вывод: <b>{s['pending_wd']}</b>")
    if s["yt_pending"] > 0:
        alerts.append(f"⚠️ Ютуберы ждут выплаты: <b>{s['yt_pending']} монет</b>")
    alert_block = ("\n" + "\n".join(alerts)) if alerts else ""
    return (
        f"🛠 <b>Панель администратора</b>{alert_block}\n\n"
        f"👥 Пользователей: <b>{s['users']}</b>\n"
        f"📦 Кейсов открыто: <b>{s['cases']}</b>\n"
        f"💰 Монет в системе: <b>{s['total_coins']}</b>\n"
        f"💸 Всего потрачено: <b>{s['total_spent']}</b>\n"
        f"🎟️ Активных промокодов: <b>{s['active_promos']}</b>\n"
        f"🎰 Бонусных кейсов (не открыто): <b>{s['free_cases_pending']}</b>\n"
        f"🔧 Тех.работы: <b>{maint}</b>"
    )

@router.message(Command("admin"))
async def admin_panel(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        return
    text = await admin_main_text()
    await m.answer(text, reply_markup=admin_main_kb(MAINTENANCE))

# ── Refresh admin panel ──
@router.callback_query(F.data == "adm_main")
async def adm_main_cb(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔", show_alert=True); return
    text = await admin_main_text()
    try:
        await cb.message.edit_text(text, reply_markup=admin_main_kb(MAINTENANCE))
    except Exception:
        pass
    await cb.answer()

# ── Статистика ──
@router.callback_query(F.data == "adm_stats")
async def adm_stats(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔", show_alert=True); return
    s = await get_admin_stats()
    top_str = "\n".join([f"  {i+1}. {r[0]} — {r[1]} кейсов" for i, r in enumerate(s["top"])])
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Обновить", callback_data="adm_stats")
    b.button(text="🔙 Назад",    callback_data="adm_main")
    b.adjust(2)
    await cb.message.edit_text(
        f"📊 <b>Подробная статистика</b>\n\n"
        f"👥 Пользователей: <b>{s['users']}</b>\n"
        f"📦 Кейсов открыто всего: <b>{s['cases']}</b>\n"
        f"💰 Монет в системе: <b>{s['total_coins']}</b>\n"
        f"💸 Всего потрачено монет: <b>{s['total_spent']}</b>\n"
        f"⏳ Заявок скриншотов (ожид.): <b>{s['pending_subs']}</b>\n"
        f"📤 Заявок на вывод (ожид.): <b>{s['pending_wd']}</b>\n"
        f"🎟️ Активных промокодов: <b>{s['active_promos']}</b>\n"
        f"🎰 Бонус. кейсов не открыто: <b>{s['free_cases_pending']}</b>\n"
        f"💵 Ютуберов к выплате: <b>{s['yt_pending']} монет</b>\n\n"
        f"🏆 <b>Топ по кейсам:</b>\n{top_str}",
        reply_markup=b.as_markup()
    )

# ── Пользователи ──
@router.callback_query(F.data == "adm_users")
async def adm_users(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔", show_alert=True); return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT username, coins, total_cases, total_spent FROM users ORDER BY coins DESC LIMIT 15"
        ) as c:
            rows = await c.fetchall()
    lines = []
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. @{r['username']} — 💰{r['coins']} | 📦{r['total_cases']} | 💸{r['total_spent']}")
    b = InlineKeyboardBuilder()
    b.button(text="👤 Найти юзера",   callback_data="adm_find_user")
    b.button(text="💰 Выдать монеты", callback_data="adm_give")
    b.button(text="🔙 Назад",         callback_data="adm_main")
    b.adjust(2, 1)
    await cb.message.edit_text(
        f"👥 <b>Топ-15 по балансу</b>\n<i>ник — монеты | кейсы | потрачено</i>\n\n" + "\n".join(lines),
        reply_markup=b.as_markup()
    )

# ── Найти юзера ──

class AdminSS(StatesGroup):
    find_user    = State()
    give_user    = State()
    give_amount  = State()
    broadcast    = State()

@router.callback_query(F.data == "adm_find_user")
async def adm_find_user_cb(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔", show_alert=True); return
    await state.set_state(AdminSS.find_user)
    await state.update_data(adm_msg_id=cb.message.message_id)
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data="adm_cancel_state")
    await cb.message.edit_text(
        "👤 <b>Поиск пользователя</b>\n\nВведи @username или ID:",
        reply_markup=b.as_markup()
    )

@router.message(AdminSS.find_user)
async def adm_find_user_input(m: Message, state: FSMContext):
    await state.clear()
    q = m.text.strip().lstrip("@")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if q.isdigit():
            async with db.execute("SELECT * FROM users WHERE user_id=?", (int(q),)) as c:
                u = await c.fetchone()
        else:
            async with db.execute("SELECT * FROM users WHERE username=?", (q,)) as c:
                u = await c.fetchone()
    if not u:
        b = InlineKeyboardBuilder(); b.button(text="🔙 Назад", callback_data="adm_users")
        await m.answer("❌ Пользователь не найден", reply_markup=b.as_markup()); return
    # Count inventory
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM inventory WHERE user_id=?", (u["user_id"],)) as c:
            inv_count = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM free_cases WHERE user_id=? AND used=0", (u["user_id"],)) as c:
            fc_count = (await c.fetchone())[0]
        async with db.execute("SELECT promo_code FROM user_active_promo WHERE user_id=?", (u["user_id"],)) as c:
            promo_row = await c.fetchone()
    promo_line = f"\n🎟️ Промокод: <code>{promo_row[0]}</code>" if promo_row else ""
    b = InlineKeyboardBuilder()
    b.button(text="💰 Выдать монеты",   callback_data=f"adm_give_uid_{u['user_id']}")
    b.button(text="🎰 Выдать кейс",     callback_data=f"adm_givecase_{u['user_id']}")
    b.button(text="🔙 Назад",           callback_data="adm_users")
    b.adjust(2, 1)
    await m.answer(
        f"👤 <b>@{u['username']}</b> (ID: <code>{u['user_id']}</code>)\n\n"
        f"💰 Баланс: <b>{u['coins']} монет</b>\n"
        f"📦 Кейсов открыто: <b>{u['total_cases']}</b>\n"
        f"💸 Потрачено: <b>{u['total_spent']} монет</b>\n"
        f"🟡 Легендарных: <b>{u['total_legendaries']}</b>\n"
        f"📅 Стрик: <b>{u['streak']} дней</b>\n"
        f"🎒 Предметов: <b>{inv_count}</b>\n"
        f"🎰 Бонус. кейсов: <b>{fc_count}</b>"
        f"{promo_line}",
        reply_markup=b.as_markup()
    )

# ── Выдать монеты через кнопку ──
@router.callback_query(F.data == "adm_give")
async def adm_give_cb(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔", show_alert=True); return
    await state.set_state(AdminSS.give_user)
    b = InlineKeyboardBuilder(); b.button(text="❌ Отмена", callback_data="adm_cancel_state")
    await cb.message.edit_text(
        "💰 <b>Выдать монеты</b>\n\nВведи: <code>@username сумма</code>\nПример: <code>@vasya 500</code>",
        reply_markup=b.as_markup()
    )

@router.callback_query(F.data.startswith("adm_give_uid_"))
async def adm_give_uid_cb(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔", show_alert=True); return
    uid = int(cb.data.split("_")[3])
    await state.set_state(AdminSS.give_amount)
    await state.update_data(give_target_uid=uid)
    b = InlineKeyboardBuilder(); b.button(text="❌ Отмена", callback_data="adm_cancel_state")
    await cb.message.edit_text(
        f"💰 Выдать монеты пользователю <code>{uid}</code>\n\nВведи сумму:",
        reply_markup=b.as_markup()
    )

@router.message(AdminSS.give_amount)
async def adm_give_amount_input(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    uid = data.get("give_target_uid")
    try:
        amount = int(m.text.strip())
    except ValueError:
        await m.answer("❌ Введи число"); return
    await add_coins(uid, amount)
    try:
        await bot.send_message(uid, f"🎁 Администратор начислил тебе <b>{amount} монет</b>!")
    except Exception:
        pass
    b = InlineKeyboardBuilder(); b.button(text="🔙 Панель", callback_data="adm_main")
    await m.answer(f"✅ Пользователю <code>{uid}</code> начислено <b>{amount} монет</b>", reply_markup=b.as_markup())

@router.message(AdminSS.give_user)
async def adm_give_user_input(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    parts = m.text.strip().split()
    if len(parts) < 2:
        await m.answer("Формат: @username сумма"); return
    target = parts[0].lstrip("@")
    try:
        amount = int(parts[1])
    except ValueError:
        await m.answer("❌ Сумма должна быть числом"); return
    async with aiosqlite.connect(DB_PATH) as db:
        if target.isdigit():
            async with db.execute("SELECT user_id FROM users WHERE user_id=?", (int(target),)) as c:
                row = await c.fetchone()
        else:
            async with db.execute("SELECT user_id FROM users WHERE username=?", (target,)) as c:
                row = await c.fetchone()
    if not row:
        await m.answer("❌ Пользователь не найден"); return
    await add_coins(row[0], amount)
    try:
        await bot.send_message(row[0], f"🎁 Администратор начислил тебе <b>{amount} монет</b>!")
    except Exception:
        pass
    b = InlineKeyboardBuilder(); b.button(text="🔙 Панель", callback_data="adm_main")
    await m.answer(f"✅ @{target} начислено <b>{amount} монет</b>", reply_markup=b.as_markup())

# ── Выдать бесплатный кейс юзеру ──
@router.callback_query(F.data.startswith("adm_givecase_"))
async def adm_givecase_cb(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔", show_alert=True); return
    uid = int(cb.data.split("_")[2])
    b = InlineKeyboardBuilder()
    for key, c in CASES.items():
        b.button(text=c["name"], callback_data=f"adm_gcdo_{uid}_{key}")
    b.button(text="❌ Отмена", callback_data="adm_main")
    b.adjust(2)
    await cb.message.edit_text(
        f"🎰 Выбери кейс для выдачи пользователю <code>{uid}</code>:",
        reply_markup=b.as_markup()
    )

@router.callback_query(F.data.startswith("adm_gcdo_"))
async def adm_givecase_do(cb: CallbackQuery, bot: Bot):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔", show_alert=True); return
    parts = cb.data.split("_")
    uid = int(parts[2])
    case_key = parts[3]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO free_cases (user_id, case_key, source) VALUES (?,?,?)",
            (uid, case_key, "admin")
        )
        await db.commit()
    cname = CASES.get(case_key, {}).get("name", case_key)
    try:
        await bot.send_message(uid, f"🎰 Администратор выдал тебе бесплатный кейс <b>{cname}</b>!\n\n📦 Открой в меню → Бонусные кейсы")
    except Exception:
        pass
    b = InlineKeyboardBuilder(); b.button(text="🔙 Панель", callback_data="adm_main")
    await cb.message.edit_text(f"✅ Кейс <b>{cname}</b> выдан пользователю <code>{uid}</code>", reply_markup=b.as_markup())

# ── Промокоды ──
@router.callback_query(F.data == "adm_promos")
async def adm_promos_cb(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔", show_alert=True); return
    promos = await list_promos()
    lines = []
    for p in promos:
        status = "✅" if p["is_active"] else "❌"
        extra = ""
        if p["bonus_percent"]: extra += f" +{p['bonus_percent']}%деп"
        if p["youtuber_id"]:   extra += f" 👤{p['youtuber_percent']}%"
        lines.append(f"{status} <code>{p['code']}</code> {p['coins']}💰 {p['used_count']}/{p['max_uses']}{extra}")
    text = "🎟️ <b>Промокоды</b>\n\n" + ("\n".join(lines) if lines else "Нет промокодов")
    b = InlineKeyboardBuilder()
    b.button(text="➕ Обычный",  callback_data="adm_promo_help_simple")
    b.button(text="➕ Бонусный", callback_data="adm_promo_help_bonus")
    b.button(text="➕ Ютубер",   callback_data="adm_promo_help_yt")
    b.button(text="🔙 Назад",    callback_data="adm_main")
    b.adjust(3, 1)
    await cb.message.edit_text(text, reply_markup=b.as_markup())

@router.callback_query(F.data.in_({"adm_promo_help_simple", "adm_promo_help_bonus", "adm_promo_help_yt"}))
async def adm_promo_help(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔", show_alert=True); return
    b = InlineKeyboardBuilder(); b.button(text="🔙 Назад", callback_data="adm_promos")
    if cb.data == "adm_promo_help_simple":
        text = (
            "📝 <b>Создать обычный промокод</b>\n\n"
            "<code>/promo create КОД МОНЕТЫ МАКС</code>\n\n"
            "Пример:\n<code>/promo create SALE500 500 100</code>\n"
            "→ 100 юзеров получат по 500 монет"
        )
    elif cb.data == "adm_promo_help_bonus":
        text = (
            "📝 <b>Создать бонусный промокод (+% к депозиту)</b>\n\n"
            "<code>/promo bonus КОД ПРОЦЕНТ МАКС</code>\n\n"
            "Пример:\n<code>/promo bonus BONUS10 10 9999</code>\n"
            "→ +10% к каждому пополнению навсегда"
        )
    else:
        text = (
            "📝 <b>Создать промокод ютубера</b>\n\n"
            "<code>/promo youtuber КОД ID %_ЮТ %_ЮЗ МОНЕТЫ МАКС</code>\n\n"
            "Пример:\n<code>/promo youtuber VASYA 123456789 5 5 0 9999</code>\n"
            "→ Юзер: +5% к депозиту\n"
            "→ Ютубер: +5% от каждого депозита зрителя"
        )
    await cb.message.edit_text(text, reply_markup=b.as_markup())

# ── Депозит-акции ──
@router.callback_query(F.data == "adm_deposit_cases")
async def adm_deposit_cases_cb(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔", show_alert=True); return
    dcs = await get_deposit_case_stats()
    lines = []
    for dc in dcs:
        status = "✅" if dc["is_active"] else "❌"
        cname = CASES.get(dc["case_key"], {}).get("name", dc["case_key"])
        lines.append(f"{status} <b>#{dc['id']}</b> {dc['name']}\n   📦 {cname} | от {dc['min_deposit']}💰 | выдано: {dc['total_grants']}")
    text = "🎰 <b>Депозит-акции</b>\n\n" + ("\n\n".join(lines) if lines else "Акций нет")
    b = InlineKeyboardBuilder()
    b.button(text="➕ Создать акцию", callback_data="adm_dc_help")
    b.button(text="❌ Удалить акцию", callback_data="adm_dc_delete_list")
    b.button(text="🔙 Назад",         callback_data="adm_main")
    b.adjust(2, 1)
    await cb.message.edit_text(text, reply_markup=b.as_markup())

@router.callback_query(F.data == "adm_dc_help")
async def adm_dc_help(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔", show_alert=True); return
    keys = ", ".join(f"<code>{k}</code>" for k in CASES.keys())
    b = InlineKeyboardBuilder(); b.button(text="🔙 Назад", callback_data="adm_deposit_cases")
    await cb.message.edit_text(
        "📝 <b>Создать депозит-акцию</b>\n\n"
        "<code>/depositcase add КЛЮЧ МИН_ДЕПОЗИТ НАЗВАНИЕ</code>\n\n"
        "Пример:\n<code>/depositcase add oasis 480 Оазис Скинов</code>\n"
        "→ При пополнении от 480 монет — бесплатный кейс oasis\n\n"
        f"<b>Ключи кейсов:</b>\n{keys}",
        reply_markup=b.as_markup()
    )

@router.callback_query(F.data == "adm_dc_delete_list")
async def adm_dc_delete_list(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔", show_alert=True); return
    dcs = await list_deposit_cases(active_only=True)
    if not dcs:
        await cb.answer("Нет активных акций", show_alert=True); return
    b = InlineKeyboardBuilder()
    for dc in dcs:
        b.button(text=f"❌ #{dc['id']} {dc['name']}", callback_data=f"adm_dc_del_{dc['id']}")
    b.button(text="🔙 Назад", callback_data="adm_deposit_cases")
    b.adjust(1)
    await cb.message.edit_text("Выбери акцию для деактивации:", reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("adm_dc_del_"))
async def adm_dc_del(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔", show_alert=True); return
    dc_id = int(cb.data.split("_")[3])
    await delete_deposit_case(dc_id)
    await cb.answer(f"✅ Акция #{dc_id} деактивирована", show_alert=True)
    await adm_deposit_cases_cb(cb)

# ── Ютуберы ──
@router.callback_query(F.data == "adm_youtubers")
async def adm_youtubers_cb(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔", show_alert=True); return
    yts = await list_youtubers()
    if not yts:
        b = InlineKeyboardBuilder(); b.button(text="🔙 Назад", callback_data="adm_main")
        await cb.message.edit_text("📊 Ютуберов нет", reply_markup=b.as_markup()); return
    lines = []
    for y in yts:
        lines.append(
            f"👤 @{y['youtuber_username']} (<code>{y['youtuber_id']}</code>)\n"
            f"   🎟 <code>{y['promo_code']}</code> | 💰 заработано: {y['total_earned']} | ⏳ к выдаче: {y['pending']} | ✅ выдано: {y['withdrawn']}"
        )
    b = InlineKeyboardBuilder(); b.button(text="🔙 Назад", callback_data="adm_main")
    await cb.message.edit_text("📊 <b>Ютуберы</b>\n\n" + "\n\n".join(lines), reply_markup=b.as_markup())

# ── Рассылка через кнопку ──
@router.callback_query(F.data == "adm_broadcast")
async def adm_broadcast_cb(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔", show_alert=True); return
    await state.set_state(AdminSS.broadcast)
    b = InlineKeyboardBuilder(); b.button(text="❌ Отмена", callback_data="adm_cancel_state")
    await cb.message.edit_text(
        "📢 <b>Рассылка</b>\n\nВведи текст сообщения (поддерживается HTML):",
        reply_markup=b.as_markup()
    )

@router.message(AdminSS.broadcast)
async def adm_broadcast_input(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as c:
            uids = [r[0] for r in await c.fetchall()]
    ok = 0
    for uid in uids:
        try:
            await bot.send_message(uid, f"📢 <b>Объявление</b>\n\n{m.text}")
            ok += 1
        except Exception:
            pass
    b = InlineKeyboardBuilder(); b.button(text="🔙 Панель", callback_data="adm_main")
    await m.answer(f"✅ Рассылка отправлена: <b>{ok}/{len(uids)}</b> пользователей", reply_markup=b.as_markup())

# ── Тех. работы через кнопку ──
@router.callback_query(F.data == "adm_toggle_maint")
async def adm_toggle_maint(cb: CallbackQuery):
    global MAINTENANCE
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔", show_alert=True); return
    MAINTENANCE = not MAINTENANCE
    status = "🔴 ВКЛЮЧЕНЫ" if MAINTENANCE else "🟢 ВЫКЛЮЧЕНЫ"
    await cb.answer(f"Тех.работы: {status}", show_alert=True)
    text = await admin_main_text()
    try:
        await cb.message.edit_text(text, reply_markup=admin_main_kb(MAINTENANCE))
    except Exception:
        pass

# ── Все команды (справка) ──
@router.callback_query(F.data == "adm_commands")
async def adm_commands_cb(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔", show_alert=True); return
    b = InlineKeyboardBuilder(); b.button(text="🔙 Назад", callback_data="adm_main")
    await cb.message.edit_text(
        "📖 <b>Все команды администратора</b>\n\n"
        "<b>👥 Монеты и юзеры:</b>\n"
        "<code>/give @username 500</code> — выдать монеты\n\n"
        "<b>📢 Рассылка:</b>\n"
        "<code>/broadcast</code> — текстовая рассылка\n\n"
        "<b>🎟️ Промокоды:</b>\n"
        "<code>/promo create КОД МОНЕТЫ МАКС</code>\n"
        "<code>/promo bonus КОД % МАКС</code>\n"
        "<code>/promo youtuber КОД ID %_ЮТ %_ЮЗ МОНЕТЫ МАКС</code>\n"
        "<code>/promo list</code> — список\n"
        "<code>/promo delete КОД</code> — деактивировать\n"
        "<code>/promo youtubers</code> — статистика ютуберов\n\n"
        "<b>🎰 Депозит-акции:</b>\n"
        "<code>/depositcase add КЛЮЧ МИН_ДЕПОЗИТ НАЗВАНИЕ</code>\n"
        "<code>/depositcase list</code> — список\n"
        "<code>/depositcase delete ID</code> — удалить\n"
        "<code>/depositcase stats</code> — статистика\n\n"
        "<b>📤 Выводы:</b>\n"
        "<code>/withdrawals</code> — заявки на вывод\n\n"
        "<b>🖼 Кейсы:</b>\n"
        "<code>/setcasephoto КЛЮЧ</code> → затем отправь фото с подписью <code>setcasephoto КЛЮЧ</code>\n\n"
        "<b>🔧 Прочее:</b>\n"
        "<code>/maintenance</code> — вкл/выкл тех.работы\n"
        "<code>/admin</code> — открыть эту панель",
        reply_markup=b.as_markup()
    )

# ── Заявки скриншоты ──
@router.callback_query(F.data == "adm_subs")
async def adm_subs_cb(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔", show_alert=True); return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM submissions WHERE status='pending' ORDER BY created_at ASC LIMIT 10"
        ) as c:
            subs = await c.fetchall()
    if not subs:
        b = InlineKeyboardBuilder(); b.button(text="🔙 Назад", callback_data="adm_main")
        await cb.message.edit_text("📭 Нет заявок на скриншоты", reply_markup=b.as_markup()); return
    b = InlineKeyboardBuilder(); b.button(text="🔙 Назад", callback_data="adm_main")
    await cb.message.edit_text(
        f"📋 <b>Заявок на скриншоты: {len(subs)}</b>\n\n"
        f"Используй /submissions для обработки каждой заявки с фото.",
        reply_markup=b.as_markup()
    )

# ── Заявки на вывод ──
@router.callback_query(F.data == "adm_withdrawals")
async def adm_withdrawals_cb(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔", show_alert=True); return
    pending = await get_pending_withdrawals()
    if not pending:
        b = InlineKeyboardBuilder(); b.button(text="🔙 Назад", callback_data="adm_main")
        await cb.message.edit_text("📭 Нет активных заявок на вывод", reply_markup=b.as_markup()); return
    b_nav = InlineKeyboardBuilder(); b_nav.button(text="🔙 Назад", callback_data="adm_main")
    await cb.message.edit_text(
        f"📤 <b>Заявок на вывод: {len(pending)}</b>\n\nИспользуй /withdrawals для обработки.",
        reply_markup=b_nav.as_markup()
    )

# ── Отмена состояния ──
@router.callback_query(F.data == "adm_cancel_state")
async def adm_cancel_state(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔", show_alert=True); return
    await state.clear()
    text = await admin_main_text()
    await cb.message.edit_text(text, reply_markup=admin_main_kb(MAINTENANCE))


@router.message(Command("setcasephoto"))
async def set_case_photo_cmd(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        return
    parts = m.text.split()
    if len(parts) < 2:
        await m.answer("Использование: /setcasephoto grand\nЗатем отправь фото с подписью /setcasephoto grand")
        return
    await m.answer(f"✅ Теперь отправь фото с подписью: setcasephoto {parts[1]}")

@router.message(F.photo)
async def handle_admin_photo(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        return
    if not m.caption or not m.caption.startswith("setcasephoto "):
        return
    parts = m.caption.split()
    if len(parts) < 2:
        return
    case_key = parts[1].lower()
    if case_key not in CASES:
        await m.answer(f"❌ Кейс '{case_key}' не найден. Доступны: {', '.join(CASES.keys())}")
        return
    photo_id = m.photo[-1].file_id
    CASES[case_key]["photo"] = photo_id
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO case_photos (case_key, photo_id) VALUES (?, ?)",
            (case_key, photo_id)
        )
        await db.commit()
    await m.answer(
        f"✅ Фото установлено и сохранено в базу данных!\n\n"
        f"Теперь фото <b>не слетит</b> при перезапуске бота 🎉"
    )

# ─── Callbacks: navigation ───

@router.callback_query(F.data == "back_main")
async def back(cb: CallbackQuery):
    global MAINTENANCE
    if MAINTENANCE and cb.from_user.id not in ADMIN_IDS:
        await cb.answer("🔧 Технические работы! Бот временно недоступен.", show_alert=True)
        return
    u = await get_user(cb.from_user.id)
    text = f"🏠 Главное меню\n💰 Баланс: <b>{u['coins']} монет</b>"
    try:
        await cb.message.edit_text(text, reply_markup=mmk())
    except Exception:
        await cb.message.delete()
        await cb.message.answer(text, reply_markup=mmk())

@router.callback_query(F.data == "daily")
async def daily(cb: CallbackQuery, bot: Bot):
    ok = await claim_daily(cb.from_user.id, DAILY_BONUS)
    if ok:
        u = await get_user(cb.from_user.id)
        streak_msg = f"\n📅 Стрик: {u['streak']} дней!" if u else ""
        await cb.answer(f"✅ +{DAILY_BONUS} монет! Заходи завтра.{streak_msg}", show_alert=True)
        await check_achievements(cb.from_user.id, bot)
    else:
        await cb.answer("⏳ Бонус уже получен сегодня!", show_alert=True)

# ─── Stats ───

@router.callback_query(F.data == "menu_stats")
async def stats_cb(cb: CallbackQuery):
    u = await get_user(cb.from_user.id)
    items = await get_inv(cb.from_user.id)
    total_inv_val = sum(i["value"] for i in items)
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Назад", callback_data="back_main")
    await cb.message.edit_text(
        f"📊 <b>Личная статистика</b>\n\n"
        f"💰 Баланс: <b>{u['coins']} монет</b>\n"
        f"📦 Кейсов открыто: <b>{u['total_cases']}</b>\n"
        f"💸 Потрачено всего: <b>{u['total_spent']} монет</b>\n"
        f"🟡 Легендарных дропов: <b>{u['total_legendaries']}</b>\n"
        f"🎒 Предметов в инвентаре: <b>{len(items)}</b>\n"
        f"💎 Стоимость инвентаря: <b>{total_inv_val} монет</b>\n"
        f"📅 Стрик: <b>{u['streak']} дней</b>",
        reply_markup=b.as_markup()
    )

# ─── Daily Tasks ───

async def show_daily_tasks(uid, target):
    task_rows = await get_today_tasks(uid)
    lines = []
    for task in DAILY_TASKS:
        row = task_rows.get(task["id"], {"progress": 0, "completed": 0})
        if row["completed"]:
            lines.append(f"✅ {task['desc']} (+{task['reward']} монет)")
        else:
            prog = row["progress"]
            goal = task["goal"]
            lines.append(f"⬜ {task['desc']} [{prog}/{goal}] +{task['reward']} монет")
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Назад", callback_data="back_main")
    text = "📋 <b>Ежедневные задания</b>\n<i>Обновляются каждый день в 00:00</i>\n\n" + "\n".join(lines)
    if isinstance(target, Message):
        await target.answer(text, reply_markup=b.as_markup())
    else:
        await target.message.edit_text(text, reply_markup=b.as_markup())

@router.callback_query(F.data == "menu_daily_tasks")
async def daily_tasks_cb(cb: CallbackQuery):
    await show_daily_tasks(cb.from_user.id, cb)

# ─── Achievements ───

async def show_achievements(uid, target):
    u = await get_user(uid)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT achievement_id FROM achievements WHERE user_id=?", (uid,)) as c:
            earned = {r[0] for r in await c.fetchall()}
    lines = []
    for ach in ACHIEVEMENTS:
        if ach["id"] in earned:
            lines.append(f"✅ <b>{ach['name']}</b> — {ach['desc']}")
        else:
            # Показываем прогресс без суммы награды
            t = ach["type"]
            if u:
                if t == "total_cases":  cur = u["total_cases"]
                elif t == "legendaries": cur = u["total_legendaries"]
                elif t == "streak":      cur = u["streak"]
                elif t == "coins":       cur = u["coins"]
                else:                    cur = 0
                cur = min(cur, ach["goal"])
                prog_str = f" [{cur}/{ach['goal']}]"
            else:
                prog_str = ""
            lines.append(f"🔒 <b>{ach['name']}</b> — {ach['desc']}{prog_str}")
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Назад", callback_data="back_main")
    text = f"🏅 <b>Достижения</b> ({len(earned)}/{len(ACHIEVEMENTS)})\n\n" + "\n".join(lines)
    if isinstance(target, Message):
        await target.answer(text, reply_markup=b.as_markup())
    else:
        await target.message.edit_text(text, reply_markup=b.as_markup())

@router.callback_query(F.data == "menu_achievements")
async def achievements_cb(cb: CallbackQuery):
    await show_achievements(cb.from_user.id, cb)

# ─── Referral ───

@router.callback_query(F.data == "menu_ref")
async def ref_cb(cb: CallbackQuery, bot: Bot):
    code = await get_or_create_ref(cb.from_user.id)
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={code}"
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT total_refs FROM referrals WHERE user_id=?", (cb.from_user.id,)) as c:
            row = await c.fetchone()
    total = row[0] if row else 0
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Назад", callback_data="back_main")
    await cb.message.edit_text(
        f"👥 <b>Реферальная программа</b>\n\n"
        f"Приглашай друзей и получай <b>+50 монет</b> за каждого!\n"
        f"Друг тоже получит <b>+50 монет</b> 🎁\n\n"
        f"🔗 Твоя ссылка:\n<code>{link}</code>\n\n"
        f"👫 Приглашено друзей: <b>{total}</b>",
        reply_markup=b.as_markup()
    )

# ─── Inventory (with sell) ───

@router.callback_query(F.data == "menu_inventory")
async def inv(cb: CallbackQuery):
    items = await get_inv(cb.from_user.id)
    b = InlineKeyboardBuilder()
    if not items:
        txt = "🎒 Инвентарь пуст!\n\nОткрывай кейсы!"
    else:
        txt = "🎒 <b>Твой инвентарь</b> (нажми на предмет чтобы продать):\n\n"
        for item in items[:15]:
            b.button(
                text=f"{item['emoji']} {item['item_name']} (+{item['value']}💰)",
                callback_data=f"sell_confirm_{item['id']}"
            )
        b.adjust(1)
    b.button(text="🔙 Назад", callback_data="back_main")
    await cb.message.edit_text(txt, reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("sell_confirm_"))
async def sell_confirm(cb: CallbackQuery):
    item_id = int(cb.data.split("_")[2])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM inventory WHERE id=? AND user_id=?", (item_id, cb.from_user.id)) as c:
            item = await c.fetchone()
    if not item:
        await cb.answer("❌ Предмет не найден", show_alert=True)
        return
    b = InlineKeyboardBuilder()
    b.button(text=f"✅ Продать за {item['value']} монет", callback_data=f"sell_do_{item_id}")
    b.button(text="❌ Отмена", callback_data="menu_inventory")
    b.adjust(1)
    await cb.message.edit_text(
        f"💰 <b>Продажа предмета</b>\n\n"
        f"{item['emoji']} <b>{item['item_name']}</b>\n"
        f"Редкость: {RARITY.get(item['rarity'])}\n\n"
        f"Цена продажи: <b>{item['value']} монет</b>",
        reply_markup=b.as_markup()
    )

@router.callback_query(F.data.startswith("sell_do_"))
async def sell_do(cb: CallbackQuery, bot: Bot):
    item_id = int(cb.data.split("_")[2])
    item = await sell_inv_item(cb.from_user.id, item_id)
    if not item:
        await cb.answer("❌ Предмет не найден", show_alert=True)
        return
    u = await get_user(cb.from_user.id)
    await update_task_progress(cb.from_user.id, "sell_item", 1, bot)
    await check_achievements(cb.from_user.id, bot)
    b = InlineKeyboardBuilder()
    b.button(text="🎒 Инвентарь", callback_data="menu_inventory")
    b.button(text="🔙 Меню",     callback_data="back_main")
    b.adjust(2)
    await cb.message.edit_text(
        f"✅ <b>Продано!</b>\n\n"
        f"{item['emoji']} <b>{item['item_name']}</b>\n"
        f"💰 +<b>{item['value']} монет</b>\n"
        f"📊 Баланс: <b>{u['coins']} монет</b>",
        reply_markup=b.as_markup()
    )

# ─── Cases ───

@router.callback_query(F.data == "menu_cases")
async def cases(cb: CallbackQuery):
    u = await get_user(cb.from_user.id)
    coins = u["coins"] if u else 0
    b = InlineKeyboardBuilder()
    for k, c in CASES.items():
        enough = "✅" if coins >= c["price"] else "❌"
        b.button(text=f"{enough} {c['name']} — {c['price']} монет", callback_data=f"open_card_{k}")
    b.button(text="🔙 Назад", callback_data="back_main")
    b.adjust(1)
    text = (
        f"📦 <b>Выбери кейс</b>\n"
        f"💰 Твой баланс: <b>{coins} монет</b>\n\n"
        f"⚪ Обычный  🔵 Редкий  🟣 Эпический  🟡 Легендарный\n"
        f"✅ — хватает монет  ❌ — недостаточно"
    )
    try:
        await cb.message.edit_text(text, reply_markup=b.as_markup())
    except Exception:
        await cb.message.delete()
        await cb.message.answer(text, reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("open_"))
async def open_case(cb: CallbackQuery, bot: Bot):
    key = cb.data.replace("open_", "")
    # Если это переход к карточке кейса (из каталога)
    if key.startswith("card_"):
        case_key = key.replace("card_", "")
        if case_key not in CASES:
            await cb.answer("Кейс не найден", show_alert=True)
            return
        c = CASES[case_key]
        u = await get_user(cb.from_user.id)
        coins = u["coins"] if u else 0
        enough = coins >= c["price"]
        b = InlineKeyboardBuilder()
        if enough:
            b.button(text=f"📦 Открыть — {c['price']} монет", callback_data=f"open_do_{case_key}")
        else:
            b.button(text=f"❌ Недостаточно монет ({coins}/{c['price']})", callback_data="menu_buy_coins")
        b.button(text="🔙 К кейсам", callback_data="menu_cases")
        b.adjust(1)
        caption = (
            f"📦 <b>{c['name']}</b> — {c['price']} монет\n\n"
            f"Описание:\n{c['desc']}\n\n"
            f"💰 Твой баланс: <b>{coins} монет</b>"
        )
        photo_id = c.get("photo")
        if photo_id and len(photo_id) > 10:
            try:
                await cb.message.delete()
                await cb.message.answer_photo(photo=photo_id, caption=caption, reply_markup=b.as_markup())
            except Exception:
                await cb.message.edit_text(caption, reply_markup=b.as_markup())
        else:
            try:
                await cb.message.edit_text(caption, reply_markup=b.as_markup())
            except Exception:
                pass
        return

    # Прямое открытие (open_do_KEY)
    if key.startswith("do_"):
        key = key.replace("do_", "")
    if key not in CASES:
        await cb.answer("Кейс не найден", show_alert=True)
        return
    await do_open_case(cb, bot, key)

async def do_open_case(cb: CallbackQuery, bot: Bot, key: str):
    c = CASES[key]
    u = await get_user(cb.from_user.id)
    if u["coins"] < c["price"]:
        await cb.answer(f"❌ Нужно {c['price']} монет, у тебя {u['coins']}", show_alert=True)
        return
    if not await spend_coins(cb.from_user.id, c["price"]):
        await cb.answer("❌ Ошибка", show_alert=True)
        return

    item = random.choices(c["items"], weights=c["weights"], k=1)[0]
    await add_inv(cb.from_user.id, item)

    # Анимация открытия
    frames = ["🎰 ⬜⬜⬜⬜⬜","🎰 🟦⬜⬜⬜⬜","🎰 🟦🟦⬜⬜⬜","🎰 🟦🟦🟦⬜⬜","🎰 🟦🟦🟦🟦⬜","🎰 🟦🟦🟦🟦🟦","✨ Открываем..."]
    try:
        msg = await cb.message.edit_text(frames[0])
    except Exception:
        msg = await cb.message.answer(frames[0])
    for f in frames[1:]:
        await asyncio.sleep(0.4)
        await msg.edit_text(f)

    upd = await get_user(cb.from_user.id)

    # Достижения проверяем для бейджа (без уведомлений)
    new_achs = await check_achievements(cb.from_user.id)
    ach_badge = f"🏅 Достижения ({len(new_achs)} новых!)" if new_achs else "🏅 Достижения"

    b = InlineKeyboardBuilder()
    b.button(text="🔄 Ещё раз",   callback_data=f"open_do_{key}")
    b.button(text="📦 Кейсы",     callback_data="menu_cases")
    b.button(text=f"💰 Продать +{item['value']}", callback_data=f"sell_confirm_{await get_last_inv_id(cb.from_user.id)}")
    b.button(text=ach_badge,       callback_data="menu_achievements")
    b.button(text="🔙 Меню",      callback_data="back_main")
    b.adjust(2, 1, 1, 1)

    # Сначала показываем дроп
    await msg.edit_text(
        f"🎁 <b>{c['name']}</b>\n\n"
        f"Ты получил:\n{item['emoji']} <b>{item['name']}</b>\n"
        f"✨ Редкость: <b>{RARITY.get(item['rarity'])}</b>\n"
        f"💎 Стоимость: <b>{item['value']} монет</b>\n\n"
        f"💰 Баланс: <b>{upd['coins']} монет</b>",
        reply_markup=b.as_markup()
    )

    # Только ПОСЛЕ показа дропа — обновляем задания
    await update_task_progress(cb.from_user.id, "open_cases", 1, bot)
    await update_task_progress(cb.from_user.id, "spend_coins", c["price"], bot)
    if key == "tactical":
        await update_task_progress(cb.from_user.id, "open_tactical", 1, bot)
    if key == "elite":
        await update_task_progress(cb.from_user.id, "open_elite", 1, bot)
    if item["rarity"] in ("rare", "epic", "legendary"):
        await update_task_progress(cb.from_user.id, "get_rare", 1, bot)
    if item["rarity"] in ("epic", "legendary"):
        await update_task_progress(cb.from_user.id, "get_epic", 1, bot)
    if item["rarity"] == "legendary":
        await update_task_progress(cb.from_user.id, "get_legendary", 1, bot)

async def get_last_inv_id(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT MAX(id) FROM inventory WHERE user_id=?", (uid,)) as c:
            row = await c.fetchone()
            return row[0] or 0

# ─── Submit (screenshot) ───

@router.callback_query(F.data == "menu_submit")
async def submit_menu(cb: CallbackQuery):
    b = InlineKeyboardBuilder()
    b.button(text="📸 Отправить скриншот", callback_data="submit_start")
    b.button(text="🔙 Назад", callback_data="back_main")
    b.adjust(1)
    await cb.message.edit_text(
        "🎯 <b>Получи монеты за покупку в игре</b>\n\n"
        "1️⃣ Купи скин в Rapira Online\n"
        "2️⃣ Сделай скриншот\n"
        "3️⃣ Отправь боту — получи монеты!\n\n"
        "⏱ Проверка до 24 часов",
        reply_markup=b.as_markup()
    )

@router.callback_query(F.data == "submit_start")
async def submit_start(cb: CallbackQuery, state: FSMContext):
    await state.set_state(SS.photo)
    await cb.message.edit_text("📸 Отправь скриншот покупки из Rapira Online:")

@router.message(SS.photo, F.photo)
async def recv_photo(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    pid = m.photo[-1].file_id
    uname = m.from_user.username or m.from_user.first_name
    await create_sub(m.from_user.id, uname, pid)
    sid = await last_sub_id()
    b = InlineKeyboardBuilder()
    for coins in [100, 200, 300, 500]:
        b.button(text=f"✅ +{coins}", callback_data=f"approve_{sid}_{coins}")
    b.button(text="❌ Отклонить", callback_data=f"reject_ask_{sid}")
    b.adjust(4, 1)
    for aid in ADMIN_IDS:
        try:
            await bot.send_photo(aid, photo=pid, caption=f"📨 <b>Заявка #{sid}</b>\n👤 @{uname} (ID: {m.from_user.id})", reply_markup=b.as_markup())
        except Exception:
            pass
    await m.answer("✅ Скриншот отправлен! Ожидай начисления монет.")

@router.message(SS.photo)
async def wrong_photo(m: Message):
    await m.answer("❗ Отправь фото!")

@router.callback_query(F.data.startswith("approve_"))
async def approve(cb: CallbackQuery, bot: Bot):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔ Нет доступа", show_alert=True)
        return
    parts = cb.data.split("_")
    sid, reward = int(parts[1]), int(parts[2])
    uid = await approve_sub(sid, reward)
    if not uid:
        await cb.answer("Не найдено", show_alert=True)
        return
    try:
        await bot.send_message(uid, f"🎉 Заявка <b>#{sid}</b> одобрена!\n💰 +<b>{reward} монет</b>")
    except Exception:
        pass
    await cb.message.edit_caption(caption=f"✅ #{sid} одобрена +{reward} монет", reply_markup=None)

@router.callback_query(F.data.startswith("reject_ask_"))
async def reject_ask(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔ Нет доступа", show_alert=True)
        return
    sid = int(cb.data.split("_")[2])
    await state.update_data(reject_sid=sid, admin_msg_id=cb.message.message_id)
    await state.set_state(SS.reject_reason)
    await cb.message.reply(f"✏️ Укажи причину отклонения заявки #{sid} (или напиши «-» без причины):")

@router.message(SS.reject_reason)
async def reject_with_reason(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    sid = data.get("reject_sid")
    reason = m.text.strip()
    uid = await reject_sub(sid, reason)
    if not uid:
        await m.answer("❌ Заявка не найдена")
        return
    reason_text = f"\n📝 Причина: {reason}" if reason != "-" else ""
    try:
        await bot.send_message(uid, f"❌ Заявка <b>#{sid}</b> отклонена.{reason_text}")
    except Exception:
        pass
    await m.answer(f"✅ Заявка #{sid} отклонена")

# ─── Buy Coins ───

@router.callback_query(F.data == "menu_buy_coins")
async def buy_coins_menu(cb: CallbackQuery):
    b = InlineKeyboardBuilder()
    b.button(text="⭐ Купить за Telegram Stars", callback_data="menu_stars")
    b.button(text="💛 Купить за Gold (Rapira)",  callback_data="menu_gold")
    b.button(text="🔙 Назад", callback_data="back_main")
    b.adjust(1)
    await cb.message.edit_text("💳 <b>Купить монеты</b>\n\nВыбери способ:", reply_markup=b.as_markup())

@router.callback_query(F.data == "menu_stars")
async def stars_menu(cb: CallbackQuery):
    b = InlineKeyboardBuilder()
    for k, p in STARS.items():
        b.button(text=f"⭐ {p['stars']} Stars → {p['label']}", callback_data=f"buy_{k}")
    b.button(text="🔙 Назад", callback_data="menu_buy_coins")
    b.adjust(1)
    await cb.message.edit_text("⭐ <b>Купить монеты за Telegram Stars</b>\n\nВыбери пакет:", reply_markup=b.as_markup())

@router.callback_query(F.data == "menu_gold")
async def gold_menu(cb: CallbackQuery, state: FSMContext):
    await state.set_state(SS.gold_custom_amount)
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Отмена", callback_data="menu_buy_coins")
    await cb.message.edit_text(
        "💛 <b>Купить монеты за Gold (Rapira)</b>\n\n"
        "📌 Курс: <b>1 Gold = 1 монета</b>\n"
        "📌 Минимальная сумма: <b>100 Gold</b>\n\n"
        "Введи количество Gold, которое хочешь потратить:",
        reply_markup=b.as_markup()
    )

@router.message(SS.gold_custom_amount)
async def gold_custom_input(m: Message, state: FSMContext):
    await state.clear()
    try:
        amount = int(m.text.strip())
    except ValueError:
        b = InlineKeyboardBuilder()
        b.button(text="🔙 Назад", callback_data="menu_gold")
        await m.answer("❌ Введи целое число (например: 500)", reply_markup=b.as_markup())
        return
    if amount < 100:
        b = InlineKeyboardBuilder()
        b.button(text="🔙 Назад", callback_data="menu_gold")
        await m.answer("❌ Минимальная сумма — <b>100 Gold</b>", reply_markup=b.as_markup())
        return
    b = InlineKeyboardBuilder()
    b.button(text="🔙 В меню", callback_data="back_main")
    await m.answer(
        f"💛 <b>Покупка за Gold — {amount} монет</b>\n\n"
        f"📋 <b>Инструкция:</b>\n\n"
        f"1️⃣ Напиши администратору: <b>{GOLD_ADMIN}</b>\n"
        f"2️⃣ Сообщи, что хочешь купить <b>{amount} монет</b> за Gold\n"
        f"3️⃣ Он выставит скины под твой бюджет\n"
        f"4️⃣ После сделки монеты будут начислены\n\n"
        f"💱 Курс: <b>1 Gold = 1 монета</b>\n"
        f"📌 Администратор: {GOLD_ADMIN}",
        reply_markup=b.as_markup()
    )

@router.callback_query(F.data.startswith("buy_"))
async def buy(cb: CallbackQuery, bot: Bot):
    k = cb.data.replace("buy_", "")
    if k not in STARS:
        await cb.answer("Не найдено", show_alert=True)
        return
    p = STARS[k]
    await bot.send_invoice(
        chat_id=cb.from_user.id,
        title=f"💰 {p['coins']} монет",
        description=f"Получи {p['coins']} монет для кейсов в Rapira Case Bot",
        payload=f"stars_{k}",
        currency="XTR",
        prices=[LabeledPrice(label=p["label"], amount=p["stars"])]
    )
    await cb.answer()

@router.pre_checkout_query()
async def precheckout(q: PreCheckoutQuery):
    await q.answer(ok=True)

@router.message(F.successful_payment)
async def paid(m: Message, bot: Bot):
    k = m.successful_payment.invoice_payload.split("_")[1]
    if k not in STARS:
        return
    p = STARS[k]
    await add_coins(m.from_user.id, p["coins"])
    bonus = await process_deposit_bonuses(m.from_user.id, p["coins"])
    granted = await grant_deposit_cases(m.from_user.id, p["coins"], bot)
    u = await get_user(m.from_user.id)
    await check_achievements(m.from_user.id, bot)

    bonus_line = f"\n🎁 Бонус промокода: <b>+{bonus} монет</b>" if bonus > 0 else ""
    case_lines = ""
    if granted:
        case_lines = "\n\n🎁 <b>Бонус за пополнение:</b>"
        for dc in granted:
            cname = CASES.get(dc["case_key"], {}).get("name", dc["case_key"])
            case_lines += f"\n➡️ <b>{dc['name']}</b> — кейс {cname} добавлен в твои бонусные кейсы!"
        case_lines += "\n\n📦 Открой через меню → <b>Бонусные кейсы</b>"

    await m.answer(
        f"✅ <b>Оплата прошла!</b>\n\n"
        f"💰 Зачислено: <b>+{p['coins']} монет</b>"
        f"{bonus_line}"
        f"{case_lines}\n\n"
        f"📊 Баланс: <b>{u['coins']} монет</b>"
    )

# ─── Промокод ───

@router.callback_query(F.data == "menu_promo")
async def promo_menu(cb: CallbackQuery, state: FSMContext):
    await state.set_state(SS.promo_input)
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Отмена", callback_data="back_main")
    await cb.message.edit_text(
        "🎟️ <b>Активация промокода</b>\n\n"
        "Введи промокод в чат:",
        reply_markup=b.as_markup()
    )

@router.message(SS.promo_input)
async def promo_activate(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    code = m.text.strip()
    coins, result, deposit_bonus_pct, youtuber_id = await use_promo(m.from_user.id, code)
    if result != "ok":
        await m.answer(result, reply_markup=back_btn())
        return
    u = await get_user(m.from_user.id)
    await check_achievements(m.from_user.id, bot)

    lines = [f"✅ <b>Промокод активирован!</b>\n\n🎟️ Код: <code>{code.upper()}</code>"]
    if coins and coins > 0:
        lines.append(f"🎁 Стартовый бонус: <b>+{coins} монет</b>")
    if deposit_bonus_pct and deposit_bonus_pct > 0:
        lines.append(f"💳 Бонус к пополнению: <b>+{deposit_bonus_pct}% к каждому депозиту</b>")
    lines.append(f"\n📊 Баланс: <b>{u['coins']} монет</b>")
    await m.answer("\n".join(lines), reply_markup=back_btn())

# ─── Бонусные кейсы (депозит-акции) ───

@router.callback_query(F.data == "menu_free_cases")
async def free_cases_menu(cb: CallbackQuery):
    uid = cb.from_user.id
    free = await get_free_cases(uid)
    active_promos = await list_deposit_cases(active_only=True)

    promo_lines = ""
    if active_promos:
        promo_lines = "\n\n🔥 <b>Текущие акции при пополнении:</b>"
        for dc in active_promos:
            cname = CASES.get(dc["case_key"], {}).get("name", dc["case_key"])
            promo_lines += f"\n➡️ <b>{dc['name']}</b> — от <b>{dc['min_deposit']} монет</b> → {cname}"

    if not free:
        b = InlineKeyboardBuilder()
        b.button(text="💳 Пополнить",  callback_data="menu_buy_coins")
        b.button(text="🔙 Назад",      callback_data="back_main")
        b.adjust(1)
        await cb.message.edit_text(
            f"🎰 <b>Бонусные кейсы</b>\n\n"
            f"У тебя нет бонусных кейсов.\n"
            f"Пополни баланс — получи кейс бесплатно!"
            f"{promo_lines}",
            reply_markup=b.as_markup()
        )
        return

    b = InlineKeyboardBuilder()
    for fc in free:
        cname = CASES.get(fc["case_key"], {}).get("name", fc["case_key"])
        b.button(text=f"🎰 Открыть {cname}", callback_data=f"open_free_{fc['id']}")
    b.button(text="🔙 Назад", callback_data="back_main")
    b.adjust(1)
    await cb.message.edit_text(
        f"🎰 <b>Бонусные кейсы</b>\n\n"
        f"У тебя <b>{len(free)}</b> бонусных кейса(-ов) от пополнений!"
        f"{promo_lines}",
        reply_markup=b.as_markup()
    )

@router.callback_query(F.data.startswith("open_free_"))
async def open_free_case(cb: CallbackQuery, bot: Bot):
    fc_id = int(cb.data.split("_")[2])
    case_key = await use_free_case(fc_id, cb.from_user.id)
    if not case_key:
        await cb.answer("❌ Кейс уже использован или не найден", show_alert=True)
        return
    if case_key not in CASES:
        await cb.answer("❌ Кейс не найден в базе", show_alert=True)
        return

    c = CASES[case_key]

    # Анимация
    frames = ["🎰 ⬜⬜⬜⬜⬜","🎰 🟨⬜⬜⬜⬜","🎰 🟨🟨⬜⬜⬜","🎰 🟨🟨🟨⬜⬜","🎰 🟨🟨🟨🟨⬜","🎰 🟨🟨🟨🟨🟨","✨ Открываем..."]
    try:
        msg = await cb.message.edit_text(frames[0])
    except Exception:
        msg = await cb.message.answer(frames[0])
    for f in frames[1:]:
        await asyncio.sleep(0.4)
        await msg.edit_text(f)

    item = random.choices(c["items"], weights=c["weights"], k=1)[0]
    await add_inv(cb.from_user.id, item)
    new_achs = await check_achievements(cb.from_user.id)
    ach_badge = f"🏅 Достижения ({len(new_achs)} новых!)" if new_achs else "🏅 Достижения"
    u = await get_user(cb.from_user.id)

    b2 = InlineKeyboardBuilder()
    b2.button(text="🎰 Мои бонусные кейсы",  callback_data="menu_free_cases")
    b2.button(text=f"💰 Продать +{item['value']}", callback_data=f"sell_confirm_{await get_last_inv_id(cb.from_user.id)}")
    b2.button(text=ach_badge,                callback_data="menu_achievements")
    b2.button(text="🔙 Меню",               callback_data="back_main")
    b2.adjust(1, 1, 1, 1)

    await msg.edit_text(
        f"🎰 <b>Бонусный кейс: {c['name']}</b>\n\n"
        f"Ты получил:\n{item['emoji']} <b>{item['name']}</b>\n"
        f"✨ Редкость: <b>{RARITY.get(item['rarity'])}</b>\n"
        f"💎 Стоимость: <b>{item['value']} монет</b>\n\n"
        f"💰 Баланс: <b>{u['coins']} монет</b>",
        reply_markup=b2.as_markup()
    )

    await update_task_progress(cb.from_user.id, "open_cases", 1, bot)
    if case_key == "tactical":
        await update_task_progress(cb.from_user.id, "open_tactical", 1, bot)
    if case_key == "elite":
        await update_task_progress(cb.from_user.id, "open_elite", 1, bot)
    if item["rarity"] in ("rare", "epic", "legendary"):
        await update_task_progress(cb.from_user.id, "get_rare", 1, bot)
    if item["rarity"] in ("epic", "legendary"):
        await update_task_progress(cb.from_user.id, "get_epic", 1, bot)
    if item["rarity"] == "legendary":
        await update_task_progress(cb.from_user.id, "get_legendary", 1, bot)

# ─── Депозит-кейсы — Админ ───

@router.message(Command("depositcase"))
async def depositcase_admin(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        return
    parts = m.text.split(maxsplit=4)
    if len(parts) < 2:
        await m.answer(
            "🎰 <b>Управление депозит-кейсами</b>\n\n"
            "<b>Создать акцию:</b>\n"
            "<code>/depositcase add КЛЮЧ_КЕЙСА МИН_ДЕПОЗИТ НАЗВАНИЕ</code>\n\n"
            "Пример:\n"
            "<code>/depositcase add oasis 480 Кейс Оазис Скинов</code>\n"
            "(при пополнении от 480 монет → бесплатный кейс oasis)\n\n"
            "<b>Доступные ключи кейсов:</b>\n"
            + ", ".join(f"<code>{k}</code>" for k in CASES.keys()) + "\n\n"
            "<code>/depositcase list</code> — список всех акций\n"
            "<code>/depositcase delete ID</code> — деактивировать акцию\n"
            "<code>/depositcase stats</code> — статистика выдачи"
        )
        return

    action = parts[1].lower()

    if action == "add":
        # /depositcase add КЛЮЧ МИН_ДЕПОЗИТ НАЗВАНИЕ...
        if len(parts) < 5:
            await m.answer(
                "Формат: /depositcase add КЛЮЧ_КЕЙСА МИН_ДЕПОЗИТ НАЗВАНИЕ\n"
                "Пример: /depositcase add oasis 480 Кейс Оазис Скинов"
            )
            return
        case_key = parts[2].lower()
        if case_key not in CASES:
            await m.answer(
                f"❌ Кейс <code>{case_key}</code> не найден!\n\n"
                f"Доступные: " + ", ".join(f"<code>{k}</code>" for k in CASES.keys())
            )
            return
        try:
            min_dep = int(parts[3])
        except ValueError:
            await m.answer("❌ МИН_ДЕПОЗИТ должен быть числом")
            return
        name = parts[4] if len(parts) > 4 else CASES[case_key]["name"]
        ok = await create_deposit_case(name, case_key, min_dep)
        if ok:
            cname = CASES[case_key]["name"]
            await m.answer(
                f"✅ <b>Депозит-акция создана!</b>\n\n"
                f"🎰 Акция: <b>{name}</b>\n"
                f"📦 Кейс: <b>{cname}</b>\n"
                f"💰 При пополнении от: <b>{min_dep} монет</b>\n\n"
                f"Теперь каждый, кто пополнит баланс от <b>{min_dep} монет</b>, "
                f"автоматически получит бесплатный кейс <b>{cname}</b>!"
            )
        else:
            await m.answer("❌ Ошибка создания акции")

    elif action == "list":
        cases_all = await get_deposit_case_stats()
        if not cases_all:
            await m.answer("📭 Депозит-акций нет")
            return
        lines = []
        for dc in cases_all:
            status = "✅" if dc["is_active"] else "❌"
            cname = CASES.get(dc["case_key"], {}).get("name", dc["case_key"])
            lines.append(
                f"{status} <b>#{dc['id']}</b> {dc['name']}\n"
                f"   📦 {cname} | от <b>{dc['min_deposit']} монет</b>\n"
                f"   📊 Выдано кейсов: <b>{dc['total_grants']}</b>"
            )
        await m.answer("🎰 <b>Депозит-акции:</b>\n\n" + "\n\n".join(lines))

    elif action == "delete":
        if len(parts) < 3:
            await m.answer("Формат: /depositcase delete ID")
            return
        try:
            dc_id = int(parts[2])
        except ValueError:
            await m.answer("❌ ID должен быть числом")
            return
        await delete_deposit_case(dc_id)
        await m.answer(f"✅ Акция #{dc_id} деактивирована")

    elif action == "stats":
        cases_all = await get_deposit_case_stats()
        if not cases_all:
            await m.answer("📭 Данных нет")
            return
        lines = []
        total = 0
        for dc in cases_all:
            cname = CASES.get(dc["case_key"], {}).get("name", dc["case_key"])
            lines.append(f"• <b>{dc['name']}</b> ({cname}): <b>{dc['total_grants']}</b> кейсов выдано")
            total += dc["total_grants"]
        lines.append(f"\n📦 Итого выдано: <b>{total}</b> бонусных кейсов")
        await m.answer("📊 <b>Статистика депозит-акций:</b>\n\n" + "\n".join(lines))

# ─── Тех.работы ───

@router.message(Command("maintenance"))
async def maintenance_toggle(m: Message):
    global MAINTENANCE
    if m.from_user.id not in ADMIN_IDS:
        return
    MAINTENANCE = not MAINTENANCE
    status = "🔴 ВКЛЮЧЕНЫ" if MAINTENANCE else "🟢 ВЫКЛЮЧЕНЫ"
    await m.answer(f"🔧 Технические работы: <b>{status}</b>")

# ─── Промокод — Админ команды ───

@router.message(Command("promo"))
async def promo_admin(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        return
    parts = m.text.split()
    if len(parts) < 2:
        await m.answer(
            "📋 <b>Управление промокодами</b>\n\n"
            "<b>Обычный промокод (фиксированные монеты):</b>\n"
            "<code>/promo create КОД МОНЕТЫ МАКС_ИСПОЛЬЗОВАНИЙ</code>\n"
            "Пример: <code>/promo create SALE100 500 50</code>\n\n"
            "<b>Бонусный промокод (+% к депозиту):</b>\n"
            "<code>/promo bonus КОД ПРОЦЕНТ [МАКС]</code>\n"
            "Пример: <code>/promo bonus BONUS10 10 9999</code>\n"
            "Юзер вводит — получает +10% к каждому пополнению навсегда\n\n"
            "<b>Промокод ютубера (+% к депозиту юзеру + % от депозита ютуберу):</b>\n"
            "<code>/promo youtuber КОД ID_ЮТУБЕРА %_ЮТУБЕРУ %_ЮЗЕРУ МОНЕТЫ_СРАЗУ [МАКС]</code>\n"
            "Пример: <code>/promo youtuber VASYA 123456789 5 5 0 9999</code>\n"
            "Юзер: +5% к каждому депозиту. Ютубер: +5% от каждого депозита юзера\n\n"
            "<code>/promo delete КОД</code> — деактивировать\n"
            "<code>/promo list</code> — список всех\n"
            "<code>/promo youtubers</code> — список ютуберов и их заработок"
        )
        return
    action = parts[1].lower()

    if action == "create":
        if len(parts) < 4:
            await m.answer("Формат: /promo create КОД МОНЕТЫ [МАКС_ИСПОЛЬЗОВАНИЙ]")
            return
        code = parts[2]
        try:
            coins = int(parts[3])
            max_uses = int(parts[4]) if len(parts) > 4 else 1
        except ValueError:
            await m.answer("❌ Неверный формат чисел")
            return
        ok = await create_promo(code, coins, max_uses)
        if ok:
            await m.answer(f"✅ Промокод <code>{code.upper()}</code> создан\n💰 {coins} монет | 👥 {max_uses} использований")
        else:
            await m.answer("❌ Такой промокод уже существует")

    elif action == "bonus":
        # /promo bonus КОД ПРОЦЕНТ МАКС
        # Промокод даёт +X% к каждому пополнению юзера
        if len(parts) < 4:
            await m.answer("Формат: /promo bonus КОД ПРОЦЕНТ [МАКС_ИСПОЛЬЗОВАНИЙ]\nПример: /promo bonus BONUS10 10 9999")
            return
        code = parts[2]
        try:
            bonus_pct = int(parts[3])
            max_uses = int(parts[4]) if len(parts) > 4 else 9999
        except ValueError:
            await m.answer("❌ Неверный формат чисел")
            return
        ok = await create_promo(code, 0, max_uses, bonus_percent=bonus_pct)
        if ok:
            await m.answer(
                f"✅ Бонусный промокод <code>{code.upper()}</code> создан\n\n"
                f"💳 Юзер получает <b>+{bonus_pct}% к каждому пополнению</b>\n"
                f"👥 Макс. активаций: {max_uses}"
            )
        else:
            await m.answer("❌ Такой промокод уже существует")

    elif action == "youtuber":
        # /promo youtuber КОД ID_ЮТУБЕРА %_ЮТУБЕРУ %_ЮЗЕРУ МОНЕТЫ_СРАЗУ [МАКС]
        # Юзер: при активации получает МОНЕТЫ_СРАЗУ + %_ЮЗЕРУ к каждому депозиту
        # Ютубер: получает %_ЮТУБЕРУ от каждого депозита своих зрителей
        if len(parts) < 7:
            await m.answer(
                "Формат: /promo youtuber КОД ID_ЮТУБЕРА %_ЮТУБЕРУ %_ЮЗЕРУ МОНЕТЫ_СРАЗУ [МАКС]\n\n"
                "Пример: /promo youtuber VASYA 123456789 5 5 0 9999\n"
                "→ Юзер: +5% к каждому депозиту\n"
                "→ Ютубер: +5% от каждого депозита зрителя"
            )
            return
        code = parts[2]
        try:
            yt_id = int(parts[3])
            yt_pct = int(parts[4])       # % ютуберу от депозита
            user_dep_pct = int(parts[5]) # % юзеру к депозиту (бонус к пополнению)
            coins = int(parts[6])        # монеты сразу при активации
            max_uses = int(parts[7]) if len(parts) > 7 else 9999
        except ValueError:
            await m.answer("❌ Неверный формат чисел")
            return
        # Получаем username ютубера
        try:
            yt_user = await m.bot.get_chat(yt_id)
            yt_username = yt_user.username or yt_user.first_name
        except Exception:
            yt_username = str(yt_id)
        ok = await create_promo(
            code, coins, max_uses,
            bonus_percent=user_dep_pct,
            youtuber_id=yt_id,
            youtuber_percent=yt_pct,
            youtuber_username=yt_username
        )
        if ok:
            await m.answer(
                f"✅ Промокод ютубера <code>{code.upper()}</code> создан\n\n"
                f"👤 Ютубер: @{yt_username} (ID: {yt_id})\n"
                f"🎁 Юзер при активации: {coins} монет сразу"
                + (f" + <b>+{user_dep_pct}%</b> к каждому депозиту" if user_dep_pct > 0 else "") + "\n"
                f"💸 Ютубер получает: <b>+{yt_pct}% от каждого депозита</b> своих зрителей\n"
                f"👥 Макс. активаций: {max_uses}\n\n"
                f"ℹ️ Ютубер видит статистику командой /mypromo"
            )
        else:
            await m.answer("❌ Такой промокод уже существует")

    elif action == "delete":
        if len(parts) < 3:
            await m.answer("Формат: /promo delete КОД")
            return
        await delete_promo(parts[2])
        await m.answer(f"✅ Промокод <code>{parts[2].upper()}</code> деактивирован")

    elif action == "list":
        promos = await list_promos()
        if not promos:
            await m.answer("Промокодов нет")
            return
        lines = []
        for p in promos:
            status = "✅" if p["is_active"] else "❌"
            extra = ""
            if p["bonus_percent"]:
                extra += f" | 🎁+{p['bonus_percent']}%"
            if p["youtuber_id"]:
                extra += f" | 👤ютубер {p['youtuber_percent']}%"
            lines.append(f"{status} <code>{p['code']}</code> — {p['coins']}💰 | {p['used_count']}/{p['max_uses']}{extra}")
        await m.answer("📋 <b>Промокоды:</b>\n\n" + "\n".join(lines))

    elif action == "youtubers":
        youtubers = await list_youtubers()
        if not youtubers:
            await m.answer("Ютуберов нет")
            return
        lines = []
        for y in youtubers:
            lines.append(
                f"👤 @{y['youtuber_username']} (ID: {y['youtuber_id']})\n"
                f"   🎟️ Промо: <code>{y['promo_code']}</code>\n"
                f"   💰 Всего заработано: {y['total_earned']} монет\n"
                f"   ⏳ К выводу: {y['pending']} монет\n"
                f"   ✅ Выведено: {y['withdrawn']} монет"
            )
        await m.answer("📊 <b>Ютуберы:</b>\n\n" + "\n\n".join(lines))

# ─── Команда ютубера /mypromo ───

@router.message(Command("mypromo"))
async def youtuber_mypromo(m: Message, bot: Bot):
    stats = await get_youtuber_stats(m.from_user.id)
    if not stats:
        await m.answer("❌ У тебя нет привязанного промокода.\nОбратись к администратору бота.")
        return
    b = InlineKeyboardBuilder()
    if stats["pending"] > 0:
        b.button(text=f"💸 Забрать {stats['pending']} монет", callback_data="youtuber_withdraw")
    b.button(text="🔙 Меню", callback_data="back_main")
    b.adjust(1)
    await m.answer(
        f"📊 <b>Твоя реферальная статистика</b>\n\n"
        f"🎟️ Твой промокод: <code>{stats['promo_code']}</code>\n\n"
        f"💰 Всего заработано: <b>{stats['total_earned']} монет</b>\n"
        f"⏳ Доступно к выводу: <b>{stats['pending']} монет</b>\n"
        f"✅ Уже выведено: <b>{stats['withdrawn']} монет</b>\n\n"
        f"ℹ️ Монеты начисляются за каждую трату твоих зрителей, активировавших твой промокод.",
        reply_markup=b.as_markup()
    )

@router.callback_query(F.data == "youtuber_withdraw")
async def youtuber_withdraw_cb(cb: CallbackQuery, bot: Bot):
    amount = await youtuber_withdraw(cb.from_user.id)
    if amount <= 0:
        await cb.answer("❌ Нечего выводить", show_alert=True)
        return
    # Уведомляем админов
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(
                aid,
                f"💸 <b>Запрос на вывод от ютубера</b>\n\n"
                f"👤 @{cb.from_user.username or cb.from_user.first_name} (ID: {cb.from_user.id})\n"
                f"💰 Сумма: <b>{amount} монет</b>\n\n"
                f"Свяжись с ютубером и выдай монеты вручную."
            )
        except Exception:
            pass
    await cb.message.edit_text(
        f"✅ <b>Заявка на вывод отправлена!</b>\n\n"
        f"💰 Сумма: <b>{amount} монет</b>\n\n"
        f"Администратор свяжется с тобой в ближайшее время.",
        reply_markup=None
    )

# ─── Вывод скинов ───

@router.callback_query(F.data == "menu_withdraw")
async def withdraw_menu(cb: CallbackQuery):
    items = await get_inv(cb.from_user.id)
    if not items:
        b = InlineKeyboardBuilder()
        b.button(text="📦 Открыть кейс", callback_data="menu_cases")
        b.button(text="🔙 Назад",        callback_data="back_main")
        b.adjust(1)
        await cb.message.edit_text(
            "📤 <b>Вывод скина</b>\n\n❌ Инвентарь пуст — нечего выводить!",
            reply_markup=b.as_markup()
        )
        return
    b = InlineKeyboardBuilder()
    # Показываем только epic и legendary (common/rare не выводим)
    withdrawable = [i for i in items if i["rarity"] in ("epic", "legendary")]
    if not withdrawable:
        b.button(text="🔙 Назад", callback_data="back_main")
        await cb.message.edit_text(
            "📤 <b>Вывод скина</b>\n\n"
            "Для вывода доступны только <b>Эпические 🟣</b> и <b>Легендарные 🟡</b> предметы.\n\n"
            "В твоём инвентаре таких нет.",
            reply_markup=b.as_markup()
        )
        return
    for item in withdrawable[:10]:
        b.button(
            text=f"{item['emoji']} {item['item_name']} ({item['value']}💰)",
            callback_data=f"withdraw_pick_{item['id']}"
        )
    b.button(text="🔙 Назад", callback_data="back_main")
    b.adjust(1)
    await cb.message.edit_text(
        "📤 <b>Вывод скина</b>\n\n"
        "Выбери предмет для вывода.\n"
        "Доступны: 🟣 Эпические и 🟡 Легендарные\n\n"
        "⚠️ Предмет будет удалён из инвентаря и передан администратору.",
        reply_markup=b.as_markup()
    )

@router.callback_query(F.data.startswith("withdraw_pick_"))
async def withdraw_pick(cb: CallbackQuery, state: FSMContext):
    item_id = int(cb.data.split("_")[2])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM inventory WHERE id=? AND user_id=?", (item_id, cb.from_user.id)) as c:
            item = await c.fetchone()
    if not item:
        await cb.answer("❌ Предмет не найден", show_alert=True)
        return
    await state.update_data(withdraw_item_id=item_id, item_name=item["item_name"], item_value=item["value"])
    await state.set_state(SS.withdraw_trade_link)
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data="back_main")
    await cb.message.edit_text(
        f"📤 <b>Вывод: {item['emoji']} {item['item_name']}</b>\n\n"
        f"💰 Стоимость: <b>{item['value']} монет</b>\n\n"
        f"Введи свой <b>ник в Rapira</b> или контакт для связи\n"
        f"(например: <code>MyCsGoNick</code>):",
        reply_markup=b.as_markup()
    )

@router.message(SS.withdraw_trade_link)
async def withdraw_confirm(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    item_id   = data["withdraw_item_id"]
    item_name = data["item_name"]
    item_value= data["item_value"]
    trade_link = m.text.strip()
    uname = m.from_user.username or m.from_user.first_name

    wid = await create_withdrawal(m.from_user.id, uname, item_id, item_name, item_value, trade_link)
    if not wid:
        await m.answer("❌ Предмет не найден или уже выведен", reply_markup=back_btn())
        return

    # Уведомление админам
    b_admin = InlineKeyboardBuilder()
    b_admin.button(text="✅ Выдан",          callback_data=f"wd_done_{wid}")
    b_admin.button(text="❌ Отменить",        callback_data=f"wd_cancel_{wid}")
    b_admin.adjust(2)
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(
                aid,
                f"📤 <b>Заявка на вывод #{wid}</b>\n\n"
                f"👤 @{uname} (ID: {m.from_user.id})\n"
                f"🎁 Предмет: <b>{item_name}</b>\n"
                f"💰 Стоимость: <b>{item_value} монет</b>\n"
                f"🔗 Контакт: <code>{trade_link}</code>",
                reply_markup=b_admin.as_markup()
            )
        except Exception:
            pass

    await m.answer(
        f"✅ <b>Заявка на вывод #{wid} принята!</b>\n\n"
        f"🎁 <b>{item_name}</b>\n\n"
        f"📩 Администратор <b>{WITHDRAW_ADMIN}</b> свяжется с тобой в ближайшее время.\n"
        f"Также можешь написать напрямую: {WITHDRAW_ADMIN}",
        reply_markup=back_btn()
    )

@router.callback_query(F.data.startswith("wd_done_"))
async def wd_done(cb: CallbackQuery, bot: Bot):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔ Нет доступа", show_alert=True)
        return
    wid = int(cb.data.split("_")[2])
    uid = await complete_withdrawal(wid)
    if not uid:
        await cb.answer("Не найдено", show_alert=True)
        return
    try:
        await bot.send_message(uid, f"✅ <b>Вывод #{wid} выполнен!</b>\n\nТвой скин передан. Спасибо за использование CaseRush! 🎉")
    except Exception:
        pass
    await cb.message.edit_text(
        cb.message.text + f"\n\n✅ <b>ВЫДАН</b> администратором @{cb.from_user.username}",
        reply_markup=None
    )

@router.callback_query(F.data.startswith("wd_cancel_"))
async def wd_cancel(cb: CallbackQuery, bot: Bot):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔ Нет доступа", show_alert=True)
        return
    wid = int(cb.data.split("_")[2])
    uid, item_name = await cancel_withdrawal(wid)
    if not uid:
        await cb.answer("Не найдено", show_alert=True)
        return
    try:
        await bot.send_message(
            uid,
            f"❌ <b>Вывод #{wid} отменён</b>\n\n"
            f"Предмет <b>{item_name}</b> возвращён в твой инвентарь.\n"
            f"По вопросам: {WITHDRAW_ADMIN}"
        )
    except Exception:
        pass
    await cb.message.edit_text(
        cb.message.text + f"\n\n❌ <b>ОТМЕНЁН</b> администратором @{cb.from_user.username}",
        reply_markup=None
    )

@router.message(Command("withdrawals"))
async def admin_withdrawals(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        return
    pending = await get_pending_withdrawals()
    if not pending:
        await m.answer("📭 Нет активных заявок на вывод")
        return
    for w in pending:
        b = InlineKeyboardBuilder()
        b.button(text="✅ Выдан",    callback_data=f"wd_done_{w['id']}")
        b.button(text="❌ Отменить", callback_data=f"wd_cancel_{w['id']}")
        b.adjust(2)
        await m.answer(
            f"📤 <b>Заявка #{w['id']}</b>\n\n"
            f"👤 @{w['username']} (ID: {w['user_id']})\n"
            f"🎁 <b>{w['item_name']}</b>\n"
            f"💰 {w['item_value']} монет\n"
            f"🔗 Контакт: <code>{w['trade_link']}</code>\n"
            f"📅 {w['created_at']}",
            reply_markup=b.as_markup()
        )

# ═══════════════════════════ MAIN ═══════════════════════════

async def main():
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await init_db()
    await bot.set_my_commands([
        BotCommand(command="start",        description="🏠 Главное меню"),
        BotCommand(command="case",         description="📦 Открыть кейс"),
        BotCommand(command="buy",          description="💳 Купить монеты"),
        BotCommand(command="coin",         description="💰 Мой баланс"),
        BotCommand(command="ref",          description="👥 Реферальная ссылка"),
        BotCommand(command="tasks",        description="📋 Ежедневные задания"),
        BotCommand(command="achievements", description="🏅 Мои достижения"),
        BotCommand(command="mypromo",      description="📊 Мой промокод (для ютуберов)"),
        BotCommand(command="depositcase",  description="🎰 Управление депозит-акциями (админ)"),
        BotCommand(command="help",         description="❓ Помощь"),
    ])
    print("✅ Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
