from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, PhotoSize
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
import database as db
from config import ADMIN_IDS

router = Router()

class SubmitStates(StatesGroup):
    waiting_photo = State()

# ── Инструкция для игрока ──────────────────────────────────
@router.callback_query(F.data == "menu_submit")
async def submit_menu(callback: CallbackQuery):
    text = (
        "🎯 <b>Задание: получи монеты за покупку</b>\n\n"
        "Как это работает:\n"
        "1️⃣ Купи скин или предмет в <b>Rapira Online</b>\n"
        "2️⃣ Сделай скриншот покупки из игры\n"
        "3️⃣ Отправь скриншот боту — нажми кнопку ниже\n"
        "4️⃣ Администратор проверит и начислит монеты\n\n"
        "💰 <b>Награда:</b> за каждую покупку ~100–500 монет\n"
        "⏱ Проверка: обычно до 24 часов"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="📸 Отправить скриншот", callback_data="submit_start")
    builder.button(text="🔙 Назад", callback_data="back_main")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@router.callback_query(F.data == "submit_start")
async def submit_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SubmitStates.waiting_photo)
    await callback.message.edit_text(
        "📸 Отправь скриншот покупки из игры Rapira Online\n\n"
        "❗ Важно: скриншот должен быть из игры, "
        "показывать купленный предмет и твой никнейм",
    )

@router.message(SubmitStates.waiting_photo, F.photo)
async def receive_photo(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    photo_id = message.photo[-1].file_id
    username = message.from_user.username or message.from_user.first_name

    await db.create_submission(message.from_user.id, username, photo_id)

    # Получаем ID последней заявки
    pending = await db.get_pending_submissions()
    sub_id = pending[-1]["id"] if pending else "?"

    # Уведомляем администраторов
    admin_text = (
        f"📨 <b>Новая заявка #{sub_id}</b>\n\n"
        f"👤 Пользователь: @{username} (ID: {message.from_user.id})\n"
        f"📸 Скриншот прикреплён ниже\n\n"
        f"Сколько монет начислить?"
    )
    builder = InlineKeyboardBuilder()
    for coins in [100, 200, 300, 500]:
        builder.button(text=f"✅ +{coins}", callback_data=f"approve_{sub_id}_{coins}")
    builder.button(text="❌ Отклонить", callback_data=f"reject_{sub_id}")
    builder.adjust(4, 1)

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_photo(
                admin_id,
                photo=photo_id,
                caption=admin_text,
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )
        except Exception:
            pass

    await message.answer(
        "✅ Скриншот отправлен на проверку!\n\n"
        "Администратор проверит и начислит монеты в течение 24 часов.\n"
        "Мы уведомим тебя о результате."
    )

@router.message(SubmitStates.waiting_photo)
async def wrong_type(message: Message):
    await message.answer("❗ Пожалуйста, отправь именно фото (скриншот), не файл и не текст.")

# ── Панель администратора ──────────────────────────────────
@router.callback_query(F.data.startswith("approve_"))
async def approve_handler(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    parts = callback.data.split("_")
    sub_id, reward = int(parts[1]), int(parts[2])

    user_id = await db.approve_submission(sub_id, reward)
    if not user_id:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    # Уведомляем игрока
    try:
        await bot.send_message(
            user_id,
            f"🎉 Твоя заявка <b>#{sub_id}</b> одобрена!\n\n"
            f"💰 Начислено: <b>+{reward} монет</b>\n\n"
            f"Открывай кейсы! /start",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await callback.message.edit_caption(
        caption=f"✅ Заявка #{sub_id} одобрена — начислено {reward} монет",
        reply_markup=None
    )

@router.callback_query(F.data.startswith("reject_"))
async def reject_handler(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    sub_id = int(callback.data.split("_")[1])
    user_id = await db.reject_submission(sub_id)

    if not user_id:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    try:
        await bot.send_message(
            user_id,
            f"❌ Твоя заявка <b>#{sub_id}</b> отклонена.\n\n"
            f"Возможная причина: скриншот не соответствует требованиям.\n"
            f"Попробуй снова — /start",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await callback.message.edit_caption(
        caption=f"❌ Заявка #{sub_id} отклонена",
        reply_markup=None
    )

# ── Команда для просмотра всех заявок ─────────────────────
@router.message(Command("pending"))
async def cmd_pending(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    submissions = await db.get_pending_submissions()
    if not submissions:
        await message.answer("✅ Нет ожидающих заявок")
        return
    await message.answer(f"📋 Ожидающих заявок: {len(submissions)}\nПроверяй уведомления от бота.")
