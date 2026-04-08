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
PRICE_CHANGE_THRESHOLD = 0.20  # уведомлять если цена выросла/упала на 20%+
EXPIRY_DAYS = 30               # автоудаление записей старше 30 дней

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS prices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product TEXT,
        store TEXT,
        address TEXT,
        price REAL,
        user_id INTEGER,
        updated_at TEXT,
        UNIQUE(product, store, address)
    )""")
    # Миграция: добавляем user_id если его нет (для обновления с v1)
    try:
        c.execute("ALTER TABLE prices ADD COLUMN user_id INTEGER")
    except sqlite3.OperationalError:
        pass
    # Таблица для подписок на уведомления об изменении цен
    c.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
        user_id INTEGER,
        product TEXT,
        PRIMARY KEY (user_id, product)
    )""")
    conn.commit()
    conn.close()

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_FILE)
    try:
        yield conn
    finally:
        conn.close()

init_db()

# --- СОСТОЯНИЯ ---
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
        [KeyboardButton(text="🔍 Быстрый поиск"),   KeyboardButton(text="📋 Мои добавления")],
        [KeyboardButton(text="📈 Статистика"),       KeyboardButton(text="📞 Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, input_field_placeholder="Выбери действие 👇")

def popular_products_kb():
    products = ["🥛 Молоко", "🍞 Хлеб", "🍗 Курица", "🥩 Говядина",
                "🍚 Рис", "🥚 Яйца", "🧈 Масло", "🍯 Финики"]
    kb = [[KeyboardButton(text=p) for p in products[i:i+2]] for i in range(0, len(products), 2)]
    kb.append([KeyboardButton(text="⬅️ Назад в меню")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def cancel_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_add")]]
    )

def my_entries_kb(entries):
    """Инлайн-кнопки для списка своих записей."""
    kb = []
    for entry_id, product, store, price in entries:
        label = f"🗑 {product.title()} — {store} ({int(price)} ₽)"
        kb.append([InlineKeyboardButton(text=label, callback_data=f"del_{entry_id}")])
    kb.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="close_my")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- ХЕЛПЕРЫ ---
def check_price_change(conn, product, store, address, new_price) -> str | None:
    """Возвращает сообщение если цена изменилась значительно."""
    c = conn.cursor()
    c.execute(
        "SELECT price FROM prices WHERE product=? AND store=? AND address=?",
        (product, store, address)
    )
    row = c.fetchone()
    if not row:
        return None
    old_price = row[0]
    if old_price == 0:
        return None
    change = (new_price - old_price) / old_price
    if change >= PRICE_CHANGE_THRESHOLD:
        pct = int(change * 100)
        return f"📈 Цена выросла на {pct}% (было {int(old_price)} ₽)"
    if change <= -PRICE_CHANGE_THRESHOLD:
        pct = int(abs(change) * 100)
        return f"📉 Цена упала на {pct}% (было {int(old_price)} ₽)"
    return None

async def cleanup_old_entries():
    """Автоудаление записей старше EXPIRY_DAYS дней."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "DELETE FROM prices WHERE updated_at < datetime('now', '+3 hours', ?)",
            (f"-{EXPIRY_DAYS} days",)
        )
        deleted = c.rowcount
        conn.commit()
    if deleted:
        print(f"🧹 Удалено {deleted} устаревших записей")

async def cleanup_loop():
    """Запускает чистку каждые 24 часа."""
    while True:
        await asyncio.sleep(86400)
        await cleanup_old_entries()

# --- СТАРТ ---
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
        "➕ *Добавить цену* — пошагово, займёт 30 секунд.\n\n"
        "📊 *Посмотреть цены* — напиши список через запятую (молоко, хлеб).\n\n"
        "🔍 *Быстрый поиск* — выбери товар из кнопок.\n\n"
        "📋 *Мои добавления* — посмотри и удали свои записи.\n\n"
        "📈 *Статистика* — сколько товаров в базе и топ магазинов.\n\n"
        f"⚠️ Записи автоматически удаляются через {EXPIRY_DAYS} дней.",
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
        "Напиши название с объёмом/весом:\n"
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
    await cq.message.answer("🏠 Главное меню", reply_markup=main_menu())
    await cq.answer()

@dp.message(lambda msg: user_states.get(msg.from_user.id) == "WAIT_PRODUCT")
async def get_product(m: types.Message):
    text = m.text.strip()
    if len(text) > MAX_LEN["product"]:
        await m.answer(f"❌ Слишком длинное название (макс. {MAX_LEN['product']} символов).")
        return
    user_data[m.from_user.id]["product"] = text.lower()
    user_states[m.from_user.id] = "WAIT_PRICE"
    await m.answer(
        f"✅ Принято: *{text}*\n\n💰 *Шаг 2/4: Какая цена?*\nНапиши только число (например: 89).",
        parse_mode="Markdown",
        reply_markup=cancel_kb()
    )

@dp.message(lambda msg: user_states.get(msg.from_user.id) == "WAIT_PRICE")
async def get_price(m: types.Message):
    try:
        price = float(m.text.replace(",", "."))
        if price < 1 or price > 50000:
            await m.answer("❌ Цена выглядит неверной (меньше 1₽ или больше 50 000₽).")
            return
        user_data[m.from_user.id]["price"] = price
        user_states[m.from_user.id] = "WAIT_STORE"
        await m.answer(
            f"✅ Цена: *{int(price)} ₽*\n\n🏪 *Шаг 3/4: Какой магазин?*\nНапиши название (Пятёрочка, Магнит).",
            parse_mode="Markdown",
            reply_markup=cancel_kb()
        )
    except ValueError:
        await m.answer("❌ Это не число. Только цифры (например: 89 или 89.50).")

@dp.message(lambda msg: user_states.get(msg.from_user.id) == "WAIT_STORE")
async def get_store(m: types.Message):
    text = m.text.strip()
    if len(text) > MAX_LEN["store"]:
        await m.answer(f"❌ Слишком длинное название магазина (макс. {MAX_LEN['store']} символов).")
        return
    user_data[m.from_user.id]["store"] = text
    user_states[m.from_user.id] = "WAIT_ADDRESS"
    await m.answer(
        f"✅ Магазин: *{text}*\n\n📍 *Шаг 4/4: Адрес или район?*\nГде это? (ул. Ленина, Рынок, центр).",
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
    price_note = None
    with get_db() as conn:
        price_note = check_price_change(conn, data["product"], data["store"], text, data["price"])
        c = conn.cursor()
        c.execute(
            """INSERT OR REPLACE INTO prices (product, store, address, price, user_id, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now', '+3 hours'))""",
            (data["product"], data["store"], text, data["price"], m.from_user.id)
        )
        conn.commit()
    user_last_add[m.from_user.id] = time.time()
    reset_user(m.from_user.id)

    reply = (
        f"✅ *Готово! Джазакя Аллаху хайран!*\n\n"
        f"📦 {data['product'].title()}: {int(data['price'])} ₽\n"
        f"🏪 {data['store']}\n"
        f"📍 {text}"
    )
    if price_note:
        reply += f"\n\n{price_note}"

    await m.answer(reply, parse_mode="Markdown", reply_markup=main_menu())

# --- МОИ ДОБАВЛЕНИЯ ---
@dp.message(F.text == "📋 Мои добавления")
async def my_entries(m: types.Message):
    user_id = m.from_user.id
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, product, store, price FROM prices WHERE user_id=? ORDER BY updated_at DESC LIMIT 20",
            (user_id,)
        )
        rows = c.fetchall()
    if not rows:
        await m.answer(
            "😔 Ты ещё ничего не добавлял.\nНажми ➕ Добавить цену!",
            reply_markup=main_menu()
        )
        return
    await m.answer(
        f"📋 *Твои записи ({len(rows)} шт.):*\nНажми на запись чтобы удалить её 👇",
        parse_mode="Markdown",
        reply_markup=my_entries_kb(rows)
    )

@dp.callback_query(F.data.startswith("del_"))
async def delete_entry(cq: types.CallbackQuery):
    entry_id = int(cq.data.split("_")[1])
    user_id = cq.from_user.id
    with get_db() as conn:
        c = conn.cursor()
        # Проверяем что запись принадлежит этому пользователю
        c.execute("SELECT product, store FROM prices WHERE id=? AND user_id=?", (entry_id, user_id))
        row = c.fetchone()
        if not row:
            await cq.answer("❌ Запись не найдена или уже удалена.", show_alert=True)
            return
        product, store = row
        c.execute("DELETE FROM prices WHERE id=? AND user_id=?", (entry_id, user_id))
        conn.commit()
        # Обновляем список
        c.execute(
            "SELECT id, product, store, price FROM prices WHERE user_id=? ORDER BY updated_at DESC LIMIT 20",
            (user_id,)
        )
        remaining = c.fetchall()

    await cq.answer(f"🗑 Удалено: {product.title()} — {store}")
    if remaining:
        await cq.message.edit_reply_markup(reply_markup=my_entries_kb(remaining))
    else:
        await cq.message.edit_text("📋 Все твои записи удалены.")

@dp.callback_query(F.data == "close_my")
async def close_my(cq: types.CallbackQuery):
    await cq.message.edit_text("📋 Закрыто.")
    await cq.answer()

# --- СТАТИСТИКА ---
@dp.message(F.text == "📈 Статистика")
async def show_stats(m: types.Message):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM prices")
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(DISTINCT product) FROM prices")
        unique_products = c.fetchone()[0]
        c.execute("SELECT COUNT(DISTINCT store) FROM prices")
        unique_stores = c.fetchone()[0]
        c.execute("SELECT COUNT(DISTINCT user_id) FROM prices WHERE user_id IS NOT NULL")
        contributors = c.fetchone()[0]
        # Топ магазинов по количеству записей
        c.execute(
            "SELECT store, COUNT(*) as cnt FROM prices GROUP BY store ORDER BY cnt DESC LIMIT 5"
        )
        top_stores = c.fetchall()
        # Самые дешёвые/дорогие добавления за сегодня
        c.execute(
            "SELECT product, price, store FROM prices "
            "WHERE date(updated_at) = date(datetime('now', '+3 hours')) "
            "ORDER BY updated_at DESC LIMIT 5"
        )
        today_entries = c.fetchall()

    reply = (
        f"📈 *Статистика базы цен*\n\n"
        f"📦 Всего записей: *{total}*\n"
        f"🛍 Уникальных товаров: *{unique_products}*\n"
        f"🏪 Магазинов: *{unique_stores}*\n"
        f"👥 Участников: *{contributors}*\n\n"
    )
    if top_stores:
        reply += "🏆 *Топ магазинов:*\n"
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        for i, (store, cnt) in enumerate(top_stores):
            medal = medals[i] if i < len(medals) else f"{i+1}."
            reply += f"{medal} {store} — {cnt} записей\n"
        reply += "\n"
    if today_entries:
        reply += "🆕 *Добавлено сегодня:*\n"
        for product, price, store in today_entries:
            reply += f"• {product.title()} — {int(price)} ₽ ({store})\n"

    await m.answer(reply, parse_mode="Markdown", reply_markup=main_menu())

# --- ПОИСК ---
@dp.message(F.text == "🔍 Быстрый поиск")
async def quick_search_menu(m: types.Message):
    await m.answer("🔍 Выбери товар:", reply_markup=popular_products_kb())

@dp.message(F.text.in_(["🥛 Молоко", "🍞 Хлеб", "🍗 Курица", "🥩 Говядина",
                         "🍚 Рис", "🥚 Яйца", "🧈 Масло", "🍯 Финики"]))
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
        await m.answer("⏳ Сейчас мы добавляем цену. Чтобы отменить — нажми ❌ в сообщении выше.")
        return
    text = m.text.lower().strip()
    if "," in text:
        items = [i.strip() for i in text.split(",") if i.strip()]
        await search_basket(m, items)
    else:
        await search_single_item(m, text)

@dp.message(F.voice)
async def handle_voice(m: types.Message):
    await m.answer("🎤 Я пока не умею слушать голосовые. Напиши текст.", reply_markup=main_menu())

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
            "Ты можешь добавить цену — нажми ➕ Добавить цену",
            reply_markup=main_menu()
        )
        return

    reply = f"🔍 *Результаты по «{product_name.title()}»:*\n\n"
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
        await m.answer("😔 Нет данных по этому списку. Попробуй добавить товары.", reply_markup=main_menu())
        return

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    reply = f"🛒 *Твой список:* {', '.join(items).title()}\n\n"
    for i, (store, data) in enumerate(valid[:5], 1):
        medal = medals[i - 1] if i <= len(medals) else f"{i}."
        reply += (
            f"{medal} *{store}* ({data['addr']})\n"
            f"   📦 Найдено: {data['count']} из {len(items)} товаров\n"
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
        reply += f"✅ *Совет:* Все товары в *{best[0]}* за {best[1]['total']} ₽ — самый выгодный!"
    else:
        bp = valid[0]
        reply += (
            f"⚠️ *Ни в одном магазине нет всего списка.*\n"
            f"💡 Лучше всего: *{bp[0]}* ({bp[1]['count']} из {len(items)}) за {bp[1]['total']} ₽"
        )

    await m.answer(reply, parse_mode="Markdown", reply_markup=main_menu())

# --- ЗАПУСК ---
async def main():
    print("✅ Бот v2 запущен.")
    asyncio.create_task(cleanup_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
