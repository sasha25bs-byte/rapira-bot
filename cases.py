from aiogram import Router, F, Bot
from aiogram.types import (
    CallbackQuery, Message, LabeledPrice,
    PreCheckoutQuery, SuccessfulPayment
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
import database as db
from config import STARS_PACKAGES

router = Router()

def stars_menu_kb():
    builder = InlineKeyboardBuilder()
    for key, pkg in STARS_PACKAGES.items():
        builder.button(
            text=f"⭐ {pkg['stars']} Stars → {pkg['label']}",
            callback_data=f"buy_{key}"
        )
    builder.button(text="🔙 Назад", callback_data="back_main")
    builder.adjust(1)
    return builder.as_markup()

@router.callback_query(F.data == "menu_stars")
async def stars_menu(callback: CallbackQuery):
    text = (
        "⭐ <b>Купить монеты за Telegram Stars</b>\n\n"
        "Stars — официальная валюта Telegram.\n"
        "Оплата мгновенная и безопасная.\n\n"
        "Выбери пакет:"
    )
    await callback.message.edit_text(text, reply_markup=stars_menu_kb(), parse_mode="HTML")

@router.callback_query(F.data.startswith("buy_"))
async def send_invoice(callback: CallbackQuery, bot: Bot):
    pkg_key = callback.data.replace("buy_", "")
    if pkg_key not in STARS_PACKAGES:
        await callback.answer("Пакет не найден", show_alert=True)
        return

    pkg = STARS_PACKAGES[pkg_key]
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"💰 {pkg['coins']} монет для Rapira Bot",
        description=f"Получи {pkg['coins']} монет для открытия кейсов в Rapira Case Bot",
        payload=f"stars_{pkg_key}_{callback.from_user.id}",
        currency="XTR",  # XTR = Telegram Stars
        prices=[LabeledPrice(label=pkg["label"], amount=pkg["stars"])],
    )
    await callback.answer()

@router.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery):
    # Всегда подтверждаем — Telegram требует ответа в течение 10 сек
    await pre_checkout_query.answer(ok=True)

@router.message(F.successful_payment)
async def payment_done(message: Message):
    payload = message.successful_payment.invoice_payload
    # payload формат: "stars_small_123456789"
    parts = payload.split("_")
    pkg_key = parts[1]

    if pkg_key not in STARS_PACKAGES:
        await message.answer("⚠️ Ошибка обработки платежа. Напиши администратору.")
        return

    pkg = STARS_PACKAGES[pkg_key]
    await db.add_coins(message.from_user.id, pkg["coins"])

    user = await db.get_user(message.from_user.id)
    await message.answer(
        f"✅ <b>Оплата прошла успешно!</b>\n\n"
        f"💰 Начислено: <b>+{pkg['coins']} монет</b>\n"
        f"📊 Твой баланс: <b>{user['coins']} монет</b>\n\n"
        f"Открывай кейсы! /start",
        parse_mode="HTML"
    )
