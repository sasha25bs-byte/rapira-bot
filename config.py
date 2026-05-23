import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_IDS = [int(os.environ.get("ADMIN_IDS", "0"))]

DAILY_BONUS = 150
START_BONUS = 300

CASES = {
    "base": {
        "name": "🟢 Базовый кейс",
        "price": 100,
        "items": [
            {"name": "Скин AK-47 | Пустыня", "rarity": "common", "emoji": "⚪"},
            {"name": "Скин M4A4 | Лесной", "rarity": "common", "emoji": "⚪"},
            {"name": "Скин Desert Eagle | Огонь", "rarity": "rare", "emoji": "🔵"},
            {"name": "Нож | Базовый", "rarity": "epic", "emoji": "🟣"},
            {"name": "Агент | Призрак", "rarity": "legendary", "emoji": "🟡"},
        ],
        "weights": [45, 35, 15, 4, 1],
    },
    "tactical": {
        "name": "🔵 Тактический кейс",
        "price": 300,
        "items": [
            {"name": "Скин AWP | Ночной охотник", "rarity": "common", "emoji": "⚪"},
            {"name": "Скин M4A1 | Хром", "rarity": "rare", "emoji": "🔵"},
            {"name": "Нож-бабочка | Синий", "rarity": "epic", "emoji": "🟣"},
            {"name": "Агент | Командир", "rarity": "epic", "emoji": "🟣"},
            {"name": "Нож-бабочка | Золотой", "rarity": "legendary", "emoji": "🟡"},
        ],
        "weights": [40, 30, 18, 9, 3],
    },
    "elite": {
        "name": "🟣 Элитный кейс",
        "price": 700,
        "items": [
            {"name": "Скин AK-47 | Дракон", "rarity": "rare", "emoji": "🔵"},
            {"name": "Нож | Тигровый", "rarity": "epic", "emoji": "🟣"},
            {"name": "Агент | Элита", "rarity": "epic", "emoji": "🟣"},
            {"name": "Нож | Рубиновый", "rarity": "legendary", "emoji": "🟡"},
            {"name": "Агент | Легенда Rapira", "rarity": "legendary", "emoji": "🟡"},
        ],
        "weights": [30, 30, 25, 10, 5],
    },
}

STARS_PACKAGES = {
    "small":  {"stars": 50,  "coins": 500,  "label": "💰 500 монет"},
    "medium": {"stars": 150, "coins": 1700, "label": "💰 1700 монет (+13%)"},
    "large":  {"stars": 300, "coins": 3600, "label": "💰 3600 монет (+20%)"},
}
