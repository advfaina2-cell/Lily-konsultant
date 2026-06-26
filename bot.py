import os
import asyncio
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
import anthropic
from dotenv import load_dotenv
from products import PRODUCTS, OBJECTION_SCRIPTS, COMPANY_NAME

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
PARTNER_CHAT_ID = os.getenv("PARTNER_CHAT_ID")

# История диалогов (user_id -> список сообщений)
conversations = {}

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

BUY_SIGNALS = [
    "передаю вас вашему личному консультанту",
    "передаю вашему консультанту",
    "партнёр свяжется",
    "консультант свяжется",
]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conversations[user_id] = []
    await update.message.reply_text(
        "Привет! 🌸 Я Лиля — консультант по здоровью и красоте.\n\n"
        "Расскажите, что вас беспокоит или чего вы хотите достичь? "
        "Помогу подобрать то, что подойдёт именно вам."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text

    if user_id not in conversations:
        conversations[user_id] = []

    conversations[user_id].append({
        "role": "user",
        "content": user_message
    })

    # Показываем что Лиля печатает
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=conversations[user_id]
    )

    reply = response.content[0].text

    conversations[user_id].append({
        "role": "assistant",
        "content": reply
    })

    # Уведомляем партнёра если клиент готов к покупке
    if any(signal in reply.lower() for signal in BUY_SIGNALS):
        await notify_partner(context, update.effective_user, user_message)

    await update.message.reply_text(reply)


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
    await context.bot.send_message(chat_id=PARTNER_CHAT_ID, text=text)


def main():
    if not TELEGRAM_TOKEN:
        print("❌ TELEGRAM_TOKEN не найден в .env")
        return
    if not ANTHROPIC_API_KEY or "СЮДА" in ANTHROPIC_API_KEY:
        print("❌ ANTHROPIC_API_KEY не заполнен в .env")
        return

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Лиля запущена! Откройте бота в Telegram и напишите /start")
    app.run_polling()


if __name__ == "__main__":
    main()
