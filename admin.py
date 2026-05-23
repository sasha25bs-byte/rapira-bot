from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
import database as db
from config import START_BONUS, DAILY_BONUS

router = Router()

def main_menu_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="📦 Открыть кейс", callback_data="menu_cases")
    builder.button(text="🎁 Задание", callback_data="menu_submit")
    builder.button(text="💳 Купить монеты ⭐", callback_data="menu_stars")
    builder.button(text="🎒 Инвентарь", callback_data="menu_inventory")
    builder.button(text="🏆 Топ игроков", callback_data="menu_top")
    builder.button(text="🎯 Ежедневный бонус", callback_data="daily")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()

@router.message(CommandStart())
async def cmd_start(message: Message):
    user = await db.get_user(message.from_user.id)
    if not user:
        await db.register_user(
            message.from_user.id,
            message.from_user.username or message.from_user.first_name,
            START_BONUS
        )
        text = (
            f"👋 Добро пожаловать в <b>Rapira Case Bot</b>!\n\n"
            f"🎁 Тебе начислено <b>{START_BONUS} монет</b> за регистрацию!\n\n"
            f"Открывай кейсы, собирай скины и соревнуйся в топе 🏆"
        )
    else:
        text = (
            f"👋 С возвращением, <b>{message.from_user.first_name}</b>!\n\n"
            f"💰 Твой баланс: <b>{user['coins']} монет</b>"
        )
    await message.answer(text, reply_markup=main_menu_kb(), parse_mode="HTML")

@router.message(Command("balance"))
async def cmd_balance(message: Message):
    user = await db.get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала напиши /start")
        return
    await message.answer(f"💰 Твой баланс: <b>{user['coins']} монет</b>", parse_mode="HTML")

@router.callback_query(F.data == "daily")
async def daily_bonus(callback: CallbackQuery):
    success = await db.claim_daily(callback.from_user.id, DAILY_BONUS)
    if success:
        await callback.answer(f"✅ +{DAILY_BONUS} монет! Заходи завтра снова.", show_alert=True)
    else:
        await callback.answer("⏳ Ты уже получал бонус сегодня. Приходи завтра!", show_alert=True)

@router.callback_query(F.data == "menu_top")
async def show_top(callback: CallbackQuery):
    top = await db.get_top(10)
    lines = []
    medals = ["🥇", "🥈", "🥉"]
    for i, row in enumerate(top):
        medal = medals[i] if i < 3 else f"{i+1}."
        name = row["username"] or "Игрок"
        lines.append(f"{medal} <b>{name}</b> — {row['coins']} монет")
    text = "🏆 <b>Топ-10 игроков</b>\n\n" + "\n".join(lines)
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="back_main")
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@router.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    await callback.message.edit_text(
        f"🏠 Главное меню\n💰 Баланс: <b>{user['coins']} монет</b>",
        reply_markup=main_menu_kb(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "menu_inventory")
async def show_inventory(callback: CallbackQuery):
    items = await db.get_inventory(callback.from_user.id)
    if not items:
        text = "🎒 Твой инвентарь пуст\n\nОткрывай кейсы чтобы получить скины!"
    else:
        lines = [f"{item['emoji']} {item['item_name']}" for item in items]
        text = f"🎒 <b>Твой инвентарь</b> (последние 20):\n\n" + "\n".join(lines)
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="back_main")
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
