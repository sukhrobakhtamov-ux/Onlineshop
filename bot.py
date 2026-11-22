"""Бот-магазин: каталог, кнопки «Заказать» (Telegram и WhatsApp), добавление товаров владельцем."""
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

# ---------- Настройки (берутся из переменных окружения на хостинге) ----------
BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = os.environ.get("OWNER_ID", "").strip()
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "mahdiashop_01").lstrip("@")
WHATSAPP_NUMBER = "".join(ch for ch in os.environ.get("WHATSAPP_NUMBER", "") if ch.isdigit())
SHOP_NAME = os.environ.get("SHOP_NAME", "наш магазин")
DATA_FILE = os.environ.get("DATA_FILE", "products.json")

OWNER_ID_INT = int(OWNER_ID) if OWNER_ID.isdigit() else 0
OWNER = filters.User(user_id=OWNER_ID_INT)

CATEGORIES = ["👶 Детская одежда", "🛏 Постельное бельё"]
PAGE = 5  # сколько товаров показывать за раз


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


# ---------- Карточка товара ----------

def caption(p: dict) -> str:
    lines = [p["name"], ""]
    if p.get("price"):
        lines.append(f"Цена: {p['price']}")
    if p.get("sizes"):
        lines.append(f"Размеры: {p['sizes']}")
    if p.get("description"):
        lines += ["", p["description"]]
    return "\n".join(lines)[:900]


def order_keyboard(p: dict) -> InlineKeyboardMarkup:
    text = f"Здравствуйте! Хочу заказать: {p['name']}"
    if p.get("price"):
        text += f" ({p['price']})"
    text += ". Размер: "
    rows = [
        [
            InlineKeyboardButton(
                "✅ Заказать в Telegram",
                url=f"https://t.me/{OWNER_USERNAME}?text={quote(text)}",
            )
        ]
    ]
    if WHATSAPP_NUMBER:
        rows.append(
            [
                InlineKeyboardButton(
                    "🟢 Заказать в WhatsApp",
                    url=f"https://wa.me/{WHATSAPP_NUMBER}?text={quote(text)}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


def home_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(name, callback_data=f"cat:{i}:0")]
        for i, name in enumerate(CATEGORIES)
    ]
    rows.append(
        [InlineKeyboardButton("💬 Написать в магазин", url=f"https://t.me/{OWNER_USERNAME}")]
    )
    return InlineKeyboardMarkup(rows)


async def send_card(bot, chat_id: int, p: dict, markup) -> None:
    try:
        await bot.send_photo(chat_id, p["photo"], caption=caption(p), reply_markup=markup)
    except Exception as e:  # например, фото больше недоступно
        log.warning("Фото не отправилось: %s", e)
        await bot.send_message(chat_id, caption(p), reply_markup=markup)


# ---------- Для клиентов ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Добро пожаловать в {SHOP_NAME}! Выберите раздел:",
        reply_markup=home_keyboard(),
    )
    if is_owner(update):
        await update.message.reply_text(
            "Команды владельца:\n"
            "/add - добавить товар\n"
            "/list - список товаров и удаление\n"
            "/backup - сохранить копию товаров"
        )


async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Ваш ID: {update.effective_user.id}")


async def on_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await context.bot.send_message(
        q.message.chat_id, "Выберите раздел:", reply_markup=home_keyboard()
    )


async def on_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, idx, off = q.data.split(":")
    idx, off = int(idx), int(off)
    chat_id = q.message.chat_id

    items = [p for p in load_data()["products"] if p["category"] == idx]
    items.reverse()  # новые сверху

    back = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ В меню", callback_data="home")]])
    if not items:
        await context.bot.send_message(
            chat_id, "В этом разделе пока ничего нет. Загляните позже!", reply_markup=back
        )
        return

    for p in items[off : off + PAGE]:
        await send_card(context.bot, chat_id, p, order_keyboard(p))

    rest = len(items) - (off + PAGE)
    buttons = []
    if rest > 0:
        buttons.append(
            [InlineKeyboardButton(f"Показать ещё ({rest})", callback_data=f"cat:{idx}:{off + PAGE}")]
        )
    buttons.append([InlineKeyboardButton("⬅️ В меню", callback_data="home")])
    await context.bot.send_message(
        chat_id, "Выберите товар или вернитесь в меню:", reply_markup=InlineKeyboardMarkup(buttons)
    )


# ---------- Для владельца: добавление товара ----------

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
        for i, name in enumerate(CATEGORIES)
    ]
    await update.message.reply_text("В какой раздел?", reply_markup=InlineKeyboardMarkup(buttons))
    return CATEGORY


async def add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    idx = int(q.data.split(":")[1])
    context.user_data["new"]["category"] = idx
    await q.edit_message_text(f"Раздел: {CATEGORIES[idx]}")
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
    data["next_id"] += 1
    data["products"].append(product)
    save_data(data)

    await update.message.reply_text("Товар добавлен ✅ Вот как его увидят клиенты:")
    await send_card(context.bot, update.effective_chat.id, product, order_keyboard(product))
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("new", None)
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


# ---------- Для владельца: список, удаление, копия ----------

async def admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = load_data()["products"]
    if not items:
        await update.message.reply_text("Товаров пока нет. Добавьте: /add")
        return
    for p in items:
        cat = CATEGORIES[p["category"]] if 0 <= p["category"] < len(CATEGORIES) else "?"
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🗑 Удалить", callback_data=f"del:{p['id']}")]]
        )
        card = dict(p, name=f"#{p['id']} · {cat}\n{p['name']}")
        await send_card(context.bot, update.effective_chat.id, card, markup)


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
        data["products"] = [p for p in data["products"] if p["id"] != pid]
        save_data(data)
        try:
            await q.edit_message_caption("Удалено ✅")
        except Exception:
            await q.edit_message_text("Удалено ✅")


async def backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = json.dumps(load_data(), ensure_ascii=False, indent=2).encode("utf-8")
    await update.message.reply_document(
        InputFile(raw, filename="products.json"),
        caption="Копия товаров. Если товары пропадут, просто отправьте этот файл мне обратно.",
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
    app.add_handler(CallbackQueryHandler(on_category, pattern="^cat:"))
    app.add_handler(CallbackQueryHandler(on_home, pattern="^home$"))
    app.add_handler(CallbackQueryHandler(on_delete, pattern="^(del|delyes|delno):"))
    app.run_polling()


if __name__ == "__main__":
    main()
