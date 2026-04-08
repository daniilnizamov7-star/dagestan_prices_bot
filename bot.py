import os
import sqlite3
import asyncio
import time
from contextlib import contextmanager
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# --- НАСТРОЙКИ ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("❌ Токен не найден! Проверь файл .env")

bot = Bot(token=TOKEN)
dp = Dispatcher()
DB_FILE = "prices.db"

MAX_LEN = {"product": 80, "store": 60, "address": 100}

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS prices (
        product TEXT, store TEXT, address TEXT, price REAL, updated_at TEXT,
        UNIQUE(product, store, address)
    )""")
    conn.commit()
    conn.close()

@contextmanager
def get_db():
    """Открываем соединение на каждый запрос — безопасно для async."""
    conn = sqlite3.connect(DB_FILE)
    try:
        yield conn
    finally:
        conn.close()

init_db()

# --- СЛОВАРИ ---
user_states = {}
user_data = {}
user_last_add = {}

def reset_user(user_id):
    user_states.pop(user_id, None)
    user_data.pop(user_id, None)

# --- КЛАВИАТУРЫ ---
def main_menu():
    kb = [
        [KeyboardButton(text="📊 Посмотреть цены"), KeyboardButton(text="➕ Добавить цену")],
        [KeyboardButton(text="🔍 Быстрый поиск"), KeyboardButton(text="📞 Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, input_field_placeholder="Выбери действие 👇")

def popular_products_kb():
    products = ["🥛 Молоко", "🍞 Хлеб", "🍗 Курица", "🥩 Говядина", "🍚 Рис", "🥚 Яйца", "🧈 Масло", "🍯 Финики"]
    kb = [[KeyboardButton(text=p) for p in products[i:i+2]] for i in range(0, len(products), 2)]
    kb.append([KeyboardButton(text="⬅️ Назад в меню")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def cancel_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_add")]]
    )

# --- ХЕНДЛЕРЫ ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    reset_user(m.from_user.id)
    await m.answer(
        "Ассаляму алейкум! 👋\n"
        "Я — помощник по ценам. Помогаю найти, где дешевле.\n\n"
        "Выбери кнопку внизу 👇",
        reply_markup=main_menu()
    )

@dp.message(F.text == "📞 Помощь")
async def cmd_help(m: types.Message):
    reset_user(m.from_user.id)
    await m.answer(
        "📖 *Как пользоваться:*\n\n"
        "➕ *Добавить цену:*\nНажми кнопку, и я сам всё спрошу по шагам.\n\n"
        "📊 *Посмотреть цены:*\nНапиши список через запятую (молоко, хлеб) или выбери из кнопок.\n\n"
        "⬅️ *Назад:*\nКнопка внизу вернёт тебя в главное меню.",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

@dp.message(F.text == "⬅️ Назад в меню")
async def cmd_back(m: types.Message):
    reset_user(m.from_user.id)
    await m.answer("🏠 Главное меню", reply_markup=main_menu())

# --- ДОБАВЛЕНИЕ ЦЕНЫ ---
@dp.message(F.text == "➕ Добавить цену")
async def start_add_price(m: types.Message):
    user_id = m.from_user.id
    if user_id in user_last_add:
        time_passed = time.time() - user_last_add[user_id]
        if time_passed < 60:
            wait_time = int(60 - time_passed)
            await m.answer(f"⏳ Подожди {wait_time} сек. перед следующим добавлением.")
            return
    user_states[user_id] = "WAIT_PRODUCT"
    user_data[user_id] = {}
    await m.answer(
        "📝 *Шаг 1/4: Какой товар?*\n"
        "Напиши название. Для порядка укажи объем/вес/процент:\n"
        "• молоко 1л 3.2%\n"
        "• хлеб белый 500г\n"
        "• яйца 10шт\n\n"
        "Так поиск будет точнее! 👇",
        parse_mode="Markdown",
        reply_markup=cancel_kb()
    )

@dp.callback_query(F.data == "cancel_add")
async def cancel_add_cb(cq: types.CallbackQuery):
    reset_user(cq.from_user.id)
    await cq.message.edit_text("❌ Добавление отменено.")
    # Отдельным сообщением возвращаем reply-клавиатуру
    await cq.message.answer("🏠 Главное меню", reply_markup=main_menu())
    await cq.answer()

@dp.message(lambda msg: user_states.get(msg.from_user.id) == "WAIT_PRODUCT")
async def get_product(m: types.Message):
    text = m.text.strip()
    if len(text) > MAX_LEN["product"]:
        await m.answer(f"❌ Слишком длинное название (макс. {MAX_LEN['product']} символов). Сократи.")
        return
    user_data[m.from_user.id]["product"] = text.lower()
    user_states[m.from_user.id] = "WAIT_PRICE"
    await m.answer(
        f"✅ Принято: {text}\n\n💰 *Шаг 2/4: Какая цена?*\nНапиши только число (например: 89).",
        parse_mode="Markdown",
        reply_markup=cancel_kb()
    )

@dp.message(lambda msg: user_states.get(msg.from_user.id) == "WAIT_PRICE")
async def get_price(m: types.Message):
    try:
        price = float(m.text.replace(",", "."))
        if price < 1 or price > 50000:
            await m.answer("❌ Цена выглядит неверной (меньше 1₽ или больше 50 000₽). Проверь и напиши снова.")
            return
        user_data[m.from_user.id]["price"] = price
        user_states[m.from_user.id] = "WAIT_STORE"
        await m.answer(
            f"✅ Цена: {int(price)} ₽\n\n🏪 *Шаг 3/4: Какой магазин?*\nНапиши название (Пятёрочка, Магнит).",
            parse_mode="Markdown",
            reply_markup=cancel_kb()
        )
    except ValueError:
        await m.answer("❌ Это не число. Попробуй ещё раз (только цифры).")

@dp.message(lambda msg: user_states.get(msg.from_user.id) == "WAIT_STORE")
async def get_store(m: types.Message):
    text = m.text.strip()
    if len(text) > MAX_LEN["store"]:
        await m.answer(f"❌ Слишком длинное название магазина (макс. {MAX_LEN['store']} символов).")
        return
    user_data[m.from_user.id]["store"] = text
    user_states[m.from_user.id] = "WAIT_ADDRESS"
    await m.answer(
        f"✅ Магазин: {text}\n\n📍 *Шаг 4/4: Адрес или район?*\nГде это находится? (ул. Ленина, Рынок).",
        parse_mode="Markdown",
        reply_markup=cancel_kb()
    )

@dp.message(lambda msg: user_states.get(msg.from_user.id) == "WAIT_ADDRESS")
async def get_address(m: types.Message):
    text = m.text.strip()
    if len(text) > MAX_LEN["address"]:
        await m.answer(f"❌ Слишком длинный адрес (макс. {MAX_LEN['address']} символов).")
        return
    data = user_data[m.from_user.id]
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """INSERT OR REPLACE INTO prices (product, store, address, price, updated_at)
               VALUES (?, ?, ?, ?, datetime('now', '+3 hours'))""",
            (data["product"], data["store"], text, data["price"])
        )
        conn.commit()
    user_last_add[m.from_user.id] = time.time()
    reset_user(m.from_user.id)
    await m.answer(
        f"✅ *Готово! Джазакя Аллаху хайран!*\n\n"
        f"📦 {data['product'].title()}: {int(data['price'])} ₽\n"
        f"🏪 {data['store']}\n"
        f"📍 {text}",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

# --- ПОИСК ---
@dp.message(F.text == "🔍 Быстрый поиск")
async def quick_search_menu(m: types.Message):
    await m.answer("🔍 Выбери товар:", reply_markup=popular_products_kb())

@dp.message(F.text.in_(["🥛 Молоко", "🍞 Хлеб", "🍗 Курица", "🥩 Говядина", "🍚 Рис", "🥚 Яйца", "🧈 Масло", "🍯 Финики"]))
async def popular_item_click(m: types.Message):
    clean_name = m.text.split(" ", 1)[1].lower()
    await search_single_item(m, clean_name)

@dp.message(F.text == "📊 Посмотреть цены")
async def ask_basket(m: types.Message):
    await m.answer(
        "📝 Напиши товары через запятую:\nПример: молоко, хлеб, курица",
        reply_markup=main_menu()
    )

@dp.message(F.text)
async def handle_text(m: types.Message):
    if m.from_user.id in user_states:
        await m.answer("⏳ Сейчас мы добавляем цену. Чтобы отменить, нажми ❌ в сообщении выше.")
        return
    text = m.text.lower().strip()
    if "," in text:
        items = [i.strip() for i in text.split(",") if i.strip()]
        await search_basket(m, items)
    else:
        await search_single_item(m, text)

@dp.message(F.voice)
async def handle_voice(m: types.Message):
    await m.answer(
        "🎤 Я пока не умею слушать голосовые. Пожалуйста, напиши текст.",
        reply_markup=main_menu()
    )

# --- ФУНКЦИИ ПОИСКА ---
async def search_single_item(m: types.Message, product_name: str):
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT product, store, address, price, updated_at FROM prices "
            "WHERE LOWER(product) LIKE ? ORDER BY price ASC",
            (f"%{product_name}%",)
        )
        rows = c.fetchall()

    if not rows:
        await m.answer(
            f"😔 По товару «{product_name}» пока нет данных.\n"
            "Ты можешь добавить цену! Нажми ➕ Добавить цену",
            reply_markup=main_menu()
        )
        return

    reply = f"🔍 *Результаты по запросу «{product_name.title()}»:*\n\n"
    for i, (prod_name, store, addr, price, updated) in enumerate(rows[:5], 1):
        date_str = updated.split(" ")[0] if updated else "—"
        reply += (
            f"{i}. 🏪 *{store}*\n"
            f"   📦 {prod_name.title()}\n"
            f"   📍 {addr}\n"
            f"   💰 {int(price)} ₽\n"
            f"   📅 {date_str}\n\n"
        )
    reply += "💡 Цены добавляют пользователи. Проверяйте на кассе."
    await m.answer(reply, parse_mode="Markdown", reply_markup=main_menu())

async def search_basket(m: types.Message, items: list):
    with get_db() as conn:
        c = conn.cursor()
        stores = {}
        for item in items:
            c.execute(
                "SELECT product, store, address, price FROM prices WHERE LOWER(product) LIKE ?",
                (f"%{item}%",)
            )
            for prod_name, store, addr, price in c.fetchall():
                if store not in stores:
                    stores[store] = {"addr": addr, "total": 0, "items": {}, "count": 0, "products_found": set()}
                if item not in stores[store]["products_found"]:
                    stores[store]["products_found"].add(item)
                    stores[store]["items"][item] = int(price)
                    stores[store]["total"] += int(price)
                    stores[store]["count"] += 1

    valid = sorted(
        [(n, d) for n, d in stores.items() if d["count"] >= 1],
        key=lambda x: (-x[1]["count"], x[1]["total"])
    )

    if not valid:
        await m.answer("😔 Пока нет данных по этому списку. Попробуй добавить товары.", reply_markup=main_menu())
        return

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]  # исправлено 4️⃣
    reply = f"🛒 *Твой список:* {', '.join(items).title()}\n\n"
    for i, (store, data) in enumerate(valid[:5], 1):
        medal = medals[i - 1] if i <= len(medals) else f"{i}."
        found_count = data["count"]
        total_count = len(items)
        reply += (
            f"{medal} *{store}* ({data['addr']})\n"
            f"   📦 Найдено: {found_count} из {total_count} товаров\n"
            f"   💰 Общая сумма: {data['total']} ₽\n"
        )
        for prod, pr in data["items"].items():
            reply += f"   • {prod.title()}: {pr} ₽\n"
        missing = [it for it in items if it not in data["items"]]
        if missing:
            reply += f"   ⚠️ Нет: {', '.join(missing).title()}\n"
        reply += "\n"

    full_stores = [(n, d) for n, d in valid if d["count"] == len(items)]
    if full_stores:
        best = full_stores[0]
        reply += f"✅ *Совет:* Все товары есть в *{best[0]}* за {best[1]['total']} ₽ — самый выгодный вариант!"
    else:
        best_partial = valid[0]
        reply += (
            f"⚠️ *Ни в одном магазине нет всего списка.*\n"
            f"💡 Лучше всего: *{best_partial[0]}* ({best_partial[1]['count']} из {len(items)} товаров) "
            f"за {best_partial[1]['total']} ₽"
        )

    await m.answer(reply, parse_mode="Markdown", reply_markup=main_menu())

async def main():
    print("✅ Бот запущен. Жду пользователей...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())