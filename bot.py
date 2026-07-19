import asyncio
import logging
import os

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
    "appearance": "Оформление (эмодзи, стиль кнопок)",
}

DEFAULT_TEXTS = {
    "welcome": "Добро пожаловать в RealPay.\nЗдесь вы можете купить услуги разных кодеров.",
    "catalog_title": "Выберите категорию услуг:",
    "feedback_prompt": "Напишите ваше сообщение, и мы передадим его администратору.",
    "feedback_sent": "Сообщение отправлено. Ожидайте ответа.",
    "feedback_reply_prefix": "Ответ от администратора:",
    "no_listings": "В этой категории пока нет услуг.",
    "listing_write_button": "Написать",
}

DEFAULT_BUTTONS = {
    "buy": {"label": "Купить услуги", "emoji": ""},
    "feedback": {"label": "Обратная связь", "emoji": ""},
    "back": {"label": "Назад", "emoji": ""},
    "write": {"label": "Написать", "emoji": ""},
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
            key TEXT PRIMARY KEY, label TEXT NOT NULL, emoji TEXT NOT NULL DEFAULT '')""")
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
        await db.commit()
        for key, value in DEFAULT_TEXTS.items():
            await db.execute("INSERT OR IGNORE INTO texts (key, value) VALUES (?, ?)", (key, value))
        for key, data in DEFAULT_BUTTONS.items():
            await db.execute("INSERT OR IGNORE INTO buttons (key, label, emoji) VALUES (?, ?, ?)",
                              (key, data["label"], data["emoji"]))
        await db.commit()


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


async def get_button(key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT label, emoji FROM buttons WHERE key = ?", (key,))
        row = await cur.fetchone()
        if row:
            return {"label": row[0], "emoji": row[1]}
        return DEFAULT_BUTTONS.get(key, {"label": key, "emoji": ""})


async def get_button_text(key: str) -> str:
    btn = await get_button(key)
    emoji = btn["emoji"]
    return f"{emoji} {btn['label']}".strip() if emoji else btn["label"]


async def set_button(key: str, label: str = None, emoji: str = None):
    current = await get_button(key)
    new_label = label if label is not None else current["label"]
    new_emoji = emoji if emoji is not None else current["emoji"]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO buttons (key, label, emoji) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET label=excluded.label, emoji=excluded.emoji",
            (key, new_label, new_emoji))
        await db.commit()


async def get_all_buttons():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT key, label, emoji FROM buttons")
        return await cur.fetchall()


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


# =============================================================================
# СОСТОЯНИЯ (FSM)
# =============================================================================
class FeedbackState(StatesGroup):
    waiting_message = State()


class AddCategoryState(StatesGroup):
    waiting_name = State()


class AddListingState(StatesGroup):
    waiting_category = State()
    waiting_title = State()
    waiting_price = State()
    waiting_username = State()
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


# =============================================================================
# КЛАВИАТУРЫ
# =============================================================================
async def main_menu_kb(user_id: int) -> InlineKeyboardMarkup:
    buy = await get_button_text("buy")
    feedback = await get_button_text("feedback")
    builder = InlineKeyboardBuilder()
    builder.button(text=buy, callback_data="menu:catalog")
    builder.button(text=feedback, callback_data="menu:feedback")
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
    back = await get_button_text("back")
    builder.row(InlineKeyboardButton(text=back, callback_data="menu:main"))
    return builder.as_markup()


async def listings_kb(category_id: int) -> InlineKeyboardMarkup:
    listings = await list_listings(category_id)
    builder = InlineKeyboardBuilder()
    for listing_id, title, price, username, description in listings:
        builder.button(text=f"{title} — {price}", callback_data=f"listing:{listing_id}")
    builder.adjust(1)
    back = await get_button_text("back")
    builder.row(InlineKeyboardButton(text=back, callback_data="menu:catalog"))
    return builder.as_markup()


async def listing_detail_kb(listing_id: int, username: str, category_id: int) -> InlineKeyboardMarkup:
    write = await get_button_text("write")
    builder = InlineKeyboardBuilder()
    clean_username = username.lstrip("@")
    builder.button(text=write, url=f"https://t.me/{clean_username}")
    back = await get_button_text("back")
    builder.row(InlineKeyboardButton(text=back, callback_data=f"cat:{category_id}"))
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
    if is_owner:
        builder.button(text="Управление админами", callback_data="admin:admins")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="В главное меню", callback_data="menu:main"))
    return builder.as_markup()


def back_to_admin_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Назад в админ панель", callback_data="admin:menu")
    return builder.as_markup()


# =============================================================================
# ХЕНДЛЕРЫ: ПОЛЬЗОВАТЕЛЬ (СТАРТ / КАТАЛОГ)
# =============================================================================
user_router = Router()


@user_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    text = await get_text("welcome")
    await message.answer(text, reply_markup=await main_menu_kb(message.from_user.id))


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
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="Назад", callback_data="admin:menu"))
    await call.message.edit_text("Управление каталогом", reply_markup=builder.as_markup())
    await call.answer()


@admin_router.callback_query(F.data == "admin:cat_add")
async def cat_add_start(call: CallbackQuery, state: FSMContext):
    if not await _check_perm(call, "catalog"):
        await call.answer("Нет доступа", show_alert=True)
        return
    await call.message.edit_text("Введите название новой категории (например: Telegram боты):")
    await state.set_state(AddCategoryState.waiting_name)
    await call.answer()


@admin_router.message(AddCategoryState.waiting_name)
async def cat_add_finish(message: Message, state: FSMContext):
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


# --- ОФОРМЛЕНИЕ ---
@admin_router.callback_query(F.data == "admin:appearance")
async def admin_appearance(call: CallbackQuery):
    if not await _check_perm(call, "appearance"):
        await call.answer("Нет доступа", show_alert=True)
        return
    buttons = await get_all_buttons()
    builder = InlineKeyboardBuilder()
    for key, label, emoji in buttons:
        display = f"{emoji} {label}".strip()
        builder.button(text=display, callback_data=f"admin:btn_edit:{key}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="Назад", callback_data="admin:menu"))
    await call.message.edit_text(
        "Оформление кнопок.\n"
        "Telegram не позволяет менять цвет кнопок и вставлять premium-эмодзи "
        "в текст самой кнопки — это ограничение платформы. Можно менять подпись "
        "и обычный эмодзи-маркер.\n\nВыберите кнопку:",
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
    await state.update_data(label=message.text.strip())
    await message.answer("Теперь отправьте эмодзи-маркер для кнопки (или '-' чтобы убрать):")
    await state.set_state(EditButtonState.waiting_emoji)


@admin_router.message(EditButtonState.waiting_emoji)
async def btn_edit_emoji(message: Message, state: FSMContext):
    data = await state.get_data()
    emoji = "" if message.text.strip() == "-" else message.text.strip()
    await set_button(data["key"], label=data["label"], emoji=emoji)
    await message.answer("Кнопка обновлена.", reply_markup=back_to_admin_kb())
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
