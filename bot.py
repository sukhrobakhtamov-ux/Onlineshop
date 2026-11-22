"""Бот для владельца магазина: добавляете товар в боте, он сам публикуется в канале
с кнопками «Написать в Telegram», «Написать в WhatsApp» и «Позвонить»."""
import json
import logging
import os
from urllib.parse import quote

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Update,
)
from telegram.ext import (
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
PHONE_NUMBER = digits(os.environ.get("PHONE_NUMBER", "")) or WHATSAPP_NUMBER
SHOP_NAME = os.environ.get("SHOP_NAME", "наш магазин")
DATA_FILE = os.environ.get("DATA_FILE", "products.json")

CHANNEL_RAW = os.environ.get("CHANNEL_ID", "").strip()
CHANNEL = int(CHANNEL_RAW) if CHANNEL_RAW.lstrip("-").isdigit() else CHANNEL_RAW
CHANNEL_LINK = os.environ.get("CHANNEL_LINK", "").strip() or (
    f"https://t.me/{CHANNEL_RAW.lstrip('@')}" if CHANNEL_RAW.startswith("@") else ""
)

OWNER_ID_INT = int(OWNER_ID) if OWNER_ID.isdigit() else 0
OWNER = filters.User(user_id=OWNER_ID_INT)

# (название раздела, хэштег в канале)
CATEGORIES = [
    ("👶 Детская одежда", "#детская_одежда"),
    ("🛏 Постельное бельё", "#постельное_бельё"),
]


# ---------- Хранение товаров ----------

def load_data() -> dict:
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"next_id": 1, "products": []}


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_owner(update: Update) -> bool:
    user = update.effective_user
    return bool(OWNER_ID_INT) and user is not None and user.id == OWNER_ID_INT


# ---------- Пост в канале ----------

def caption(p: dict) -> str:
    lines = [p["name"], ""]
    if p.get("price"):
        lines.append(f"Цена: {p['price']}")
    if p.get("sizes"):
        lines.append(f"Размеры: {p['sizes']}")
    if p.get("description"):
        lines += ["", p["description"][:600]]
    lines += ["", CATEGORIES[p["category"]][1]]
    if PHONE_NUMBER:
        lines.append(f"📞 +{PHONE_NUMBER}")
    lines.append(f"Артикул: {p['id']}")
    return "\n".join(lines)[:1000]


def order_keyboard(p: dict, bot_username: str) -> InlineKeyboardMarkup:
    text = f"Здравствуйте! Хочу заказать: {p['name']} (арт. {p['id']})"
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
    if PHONE_NUMBER and bot_username:
        rows.append(
            [InlineKeyboardButton("📞 Позвонить", url=f"https://t.me/{bot_username}?start=call")]
        )
    return InlineKeyboardMarkup(rows)


async def publish(bot, p: dict) -> int:
    """Публикует товар в канале и возвращает номер сообщения."""
    if not CHANNEL:
        raise RuntimeError("не указан CHANNEL_ID")
    msg = await bot.send_photo(
        CHANNEL,
        p["photo"],
        caption=caption(p),
        reply_markup=order_keyboard(p, bot.username),
    )
    return msg.message_id


# ---------- /start (для всех) ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Кнопка «Позвонить» из канала ведёт сюда
    if context.args and context.args[0] == "call" and PHONE_NUMBER:
        await update.message.reply_text("Нажмите на контакт, чтобы позвонить 👇")
        await update.message.reply_contact(phone_number=f"+{PHONE_NUMBER}", first_name=SHOP_NAME)
        return

    if is_owner(update):
        await update.message.reply_text(
            "Команды владельца:\n"
            "/add - добавить товар (сразу появится в канале)\n"
            "/list - список товаров и удаление\n"
            "/backup - сохранить копию товаров"
        )
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


# ---------- Добавление товара (владелец) ----------

PHOTO, NAME, PRICE, SIZES, CATEGORY, DESC = range(6)
TEXT = filters.TEXT & ~filters.COMMAND


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new"] = {}
    await update.message.reply_text("Пришлите фото товара. Отмена: /cancel")
    return PHOTO


async def add_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new"]["photo"] = update.message.photo[-1].file_id
    await update.message.reply_text("Название товара?")
    return NAME


async def need_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Пришлите именно фото (не файлом). Отмена: /cancel")
    return PHOTO


async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new"]["name"] = update.message.text.strip()
    await update.message.reply_text("Цена? Например: 250 сомони")
    return PRICE


async def add_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new"]["price"] = update.message.text.strip()
    await update.message.reply_text(
        "Размеры? Например: 1-3 года. Если не нужно, отправьте знак -"
    )
    return SIZES


async def add_sizes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["new"]["sizes"] = "" if text == "-" else text
    buttons = [
        [InlineKeyboardButton(name, callback_data=f"addcat:{i}")]
        for i, (name, _tag) in enumerate(CATEGORIES)
    ]
    await update.message.reply_text("В какой раздел?", reply_markup=InlineKeyboardMarkup(buttons))
    return CATEGORY


