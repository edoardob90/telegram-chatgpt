#!/usr/bin/env python
# pylint: disable=unused-argument, wrong-import-position
# This program is dedicated to the public domain under the CC0 license.

import html
import json
import logging
import os
import pathlib
import re
import traceback
from functools import wraps
from random import choice
from typing import Callable, Set, Union, Any

import dotenv
from pydub import AudioSegment
from telegram import Update
from telegram.constants import ParseMode, ChatType
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
    PicklePersistence
)
from telegram.helpers import escape_markdown as _escape_markdown

import openai_api

# Load .env file
dotenv.load_dotenv()

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# Conversation states
AUTHORIZE, VERIFY, QUESTION = range(3)

# Config
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENAI_API = os.environ.get("OPENAI_API")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID"))

# Load the question/answer file for user verification
try:
    AUTH_QUESTIONS = json.load(pathlib.Path(".verify.json").open(mode="r", encoding="utf-8"))
except FileNotFoundError:
    logger.error("The file '.verify.json' containing users' verification questions does not exists.")
    raise

# Set OpenAI API key
openai_api.set_api_key(OPENAI_API)


# Helper function to escape Markdown reserved characters
def escape_markdown(text: str) -> str:
    """Helper function to escape telegram markup symbols.
    A slightly customized version of `telegram.helpers.escape_markdown`
    """
    patt = re.compile(r"(`+.*?`+)", re.DOTALL | re.MULTILINE)
    _text = [_escape_markdown(s, version=2) if not s.startswith("`") else s for s in re.split(patt, text)]

    return "".join(_text)


# Define an authorization mechanism with a decorator
def auth(admin_id: Union[int, None]) -> Callable:
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Union[Any, None]:
            logger.info("Auth request: user ID is %s, admin ID is %s", update.effective_user.id, admin_id)
            if (admin_id is not None and update.effective_user.id == admin_id) \
                    or update.effective_user.id in context.bot_data.get("authorized_users", set()):
                return await func(update, context)
            else:
                await update.message.reply_text(
                    "You are not authorized to use this bot, sorry."
                    if admin_id is None else "This command can only be run by an administrator."
                )
        return wrapper
    return decorator


async def start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    await update.message.reply_text(
        f"Hey, {update.message.from_user.first_name}! I'm happy to chat with you. What do you want to know?"
        "Use the /help command if you want some help."
    )


