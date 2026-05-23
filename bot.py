import asyncio, logging, random, os
import aiosqlite
from datetime import date
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, LabeledPrice, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "0").split(",")]
DB_PATH = "/tmp/rapira.db"
START_BONUS = 300
DAILY_BONUS = 150
CASES = {
    "base": {"name": "🟢 Базовый кейс", "price": 100, "items": [
        {"name": "Скин AK-47 | Пустыня", "rarity": "common", "emoji": "⚪"},
        {"name": "Скин M4A4 | Лесной", "rarity": "common", "emoji": "⚪"},
        {"name": "Скин Desert Eagle | Огонь", "rarity": "rare", "emoji": "🔵"},
        {"name": "Нож | Базовый", "rarity": "epic", "emoji": "🟣"},
        {"name": "Агент | Призрак", "rarity": "legendary", "emoji": "🟡"},
    ], "weights": [45, 35, 15, 4, 1]},
    "tactical": {"name": "🔵 Тактический кейс", "price": 300, "items": [
        {"name": "Скин AWP | Охотник", "rarity": "common", "emoji": "⚪"},
        {"name": "Скин M4A1 | Хром", "rarity": "rare", "emoji": "🔵"},
        {"name": "Нож-бабочка | Синий", "rarity": "epic", "emoji": "🟣"},
        {"name": "Агент | Командир", "rarity": "epic", "emoji": "🟣"},
        {"name": "Нож-бабочка | Золотой", "rarity": "legendary", "emoji": "🟡"},
    ], "weights": [40, 30, 18, 9, 3]},
    "elite": {"name": "🟣 Элитный кейс", "price": 700, "items": [
        {"name": "Скин AK-47 | Дракон", "rarity": "rare", "emoji": "🔵"},
        {"name": "Нож | Тигровый", "rarity": "epic", "emoji": "🟣"},
        {"name": "Агент | Элита", "rarity": "epic", "emoji": "🟣"},
        {"name": "Нож | Рубиновый", "rarity": "legendary", "emoji": "🟡"},
        {"name": "Агент | Легенда Rapira", "rarity": "legendary", "emoji": "🟡"},
    ], "weights": [30, 30, 25, 10, 5]},
}
STARS = {
    "small": {"stars": 50, "coins": 500, "label": "500 монет"},
    "medium": {"stars": 150, "coins": 1700, "label": "1700 монет"},
    "large": {"stars": 300, "coins": 3600, "label": "3600 монет"},
}
RARITY = {"common": "Обычный", "rare": "Редкий", "epic": "Эпический", "legendary": "Легендарный"}

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, coins INTEGER DEFAULT 0, last_daily TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, item_name TEXT, rarity TEXT, emoji TEXT, obtained_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS submissions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, photo_id TEXT, reward INTEGER DEFAULT 0, status TEXT DEFAULT 'pending', created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        await db.commit()

async def get_user(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id=?", (uid,)) as c:
            return await c.fetchone()

async def reg_user(uid, uname, bonus):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id,username,coins) VALUES (?,?,?)", (uid, uname, bonus))
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
        await db.execute("UPDATE users SET coins=coins-? WHERE user_id=?", (amt, uid))
        await db.commit()
        return True

async def claim_daily(uid, bonus):
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT last_daily FROM users WHERE user_id=?", (uid,)) as c:
            row = await c.fetchone()
        if row and row[0] == today:
            return False
        await db.execute("UPDATE users SET coins=coins+?, last_daily=? WHERE user_id=?", (bonus, today, uid))
        await db.commit()
        return True

async def get_top():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT username,coins FROM users ORDER BY coins DESC LIMIT 10") as c:
            return await c.fetchall()

async def add_inv(uid, item):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO inventory (user_id,item_name,rarity,emoji) VALUES (?,?,?,?)", (uid, item["name"], item["rarity"], item["emoji"]))
        await db.commit()

