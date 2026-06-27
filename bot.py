import os
import time
import logging
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, MessageHandler, CommandHandler,
    CallbackQueryHandler, filters, ContextTypes,
)
import anthropic
from anthropic import APIError, RateLimitError
from dotenv import load_dotenv
from products import PRODUCTS, OBJECTION_SCRIPTS, COMPANY_NAME

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("lily.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
PARTNER_CHAT_ID = os.getenv("PARTNER_CHAT_ID")

MAX_HISTORY = 20
SESSION_TTL = 60 * 60   # 1 час без активности — сброс сессии
RATE_LIMIT = 10         # максимум сообщений
RATE_WINDOW = 60        # за 60 секунд

SYSTEM_PROMPT = f"""Ты — Лиля, тёплый и профессиональный консультант компании {COMPANY_NAME}.

ТВОЯ ЛИЧНОСТЬ:
- Ты живая, тёплая, искренняя. Не робот, не скрипт.
- Ты заботишься о человеке, а не о продаже.
- Пишешь коротко, по делу, без воды.
- Используешь эмодзи умеренно (1-2 на сообщение).
- Обращаешься на "вы" если клиент не перешёл на "ты".

ТВОЯ ЗАДАЧА:
1. Выяснить, что беспокоит клиента или чего он хочет достичь
2. Подобрать подходящий продукт из каталога
3. Рассказать о нём так, чтобы клиент понял ценность
4. Мягко закрыть возражения
5. Довести до решения о покупке

КАК ВЕСТИ ДИАЛОГ:
- Начни с вопроса о запросе/проблеме клиента, не с рекламы
- Задавай уточняющие вопросы по одному, не засыпай вопросами
- После выяснения запроса — рекомендуй один конкретный продукт (не несколько сразу)
- Используй конкретику: не "помогает", а "85% клиентов отмечают улучшение через 3 недели"
- Истории реальных людей работают лучше характеристик

КОГДА КЛИЕНТ ГОТОВ К ПОКУПКЕ:
Если клиент говорит "хочу заказать", "беру", "как купить", "оформите" — напиши:
"Отлично! 🎉 Передаю вас вашему личному консультанту — он свяжется с вами в течение нескольких минут и оформит всё удобным для вас способом."

ЧЕГО НЕ ДЕЛАТЬ:
- Не давить и не торопить
- Не перечислять все продукты сразу
- Не использовать слова "уникальный", "революционный", "лучший в мире"
- Не обещать того, чего нет в каталоге
- Не говорить что ты ИИ или бот (если спросят — ты консультант Лиля)

{PRODUCTS}

{OBJECTION_SCRIPTS}
"""

PLACEHOLDER_MARKERS = ["Впишите название", "Название вашей компании", "0000 руб"]

# Один клиент на весь процесс
client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

# Сессии: {user_id: {"messages": [...], "last_active": float}}
conversations: dict[int, dict] = {}

# Для rate limiting: {user_id: [timestamp, ...]}
user_message_times: dict[int, list] = defaultdict(list)


def validate_config() -> str | None:
    if not TELEGRAM_TOKEN:
        return "TELEGRAM_TOKEN не найден в .env"
    if not ANTHROPIC_API_KEY or "СЮДА" in ANTHROPIC_API_KEY:
        return "ANTHROPIC_API_KEY не заполнен в .env"
    if PARTNER_CHAT_ID:
        try:
            int(PARTNER_CHAT_ID)
        except ValueError:
            return f"PARTNER_CHAT_ID должен быть числом, получено: '{PARTNER_CHAT_ID}'"
    for marker in PLACEHOLDER_MARKERS:
        if marker in PRODUCTS or marker in COMPANY_NAME:
            return "products.py содержит незаполненные шаблонные данные — заполните каталог!"
    return None


def get_session(user_id: int) -> list:
    now = time.time()
    session = conversations.get(user_id)
    if session and now - session["last_active"] > SESSION_TTL:
        del conversations[user_id]
        session = None
    if session is None:
        conversations[user_id] = {"messages": [], "last_active": now}
    else:
        conversations[user_id]["last_active"] = now
    return conversations[user_id]["messages"]


def trim_history(messages: list) -> list:
    return messages[-MAX_HISTORY:] if len(messages) > MAX_HISTORY else messages


def is_rate_limited(user_id: int) -> bool:
    now = time.time()
    times = user_message_times[user_id]
    user_message_times[user_id] = [t for t in times if now - t < RATE_WINDOW]
    if len(user_message_times[user_id]) >= RATE_LIMIT:
        return True
    user_message_times[user_id].append(now)
    return False


async def is_buy_intent(reply: str) -> bool:
    try:
        probe = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            system="Ответь только 'да' или 'нет'.",
            messages=[{
                "role": "user",
                "content": (
                    "В этом сообщении консультант передаёт клиента партнёру "
                    f"для оформления покупки?\n\n{reply}"
                ),
            }],
        )
        return "да" in probe.content[0].text.lower()
    except APIError:
        return False