async def add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    idx = int(q.data.split(":")[1])
    context.user_data["new"]["category"] = idx
    await q.edit_message_text(f"Раздел: {CATEGORIES[idx][0]}")
    await context.bot.send_message(
        q.message.chat_id, "Описание товара? Если не нужно, отправьте знак -"
    )
    return DESC


async def add_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    product = context.user_data.pop("new")
    product["description"] = "" if text == "-" else text

    data = load_data()
    product["id"] = data["next_id"]

    try:
        product["channel_msg"] = await publish(context.bot, product)
    except Exception as e:
        log.error("Не удалось опубликовать: %s", e)
        await update.message.reply_text(
            f"Не получилось опубликовать в канале: {e}\n\n"
            "Проверьте, что бот добавлен в канал как администратор "
            "и в настройках указан правильный CHANNEL_ID. "
            "Потом снова нажмите /add."
        )
        return ConversationHandler.END

    data["next_id"] += 1
    data["products"].append(product)
    save_data(data)
    await update.message.reply_text("Товар добавлен и опубликован в канале ✅")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("new", None)
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


# ---------- Список, удаление, копия (владелец) ----------

async def admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = load_data()["products"]
    if not items:
        await update.message.reply_text("Товаров пока нет. Добавьте: /add")
        return
    for p in items:
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🗑 Удалить", callback_data=f"del:{p['id']}")]]
        )
        try:
            await context.bot.send_photo(
                update.effective_chat.id, p["photo"], caption=caption(p), reply_markup=markup
            )
        except Exception:
            await update.message.reply_text(caption(p), reply_markup=markup)


async def on_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != OWNER_ID_INT:
        await q.answer("Только для владельца.", show_alert=True)
        return
    await q.answer()
    action, pid = q.data.split(":")
    pid = int(pid)

    if action == "del":
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Да, удалить", callback_data=f"delyes:{pid}"),
                    InlineKeyboardButton("Отмена", callback_data=f"delno:{pid}"),
                ]
            ]
        )
        await q.edit_message_reply_markup(markup)
    elif action == "delno":
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🗑 Удалить", callback_data=f"del:{pid}")]]
        )
        await q.edit_message_reply_markup(markup)
    elif action == "delyes":
        data = load_data()
        product = next((p for p in data["products"] if p["id"] == pid), None)
        data["products"] = [p for p in data["products"] if p["id"] != pid]
        save_data(data)

        removed_from_channel = False
        if product and product.get("channel_msg") and CHANNEL:
            try:
                await context.bot.delete_message(CHANNEL, product["channel_msg"])
                removed_from_channel = True
            except Exception as e:
                log.warning("Не удалось удалить пост в канале: %s", e)

        note = "Удалено ✅" if removed_from_channel else (
            "Удалено из списка ✅\nПост в канале удалите вручную."
        )
        try:
            await q.edit_message_caption(note)
        except Exception:
            await q.edit_message_text(note)


async def backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = json.dumps(load_data(), ensure_ascii=False, indent=2).encode("utf-8")
    await update.message.reply_document(
        InputFile(raw, filename="products.json"),
        caption="Копия товаров. Если список пропадёт, просто отправьте этот файл мне обратно.",
    )


async def restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tg_file = await update.message.document.get_file()
        data = json.loads(bytes(await tg_file.download_as_bytearray()).decode("utf-8"))
        assert isinstance(data["products"], list)
        data.setdefault("next_id", max([p["id"] for p in data["products"]] + [0]) + 1)
    except Exception:
        await update.message.reply_text("Этот файл не подходит.")
        return
    save_data(data)
    await update.message.reply_text(f"Восстановлено товаров: {len(data['products'])} ✅")


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start, filters=OWNER)],
        states={
            PHOTO: [
                MessageHandler(filters.PHOTO, add_photo),
                MessageHandler(TEXT, need_photo),
            ],
            NAME: [MessageHandler(TEXT, add_name)],
            PRICE: [MessageHandler(TEXT, add_price)],
            SIZES: [MessageHandler(TEXT, add_sizes)],
            CATEGORY: [CallbackQueryHandler(add_category, pattern="^addcat:")],
            DESC: [MessageHandler(TEXT, add_desc)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(add_conv)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", show_id))
    app.add_handler(CommandHandler("list", admin_list, filters=OWNER))
    app.add_handler(CommandHandler("backup", backup, filters=OWNER))
    app.add_handler(
        MessageHandler(filters.Document.FileExtension("json") & OWNER, restore)
    )
    app.add_handler(CallbackQueryHandler(on_delete, pattern="^(del|delyes|delno):"))
    app.run_polling()


if __name__ == "__main__":
    main()
