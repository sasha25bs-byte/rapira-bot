import random
from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
import database as db
from config import CASES

router = Router()

def cases_menu_kb():
    builder = InlineKeyboardBuilder()
    for key, case in CASES.items():
        builder.button(
            text=f"{case['name']} — {case['price']} монет",
            callback_data=f"open_{key}"
        )
    builder.button(text="🔙 Назад", callback_data="back_main")
    builder.adjust(1)
    return builder.as_markup()

def open_case(case_key: str) -> dict:
    case = CASES[case_key]
    item = random.choices(case["items"], weights=case["weights"], k=1)[0]
    return item

RARITY_NAMES = {
    "common": "Обычный",
    "rare": "Редкий",
    "epic": "Эпический",
    "legendary": "Легендарный",
}

@router.callback_query(F.data == "menu_cases")
async def show_cases(callback: CallbackQuery):
    text = (
        "📦 <b>Выбери кейс для открытия</b>\n\n"
        "⚪ Обычный — 60%\n"
        "🔵 Редкий — ~20%\n"
        "🟣 Эпический — ~15%\n"
        "🟡 Легендарный — ~5%\n"
    )
    await callback.message.edit_text(text, reply_markup=cases_menu_kb(), parse_mode="HTML")

@router.callback_query(F.data.startswith("open_"))
async def open_case_handler(callback: CallbackQuery):
    case_key = callback.data.replace("open_", "")
    if case_key not in CASES:
        await callback.answer("Кейс не найден", show_alert=True)
        return

    case = CASES[case_key]
    user = await db.get_user(callback.from_user.id)
    if not user:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return

    if user["coins"] < case["price"]:
        await callback.answer(
            f"❌ Недостаточно монет!\nНужно: {case['price']}, у тебя: {user['coins']}",
            show_alert=True
        )
        return

    success = await db.spend_coins(callback.from_user.id, case["price"])
    if not success:
        await callback.answer("❌ Ошибка списания монет", show_alert=True)
        return

    item = open_case(case_key)
    await db.add_to_inventory(callback.from_user.id, item)

    updated_user = await db.get_user(callback.from_user.id)
    rarity_name = RARITY_NAMES.get(item["rarity"], item["rarity"])

    # Анимация открытия
    opening_frames = ["🎰 Крутим...", "🎰 Крутим... 🎰", "⚡ Почти..."]
    msg = await callback.message.edit_text(opening_frames[0])
    import asyncio
    for frame in opening_frames[1:]:
        await asyncio.sleep(0.6)
        await msg.edit_text(frame)
    await asyncio.sleep(0.6)

    text = (
        f"🎁 <b>{case['name']}</b>\n\n"
        f"Ты получил:\n"
        f"{item['emoji']} <b>{item['name']}</b>\n"
        f"✨ Редкость: <b>{rarity_name}</b>\n\n"
        f"💰 Остаток: <b>{updated_user['coins']} монет</b>"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Открыть ещё", callback_data=f"open_{case_key}")
    builder.button(text="📦 Другие кейсы", callback_data="menu_cases")
    builder.button(text="🔙 Главное меню", callback_data="back_main")
    builder.adjust(2, 1)

    await msg.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