async def notify_partner(context: ContextTypes.DEFAULT_TYPE, user, last_message: str):
    if not PARTNER_CHAT_ID:
        return
    name = user.full_name or user.username or "Клиент"
    username = f"@{user.username}" if user.username else "(нет username, напишите в бот)"
    text = (
        f"🔥 Горячий клиент готов к покупке!\n\n"
        f"👤 {name}\n"
        f"📱 {username}\n"
        f"💬 Последнее: «{last_message}»\n\n"
        f"Лиля подготовила клиента — свяжитесь с ним как можно скорее!"
    )
    try:
        await context.bot.send_message(chat_id=int(PARTNER_CHAT_ID), text=text)
    except Exception as e:
        logger.error(f"Не удалось уведомить партнёра: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conversations[user_id] = {"messages": [], "last_active": time.time()}
    await update.message.reply_text(
        "Привет! 🌸 Я Лиля — консультант по здоровью и красоте.\n\n"
        "Расскажите, что вас беспокоит или чего вы хотите достичь? "
        "Помогу подобрать то, что подойдёт именно вам."
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Да, начать заново", callback_data="reset_confirm"),
        InlineKeyboardButton("Нет, продолжить", callback_data="reset_cancel"),
    ]])
    await update.message.reply_text("Сбросить историю диалога?", reply_markup=keyboard)


async def reset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if query.data == "reset_confirm":
        conversations.pop(user_id, None)
        await query.edit_message_text("История сброшена. Напишите что-нибудь, начнём сначала 🌸")
    else:
        await query.edit_message_text("Продолжаем диалог 👍")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text

    if is_rate_limited(user_id):
        await update.message.reply_text(
            "Давайте чуть помедленнее 🙏 Я обдумываю ваш вопрос."
        )
        return

    messages = get_session(user_id)
    messages.append({"role": "user", "content": user_message})

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=trim_history(messages),
        )
        reply = response.content[0].text
    except RateLimitError:
        logger.warning(f"Rate limit от Anthropic для user {user_id}")
        await update.message.reply_text(
            "Лиля сейчас очень занята 🙏 Напишите через минуту!"
        )
        messages.pop()
        return
    except APIError as e:
        logger.error(f"Anthropic API error для user {user_id}: {e}")
        await update.message.reply_text(
            "Что-то пошло не так. Попробуйте ещё раз 🙏"
        )
        messages.pop()
        return

    messages.append({"role": "assistant", "content": reply})

    if await is_buy_intent(reply):
        await notify_partner(context, update.effective_user, user_message)

    await update.message.reply_text(reply)


async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Я пока умею работать только с текстом 🙏 Напишите ваш вопрос словами."
    )


def main():
    error = validate_config()
    if error:
        logger.error(f"❌ {error}")
        return

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CallbackQueryHandler(reset_callback, pattern="^reset_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(~filters.TEXT & ~filters.COMMAND, handle_unsupported))

    logger.info("✅ Лиля запущена! Откройте бота в Telegram и напишите /start")
    app.run_polling()


if __name__ == "__main__":
    main()