async def get_inv(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM inventory WHERE user_id=? ORDER BY obtained_at DESC LIMIT 20", (uid,)) as c:
            return await c.fetchall()

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

async def reject_sub(sid):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM submissions WHERE id=?", (sid,)) as c:
            row = await c.fetchone()
        if not row:
            return None
        await db.execute("UPDATE submissions SET status='rejected' WHERE id=?", (sid,))
        await db.commit()
        return row[0]

router = Router()

class SS(StatesGroup):
    photo = State()

def mmk():
    b = InlineKeyboardBuilder()
    b.button(text="📦 Открыть кейс", callback_data="menu_cases")
    b.button(text="🎯 Задание", callback_data="menu_submit")
    b.button(text="💳 Купить монеты ⭐", callback_data="menu_stars")
    b.button(text="🎒 Инвентарь", callback_data="menu_inventory")
    b.button(text="🏆 Топ игроков", callback_data="menu_top")
    b.button(text="🎁 Ежедневный бонус", callback_data="daily")
    b.adjust(2, 2, 1, 1)
    return b.as_markup()

@router.message(CommandStart())
async def start(m: Message):
    u = await get_user(m.from_user.id)
    if not u:
        await reg_user(m.from_user.id, m.from_user.username or m.from_user.first_name, START_BONUS)
        txt = f"👋 Добро пожаловать в <b>Rapira Case Bot</b>!\n\n🎁 Стартовый бонус: <b>{START_BONUS} монет</b>!\n\nОткрывай кейсы и собирай скины 🏆"
    else:
        txt = f"👋 С возвращением, <b>{m.from_user.first_name}</b>!\n\n💰 Баланс: <b>{u['coins']} монет</b>"
    await m.answer(txt, reply_markup=mmk())

@router.callback_query(F.data == "back_main")
async def back(cb: CallbackQuery):
    u = await get_user(cb.from_user.id)
    await cb.message.edit_text(f"🏠 Главное меню\n💰 Баланс: <b>{u['coins']} монет</b>", reply_markup=mmk())

@router.callback_query(F.data == "daily")
async def daily(cb: CallbackQuery):
    ok = await claim_daily(cb.from_user.id, DAILY_BONUS)
    await cb.answer(f"✅ +{DAILY_BONUS} монет! Заходи завтра." if ok else "⏳ Бонус уже получен сегодня!", show_alert=True)

@router.callback_query(F.data == "menu_top")
async def top(cb: CallbackQuery):
    rows = await get_top()
    medals = ["🥇","🥈","🥉"]
    lines = [f"{medals[i] if i<3 else str(i+1)+'.'} <b>{r['username']}</b> — {r['coins']} монет" for i,r in enumerate(rows)]
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Назад", callback_data="back_main")
    await cb.message.edit_text("🏆 <b>Топ-10 игроков</b>\n\n" + "\n".join(lines), reply_markup=b.as_markup())

@router.callback_query(F.data == "menu_inventory")
async def inv(cb: CallbackQuery):
    items = await get_inv(cb.from_user.id)
    txt = "🎒 Инвентарь пуст!\n\nОткрывай кейсы!" if not items else "🎒 <b>Твой инвентарь</b>:\n\n" + "\n".join([f"{i['emoji']} {i['item_name']}" for i in items])
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Назад", callback_data="back_main")
    await cb.message.edit_text(txt, reply_markup=b.as_markup())

@router.callback_query(F.data == "menu_cases")
async def cases(cb: CallbackQuery):
    b = InlineKeyboardBuilder()
    for k, c in CASES.items():
        b.button(text=f"{c['name']} — {c['price']} монет", callback_data=f"open_{k}")
    b.button(text="🔙 Назад", callback_data="back_main")
    b.adjust(1)
    await cb.message.edit_text("📦 <b>Выбери кейс</b>\n\n⚪ Обычный\n🔵 Редкий\n🟣 Эпический\n🟡 Легендарный", reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("open_"))
async def open_case(cb: CallbackQuery):
    key = cb.data.replace("open_", "")
    if key not in CASES:
        await cb.answer("Кейс не найден", show_alert=True)
        return
    c = CASES[key]
    u = await get_user(cb.from_user.id)
    if not u:
        await cb.answer("Сначала напиши /start", show_alert=True)
        return
    if u["coins"] < c["price"]:
        await cb.answer(f"❌ Нужно {c['price']} монет, у тебя {u['coins']}", show_alert=True)
        return
    if not await spend_coins(cb.from_user.id, c["price"]):
        await cb.answer("❌ Ошибка", show_alert=True)
        return
    item = random.choices(c["items"], weights=c["weights"], k=1)[0]
    await add_inv(cb.from_user.id, item)
    upd = await get_user(cb.from_user.id)
    frames = ["🎰 ⬜⬜⬜⬜⬜","🎰 🟦⬜⬜⬜⬜","🎰 🟦🟦⬜⬜⬜","🎰 🟦🟦🟦⬜⬜","🎰 🟦🟦🟦🟦⬜","🎰 🟦🟦🟦🟦🟦","✨ Открываем..."]
    msg = await cb.message.edit_text(frames[0])
    for f in frames[1:]:
        await asyncio.sleep(0.5)
        await msg.edit_text(f)
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Ещё раз", callback_data=f"open_{key}")
    b.button(text="📦 Кейсы", callback_data="menu_cases")
    b.button(text="🔙 Меню", callback_data="back_main")
    b.adjust(2, 1)
    await msg.edit_text(f"🎁 <b>{c['name']}</b>\n\nТы получил:\n{item['emoji']} <b>{item['name']}</b>\n✨ Редкость: <b>{RARITY.get(item['rarity'])}</b>\n\n💰 Остаток: <b>{upd['coins']} монет</b>", reply_markup=b.as_markup())

@router.callback_query(F.data == "menu_submit")
async def submit_menu(cb: CallbackQuery):
    b = InlineKeyboardBuilder()
    b.button(text="📸 Отправить скриншот", callback_data="submit_start")
    b.button(text="🔙 Назад", callback_data="back_main")
    b.adjust(1)
    await cb.message.edit_text("🎯 <b>Получи монеты за покупку в игре</b>\n\n1️⃣ Купи скин в Rapira Online\n2️⃣ Сделай скриншот\n3️⃣ Отправь боту — получи монеты!\n\n⏱ Проверка до 24 часов", reply_markup=b.as_markup())

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
    b.button(text="❌ Отклонить", callback_data=f"reject_{sid}")
    b.adjust(4, 1)
    for aid in ADMIN_IDS:
        try:
            await bot.send_photo(aid, photo=pid, caption=f"📨 <b>Заявка #{sid}</b>\n👤 @{uname} (ID: {m.from_user.id})", reply_markup=b.as_markup())
        except Exception:
            pass
    await m.answer("✅ Скриншот отправлен! Ожидай начисления монет.")

@router.message(SS.photo)
async def wrong(m: Message):
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

@router.callback_query(F.data.startswith("reject_"))
async def reject(cb: CallbackQuery, bot: Bot):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("⛔ Нет доступа", show_alert=True)
        return
    sid = int(cb.data.split("_")[1])
    uid = await reject_sub(sid)
    if not uid:
        await cb.answer("Не найдено", show_alert=True)
        return
    try:
        await bot.send_message(uid, f"❌ Заявка <b>#{sid}</b> отклонена.")
    except Exception:
        pass
    await cb.message.edit_caption(caption=f"❌ #{sid} отклонена", reply_markup=None)

@router.callback_query(F.data == "menu_stars")
async def stars_menu(cb: CallbackQuery):
    b = InlineKeyboardBuilder()
    for k, p in STARS.items():
        b.button(text=f"⭐ {p['stars']} Stars → {p['label']}", callback_data=f"buy_{k}")
    b.button(text="🔙 Назад", callback_data="back_main")
    b.adjust(1)
    await cb.message.edit_text("⭐ <b>Купить монеты за Telegram Stars</b>\n\nВыбери пакет:", reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("buy_"))
async def buy(cb: CallbackQuery, bot: Bot):
    k = cb.data.replace("buy_", "")
    if k not in STARS:
        await cb.answer("Не найдено", show_alert=True)
        return
    p = STARS[k]
    await bot.send_invoice(chat_id=cb.from_user.id, title=f"💰 {p['coins']} монет",
        description=f"Получи {p['coins']} монет для кейсов", payload=f"stars_{k}",
        currency="XTR", prices=[LabeledPrice(label=p["label"], amount=p["stars"])])
    await cb.answer()

@router.pre_checkout_query()
async def precheckout(q: PreCheckoutQuery):
    await q.answer(ok=True)

@router.message(F.successful_payment)
async def paid(m: Message):
    k = m.successful_payment.invoice_payload.split("_")[1]
    if k not in STARS:
        return
    p = STARS[k]
    await add_coins(m.from_user.id, p["coins"])
    u = await get_user(m.from_user.id)
    await m.answer(f"✅ <b>Оплата прошла!</b>\n\n💰 +<b>{p['coins']} монет</b>\n📊 Баланс: <b>{u['coins']} монет</b>")

async def main():
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await init_db()
    print("✅ Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
