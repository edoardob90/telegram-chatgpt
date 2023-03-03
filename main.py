#!/usr/bin/env python
# pylint: disable=unused-argument, wrong-import-position
# This program is dedicated to the public domain under the CC0 license.

import logging
import os
import pathlib
from html import escape
from uuid import uuid4
import json
from random import choice
from typing import Callable, Dict
from functools import wraps

from telegram import InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, InlineQueryHandler, ConversationHandler, \
    MessageHandler, filters, PicklePersistence

import dotenv

# Load .env file
dotenv.load_dotenv()

# Load the question/answer file for user verification
AUTH_QUESTIONS = json.load(pathlib.Path(".verify.json").open(mode="r", encoding="utf-8"))

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
AUTHORIZE, VERIFY = range(2)

# Config
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", None)
OPENAI_API = os.environ.get("OPENAI_API", None)


# Define an authorization mechanism with a decorator
def auth(func: Callable) -> Callable:
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id in context.bot_data.get("authorized_users", set()):
            await func(update, context)
        else:
            await update.message.reply_text("You are not authorized to use this bot, sorry.")

    return wrapper


# Define a few command handlers. These usually take the two arguments update and
# context.
async def start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    await update.message.reply_text("Hi!")


async def help_command(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    await update.message.reply_text("Help!")


@auth
async def echo(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo the user's message"""
    await update.message.reply_text(update.message.text)


async def authorize(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Authorize a user (step 1)"""
    user = update.effective_user

    if "authorized_users" not in ctx.bot_data:
        logger.info("Initializing 'authorized_users'")
        ctx.bot_data["authorized_users"] = set()

    logger.info("User %s (%s) entered the authorization step", user.first_name, user.id)

    if update.effective_user.id in ctx.bot_data["authorized_users"]:
        logger.info("User %s (%s) is already authorized", user.first_name, user.id)
        await update.message.reply_text("You are already authorized!")

        return ConversationHandler.END
    else:
        if "auth_attempts" not in ctx.user_data or ctx.user_data["auth_attempts"] != 3:
            logger.info("Resetting user %s auth attempts", user.id)
            ctx.user_data["auth_attempts"] = 3

        # FIXME: this might be useless
        if "verify" not in (user_data := ctx.user_data):
            user_data["verify"] = None

        user_data["verify"] = choice(AUTH_QUESTIONS)

        await update.message.reply_text(
            "Okay. Answer the following question to verify that you know my creator:\n\n"
            f"'{user_data['verify']['question']}'"
        )

        return VERIFY


async def verify(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Authorize a user (step 2)"""
    authorized_users = ctx.bot_data["authorized_users"]
    user = update.message.from_user

    if ctx.user_data["verify"]["answer"] == update.message.text:
        logger.info("User %s (%s) is now authorized", user.first_name, user.id)

        await update.message.reply_text("That's correct! You have been authorized.")
        authorized_users.add(update.effective_user.id)

        return ConversationHandler.END
    else:
        ctx.user_data["auth_attempts"] -= 1

        if ctx.user_data["auth_attempts"] == 0:
            logger.info("User %s (%s) has given 3 wrong answer. Banned", user.first_name, user.id)
            await update.message.reply_text(
                "You gave 3 wrong answers! I'm sorry, but you are banned. Ask the administrator to un-ban you."
            )
            return ConversationHandler.END

        logger.info(
            "User %s (%s) gave the wrong answer. Attempts left: %s",
            user.first_name,
            user.id,
            ctx.user_data["auth_attempts"]
        )

        await update.message.reply_text(
            f"I'm sorry, that's not the right answer. Try again. You have {ctx.user_data['auth_attempts']} attempts left."
        )

        return VERIFY


async def cancel(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    user = update.message.from_user
    logger.info("User %s (%s) canceled the conversation.", user.first_name, user.id)

    await update.message.reply_text("Bye! I hope we can talk again some day.")

    return ConversationHandler.END


# async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
#     """Handle the inline query. This is run when you type: @botusername <query>"""
#     query = update.inline_query.query
#
#     if query == "":
#         return
#
#     results = [
#         InlineQueryResultArticle(
#             id=str(uuid4()),
#             title="Caps",
#             input_message_content=InputTextMessageContent(query.upper()),
#         ),
#         InlineQueryResultArticle(
#             id=str(uuid4()),
#             title="Bold",
#             input_message_content=InputTextMessageContent(
#                 f"<b>{escape(query)}</b>", parse_mode=ParseMode.HTML
#             ),
#         ),
#         InlineQueryResultArticle(
#             id=str(uuid4()),
#             title="Italic",
#             input_message_content=InputTextMessageContent(
#                 f"<i>{escape(query)}</i>", parse_mode=ParseMode.HTML
#             ),
#         ),
#     ]
#
#     await update.inline_query.answer(results)


def main() -> None:
    """Run the bot."""
    if not (TELEGRAM_BOT_TOKEN and OPENAI_API):
        raise RuntimeError("Either Telegram Bot token or OpenAI API key are missing! Abort.")

    # Bot persistence
    memory = PicklePersistence(filepath=pathlib.Path("telegram-chatgpt.pickle"))

    # Create the Application and pass it your bot's token.
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(memory).build()

    # Start/Stop/Help commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    # Authorize step
    auth_handler = ConversationHandler(
        entry_points=[
            CommandHandler("auth", authorize)
        ],
        states={
            VERIFY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, verify)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(auth_handler)

    # Default action: echo the user's message
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # Run the bot until the user presses Ctrl-C
    application.run_polling()


if __name__ == "__main__":
    main()
