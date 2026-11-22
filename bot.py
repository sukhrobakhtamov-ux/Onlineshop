"""Бот для владельца магазина.
Добавляете товар в боте, он сам публикуется в канале с кнопками заказа.
Есть меню, отметка «продано», артикулы, страна производства и текст для Instagram."""
import html
import json
import logging
import os
import random
from urllib.parse import quote

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shop-bot")


def digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


# ---------- Настройки (переменные окружения на хостинге) ----------
BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = os.environ.get("OWNER_ID", "").strip()
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "mahdiashop_01").lstrip("@")
WHATSAPP_NUMBER = digits(os.environ.get("WHATSAPP_NUMBER", ""))
SHOP_NAME = os.environ.get("SHOP_NAME", "наш магазин")
DATA_FILE = os.environ.get("DATA_FILE", "products.json")

CHANNEL_RAW = os.environ.get("CHANNEL_ID", "").strip()
CHANNEL = int(CHANNEL_RAW) if CHANNEL_RAW.lstrip("-").isdigit() else CHANNEL_RAW
CHANNEL_LINK = os.environ.get("CHANNEL_LINK", "").strip() or (
    f"https://t.me/{CHANNEL_RAW.lstrip('@')}" if CHANNEL_RAW.startswith("@") else ""
)

OWNER_ID_INT = int(OWNER_ID) if OWNER_ID.isdigit() else 0
OWNER = filters.User(user_id=OWNER_ID_INT)

# ---------- Виды товаров ----------
TYPES = {
    "mo": {
        "label": "👦 Одежда для мальчика",
        "emoji": "👦",
        "prefix": "MO",
        "tags": "#детская_одежда #для_мальчиков",
        "ig": "#детскаяодежда #одеждадлямальчиков #мальчик #одеждадлядетей #таджикистан",
    },
    "do": {
        "label": "👧 Одежда для девочки",
        "emoji": "👧",
        "prefix": "DO",
        "tags": "#детская_одежда #для_девочек",
        "ig": "#детскаяодежда #одеждадлядевочек #девочка #одеждадлядетей #таджикистан",
    },
    "os": {
        "label": "🛏 Односпальное бельё",
        "emoji": "🛏",
        "prefix": "OS",
        "tags": "#постельное_бельё #односпальное",
        "ig": "#постельноебельё #односпальноебельё #детскаякроватка #текстильдлядома #таджикистан",
    },
    "ds": {
        "label": "🛌 Двуспальное бельё",
        "emoji": "🛌",
        "prefix": "DS",
        "tags": "#постельное_бельё #двуспальное",
        "ig": "#постельноебельё #двуспальноебельё #текстильдлядома #уютвдоме #таджикистан",
    },
}

COUNTRIES = [
    ("Китай", "🇨🇳"),
    ("Турция", "🇹🇷"),
    ("Узбекистан", "🇺🇿"),
    ("Таджикистан", "🇹🇯"),
    ("Россия", "🇷🇺"),
    ("Кыргызстан", "🇰🇬"),
    ("Казахстан", "🇰🇿"),
    ("Пакистан", "🇵🇰"),
    ("Индия", "🇮🇳"),
]

# ---------- Кнопки меню владельца ----------
ADD_LABEL = "➕ Добавить товар"
LIST_LABEL = "📋 Товары"
SOLD_LABEL = "✅ Отметить продано"
BACKUP_LABEL = "💾 Копия"
MENU_LABELS = [ADD_LABEL, LIST_LABEL, SOLD_LABEL, BACKUP_LABEL]
MENU = ReplyKeyboardMarkup(
    [[ADD_LABEL, LIST_LABEL], [SOLD_LABEL, BACKUP_LABEL]], resize_keyboard=True
)

PAGE = 6

# ---------- Хранение товаров ----------
# Данные живут в памяти. Копия пишется в файл и в закреплённое сообщение
# в вашем чате с ботом (на случай, если хостинг стирает файлы).
STORE = {"data": {"next_id": 1, "products": []}}
BACKUP = {"id": None}


def parse_data(raw: bytes):
    try:
        d = json.loads(raw.decode("utf-8"))
        products = [p for p in d["products"] if p.get("type") in TYPES]
        next_id = max([p["id"] for p in products] + [0]) + 1
        return {"next_id": max(int(d.get("next_id", 1)), next_id), "products": products}
    except Exception:
        return None


def read_file():
    try:
        with open(DATA_FILE, "rb") as f:
            return parse_data(f.read())
    except OSError:
        return None


def dump_bytes() -> bytes:
    return json.dumps(STORE["data"], ensure_ascii=False, indent=2).encode("utf-8")


