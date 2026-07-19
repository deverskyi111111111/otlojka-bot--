import asyncio
import logging
import os
from datetime import datetime, date

import aiosqlite
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# =============================================================================
# КОНФИГ
# =============================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "shop.db")

ALL_PERMISSIONS = {
    "catalog": "Управление каталогом (добавление/удаление услуг)",
    "texts": "Редактирование текстов бота",
    "feedback": "Обратная связь (переписка с пользователями)",
    "appearance": "Оформление (цвета, premium-эмодзи)",
    "stats": "Просмотр статистики",
}

DEFAULT_TEXTS = {
    "welcome": "Добро пожаловать в RealPay.\nЗдесь вы можете купить услуги разных кодеров.",
    "catalog_title": "Выберите категорию услуг:",
    "feedback_prompt": "Напишите ваше сообщение, и мы передадим его администратору.",
    "feedback_sent": "Сообщение отправлено. Ожидайте ответа.",
    "feedback_reply_prefix": "Ответ от администратора:",
    "no_listings": "В этой категории пока нет услуг.",
    "listing_write_button": "Написать",
    "broadcast_prefix": "Объявление:",
}

# style: None | "primary" (синий) | "success" (зелёный) | "danger" (красный)
DEFAULT_BUTTONS = {
    "buy": {"label": "Купить услуги", "style": "primary", "custom_emoji_id": None},
    "feedback": {"label": "Обратная связь", "style": None, "custom_emoji_id": None},
    "back": {"label": "Назад", "style": None, "custom_emoji_id": None},
    "write": {"label": "Написать", "style": "success", "custom_emoji_id": None},
}

PRESET_CATEGORIES = [
    "Telegram боты",
    "Веб-сайты",
    "Мобильные приложения",
    "Дизайн",
    "Копирайтинг / тексты",
    "Серверы и хостинг",
    "Парсеры и автоматизация",
]

STYLE_LABELS = {
    "primary": "Синий",
    "success": "Зелёный",
    "danger": "Красный",
    "none": "Без цвета",
}