async def help_command(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    await update.message.reply_text(
        "Here's how you can interact with me:\n\n"

        "\- /auth: authorize yourself by answering a secret question\. It *must* be used in a private chat\n\n"

        "\- /ask: start a new conversation\. If you add something after the command, "
        "it will be used to prime the assistant, "
        "i\.e\., how you want me to behave\. For example, you can ask me to be _a friendly high\-school teacher_ "
        "or _an expert with italian dialects_\n\n"

        "\- /done or /stop: end the current chat\. It will also *erase* your message history\n\n"

        "\- /cancel: stop the currently active action \(if any\)",
        parse_mode=ParseMode.MARKDOWN_V2)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a telegram message to notify the developer."""

    # Log the error before we do anything else, so we can see it even if something breaks.
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

    # traceback.format_exception returns the usual python message about an exception, but as a
    # list of strings rather than a single string, so we have to join them together.
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)

    # Build the message with some markup and additional information about what happened.
    # You might need to add some logic to deal with messages longer than the 4096-character limit.
    update_str = update.to_dict() if isinstance(update, Update) else str(update)

    message = (
        f"An exception was raised while handling an update\n\n"
        f"<pre>update = {html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))}"
        "</pre>\n\n"
        f"<pre>context.bot_data = {html.escape(str(context.bot_data))}</pre>\n\n"
        f"<pre>context.user_data = {html.escape(str(context.user_data))}</pre>\n\n"
        f"<pre>{html.escape(tb_string)}</pre>"
    )

    # Finally, send the message
    if ADMIN_USER_ID is not None:
        await context.bot.send_message(chat_id=ADMIN_USER_ID, text=message, parse_mode=ParseMode.HTML)


@auth(None)
async def start_chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Initiate a new conversation with ChatGPT"""
    user = update.message.from_user
    user_data = ctx.user_data

    if "messages" not in user_data:
        logger.info("Initializing user %s (%s) message history", user.first_name, user.id)
        user_data["messages"] = []

    # Reset user's message history every time a new chat is opened
    logger.info("User %s (%s) started a new chat, resetting message history", user.first_name, user.id)
    user_data["messages"].clear()

    if ctx.args:
        # TODO: double-check that there aren't multiple "system" messages
        logger.info("User %s (%s) sent a message to prime the assistant", user.first_name, user.id)
        user_data["messages"].append({"role": "system", "content": " ".join(ctx.args)})

    logger.info("User's messages so far: %s", user_data["messages"])

    await update.message.reply_text(f"Okay {user.first_name}, go ahead, ask me anything! "
                                    "Write me or send me a voice message...")

    return QUESTION


async def ask_question(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Ask a question, taking into account the previous messages"""
    user = update.message.from_user
    user_data = ctx.user_data

    # Check if the message contained audio
    if audio := update.message.voice:
        # Download voice message from Telegram
        audio_file = await audio.get_file()
        temp_audio = pathlib.Path("audio") / f"{audio_file.file_id}.ogg"
        await audio_file.download_to_drive(custom_path=temp_audio)
        # Convert OGG to MP3
        temp_mp3 = temp_audio.parent / f"{temp_audio.stem}.mp3"
        AudioSegment.from_ogg(temp_audio).export(temp_mp3, format="mp3")
        # Transcribe and translate audio
        try:
            audio_text = await openai_api.transcribe_audio(temp_mp3)
        except RuntimeError as err:
            logger.error("An error occurred with Whisper API", exc_info=err)
            await update.message.reply_text("I'm sorry, but I had some troubles with your audio message. "
                                            "Use /ask to record it once again.")
            return ConversationHandler.END
        else:
            user_data["messages"].append({"role": "user", "content": audio_text["text"]})
        finally:
            temp_mp3.unlink()
            temp_audio.unlink()
    elif update.message.text:
        user_data["messages"].append({"role": "user", "content": update.message.text})

    logger.info("User's messages so far: %s", user_data["messages"])

    try:
        logger.info("User %s (%s) is sending a Chat API request...", user.first_name, user.id)
        response = await openai_api.chat_completion(user_data["messages"])
    except RuntimeError as err:
        logger.error("An error occurred with Chat API", exc_info=err)
        await update.message.reply_text("I'm sorry, but something went wrong. Please, try again with the /ask command.")
        return ConversationHandler.END
    else:
        logger.info("Response OK, no errors, replying back to the user...")
        reply = response["choices"][0]["message"]["content"]
        # Store the assistant's reply in user's message history
        user_data["messages"].append({"role": "assistant", "content": reply})
        await update.message.reply_text(escape_markdown(reply), parse_mode=ParseMode.MARKDOWN_V2)

    return QUESTION


async def end_chat(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    """Terminates an open chat session"""
    user = update.message.from_user
    logger.info("User %s (%s) ended the conversation.", user.first_name, user.id)

    await update.message.reply_text(f"Bye, {user.first_name}! Feel free to open a new chat anytime with /ask.")

    return ConversationHandler.END


@auth(ADMIN_USER_ID)
async def admin(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command"""
    # If /admin is used in a group, warn the user and do nothing
    if update.message.chat.type != ChatType.PRIVATE:
        logger.info("Admin functions should be accessed via a private chat only")
        await update.message.reply_text(
            "This command can only be run in a *private* chat\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    await update.message.reply_text("Hello, admin!")


async def authorize(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Authorize a user (step 1)"""
    user = update.message.from_user

    # If /auth is used in a group, warn and end the conversation
    if update.message.chat.type != ChatType.PRIVATE:
        logger.info(
            "User %s (%s) attempted authorization in a group: it should be done in a private chat only",
            user.first_name,
            user.id
        )
        await update.message.reply_text(
            "This command can only be run in a *private* chat\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return ConversationHandler.END

    if "authorized_users" not in ctx.bot_data:
        logger.info("Initializing 'authorized_users'")
        ctx.bot_data["authorized_users"] = set()

    if "banned_users" not in ctx.bot_data:
        logger.info("Initializing 'banned_users'")
        ctx.bot_data["banned_users"] = set()

    logger.info("User %s (%s) entered the authorization step", user.first_name, user.id)

    if user.id in ctx.bot_data["banned_users"]:
        logger.info("User %s (%s) is banned", user.first_name, user.id)

        await update.message.reply_text("I'm sorry, but you have been banned. Contact the admin to un-ban you.")

        return ConversationHandler.END
    elif user.id in ctx.bot_data["authorized_users"]:
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

        # Pick a random "secret" question to verify the user
        user_data["verify"] = choice(AUTH_QUESTIONS)

        await update.message.reply_text(
            "Okay\. Answer the following question to verify that you know my creator: "
            f"_{user_data['verify']['question']}_",
            parse_mode=ParseMode.MARKDOWN_V2
        )

        return VERIFY


async def verify(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Authorize a user (step 2)"""
    authorized_users: Set = ctx.bot_data["authorized_users"]
    banned_users: Set = ctx.bot_data["banned_users"]
    user = update.message.from_user

    if update.message.text.lower() == ctx.user_data["verify"]["answer"]:
        logger.info("User %s (%s) is now authorized", user.first_name, user.id)

        await update.message.reply_text("That's correct 🎉! You have been authorized.")

        authorized_users.add(user.id)
        banned_users.discard(user.id)
        ctx.user_data["verify"] = None

        return ConversationHandler.END
    else:
        ctx.user_data["auth_attempts"] -= 1

        if ctx.user_data["auth_attempts"] == 0:
            logger.info("User %s (%s) has given 3 wrong answer. Banned", user.first_name, user.id)
            await update.message.reply_text(
                "You gave 3 wrong answers! I'm sorry, but you are banned. Ask the admin to un-ban you."
            )
            banned_users.add(user.id)
            authorized_users.discard(user.id)

            return ConversationHandler.END

        logger.info(
            "User %s (%s) gave the wrong answer. Attempts left: %s",
            user.first_name,
            user.id,
            ctx.user_data["auth_attempts"]
        )

        await update.message.reply_text(
            "I'm sorry, that's not the right answer ☹️\. Try again\. "
            f"You have *{ctx.user_data['auth_attempts']}* attempts left\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )

        return VERIFY


async def cancel(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    user = update.message.from_user
    logger.info("User %s (%s) canceled the conversation.", user.first_name, user.id)

    await update.message.reply_text("Bye! I hope we can talk again some day.")

    return ConversationHandler.END


async def fallback(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle unrecognized commands"""
    await update.message.reply_text(f"I couldn't recognize your command `{update.message.text}`\. "
                                    "Would you try again? Use /help if you're unsure\.",
                                    parse_mode=ParseMode.MARKDOWN_V2)


def main() -> None:
    """Run the bot."""
    if not (TELEGRAM_BOT_TOKEN and OPENAI_API):
        raise RuntimeError("Either Telegram Bot token or OpenAI API key are missing! Abort.")

    # Bot persistence
    memory = PicklePersistence(filepath=pathlib.Path("telegram-chatgpt.pickle"))

    # Create the Application and pass it your bot's token.
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(memory).build()

    # Basic commands: /start, /help, /admin
    application.add_handlers(
        [
            CommandHandler("start", start),
            CommandHandler("help", help_command),
            CommandHandler("admin", admin)
        ],
        group=1
    )

    # Authorization handler
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
    application.add_handler(auth_handler, group=1)

    # ChatGPT conversation handler
    gpt_handler = ConversationHandler(
        entry_points=[
            CommandHandler("ask", start_chat)
        ],
        states={
            QUESTION: [
                MessageHandler(~filters.COMMAND & (filters.TEXT | filters.VOICE), ask_question)
            ]
        },
        fallbacks=[
            CommandHandler("done", end_chat),
            CommandHandler("stop", end_chat),
            CommandHandler("cancel", end_chat)
        ]
    )
    application.add_handler(gpt_handler, group=1)

    # Fallback handler for unknown commands
    application.add_handler(MessageHandler(filters.COMMAND, fallback), group=1)

    # Error handler
    application.add_error_handler(error_handler)

    # Run the bot until the user presses Ctrl-C
    application.run_polling()


if __name__ == "__main__":
    main()