async def load_pinned_backup(bot):
    """Ищет закреплённую копию товаров в чате владельца с ботом."""
    if not OWNER_ID_INT:
        return None
    try:
        chat = await bot.get_chat(OWNER_ID_INT)
        pm = chat.pinned_message
        if pm and pm.document and pm.document.file_name == "products.json":
            tg_file = await bot.get_file(pm.document.file_id)
            data = parse_data(bytes(await tg_file.download_as_bytearray()))
            return pm.message_id, data
    except Exception as e:
        log.warning("Не удалось прочитать закреплённую копию: %s", e)
    return None


async def persist(bot) -> None:
    """Сохраняет товары: в файл и в закреплённое сообщение."""
    try:
        with open(DATA_FILE, "wb") as f:
            f.write(dump_bytes())
    except OSError as e:
        log.warning("Файл не записался: %s", e)

    if not OWNER_ID_INT:
        return
    caption_text = "💾 Копия товаров. Обновляется сама после каждого изменения."
    try:
        if BACKUP["id"]:
            try:
                await bot.edit_message_media(
                    media=InputMediaDocument(
                        media=dump_bytes(), filename="products.json", caption=caption_text
                    ),
                    chat_id=OWNER_ID_INT,
                    message_id=BACKUP["id"],
                )
                return
            except Exception as e:
                if "not modified" in str(e).lower():
                    return
                log.info("Копию придётся отправить заново: %s", e)
        msg = await bot.send_document(
            OWNER_ID_INT,
            document=dump_bytes(),
            filename="products.json",
            caption=caption_text,
            disable_notification=True,
        )
        BACKUP["id"] = msg.message_id
        try:
            await bot.pin_chat_message(OWNER_ID_INT, msg.message_id, disable_notification=True)
        except Exception as e:
            log.warning("Не удалось закрепить копию: %s", e)
    except Exception as e:
        log.warning("Копия в чат не отправилась: %s", e)


async def post_init(app: Application) -> None:
    file_data = read_file()
    pinned = await load_pinned_backup(app.bot)
    if pinned:
        BACKUP["id"] = pinned[0]
    if file_data is not None:
        STORE["data"] = file_data
    elif pinned and pinned[1] is not None:
        STORE["data"] = pinned[1]
        log.info("Товары восстановлены из закреплённой копии")
    log.info("Товаров загружено: %s", len(STORE["data"]["products"]))
    await app.bot.set_my_commands([BotCommand("start", "Открыть меню")])


def find_product(pid: int):
    return next((p for p in STORE["data"]["products"] if p["id"] == pid), None)


def new_article(prefix: str) -> str:
    used = {p.get("article") for p in STORE["data"]["products"]}
    while True:
        article = f"{prefix}-{random.randint(1000, 9999)}"
        if article not in used:
            return article


def is_owner(update: Update) -> bool:
    user = update.effective_user
    return bool(OWNER_ID_INT) and user is not None and user.id == OWNER_ID_INT


# ---------- Пост в канале ----------

def caption(p: dict, sold: bool = False) -> str:
    t = TYPES[p["type"]]
    lines = [f"{t['emoji']} {p['name']}", ""]
    if p.get("price"):
        lines.append(f"💰 Цена: {p['price']}")
    if p.get("sizes"):
        lines.append(f"📏 Размеры: {p['sizes']}")
    if p.get("country"):
        lines.append(f"🌍 Производство: {p['country']}")
    if p.get("description"):
        lines += ["", f"📝 {p['description'][:500]}"]
    lines += ["", t["tags"]]
    if WHATSAPP_NUMBER:
        lines.append(f"🟢 WhatsApp: +{WHATSAPP_NUMBER}")
    lines.append(f"🔖 Артикул: {p['article']}")
    text = "\n".join(lines)[:1000]
    return ("🔴 ПРОДАНО\n\n" + text) if sold else text


def order_keyboard(p: dict, bot_username: str) -> InlineKeyboardMarkup:
    text = f"Здравствуйте! Хочу заказать: {p['name']} (арт. {p['article']})"
    if p.get("price"):
        text += f", {p['price']}"
    text += ". Размер: "
    rows = [
        [
            InlineKeyboardButton(
                "✈️ Написать в Telegram",
                url=f"https://t.me/{OWNER_USERNAME}?text={quote(text)}",
            )
        ]
    ]
    if WHATSAPP_NUMBER:
        rows.append(
            [
                InlineKeyboardButton(
                    "🟢 Написать в WhatsApp",
                    url=f"https://wa.me/{WHATSAPP_NUMBER}?text={quote(text)}",
                )
            ]
        )
    if WHATSAPP_NUMBER:
        rows.append(
            [InlineKeyboardButton("📞 Позвонить в WhatsApp", url=f"https://wa.me/{WHATSAPP_NUMBER}")]
        )
    return InlineKeyboardMarkup(rows)


