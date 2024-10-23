import re
import logging
import os
import emoji
from functools import wraps
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackContext
from telegram.error import TelegramError, BadRequest, Forbidden

from tinydb import TinyDB, Query
from spam_tokens import BETTING_TOKENS
from is_spam_message import new_is_spam_message, has_critical_patterns

load_dotenv()

TOKEN = os.getenv('ANTISPAM_TOKEN')

logging.basicConfig(level=logging.WARNING, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
                    filename="bot.log",
                    filemode="a")

# Initialize TinyDB
db_main_file = "./bot_database.json"
if not os.path.exists(db_main_file):
    with open(db_main_file, "w") as file:
        file.write("{}")
db_main = TinyDB(db_main_file)
User = Query()

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

@private_chat_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Здравствуйте! Я бот, удаляющий спам.\n\nЧтобы начать работу, добавьте меня в чат как администратора с правами на удаление сообщений. Затем используйте команду /register <chat_id> чтобы зарегистрировать чат и начать получать логи удаленных сообщений. Используйте /unregister <chat_id> чтобы отменить регистрацию чата.\n\nИдентификатор чата выглядит примерно так: -100234567890. Чтобы получить такой идентификатор, воспользуйтесь одним из сторонних ботов, например @username_to_id_bot или @getmy_idbot.\n\nВы также можете настроить удаление технических сообщений со статусами, см. полный список команд.')

@private_chat_only
async def register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text('Добавьте идентификатор чата после команды.')
        return
    
    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text('Неверный формат идентификатора чата. Используйте числовой ID.')
        return
    
    try:
        chat_member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        if chat_member.status in ['creator', 'administrator']:
            user_data = db_main.get(User.user_id == user_id)
            if user_data:
                if chat_id not in user_data['chats']:
                    user_data['chats'].append(chat_id)
                    if 'delete_statuses' not in user_data:
                        user_data['delete_statuses'] = {}
                    user_data['delete_statuses'][str(chat_id)] = False
                    db_main.update(user_data, User.user_id == user_id)
            else:
                db_main.insert({'user_id': user_id, 'chats': [chat_id], 'delete_statuses': {str(chat_id): False}})
            await update.message.reply_text(f'Зарегистрирован чат {chat_id}.')
        else:
            await update.message.reply_text('Вы не администратор этого чата.')
    except Forbidden:
        await update.message.reply_text('Бот не имеет доступа к этому чату. Пожалуйста, добавьте бота в чат и сделайте его администратором.')
    except BadRequest as e:
        await update.message.reply_text(f'Ошибка: {str(e)}. Проверьте правильность ID чата и убедитесь, что бот добавлен в чат.')