# =============================================================================
# БАЗА ДАННЫХ
# =============================================================================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY, permissions TEXT NOT NULL DEFAULT '')""")
        await db.execute("""CREATE TABLE IF NOT EXISTS texts (
            key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS buttons (
            key TEXT PRIMARY KEY, label TEXT NOT NULL,
            style TEXT, custom_emoji_id TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, category_id INTEGER NOT NULL,
            title TEXT NOT NULL, price TEXT NOT NULL, username TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(category_id) REFERENCES categories(id))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS fb_users (
            user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT,
            first_seen TEXT DEFAULT CURRENT_TIMESTAMP)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS fb_map (
            admin_message_id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL)""")
        # --- статистика ---
        await db.execute("""CREATE TABLE IF NOT EXISTS stats_users (
            user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT,
            first_seen TEXT DEFAULT CURRENT_TIMESTAMP, last_seen TEXT, visits INTEGER DEFAULT 1)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS stats_listing_views (
            id INTEGER PRIMARY KEY AUTOINCREMENT, listing_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL, viewed_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        await db.commit()
        for key, value in DEFAULT_TEXTS.items():
            await db.execute("INSERT OR IGNORE INTO texts (key, value) VALUES (?, ?)", (key, value))
        for key, data in DEFAULT_BUTTONS.items():
            await db.execute(
                "INSERT OR IGNORE INTO buttons (key, label, style, custom_emoji_id) VALUES (?, ?, ?, ?)",
                (key, data["label"], data["style"], data["custom_emoji_id"]))
        await db.commit()


# --- тексты ---
async def get_text(key: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM texts WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else DEFAULT_TEXTS.get(key, "")


async def set_text(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO texts (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        await db.commit()


async def get_all_texts():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT key, value FROM texts")
        return await cur.fetchall()


# --- кнопки (текст + цвет + premium-эмодзи) ---
async def get_button(key: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT label, style, custom_emoji_id FROM buttons WHERE key = ?", (key,))
        row = await cur.fetchone()
        if row:
            return {"label": row[0], "style": row[1], "custom_emoji_id": row[2]}
        d = DEFAULT_BUTTONS.get(key, {"label": key, "style": None, "custom_emoji_id": None})
        return dict(d)


async def set_button(key: str, label: str = None, style: str = None, clear_style: bool = False,
                      custom_emoji_id: str = None, clear_emoji: bool = False):
    current = await get_button(key)
    new_label = label if label is not None else current["label"]
    new_style = None if clear_style else (style if style is not None else current["style"])
    new_emoji = None if clear_emoji else (custom_emoji_id if custom_emoji_id is not None else current["custom_emoji_id"])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO buttons (key, label, style, custom_emoji_id) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET label=excluded.label, style=excluded.style, "
            "custom_emoji_id=excluded.custom_emoji_id",
            (key, new_label, new_style, new_emoji))
        await db.commit()


async def get_all_buttons():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT key, label, style, custom_emoji_id FROM buttons")
        return await cur.fetchall()


async def button_kwargs(key: str, **extra) -> dict:
    """Готовые kwargs для InlineKeyboardBuilder.button(**kwargs) с цветом и premium-эмодзи."""
    btn = await get_button(key)
    kwargs = {"text": btn["label"]}
    if btn["style"]:
        kwargs["style"] = btn["style"]
    if btn["custom_emoji_id"]:
        kwargs["icon_custom_emoji_id"] = btn["custom_emoji_id"]
    kwargs.update(extra)
    return kwargs


# --- админы ---
async def add_admin(user_id: int, permissions: list):
    perms = ",".join(permissions)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO admins (user_id, permissions) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET permissions=excluded.permissions",
            (user_id, perms))
        await db.commit()


async def remove_admin(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        await db.commit()


async def list_admins():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, permissions FROM admins")
        return await cur.fetchall()


async def get_admin_permissions(user_id: int) -> list:
    if user_id == OWNER_ID:
        return list(ALL_PERMISSIONS.keys())
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT permissions FROM admins WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return [p for p in row[0].split(",") if p] if row else []


async def _admin_exists(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
        return await cur.fetchone() is not None


async def is_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    return await _admin_exists(user_id)


async def has_permission(user_id: int, permission: str) -> bool:
    if user_id == OWNER_ID:
        return True
    return permission in await get_admin_permissions(user_id)


# --- категории ---
async def add_category(name: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("INSERT INTO categories (name) VALUES (?)", (name,))
        await db.commit()
        return cur.lastrowid


async def list_categories():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, name FROM categories ORDER BY id")
        return await cur.fetchall()


async def delete_category(category_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM listings WHERE category_id = ?", (category_id,))
        await db.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        await db.commit()


# --- услуги ---
async def add_listing(category_id: int, title: str, price: str, username: str, description: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO listings (category_id, title, price, username, description) VALUES (?, ?, ?, ?, ?)",
            (category_id, title, price, username, description))
        await db.commit()
        return cur.lastrowid


async def list_listings(category_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, title, price, username, description FROM listings WHERE category_id = ?",
            (category_id,))
        return await cur.fetchall()


async def get_listing(listing_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, category_id, title, price, username, description FROM listings WHERE id = ?",
            (listing_id,))
        return await cur.fetchone()


async def delete_listing(listing_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM listings WHERE id = ?", (listing_id,))
        await db.commit()


async def update_listing(listing_id: int, price: str = None, description: str = None):
    listing = await get_listing(listing_id)
    if not listing:
        return
    new_price = price if price is not None else listing[3]
    new_desc = description if description is not None else listing[5]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE listings SET price = ?, description = ? WHERE id = ?",
                          (new_price, new_desc, listing_id))
        await db.commit()


# --- обратная связь ---
async def upsert_fb_user(user_id: int, username: str, full_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO fb_users (user_id, username, full_name) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name",
            (user_id, username, full_name))
        await db.commit()


async def list_fb_users():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, username, full_name FROM fb_users ORDER BY first_seen DESC")
        return await cur.fetchall()


async def delete_fb_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM fb_users WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM fb_map WHERE user_id = ?", (user_id,))
        await db.commit()


async def map_fb_message(admin_message_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO fb_map (admin_message_id, user_id) VALUES (?, ?)",
            (admin_message_id, user_id))
        await db.commit()


async def get_fb_map_user(admin_message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM fb_map WHERE admin_message_id = ?", (admin_message_id,))
        row = await cur.fetchone()
        return row[0] if row else None


# --- статистика ---
async def track_user_visit(user_id: int, username: str, full_name: str):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT visits FROM stats_users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        if row:
            await db.execute(
                "UPDATE stats_users SET username=?, full_name=?, last_seen=?, visits=visits+1 WHERE user_id=?",
                (username, full_name, now, user_id))
        else:
            await db.execute(
                "INSERT INTO stats_users (user_id, username, full_name, first_seen, last_seen, visits) "
                "VALUES (?, ?, ?, ?, ?, 1)", (user_id, username, full_name, now, now))
        await db.commit()


async def track_listing_view(listing_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO stats_listing_views (listing_id, user_id) VALUES (?, ?)", (listing_id, user_id))
        await db.commit()


async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM stats_users")
        total_users = (await cur.fetchone())[0]

        today_prefix = date.today().isoformat()
        cur = await db.execute(
            "SELECT COUNT(*) FROM stats_users WHERE substr(first_seen, 1, 10) = ?", (today_prefix,))
        new_today = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM categories")
        total_categories = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM listings")
        total_listings = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM admins")
        total_admins = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM fb_users")
        total_feedback = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM stats_listing_views")
        total_views = (await cur.fetchone())[0]

        cur = await db.execute("""
            SELECT l.title, l.price, COUNT(v.id) as cnt
            FROM stats_listing_views v
            JOIN listings l ON l.id = v.listing_id
            GROUP BY v.listing_id
            ORDER BY cnt DESC
            LIMIT 5
        """)
        top_listings = await cur.fetchall()

        return {
            "total_users": total_users,
            "new_today": new_today,
            "total_categories": total_categories,
            "total_listings": total_listings,
            "total_admins": total_admins,
            "total_feedback": total_feedback,
            "total_views": total_views,
            "top_listings": top_listings,
        }


async def get_all_user_ids():
    ids = set()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM stats_users")
        for row in await cur.fetchall():
            ids.add(row[0])
        cur = await db.execute("SELECT user_id FROM fb_users")
        for row in await cur.fetchall():
            ids.add(row[0])
    return list(ids)


# =============================================================================
# СОСТОЯНИЯ (FSM)
# =============================================================================
class FeedbackState(StatesGroup):
    waiting_message = State()


class AddCategoryState(StatesGroup):
    waiting_custom_name = State()


class AddListingState(StatesGroup):
    waiting_category = State()
    waiting_title = State()
    waiting_price = State()
    waiting_username = State()
    waiting_description = State()


class EditListingState(StatesGroup):
    waiting_price = State()
    waiting_description = State()


class EditTextState(StatesGroup):
    waiting_value = State()


class EditButtonState(StatesGroup):
    waiting_label = State()
    waiting_emoji = State()


class AddAdminState(StatesGroup):
    waiting_id = State()
    waiting_permissions = State()


class RemoveAdminState(StatesGroup):
    waiting_id = State()


class BroadcastState(StatesGroup):
    waiting_message = State()


# =============================================================================
# КЛАВИАТУРЫ
# =============================================================================
async def main_menu_kb(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(**await button_kwargs("buy", callback_data="menu:catalog"))
    builder.button(**await button_kwargs("feedback", callback_data="menu:feedback"))
    builder.adjust(1)
    if await is_admin(user_id):
        builder.row(InlineKeyboardButton(text="Админ панель", callback_data="admin:menu"))
    return builder.as_markup()


async def catalog_kb() -> InlineKeyboardMarkup:
    categories = await list_categories()
    builder = InlineKeyboardBuilder()
    for cat_id, name in categories:
        builder.button(text=name, callback_data=f"cat:{cat_id}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(**await button_kwargs("back", callback_data="menu:main")))
    return builder.as_markup()


async def listings_kb(category_id: int) -> InlineKeyboardMarkup:
    listings = await list_listings(category_id)
    builder = InlineKeyboardBuilder()
    for listing_id, title, price, username, description in listings:
        builder.button(text=f"{title} — {price}", callback_data=f"listing:{listing_id}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(**await button_kwargs("back", callback_data="menu:catalog")))
    return builder.as_markup()


async def listing_detail_kb(listing_id: int, username: str, category_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    clean_username = username.lstrip("@")
    builder.button(**await button_kwargs("write", url=f"https://t.me/{clean_username}"))
    builder.row(InlineKeyboardButton(**await button_kwargs("back", callback_data=f"cat:{category_id}")))
    return builder.as_markup()


def admin_menu_kb(perms: list, is_owner: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if "catalog" in perms:
        builder.button(text="Каталог", callback_data="admin:catalog")
    if "texts" in perms:
        builder.button(text="Тексты", callback_data="admin:texts")
    if "feedback" in perms:
        builder.button(text="Обратная связь", callback_data="admin:feedback")
    if "appearance" in perms:
        builder.button(text="Оформление", callback_data="admin:appearance")
    if "stats" in perms:
        builder.button(text="Статистика", callback_data="admin:stats")
    if "feedback" in perms:
        builder.button(text="Рассылка", callback_data="admin:broadcast")
    if is_owner:
        builder.button(text="Управление админами", callback_data="admin:admins")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="В главное меню", callback_data="menu:main"))
    return builder.as_markup()


def back_to_admin_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Назад в админ панель", callback_data="admin:menu")
    return builder.as_markup()


def style_choice_kb(key: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Синий", callback_data=f"admin:style:{key}:primary")
    builder.button(text="Зелёный", callback_data=f"admin:style:{key}:success")
    builder.button(text="Красный", callback_data=f"admin:style:{key}:danger")
    builder.button(text="Без цвета", callback_data=f"admin:style:{key}:none")
    builder.adjust(2)
    return builder.as_markup()


def extract_custom_emoji_id(message: Message):
    entities = message.entities or message.caption_entities or []
    for e in entities:
        if e.type == "custom_emoji" and getattr(e, "custom_emoji_id", None):
            return e.custom_emoji_id
    return None


# =============================================================================
# ХЕНДЛЕРЫ: ПОЛЬЗОВАТЕЛЬ (СТАРТ / КАТАЛОГ)
# =============================================================================
user_router = Router()


@user_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user = message.from_user
    await track_user_visit(user.id, user.username or "", user.full_name)
    text = await get_text("welcome")
    await message.answer(text, reply_markup=await main_menu_kb(user.id))


@user_router.callback_query(F.data == "menu:main")
async def cb_main_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    text = await get_text("welcome")
    await call.message.edit_text(text, reply_markup=await main_menu_kb(call.from_user.id))
    await call.answer()


@user_router.callback_query(F.data == "menu:catalog")
async def cb_catalog(call: CallbackQuery):
    text = await get_text("catalog_title")
    categories = await list_categories()
    if not categories:
        await call.answer("Каталог пока пуст", show_alert=True)
        return
    await call.message.edit_text(text, reply_markup=await catalog_kb())
    await call.answer()


@user_router.callback_query(F.data.startswith("cat:"))
async def cb_category(call: CallbackQuery):
    category_id = int(call.data.split(":")[1])
    listings = await list_listings(category_id)
    if not listings:
        await call.answer(await get_text("no_listings"), show_alert=True)
        return
    categories = dict(await list_categories())
    name = categories.get(category_id, "Категория")
    await call.message.edit_text(f"{name}\n\nВыберите услугу:", reply_markup=await listings_kb(category_id))
    await call.answer()


@user_router.callback_query(F.data.startswith("listing:"))
async def cb_listing(call: CallbackQuery):
    listing_id = int(call.data.split(":")[1])
    listing = await get_listing(listing_id)
    if not listing:
        await call.answer("Услуга не найдена", show_alert=True)
        return
    _id, category_id, title, price, username, description = listing
    await track_listing_view(listing_id, call.from_user.id)
    text = f"{title}\nЦена: {price}\n\n{description or 'Описание не указано.'}"
    await call.message.edit_text(text, reply_markup=await listing_detail_kb(listing_id, username, category_id))
    await call.answer()


# =============================================================================
# ХЕНДЛЕРЫ: ОБРАТНАЯ СВЯЗЬ
# =============================================================================
feedback_router = Router()


@feedback_router.callback_query(F.data == "menu:feedback")
async def cb_feedback_start(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text(await get_text("feedback_prompt"))
    await state.set_state(FeedbackState.waiting_message)
    await call.answer()


@feedback_router.message(FeedbackState.waiting_message)
async def feedback_receive(message: Message, state: FSMContext, bot: Bot):
    user = message.from_user
    await upsert_fb_user(user.id, user.username or "", user.full_name)

    recipients = {OWNER_ID}
    for admin_id, perms in await list_admins():
        if "feedback" in perms.split(","):
            recipients.add(admin_id)

    header = (
        f"Новое сообщение обратной связи\n"
        f"От: {user.full_name} (@{user.username or 'без username'})\n"
        f"ID: {user.id}\n\n{message.text}"
    )

    for admin_id in recipients:
        if not admin_id:
            continue
        try:
            sent = await bot.send_message(admin_id, header)
            await map_fb_message(sent.message_id, user.id)
        except Exception:
            continue

    await message.answer(await get_text("feedback_sent"), reply_markup=await main_menu_kb(user.id))
    await state.clear()


@feedback_router.message(F.reply_to_message, F.chat.type == "private")
async def admin_reply(message: Message, bot: Bot):
    if not await is_admin(message.from_user.id):
        return
    if not await has_permission(message.from_user.id, "feedback"):
        return

    target_user_id = await get_fb_map_user(message.reply_to_message.message_id)
    if not target_user_id:
        return

    prefix = await get_text("feedback_reply_prefix")
    try:
        await bot.send_message(target_user_id, f"{prefix}\n\n{message.text}")
        await message.reply("Ответ отправлен пользователю.")
    except Exception:
        await message.reply("Не удалось отправить ответ — пользователь мог заблокировать бота.")


# =============================================================================
# ХЕНДЛЕРЫ: АДМИН-ПАНЕЛЬ
# =============================================================================
admin_router = Router()


async def _check_perm(call_or_msg, permission: str) -> bool:
    user_id = call_or_msg.from_user.id
    if user_id == OWNER_ID:
        return True
    return await has_permission(user_id, permission)


@admin_router.callback_query(F.data == "admin:menu")
async def admin_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    is_owner = call.from_user.id == OWNER_ID
    perms = await get_admin_permissions(call.from_user.id)
    await call.message.edit_text("Админ панель", reply_markup=admin_menu_kb(perms, is_owner))
    await call.answer()


# --- КАТАЛОГ ---
@admin_router.callback_query(F.data == "admin:catalog")
async def admin_catalog(call: CallbackQuery):
    if not await _check_perm(call, "catalog"):
        await call.answer("Нет доступа", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="Добавить категорию", callback_data="admin:cat_add")
    builder.button(text="Добавить услугу", callback_data="admin:listing_add")
    builder.button(text="Список категорий / удаление", callback_data="admin:cat_list")
    builder.button(text="Все услуги / редактирование", callback_data="admin:listing_list")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="Назад", callback_data="admin:menu"))
    await call.message.edit_text("Управление каталогом", reply_markup=builder.as_markup())
    await call.answer()


@admin_router.callback_query(F.data == "admin:cat_add")
async def cat_add_start(call: CallbackQuery, state: FSMContext):
    if not await _check_perm(call, "catalog"):
        await call.answer("Нет доступа", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    for name in PRESET_CATEGORIES:
        builder.button(text=name, callback_data=f"admin:cat_preset:{name}")
    builder.button(text="Своя категория", callback_data="admin:cat_custom")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="Назад", callback_data="admin:catalog"))
    await call.message.edit_text("Выберите готовую категорию или создайте свою:", reply_markup=builder.as_markup())
    await call.answer()


@admin_router.callback_query(F.data.startswith("admin:cat_preset:"))
async def cat_add_preset(call: CallbackQuery):
    if not await _check_perm(call, "catalog"):
        await call.answer("Нет доступа", show_alert=True)
        return
    name = call.data.split(":", 2)[2]
    await add_category(name)
    await call.answer("Категория добавлена.", show_alert=True)
    await admin_catalog(call)


@admin_router.callback_query(F.data == "admin:cat_custom")
async def cat_add_custom_start(call: CallbackQuery, state: FSMContext):
    if not await _check_perm(call, "catalog"):
        await call.answer("Нет доступа", show_alert=True)
        return
    await call.message.edit_text("Введите название новой категории:")
    await state.set_state(AddCategoryState.waiting_custom_name)
    await call.answer()


@admin_router.message(AddCategoryState.waiting_custom_name)
async def cat_add_custom_finish(message: Message, state: FSMContext):
    await add_category(message.text.strip())
    await message.answer("Категория добавлена.", reply_markup=back_to_admin_kb())
    await state.clear()


@admin_router.callback_query(F.data == "admin:cat_list")
async def cat_list(call: CallbackQuery):
    if not await _check_perm(call, "catalog"):
        await call.answer("Нет доступа", show_alert=True)
        return
    categories = await list_categories()
    builder = InlineKeyboardBuilder()
    for cat_id, name in categories:
        builder.button(text=f"Удалить: {name}", callback_data=f"admin:cat_del:{cat_id}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="Назад", callback_data="admin:catalog"))
    text = "Категории:" if categories else "Категорий пока нет."
    await call.message.edit_text(text, reply_markup=builder.as_markup())
    await call.answer()


@admin_router.callback_query(F.data.startswith("admin:cat_del:"))
async def cat_delete(call: CallbackQuery):
    if not await _check_perm(call, "catalog"):
        await call.answer("Нет доступа", show_alert=True)
        return
    cat_id = int(call.data.split(":")[2])
    await delete_category(cat_id)
    await call.answer("Категория и её услуги удалены.", show_alert=True)
    await cat_list(call)


@admin_router.callback_query(F.data == "admin:listing_add")
async def listing_add_start(call: CallbackQuery, state: FSMContext):
    if not await _check_perm(call, "catalog"):
        await call.answer("Нет доступа", show_alert=True)
        return
    categories = await list_categories()
    if not categories:
        await call.answer("Сначала добавьте категорию.", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    for cat_id, name in categories:
        builder.button(text=name, callback_data=f"admin:listing_cat:{cat_id}")
    builder.adjust(1)
    await call.message.edit_text("Выберите категорию для новой услуги:", reply_markup=builder.as_markup())
    await state.set_state(AddListingState.waiting_category)
    await call.answer()


@admin_router.callback_query(AddListingState.waiting_category, F.data.startswith("admin:listing_cat:"))
async def listing_add_category(call: CallbackQuery, state: FSMContext):
    cat_id = int(call.data.split(":")[2])
    await state.update_data(category_id=cat_id)
    await call.message.edit_text("Введите название услуги (например: Telegram бот):")
    await state.set_state(AddListingState.waiting_title)
    await call.answer()


@admin_router.message(AddListingState.waiting_title)
async def listing_add_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await message.answer("Введите цену (например: от 50$):")
    await state.set_state(AddListingState.waiting_price)


@admin_router.message(AddListingState.waiting_price)
async def listing_add_price(message: Message, state: FSMContext):
    await state.update_data(price=message.text.strip())
    await message.answer("Введите username продавца (например: @username):")
    await state.set_state(AddListingState.waiting_username)


@admin_router.message(AddListingState.waiting_username)
async def listing_add_username(message: Message, state: FSMContext):
    await state.update_data(username=message.text.strip())
    await message.answer("Введите описание услуги:")
    await state.set_state(AddListingState.waiting_description)


@admin_router.message(AddListingState.waiting_description)
async def listing_add_description(message: Message, state: FSMContext):
    data = await state.get_data()
    await add_listing(data["category_id"], data["title"], data["price"], data["username"], message.text.strip())
    await message.answer("Услуга добавлена в каталог.", reply_markup=back_to_admin_kb())
    await state.clear()


@admin_router.callback_query(F.data == "admin:listing_list")
async def listing_list_all(call: CallbackQuery):
    if not await _check_perm(call, "catalog"):
        await call.answer("Нет доступа", show_alert=True)
        return
    categories = await list_categories()
    builder = InlineKeyboardBuilder()
    found = False
    for cat_id, cat_name in categories:
        for listing_id, title, price, username, description in await list_listings(cat_id):
            found = True
            builder.button(text=f"{title} — {price}", callback_data=f"admin:listing_view:{listing_id}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="Назад", callback_data="admin:catalog"))
    text = "Все услуги:" if found else "Услуг пока нет."
    await call.message.edit_text(text, reply_markup=builder.as_markup())
    await call.answer()


@admin_router.callback_query(F.data.startswith("admin:listing_view:"))
async def listing_view(call: CallbackQuery):
    if not await _check_perm(call, "catalog"):
        await call.answer("Нет доступа", show_alert=True)
        return
    listing_id = int(call.data.split(":")[2])
    listing = await get_listing(listing_id)
    if not listing:
        await call.answer("Не найдено", show_alert=True)
        return
    _id, category_id, title, price, username, description = listing
    builder = InlineKeyboardBuilder()
    builder.button(text="Изменить цену", callback_data=f"admin:listing_edit_price:{listing_id}")
    builder.button(text="Изменить описание", callback_data=f"admin:listing_edit_desc:{listing_id}")
    builder.button(text="Удалить услугу", callback_data=f"admin:listing_del:{listing_id}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="Назад", callback_data="admin:listing_list"))
    text = f"{title}\nЦена: {price}\nПродавец: {username}\n\n{description}"
    await call.message.edit_text(text, reply_markup=builder.as_markup())
    await call.answer()


@admin_router.callback_query(F.data.startswith("admin:listing_del:"))
async def listing_delete_cb(call: CallbackQuery):
    if not await _check_perm(call, "catalog"):
        await call.answer("Нет доступа", show_alert=True)
        return
    listing_id = int(call.data.split(":")[2])
    await delete_listing(listing_id)
    await call.answer("Услуга удалена.", show_alert=True)
    await listing_list_all(call)


@admin_router.callback_query(F.data.startswith("admin:listing_edit_price:"))
async def listing_edit_price_start(call: CallbackQuery, state: FSMContext):
    if not await _check_perm(call, "catalog"):
        await call.answer("Нет доступа", show_alert=True)
        return
    listing_id = int(call.data.split(":")[2])
    await state.update_data(listing_id=listing_id)
    await call.message.edit_text("Введите новую цену:")
    await state.set_state(EditListingState.waiting_price)
    await call.answer()


@admin_router.message(EditListingState.waiting_price)
async def listing_edit_price_finish(message: Message, state: FSMContext):
    data = await state.get_data()
    await update_listing(data["listing_id"], price=message.text.strip())
    await message.answer("Цена обновлена.", reply_markup=back_to_admin_kb())
    await state.clear()


@admin_router.callback_query(F.data.startswith("admin:listing_edit_desc:"))
async def listing_edit_desc_start(call: CallbackQuery, state: FSMContext):
    if not await _check_perm(call, "catalog"):
        await call.answer("Нет доступа", show_alert=True)
        return
    listing_id = int(call.data.split(":")[2])
    await state.update_data(listing_id=listing_id)
    await call.message.edit_text("Введите новое описание:")
    await state.set_state(EditListingState.waiting_description)
    await call.answer()


@admin_router.message(EditListingState.waiting_description)
async def listing_edit_desc_finish(message: Message, state: FSMContext):
    data = await state.get_data()
    await update_listing(data["listing_id"], description=message.text.strip())
    await message.answer("Описание обновлено.", reply_markup=back_to_admin_kb())
    await state.clear()


# --- ТЕКСТЫ ---
@admin_router.callback_query(F.data == "admin:texts")
async def admin_texts(call: CallbackQuery):
    if not await _check_perm(call, "texts"):
        await call.answer("Нет доступа", show_alert=True)
        return
    texts = await get_all_texts()
    builder = InlineKeyboardBuilder()
    for key, _value in texts:
        builder.button(text=key, callback_data=f"admin:text_edit:{key}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="Назад", callback_data="admin:menu"))
    await call.message.edit_text("Выберите текст для редактирования:", reply_markup=builder.as_markup())
    await call.answer()


@admin_router.callback_query(F.data.startswith("admin:text_edit:"))
async def text_edit_start(call: CallbackQuery, state: FSMContext):
    if not await _check_perm(call, "texts"):
        await call.answer("Нет доступа", show_alert=True)
        return
    key = call.data.split(":", 2)[2]
    current = await get_text(key)
    await state.update_data(key=key)
    await call.message.edit_text(f"Текущее значение [{key}]:\n\n{current}\n\nОтправьте новый текст:")
    await state.set_state(EditTextState.waiting_value)
    await call.answer()


@admin_router.message(EditTextState.waiting_value)
async def text_edit_finish(message: Message, state: FSMContext):
    data = await state.get_data()
    await set_text(data["key"], message.text)
    await message.answer("Текст обновлён.", reply_markup=back_to_admin_kb())
    await state.clear()


# --- ОБРАТНАЯ СВЯЗЬ (СПИСОК) ---
@admin_router.callback_query(F.data == "admin:feedback")
async def admin_feedback(call: CallbackQuery):
    if not await _check_perm(call, "feedback"):
        await call.answer("Нет доступа", show_alert=True)
        return
    users = await list_fb_users()
    builder = InlineKeyboardBuilder()
    for user_id, username, full_name in users:
        label = f"{full_name} (@{username})" if username else full_name
        builder.button(text=f"Удалить: {label}", callback_data=f"admin:fb_del:{user_id}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="Назад", callback_data="admin:menu"))
    text = "Пользователи обратной связи:" if users else "Обращений пока нет."
    await call.message.edit_text(text, reply_markup=builder.as_markup())
    await call.answer()


@admin_router.callback_query(F.data.startswith("admin:fb_del:"))
async def fb_delete(call: CallbackQuery):
    if not await _check_perm(call, "feedback"):
        await call.answer("Нет доступа", show_alert=True)
        return
    user_id = int(call.data.split(":")[2])
    await delete_fb_user(user_id)
    await call.answer("Удалено.", show_alert=True)
    await admin_feedback(call)


# --- РАССЫЛКА ---
@admin_router.callback_query(F.data == "admin:broadcast")
async def broadcast_start(call: CallbackQuery, state: FSMContext):
    if not await _check_perm(call, "feedback"):
        await call.answer("Нет доступа", show_alert=True)
        return
    await call.message.edit_text(
        "Отправьте текст рассылки. Он уйдёт всем, кто хотя бы раз запускал бота "
        "или писал в обратную связь."
    )
    await state.set_state(BroadcastState.waiting_message)
    await call.answer()


@admin_router.message(BroadcastState.waiting_message)
async def broadcast_finish(message: Message, state: FSMContext, bot: Bot):
    user_ids = await get_all_user_ids()
    prefix = await get_text("broadcast_prefix")
    sent, failed = 0, 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, f"{prefix}\n\n{message.text}")
            sent += 1
        except Exception:
            failed += 1
    await message.answer(f"Рассылка завершена. Доставлено: {sent}, не доставлено: {failed}.",
                          reply_markup=back_to_admin_kb())
    await state.clear()


# --- СТАТИСТИКА ---
@admin_router.callback_query(F.data == "admin:stats")
async def admin_stats(call: CallbackQuery):
    if not await _check_perm(call, "stats"):
        await call.answer("Нет доступа", show_alert=True)
        return
    s = await get_stats()
    lines = [
        "Статистика",
        "",
        f"Всего пользователей бота: {s['total_users']}",
        f"Новых сегодня: {s['new_today']}",
        f"Обращений в обратную связь: {s['total_feedback']}",
        f"Категорий: {s['total_categories']}",
        f"Услуг в каталоге: {s['total_listings']}",
        f"Админов: {s['total_admins']}",
        f"Просмотров карточек услуг: {s['total_views']}",
    ]
    if s["top_listings"]:
        lines.append("")
        lines.append("Топ услуг по просмотрам:")
        for title, price, cnt in s["top_listings"]:
            lines.append(f"— {title} ({price}): {cnt}")
    lines.append("")
    lines.append(
        "Примечание: Telegram не сообщает боту, когда пользователь переходит "
        "по кнопке «Написать» (это обычная ссылка), поэтому точное число "
        "покупок бот отследить не может — только интерес (открытие карточки услуги)."
    )
    await call.message.edit_text("\n".join(lines), reply_markup=back_to_admin_kb())
    await call.answer()


# --- ОФОРМЛЕНИЕ (цвет + premium-эмодзи) ---
@admin_router.callback_query(F.data == "admin:appearance")
async def admin_appearance(call: CallbackQuery):
    if not await _check_perm(call, "appearance"):
        await call.answer("Нет доступа", show_alert=True)
        return
    buttons = await get_all_buttons()
    builder = InlineKeyboardBuilder()
    for key, label, style, custom_emoji_id in buttons:
        mark = f" [{STYLE_LABELS.get(style, 'без цвета')}]" if style else ""
        mark += " [premium]" if custom_emoji_id else ""
        builder.button(text=f"{label}{mark}", callback_data=f"admin:btn_edit:{key}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="Назад", callback_data="admin:menu"))
    await call.message.edit_text(
        "Оформление кнопок. Можно изменить подпись, цвет (синий/зелёный/красный) "
        "и добавить premium-эмодзи перед текстом кнопки (нужна Telegram Premium "
        "у владельца бота).\n\nВыберите кнопку:",
        reply_markup=builder.as_markup(),
    )
    await call.answer()


@admin_router.callback_query(F.data.startswith("admin:btn_edit:"))
async def btn_edit_start(call: CallbackQuery, state: FSMContext):
    if not await _check_perm(call, "appearance"):
        await call.answer("Нет доступа", show_alert=True)
        return
    key = call.data.split(":", 2)[2]
    await state.update_data(key=key)
    await call.message.edit_text("Отправьте новую подпись для кнопки (текст):")
    await state.set_state(EditButtonState.waiting_label)
    await call.answer()


@admin_router.message(EditButtonState.waiting_label)
async def btn_edit_label(message: Message, state: FSMContext):
    data = await state.get_data()
    await set_button(data["key"], label=message.text.strip())
    await message.answer("Подпись обновлена. Теперь выберите цвет кнопки:",
                          reply_markup=style_choice_kb(data["key"]))


@admin_router.callback_query(F.data.startswith("admin:style:"))
async def btn_edit_style(call: CallbackQuery, state: FSMContext):
    _, _, key, style = call.data.split(":")
    if style == "none":
        await set_button(key, clear_style=True)
    else:
        await set_button(key, style=style)
    await state.update_data(key=key)
    await call.message.edit_text(
        "Цвет обновлён. Теперь можно добавить premium-эмодзи: перешлите сюда любое "
        "своё сообщение, содержащее premium-эмодзи (нужна Telegram Premium у владельца "
        "бота), либо отправьте '-' чтобы оставить без иконки."
    )
    await state.set_state(EditButtonState.waiting_emoji)
    await call.answer()


@admin_router.message(EditButtonState.waiting_emoji)
async def btn_edit_emoji(message: Message, state: FSMContext):
    data = await state.get_data()
    if message.text and message.text.strip() == "-":
        await set_button(data["key"], clear_emoji=True)
        await message.answer("Иконка убрана.", reply_markup=back_to_admin_kb())
        await state.clear()
        return
    emoji_id = extract_custom_emoji_id(message)
    if not emoji_id:
        await message.answer(
            "Не нашёл premium-эмодзи в сообщении. Отправьте сообщение с эмодзи "
            "(набранным через панель эмодзи с premium-стикерами), либо '-' чтобы пропустить."
        )
        return
    await set_button(data["key"], custom_emoji_id=emoji_id)
    await message.answer("Premium-эмодзи добавлен на кнопку.", reply_markup=back_to_admin_kb())
    await state.clear()


# --- УПРАВЛЕНИЕ АДМИНАМИ ---
@admin_router.callback_query(F.data == "admin:admins")
async def admins_menu(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("Только для главного админа", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="Добавить админа", callback_data="admin:admin_add")
    builder.button(text="Удалить админа", callback_data="admin:admin_del")
    builder.button(text="Список админов", callback_data="admin:admin_list")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="Назад", callback_data="admin:menu"))
    await call.message.edit_text("Управление админами", reply_markup=builder.as_markup())
    await call.answer()


@admin_router.callback_query(F.data == "admin:admin_list")
async def admin_list_view(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("Только для главного админа", show_alert=True)
        return
    admins = await list_admins()
    text = ("Админы:\n" + "\n".join(f"{uid}: {perms}" for uid, perms in admins)) if admins \
        else "Дополнительных админов пока нет."
    await call.message.edit_text(text, reply_markup=back_to_admin_kb())
    await call.answer()


@admin_router.callback_query(F.data == "admin:admin_add")
async def admin_add_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        await call.answer("Только для главного админа", show_alert=True)
        return
    await call.message.edit_text("Отправьте Telegram ID нового админа (числом):")
    await state.set_state(AddAdminState.waiting_id)
    await call.answer()


@admin_router.message(AddAdminState.waiting_id)
async def admin_add_id(message: Message, state: FSMContext):
    if not message.text.strip().isdigit():
        await message.answer("ID должен быть числом. Попробуйте ещё раз:")
        return
    await state.update_data(user_id=int(message.text.strip()))
    perms_list = ", ".join(ALL_PERMISSIONS.keys())
    await message.answer(
        f"Доступные права: {perms_list}\n"
        "Отправьте нужные права через запятую (например: catalog,texts):"
    )
    await state.set_state(AddAdminState.waiting_permissions)


@admin_router.message(AddAdminState.waiting_permissions)
async def admin_add_permissions(message: Message, state: FSMContext):
    raw = [p.strip() for p in message.text.split(",")]
    valid = [p for p in raw if p in ALL_PERMISSIONS]
    if not valid:
        await message.answer("Не распознано ни одного права. Попробуйте ещё раз:")
        return
    data = await state.get_data()
    await add_admin(data["user_id"], valid)
    await message.answer(
        f"Админ {data['user_id']} добавлен с правами: {', '.join(valid)}",
        reply_markup=back_to_admin_kb(),
    )
    await state.clear()


@admin_router.callback_query(F.data == "admin:admin_del")
async def admin_del_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        await call.answer("Только для главного админа", show_alert=True)
        return
    await call.message.edit_text("Отправьте Telegram ID админа для удаления:")
    await state.set_state(RemoveAdminState.waiting_id)
    await call.answer()


@admin_router.message(RemoveAdminState.waiting_id)
async def admin_del_finish(message: Message, state: FSMContext):
    if not message.text.strip().isdigit():
        await message.answer("ID должен быть числом. Попробуйте ещё раз:")
        return
    await remove_admin(int(message.text.strip()))
    await message.answer("Админ удалён.", reply_markup=back_to_admin_kb())
    await state.clear()


# =============================================================================
# ЗАПУСК
# =============================================================================
async def main():
    logging.basicConfig(level=logging.INFO)
    if BOT_TOKEN == "PUT_YOUR_TOKEN_HERE" or not BOT_TOKEN:
        raise SystemExit("Укажите токен бота: переменная окружения BOT_TOKEN")

    await init_db()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(user_router)
    dp.include_router(admin_router)
    dp.include_router(feedback_router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