def instagram_text(p: dict) -> str:
    t = TYPES[p["type"]]
    lines = [f"{t['emoji']} {p['name']}", ""]
    if p.get("price"):
        lines.append(f"💰 Цена: {p['price']}")
    if p.get("sizes"):
        lines.append(f"📏 Размеры: {p['sizes']}")
    if p.get("country"):
        lines.append(f"🌍 Производство: {p['country']}")
    if p.get("description"):
        lines += ["", f"📝 {p['description'][:500]}"]
    lines += ["", "🛒 Заказать:"]
    lines.append(f"✈️ Telegram: @{OWNER_USERNAME}")
    if WHATSAPP_NUMBER:
        lines.append(f"🟢 WhatsApp: +{WHATSAPP_NUMBER}")
    lines += ["", f"🔖 Артикул: {p['article']}", "", t["ig"]]
    return "\n".join(lines)


async def publish(bot, p: dict) -> int:
    if not CHANNEL:
        raise RuntimeError("не указан CHANNEL_ID")
    msg = await bot.send_photo(
        CHANNEL, p["photo"], caption=caption(p), reply_markup=order_keyboard(p, bot.username)
    )
    return msg.message_id


async def update_channel(bot, p: dict) -> str:
    """Обновляет пост в канале (продано / в продаже). Возвращает пояснение при ошибке."""
    if not (CHANNEL and p.get("channel_msg")):
        return "Пост в канале не найден."
    sold = bool(p.get("sold"))
    try:
        await bot.edit_message_caption(
            chat_id=CHANNEL,
            message_id=p["channel_msg"],
            caption=caption(p, sold),
            reply_markup=InlineKeyboardMarkup([]) if sold else order_keyboard(p, bot.username),
        )
        return ""
    except Exception as e:
        if "not modified" in str(e).lower():
            return ""
        log.warning("Пост в канале не обновился: %s", e)
        return f"Не удалось обновить пост в канале: {e}"


# ---------- /start ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_owner(update):
        await update.message.reply_text("Меню владельца внизу экрана 👇", reply_markup=MENU)
        return

    rows = []
    if CHANNEL_LINK:
        rows.append([InlineKeyboardButton("🛍 Перейти в канал с товарами", url=CHANNEL_LINK)])
    rows.append(
        [InlineKeyboardButton("✈️ Написать в магазин", url=f"https://t.me/{OWNER_USERNAME}")]
    )
    await update.message.reply_text(
        f"Добро пожаловать в {SHOP_NAME}! Все товары в нашем канале.",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Ваш ID: {update.effective_user.id}")


# ---------- Добавление товара ----------

S_TYPE, S_PHOTO, S_NAME, S_PRICE, S_SIZES, S_COUNTRY, S_COUNTRY_TEXT, S_DESC = range(8)
TEXT = filters.TEXT & ~filters.COMMAND & ~filters.Text(MENU_LABELS)


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new"] = {}
    buttons = [
        [InlineKeyboardButton(t["label"], callback_data=f"type:{key}")]
        for key, t in TYPES.items()
    ]
    await update.message.reply_text(
        "Что добавляем? (отмена: /cancel)", reply_markup=InlineKeyboardMarkup(buttons)
    )
    return S_TYPE


async def add_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    key = q.data.split(":")[1]
    context.user_data["new"]["type"] = key
    await q.edit_message_text(f"Выбрано: {TYPES[key]['label']}")
    await context.bot.send_message(q.message.chat_id, "Пришлите фото товара 📸")
    return S_PHOTO


async def add_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new"]["photo"] = update.message.photo[-1].file_id
    await update.message.reply_text("Название товара?")
    return S_NAME


async def need_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Пришлите именно фото (не файлом). Отмена: /cancel")
    return S_PHOTO


async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new"]["name"] = update.message.text.strip()
    await update.message.reply_text("💰 Цена? Например: 250 сомони")
    return S_PRICE


async def add_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new"]["price"] = update.message.text.strip()
    await update.message.reply_text(
        "📏 Размеры? Например: 1-3 года или 160x220. Если не нужно, отправьте знак -"
    )
    return S_SIZES


def country_keyboard() -> InlineKeyboardMarkup:
    rows, row = [], []
    for i, (name, flag) in enumerate(COUNTRIES):
        row.append(InlineKeyboardButton(f"{flag} {name}", callback_data=f"cty:{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton("✏️ Другая страна", callback_data="cty:other"),
            InlineKeyboardButton("⏭ Пропустить", callback_data="cty:none"),
        ]
    )
    return InlineKeyboardMarkup(rows)


async def add_sizes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["new"]["sizes"] = "" if text == "-" else text
    await update.message.reply_text("🌍 Страна производства?", reply_markup=country_keyboard())
    return S_COUNTRY


async def add_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    choice = q.data.split(":")[1]
    chat_id = q.message.chat_id

    if choice == "other":
        await q.edit_message_text("Страна: другая")
        await context.bot.send_message(
            chat_id, "Напишите страну (можно сразу со смайликом флага)."
        )
        return S_COUNTRY_TEXT

    if choice == "none":
        context.user_data["new"]["country"] = ""
        await q.edit_message_text("Страна: не указана")
    else:
        name, flag = COUNTRIES[int(choice)]
        context.user_data["new"]["country"] = f"{flag} {name}"
        await q.edit_message_text(f"Страна: {flag} {name}")

    await context.bot.send_message(
        chat_id, "📝 Описание товара? Если не нужно, отправьте знак -"
    )
    return S_DESC


async def add_country_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new"]["country"] = update.message.text.strip()
    await update.message.reply_text("📝 Описание товара? Если не нужно, отправьте знак -")
    return S_DESC


async def add_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    product = context.user_data.pop("new")
    product["description"] = "" if text == "-" else text

    data = STORE["data"]
    product["id"] = data["next_id"]
    product["article"] = new_article(TYPES[product["type"]]["prefix"])
    product["sold"] = False

    try:
        product["channel_msg"] = await publish(context.bot, product)
    except Exception as e:
        log.error("Не удалось опубликовать: %s", e)
        await update.message.reply_text(
            f"Не получилось опубликовать в канале: {e}\n\n"
            "Проверьте, что бот добавлен в канал как администратор "
            "и в настройках указан правильный CHANNEL_ID. Потом снова добавьте товар.",
            reply_markup=MENU,
        )
        return ConversationHandler.END

    data["next_id"] += 1
    data["products"].append(product)
    await persist(context.bot)

    await update.message.reply_text(
        f"Товар добавлен и опубликован в канале ✅\n🔖 Артикул: {product['article']}",
        reply_markup=MENU,
    )
    await update.message.reply_text(
        "📸 Текст для Instagram (нажмите на него, чтобы скопировать):\n\n"
        f"<pre>{html.escape(instagram_text(product))}</pre>",
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("new", None)
    await update.message.reply_text("Отменено.", reply_markup=MENU)
    return ConversationHandler.END


# ---------- Список товаров, продано, удаление ----------

def card_keyboard(p: dict) -> InlineKeyboardMarkup:
    if p.get("sold"):
        first = InlineKeyboardButton("↩️ Вернуть в продажу", callback_data=f"back:{p['id']}")
    else:
        first = InlineKeyboardButton("✅ Продано", callback_data=f"sold:{p['id']}")
    return InlineKeyboardMarkup(
        [[first], [InlineKeyboardButton("🗑 Удалить", callback_data=f"del:{p['id']}")]]
    )


async def show_products(bot, chat_id: int, offset: int = 0):
    items = list(reversed(STORE["data"]["products"]))
    if not items:
        await bot.send_message(chat_id, "Товаров пока нет. Нажмите «➕ Добавить товар».")
        return
    for p in items[offset : offset + PAGE]:
        text = caption(p, bool(p.get("sold")))
        try:
            await bot.send_photo(chat_id, p["photo"], caption=text, reply_markup=card_keyboard(p))
        except Exception:
            await bot.send_message(chat_id, text, reply_markup=card_keyboard(p))
    rest = len(items) - (offset + PAGE)
    if rest > 0:
        await bot.send_message(
            chat_id,
            f"Ещё товаров: {rest}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Показать ещё", callback_data=f"lst:{offset + PAGE}")]]
            ),
        )


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_products(context.bot, update.effective_chat.id, 0)


async def sold_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = [p for p in reversed(STORE["data"]["products"]) if not p.get("sold")]
    if not items:
        await update.message.reply_text("Нет товаров в продаже.")
        return
    buttons = [
        [InlineKeyboardButton(f"{p['article']} · {p['name'][:30]}", callback_data=f"mk:{p['id']}")]
        for p in items[:40]
    ]
    await update.message.reply_text(
        "Что продано? Нажмите на товар:", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def on_more(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != OWNER_ID_INT:
        await q.answer()
        return
    await q.answer()
    offset = int(q.data.split(":")[1])
    await show_products(context.bot, q.message.chat_id, offset)


async def on_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != OWNER_ID_INT:
        await q.answer("Только для владельца.", show_alert=True)
        return
    await q.answer()
    action, pid = q.data.split(":")
    p = find_product(int(pid))
    is_card = bool(q.message.photo)

    async def say(text, markup=None):
        try:
            if is_card:
                await q.edit_message_caption(text, reply_markup=markup)
            else:
                await q.edit_message_text(text, reply_markup=markup)
        except Exception as e:
            log.info("Сообщение не изменилось: %s", e)

    if p is None:
        await say("Этого товара уже нет.")
        return

    if action in ("mk", "sold", "back"):
        p["sold"] = action != "back"
        note = await update_channel(context.bot, p)
        await persist(context.bot)
        if action == "mk":
            await say(f"✅ Продано: {p['article']} · {p['name']}" + (f"\n{note}" if note else ""))
        else:
            text = caption(p, p["sold"]) + (f"\n\n⚠️ {note}" if note else "")
            await say(text[:1024], card_keyboard(p))

    elif action == "del":
        await q.edit_message_reply_markup(
            InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("Да, удалить", callback_data=f"delyes:{p['id']}"),
                        InlineKeyboardButton("Отмена", callback_data=f"delno:{p['id']}"),
                    ]
                ]
            )
        )

    elif action == "delno":
        await q.edit_message_reply_markup(card_keyboard(p))

    elif action == "delyes":
        STORE["data"]["products"] = [x for x in STORE["data"]["products"] if x["id"] != p["id"]]
        removed = False
        if p.get("channel_msg") and CHANNEL:
            try:
                await context.bot.delete_message(CHANNEL, p["channel_msg"])
                removed = True
            except Exception as e:
                log.warning("Не удалось удалить пост в канале: %s", e)
        await persist(context.bot)
        await say("Удалено ✅" if removed else "Удалено из списка ✅\nПост в канале удалите вручную.")


# ---------- Копия ----------

async def backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_document(
        document=dump_bytes(),
        filename="products.json",
        caption="Копия товаров. Если список пропадёт, отправьте этот файл боту обратно.",
    )


async def restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tg_file = await update.message.document.get_file()
        data = parse_data(bytes(await tg_file.download_as_bytearray()))
        assert data is not None
    except Exception:
        await update.message.reply_text("Этот файл не подходит.")
        return
    STORE["data"] = data
    await persist(context.bot)
    await update.message.reply_text(f"Восстановлено товаров: {len(data['products'])} ✅")


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    add_conv = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_start, filters=OWNER),
            MessageHandler(filters.Text([ADD_LABEL]) & OWNER, add_start),
        ],
        states={
            S_TYPE: [CallbackQueryHandler(add_type, pattern="^type:")],
            S_PHOTO: [
                MessageHandler(filters.PHOTO, add_photo),
                MessageHandler(TEXT, need_photo),
            ],
            S_NAME: [MessageHandler(TEXT, add_name)],
            S_PRICE: [MessageHandler(TEXT, add_price)],
            S_SIZES: [MessageHandler(TEXT, add_sizes)],
            S_COUNTRY: [CallbackQueryHandler(add_country, pattern="^cty:")],
            S_COUNTRY_TEXT: [MessageHandler(TEXT, add_country_text)],
            S_DESC: [MessageHandler(TEXT, add_desc)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Text(MENU_LABELS) & OWNER, cancel),
        ],
    )
    app.add_handler(add_conv)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", show_id))
    app.add_handler(CommandHandler("list", list_cmd, filters=OWNER))
    app.add_handler(CommandHandler("backup", backup, filters=OWNER))
    app.add_handler(MessageHandler(filters.Text([LIST_LABEL]) & OWNER, list_cmd))
    app.add_handler(MessageHandler(filters.Text([SOLD_LABEL]) & OWNER, sold_menu))
    app.add_handler(MessageHandler(filters.Text([BACKUP_LABEL]) & OWNER, backup))
    app.add_handler(MessageHandler(filters.Document.FileExtension("json") & OWNER, restore))
    app.add_handler(CallbackQueryHandler(on_more, pattern=r"^lst:\d+$"))
    app.add_handler(
        CallbackQueryHandler(on_action, pattern=r"^(mk|sold|back|del|delyes|delno):\d+$")
    )
    app.run_polling()


if __name__ == "__main__":
    main()