@private_chat_only
async def unregister(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text('Добавьте идентификатор чата после команды.')
        return
    
    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text('Неверный формат идентификатора чата. Используйте числовой ID.')
        return
    
    user_data = db_main.get(User.user_id == user_id)
    if user_data and chat_id in user_data['chats']:
        user_data['chats'].remove(chat_id)
        if 'delete_statuses' in user_data:
            user_data['delete_statuses'].pop(str(chat_id), None)
        db_main.update(user_data, User.user_id == user_id)
        await update.message.reply_text(f'Отменена регистрация чата {chat_id}.')
    else:
        await update.message.reply_text('Чат не зарегистрирован.')

@private_chat_only
async def list_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_data = db_main.get(User.user_id == user_id)
    
    if user_data and user_data['chats']:
        chat_list = "Ваши зарегистрированные чаты:\n\n"
        for chat_id in user_data['chats']:
            try:
                chat = await context.bot.get_chat(chat_id)
                chat_name = chat.title if chat.title else "Unknown"
                delete_status = user_data.get('delete_statuses', {}).get(str(chat_id), False)
                status = "Включено" if delete_status else "Выключено"
                chat_list += f"Название: {chat_name} || Идентификатор: {chat_id} || Удаление статусов: {status}\n"

            except BadRequest:
                chat_list += f"Недоступно: {chat_id} (Бот не имеет доступа к чату)\n"
        await update.message.reply_text(chat_list)
    else:
        await update.message.reply_text("У вас нет зарегистрированных чатов.")

@private_chat_only
async def delete_statuses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text('Добавьте идентификатор чата после команды.')
        return
    
    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text('Неверный формат идентификатора чата. Используйте числовой ID.')
        return
    
    user_data = db_main.get(User.user_id == user_id)
    if user_data and chat_id in user_data['chats']:
        try:
            chat_member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if chat_member.status in ['creator', 'administrator']:
                if 'delete_statuses' not in user_data:
                    user_data['delete_statuses'] = {}
                user_data['delete_statuses'][str(chat_id)] = True
                db_main.update(user_data, User.user_id == user_id)
                await update.message.reply_text(f'Автоматическое удаление статусов включено для чата {chat_id}.')
            else:
                await update.message.reply_text('Вы не администратор этого чата.')
        except BadRequest:
            await update.message.reply_text('Не удалось проверить права администратора. Убедитесь, что бот добавлен в чат.')
    else:
        await update.message.reply_text('Чат не зарегистрирован.')

@private_chat_only
async def allow_statuses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text('Добавьте идентификатор чата после команды.')
        return
    
    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text('Неверный формат идентификатора чата. Используйте числовой ID.')
        return
    
    user_data = db_main.get(User.user_id == user_id)
    if user_data and chat_id in user_data['chats']:
        try:
            chat_member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if chat_member.status in ['creator', 'administrator']:
                if 'delete_statuses' in user_data:
                    user_data['delete_statuses'][str(chat_id)] = False
                db_main.update(user_data, User.user_id == user_id)
                await update.message.reply_text(f'Автоматическое удаление статусов отключено для чата {chat_id}.')
            else:
                await update.message.reply_text('Вы не администратор этого чата.')
        except BadRequest:
            await update.message.reply_text('Не удалось проверить права администратора. Убедитесь, что бот добавлен в чат.')
    else:
        await update.message.reply_text('Чат не зарегистрирован.')

async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.is_bot:  # Ignore messages from bots
        return

    chat_id = update.effective_chat.id
    
    # Check if this chat is registered by any user
    registered_users = db_main.search(User.chats.any([chat_id]))
    
    for user_data in registered_users:
        delete_status = user_data.get('delete_statuses', {}).get(str(chat_id), False)
        if delete_status:
            try:
                await update.effective_message.delete()
                
            except BadRequest as e:
                print(f"Не удалось удалить статус в чате {chat_id}: {str(e)}")
            
            # Break after first successful deletion to avoid multiple attempts
            break

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = """
Команды:
/start - Начать работу
/register <chat_id> - Зарегистрировать чат
/list - Показать ваши зарегистрированные чаты и их идентификаторы
/unregister <chat_id> - Отменить регистрацию чата
/delete_statuses <chat_id> - Включить автоматическое удаление статусов (по умолчанию выключено)
/allow_statuses <chat_id> - Отключить автоматическое удаление статусов
/help - Показать справку
"""
    await update.message.reply_text(help_text)

def find_mixed_words(text):
    regex = r"\b(?=[^\s_-]*[а-яА-ЯёЁ]+)[^\s_-]*[^-\sа-яА-ЯёЁ\W\d_]+[^\s_-]*\b"

    matches = re.findall(regex, text)
    return matches

async def check_automatically(update: Update, context: CallbackContext):
    message = update.message
    chat_id = update.effective_chat.id
    from_user = message.from_user
    if from_user.last_name is not None:
        user_display_name = f"{from_user.first_name} {from_user.last_name}"
    elif from_user.last_name is None:
        user_display_name = f"{from_user.first_name}"
    user_link = f"https://t.me/{from_user.username}"

    str_chat_id = str(chat_id).replace("-100", "")
    link = f"https://t.me/c/{str_chat_id}"
    
    if message.text is None and message.caption is None:
        return

    words = message.text or message.caption
        
    betting_pattern = '|'.join(map(re.escape, BETTING_TOKENS))
    betting_patterns = re.findall(betting_pattern, words)
    num_betting = len(betting_patterns)
    
    mixed_words = find_mixed_words(words)
    num_mixed = len(mixed_words)
    
    spam_tokens = new_is_spam_message(words)
    crit_tokens = has_critical_patterns(words)
    crit_tokens_bool = crit_tokens is not None
    if crit_tokens:
        crit_tokens_string = crit_tokens.group()
    else: crit_tokens_string = None
    
    emoji_num = sum(1 for _ in emoji.emoji_list(words))
    if emoji_num > 12:
        emoji_critical_num = True
    else:
        emoji_critical_num = False

    for user in db_main.all():
        if chat_id in user['chats']:
            try:
                chat = await context.bot.get_chat(chat_id)
                chat_title = chat.title if chat.title else f"Chat {chat_id}"

                # Ban automatically
                if (len(words) < 500) and (("✅✅✅✅" in words or "✅✅✅✅" in words.replace('\U0001F537', '✅') or crit_tokens_bool is True or num_betting > 1 or num_mixed > 1 or spam_tokens is not None or emoji_critical_num is True)):
                    verdict = f"""
<b>Основное регулярное выражению:</b> {spam_tokens is not None}
<b>Критические токены:</b> {crit_tokens_bool} | {crit_tokens_string}
<b>Смешанные слова:</b> {num_mixed}; [ {', '.join(mixed_words)} ]
<b>Гемблинг:</b> {num_betting}; [ {', '.join(betting_patterns)} ]
<b>Более 12 эмодзи:</b> {emoji_critical_num}
            """
                    if message.text is not None:
                        message_text = message.text_html_urled
                        text_message_content = f"🎯 <b>Автоматический бан:</b>\n\n👤 <a href='{user_link}'><b>{user_display_name}</b></a> из чата <a href='{link}'>{chat_title}</a>\n\n{message_text}\n{verdict}"

                        try:
                            await context.bot.delete_message(chat_id=message.chat_id, message_id=message.message_id)
                            await context.bot.ban_chat_member(chat_id=message.chat_id, user_id=message.from_user.id)
                            await context.bot.send_message(chat_id=user['user_id'],
                                text=text_message_content,
                                disable_web_page_preview=True,
                                parse_mode="HTML")
                            return

                        except TelegramError as e:
                            error_message = f"Возникла ошибка при автоматическом бане: {str(e)}\n\n<a href='{user_link}'><b>{user_display_name}</b></a> из чата <a href='{link}'>{chat_title}</a>\n\n{message_text}\n{verdict}"
                            await context.bot.send_message(chat_id=user['user_id'],
                                text=error_message,
                                disable_web_page_preview=True,
                                parse_mode="HTML")
                
                            return

                    elif message.text is None:
                        message_text = message.caption_html_urled
                        message_content = f"🎯 <b>Автоматический бан:</b>\n\n👤 <a href='{user_link}'><b>{user_display_name}</b></a> из чата <a href='{link}'>{chat_title}</a>\n\n{message_text}\n{verdict}"
            
                        try:
                            await context.bot.ban_chat_member(chat_id=message.chat_id, user_id=message.from_user.id)
                            await context.bot.copy_message(chat_id=user['user_id'],
                                from_chat_id=message.chat_id,
                                message_id=message.message_id,
                                caption=message_content,
                                parse_mode="HTML")
                            await context.bot.delete_message(chat_id=message.chat_id, message_id=message.message_id)

                            return

                        except TelegramError as e:
                            error_message = f"Возникла ошибка при автоматическом бане: {str(e)}\n\n<a href='{user_link}'><b>{user_display_name}</b></a> из чата <a href='{link}'>{chat_title}</a>\n\n{message_text}\n{verdict}"
                            await context.bot.copy_message(chat_id=user['user_id'],
                                from_chat_id=message.chat_id,
                                message_id=message.message_id,
                                caption=error_message,
                                parse_mode="HTML")
                            return
            except Exception as e:
                print(f"Ошибка при обработке сообщения: {str(e)}")
        else:
            print(f"{message}")

def main() -> None:
    print("I'm working")
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("register", register))
    application.add_handler(CommandHandler("unregister", unregister))
    application.add_handler(CommandHandler("list", list_chats))
    application.add_handler(CommandHandler("delete_statuses", delete_statuses))
    application.add_handler(CommandHandler("allow_statuses", allow_statuses))

    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND & ~filters.STORY & ~filters.StatusUpdate.ALL, check_automatically), group=0)
    application.add_handler(MessageHandler(filters.StatusUpdate.ALL, handle_status), group=1)

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
