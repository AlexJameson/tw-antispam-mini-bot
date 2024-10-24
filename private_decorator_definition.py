from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes

def is_private_chat(update: Update) -> bool:
    return update.effective_chat.type == "private"

def private_chat_only(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not is_private_chat(update):
            await update.message.reply_text("Эта команда доступна только в личном чате с ботом.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped
