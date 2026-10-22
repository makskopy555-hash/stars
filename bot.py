import asyncio
import os
import re
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "orders.db")

# ключ: (название, цена в ₽) — цены тестовые
STARS = {
    "50": ("50 ⭐", 85),
    "100": ("100 ⭐", 170),
    "250": ("250 ⭐", 420),
    "500": ("500 ⭐", 830),
    "1000": ("1000 ⭐", 1650),
}
PREMIUM = {
    "3": ("Premium 3 мес.", 1100),
    "6": ("Premium 6 мес.", 1500),
    "12": ("Premium 12 мес.", 2600),
}
PAY = {"sbp": "СБП", "card": "Карта", "crypto": "Крипта"}


# ---------- база чеков ----------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item TEXT NOT NULL,
            price INTEGER NOT NULL,
            method TEXT NOT NULL,
            recipient TEXT NOT NULL,
            created TEXT NOT NULL
        )""")


def add_receipt(user_id, item, price, method, recipient) -> int:
    with db() as c:
        cur = c.execute(
            "INSERT INTO receipts (user_id,item,price,method,recipient,created) VALUES (?,?,?,?,?,?)",
            (user_id, item, price, method, recipient, datetime.now().strftime("%d.%m.%Y %H:%M")),
        )
        return cur.lastrowid


def get_receipt(rid, user_id):
    with db() as c:
        return c.execute("SELECT * FROM receipts WHERE id=? AND user_id=?", (rid, user_id)).fetchone()


def user_receipts(user_id, limit=10):
    with db() as c:
        return c.execute(
            "SELECT * FROM receipts WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)
        ).fetchall()


def user_stats(user_id):
    with db() as c:
        r = c.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(price),0) s FROM receipts WHERE user_id=?", (user_id,)
        ).fetchone()
        return r["n"], r["s"]


def format_receipt(r) -> str:
    return (
        f"🧾 <b>Чек №{r['id']:06d}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"Товар: <b>{r['item']}</b>\n"
        f"Получатель: {r['recipient']}\n"
        f"Способ оплаты — {r['method']} Привязан ✅\n"
        f"Сумма: <b>{r['price']} ₽</b>\n"
        f"Дата: {r['created']}\n"
        f"━━━━━━━━━━━━━━\n"
        f"Статус: Оплачено ✅\n"
        f"Отправлено через Fragment ✅"
    )


# ---------- клавиатуры ----------
class Flow(StatesGroup):
    username = State()


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_menu():
    return kb([
        [InlineKeyboardButton(text="⭐ Отправка звезд", callback_data="menu:stars")],
        [InlineKeyboardButton(text="💎 Отправка премиум", callback_data="menu:premium")],
        [
            InlineKeyboardButton(text="🧾 Мои чеки", callback_data="menu:receipts"),
            InlineKeyboardButton(text="👤 Профиль", callback_data="menu:profile"),
        ],
    ])


def back_btn(to="home"):
    return [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"back:{to}")]


dp = Dispatcher(storage=MemoryStorage())


@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("👋 Привет! Выбери, что хочешь сделать:", reply_markup=main_menu())


@dp.callback_query(F.data == "back:home")
async def back_home(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("👋 Выбери, что хочешь сделать:", reply_markup=main_menu())
    await c.answer()


# ---------- выбор товара ----------
@dp.callback_query(F.data == "menu:stars")
async def menu_stars(c: CallbackQuery):
    rows = [[InlineKeyboardButton(text=f"{v[0]} — {v[1]} ₽", callback_data=f"item:stars:{k}")]
            for k, v in STARS.items()]
    rows.append(back_btn())
    await c.message.edit_text("⭐ Сколько звезд отправить?", reply_markup=kb(rows))
    await c.answer()


@dp.callback_query(F.data == "menu:premium")
async def menu_premium(c: CallbackQuery):
    rows = [[InlineKeyboardButton(text=f"{v[0]} — {v[1]} ₽", callback_data=f"item:premium:{k}")]
            for k, v in PREMIUM.items()]
    rows.append(back_btn())
    await c.message.edit_text("💎 Выбери срок Premium:", reply_markup=kb(rows))
    await c.answer()


# ---------- выбор оплаты ----------
@dp.callback_query(F.data.startswith("item:"))
async def choose_item(c: CallbackQuery, state: FSMContext):
    _, kind, key = c.data.split(":")
    label, price = (STARS if kind == "stars" else PREMIUM)[key]
    await state.update_data(kind=kind, label=label, price=price)
    rows = [[InlineKeyboardButton(text=f"💳 {v}", callback_data=f"pay:{k}")] for k, v in PAY.items()]
    rows.append(back_btn())
    await c.message.edit_text(
        f"Ты выбрал: <b>{label}</b>\nК оплате: <b>{price} ₽</b>\n\nВыбери способ оплаты:",
        reply_markup=kb(rows), parse_mode="HTML",
    )
    await c.answer()


# ---------- «оплата» (тест, автоматически) ----------
@dp.callback_query(F.data.startswith("pay:"))
async def pay(c: CallbackQuery, state: FSMContext):
    method = PAY[c.data.split(":")[1]]
    data = await state.get_data()
    if not data.get("label"):
        await c.answer("Начни заново: /start", show_alert=True)
        return

    await state.update_data(method=method)
    await c.message.edit_text(f"Способ оплаты — <b>{method}</b>\n⏳ Оплата...", parse_mode="HTML")
    await asyncio.sleep(1.2)
    await c.message.edit_text(
        f"Способ оплаты — <b>{method}</b> Привязан ✅\n"
        f"Оплата прошла автоматически ✅",
        parse_mode="HTML",
    )
    await asyncio.sleep(0.8)

    who = "Premium" if data["kind"] == "premium" else "звезд"
    await c.message.answer(f"Отправь @username получателя {who}:")
    await state.set_state(Flow.username)
    await c.answer()


# ---------- юз получателя -> чек ----------
@dp.message(Flow.username)
async def got_username(m: Message, state: FSMContext):
    text = (m.text or "").strip()
    if not re.fullmatch(r"@?[A-Za-z0-9_]{5,32}", text):
        await m.answer("❌ Неверный username. Пример: @durov")
        return
    username = text if text.startswith("@") else "@" + text
    data = await state.get_data()
    await state.clear()

    rid = add_receipt(m.from_user.id, data["label"], data["price"], data["method"], username)
    await m.answer(
        f"Отправлено <b>{data['label']}</b> → {username} через Fragment ✅",
        parse_mode="HTML",
    )
    await m.answer(
        format_receipt(get_receipt(rid, m.from_user.id)),
        parse_mode="HTML",
        reply_markup=kb([
            [InlineKeyboardButton(text="🧾 Все чеки", callback_data="menu:receipts")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="back:home")],
        ]),
    )


# ---------- чеки и профиль ----------
@dp.callback_query(F.data == "menu:receipts")
async def receipts(c: CallbackQuery):
    items = user_receipts(c.from_user.id)
    if not items:
        await c.message.answer("🧾 У тебя пока нет чеков.", reply_markup=kb([back_btn()]))
        await c.answer()
        return
    rows = [[InlineKeyboardButton(
        text=f"№{r['id']:06d} • {r['item']} • {r['price']} ₽", callback_data=f"receipt:{r['id']}")]
        for r in items]
    rows.append(back_btn())
    await c.message.answer("🧾 <b>Твои чеки</b> (последние 10):", parse_mode="HTML", reply_markup=kb(rows))
    await c.answer()


@dp.callback_query(F.data.startswith("receipt:"))
async def show_receipt(c: CallbackQuery):
    r = get_receipt(int(c.data.split(":")[1]), c.from_user.id)
    if not r:
        await c.answer("Чек не найден", show_alert=True)
        return
    await c.message.answer(
        format_receipt(r), parse_mode="HTML",
        reply_markup=kb([[InlineKeyboardButton(text="⬅️ К чекам", callback_data="menu:receipts")]]),
    )
    await c.answer()


@dp.callback_query(F.data == "menu:profile")
async def profile(c: CallbackQuery):
    n, total = user_stats(c.from_user.id)
    await c.message.edit_text(
        f"👤 <b>Профиль</b>\n"
        f"ID: <code>{c.from_user.id}</code>\n"
        f"Заказов: <b>{n}</b>\n"
        f"Потрачено: <b>{total} ₽</b>",
        parse_mode="HTML",
        reply_markup=kb([back_btn()]),
    )
    await c.answer()


async def main():
    if not TOKEN:
        raise SystemExit("Не задана переменная BOT_TOKEN")
    init_db()
    bot = Bot(TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
