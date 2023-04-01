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
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.constants import ParseMode, ChatType
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    PicklePersistence,
)
from telegram.helpers import escape_markdown as _escape_markdown

import openai.error
import openai_api
import utils

# Load .env file
dotenv.load_dotenv()

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=(os.environ.get("LOG_LEVEL") or logging.DEBUG),
)
logger = logging.getLogger(__name__)

# Manually adjust logging level of verbose module(s)
logging.getLogger("hpack").setLevel(logging.WARNING)

# Conversation states
(
    AUTHORIZE,
    VERIFY,
    QUESTION,
    SETTINGS,
    STORE,
) = range(5)

# Chat's types
PRIVATE, GROUP = ChatType.PRIVATE, ChatType.GROUP

# User's settings names
SETTINGS_NAMES = {
    "temperature": "Temperature",
    "top_p": "Top probability",
    "presence_penalty": "Presence penalty",
    "frequency_penalty": "Frequency penalty",
    "model": "Language model",
}

# A few goodbye messages
goodbye_messages = [
    "May the Force be with you, {user}. Goodbye!",
    "Here's looking at you, {user}. Farewell!",
    "To infinity and beyond, {user}. See ya!",
    "Keep calm and carry on, {user}. Bye for now!",
    "I'll be back, {user}. Until then, goodbye!",
    "It's not goodbye, {user}. It's see you later!",
    "Goodbye, {user}. And thanks for all the fish!",
    "Parting is such sweet sorrow, {user}. Fare thee well!",
    "May the odds be ever in your favor, {user}. Goodbye!",
    "So long, {user}, and thanks for all the memories!",
    "Live long and prosper, {user}!",
    "Take care, {user}.",
    "Cheerio, {user}!",
    "See you soon, {user}.",
    "Goodnight {user}, sweet prince.",
]

# Config
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENAI_API = os.environ.get("OPENAI_API")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID"))

# Load the question/answer file for user verification
verify_file = pathlib.Path(".verify.json")
verify_file_hashed = verify_file.parent / f"{verify_file.stem}.sha256.json"
try:
    with verify_file_hashed.open(mode="r", encoding="utf-8") as verify_json:
        AUTH_QUESTIONS = json.load(verify_json)
except FileNotFoundError:
    if not verify_file.exists():
        raise FileNotFoundError("Either a '.verify.json' or '.verify.hashed.json' must exist. None found.")

    with verify_file_hashed.open(mode="w", encoding="utf-8") as verify_hashed:
        AUTH_QUESTIONS = [
            {k: utils.hash_data(v) if k == "answer" else v for k, v in q_a.items()}
            for q_a in json.load(verify_file.open(mode="r", encoding="utf-8"))
        ]
        json.dump(AUTH_QUESTIONS, verify_hashed, ensure_ascii=True, indent=2)

    # Remove '.verify.json'
    verify_file.unlink(missing_ok=True)

# Set OpenAI API key
openai_api.set_api_key(OPENAI_API)


# Helper function to escape Markdown reserved characters
def escape_markdown(text: str) -> str:
    """Helper function to escape telegram markup symbols.
    A slightly customized version of `telegram.helpers.escape_markdown`
    """
    patt = re.compile(r"(`+.*?`+)", re.DOTALL | re.MULTILINE)
    _text = [
        _escape_markdown(s, version=2) if not s.startswith("`") else s
        for s in re.split(patt, text)
    ]

    return "".join(_text)


def auth(admin_id: Union[int, None]) -> Callable:
    """Requires user's authorization (decorator)"""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(
                update: Update, context: ContextTypes.DEFAULT_TYPE
        ) -> Union[Any, None]:
            logger.info(
                "Auth request: user ID is %s, admin ID is %s",
                update.effective_user.id,
                admin_id,
            )
            if (
                    admin_id is not None and update.effective_user.id == admin_id
            ) or update.effective_user.id in context.bot_data.get(
                "authorized_users", set()
            ):
                return await func(update, context)
            else:
                await update.message.reply_text(
                    "You are not authorized to use this bot, sorry."
                    if admin_id is None
                    else "This command can only be run by an administrator."
                )

        return wrapper

    return decorator


def chat_type(c_type: str) -> Callable:
    """Verifies that a command can be used in a given chat type (decorator)"""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(
                update: Update, context: ContextTypes.DEFAULT_TYPE
        ) -> Union[Any, None]:
            if update.effective_chat.type == c_type:
                return await func(update, context)
            else:
                user = update.effective_user
                if c_type == ChatType.PRIVATE:
                    logger.info(
                        "User %s (%s) wants to run '%s', which can be run only PRIVATE chats",
                        user.first_name,
                        user.id,
                        func.__name__,
                    )
                    await update.message.reply_text(
                        "This command can only be run in a *private* chat\.",
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )
                    return ConversationHandler.END
                elif c_type == ChatType.GROUP:
                    logger.info(
                        "User %s (%s) wants to run '%s', which can be run only in GROUP chats",
                        user.first_name,
                        user.id,
                        func.__name__,
                    )
                    await update.message.reply_text(
                        "This command can only be run in a *group* chat\.)",
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )
                    return ConversationHandler.END

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
        "\- /cancel: stop the currently active action \(if any\)\n\n"
        "\- /settings: change the user's settings \(model type and properties\)",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a telegram message to notify the developer."""

    # Log the error before we do anything else, so we can see it even if something breaks.
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

    # traceback.format_exception returns the usual python message about an exception, but as a
    # list of strings rather than a single string, so we have to join them together.
    tb_list = traceback.format_exception(
        None, context.error, context.error.__traceback__
    )
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
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID, text=message, parse_mode=ParseMode.HTML
        )


@auth(None)
async def start_chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Initiate a new conversation with ChatGPT"""
    user = update.message.from_user
    user_data = ctx.user_data

    if "settings" not in user_data:
        user_data["settings"] = {
            "temperature": 0.8,
            "presence_penalty": 0.0,
            "frequency_penalty": 0.0,
            "top_p": 1.0,
            "model": "default",
        }

    if "messages" not in user_data:
        logger.info(
            "Initializing user %s (%s) message history", user.first_name, user.id
        )
        user_data["messages"] = []

    # Reset user's message history every time a new chat is opened
    logger.info(
        "User %s (%s) started a new chat, resetting message history",
        user.first_name,
        user.id,
    )
    user_data["messages"].clear()

    if ctx.args:
        # TODO: double-check that there aren't multiple "system" messages
        logger.info(
            "User %s (%s) sent a message to prime the assistant",
            user.first_name,
            user.id,
        )
        user_data["messages"].append({"role": "system", "content": " ".join(ctx.args)})

    logger.info("User's messages so far: %s", user_data["messages"])

    await update.message.reply_text(
        f"Okay {user.first_name}, go ahead, ask me anything! "
        "Write me or send me a voice message..."
    )

    return QUESTION


async def ask_question(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Ask a question, taking into account the previous messages"""
    user = update.message.from_user
    user_data = ctx.user_data

    # Check if the message contained a voice message
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
        except openai.error.OpenAIError as err:
            await update.message.reply_text(
                "I'm sorry, but I couldn't understand your voice message. Please, try again."
            )
            raise err
        else:
            # Add the transcribed audio to the user's messages
            user_data["messages"].append(
                {"role": "user", "content": audio_text["text"]}
            )
        finally:
            temp_mp3.unlink()
            temp_audio.unlink()
    elif update.message.text:
        user_data["messages"].append({"role": "user", "content": update.message.text})

    logger.info("User's messages so far: %s", user_data["messages"])

    try:
        logger.info(
            "User %s (%s) is sending a Chat API request...", user.first_name, user.id
        )
        response = await openai_api.chat_completion(
            user_data["messages"], **user_data["settings"]
        )
    except openai.error.OpenAIError as error:
        # Remove the last message from user's history to avoid duplicates
        user_data["messages"].pop()

        if isinstance(error, openai.error.RateLimitError):
            await update.message.reply_text(
                "Whoa! Your request have been rate-limited. Either you reached the monthly billing limit or "
                "you sent too many requests. Please, slow down a bit.\n"
                "Feel free to start a new chat with /ask."
            )
            if ADMIN_USER_ID is not None:
                ctx.bot.send_message(
                    chat_id=ADMIN_USER_ID,
                    text=f"User '{user.first_name}' ({user.id}) has been rate-limited. Check your OpenAI API usage."
                )
            return ConversationHandler.END
        else:
            await update.message.reply_text(
                "I'm sorry, but something went wrong. Please, try again."
            )
            raise error
    else:
        logger.info("Response OK, no errors, replying back to the user...")
        # Store the assistant's reply in user's message history
        reply = response["choices"][0]["message"]["content"]
        user_data["messages"].append({"role": "assistant", "content": reply})

        await update.message.reply_text(
            escape_markdown(reply), parse_mode=ParseMode.MARKDOWN_V2
        )

        return QUESTION


async def end_chat(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    """Terminates an open chat session"""
    user = update.effective_user
    logger.info("User %s (%s) ended the conversation.", user.first_name, user.id)

    await update.message.reply_text(
        choice(goodbye_messages).format(user=user.first_name)
    )

    return ConversationHandler.END


@chat_type(PRIVATE)
@auth(ADMIN_USER_ID)
async def admin(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command"""
    # TODO: to implement
    await update.message.reply_text("Hello, admin!")


@chat_type(PRIVATE)
async def authorize(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Authorize a user (step 1)"""
    user = update.message.from_user

    if "authorized_users" not in ctx.bot_data:
        logger.info("Initializing 'authorized_users'")
        ctx.bot_data["authorized_users"] = set()

    if "banned_users" not in ctx.bot_data:
        logger.info("Initializing 'banned_users'")
        ctx.bot_data["banned_users"] = set()

    logger.info("User %s (%s) entered the authorization step", user.first_name, user.id)

    if user.id in ctx.bot_data["banned_users"]:
        logger.info("User %s (%s) is banned", user.first_name, user.id)

        await update.message.reply_text(
            "I'm sorry, but you have been banned. Contact the admin to un-ban you."
        )

        return ConversationHandler.END
    elif user.id in ctx.bot_data["authorized_users"]:
        logger.info("User %s (%s) is already authorized", user.first_name, user.id)

        await update.message.reply_text("You are already authorized!")

        return ConversationHandler.END
    else:
        if "auth_attempts" not in ctx.user_data or ctx.user_data["auth_attempts"] != 3:
            logger.info("Resetting user %s auth attempts", user.id)
            ctx.user_data["auth_attempts"] = 3

        if "verify" not in (user_data := ctx.user_data):
            user_data["verify"] = None

        # Pick a random "secret" question to verify the user
        user_data["verify"] = choice(AUTH_QUESTIONS)

        await update.message.reply_text(
            "Okay\. Answer the following question to verify that you know my creator: "
            f"_{user_data['verify']['question']}_",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

        return VERIFY


async def verify(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Authorize a user (step 2)"""
    authorized_users: Set = ctx.bot_data["authorized_users"]
    banned_users: Set = ctx.bot_data["banned_users"]
    user = update.message.from_user

    if utils.hash_data(update.message.text.lower()) == ctx.user_data["verify"]["answer"]:
        logger.info("User %s (%s) is now authorized", user.first_name, user.id)

        await update.message.reply_text("That's correct 🎉! You have been authorized.")

        authorized_users.add(user.id)
        banned_users.discard(user.id)
        ctx.user_data["verify"] = None

        return ConversationHandler.END
    else:
        ctx.user_data["auth_attempts"] -= 1

        if ctx.user_data["auth_attempts"] == 0:
            logger.info(
                "User %s (%s) has given 3 wrong answer. Banned",
                user.first_name,
                user.id,
            )
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
            ctx.user_data["auth_attempts"],
        )

        await update.message.reply_text(
            "I'm sorry, that's not the right answer ☹️\. Try again\. "
            f"You have *{ctx.user_data['auth_attempts']}* attempts left\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

        return VERIFY


@chat_type(PRIVATE)
@auth(None)
async def enter_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Enter user's settings"""
    user_data = ctx.user_data

    # The very first time entering /settings, we're NOT starting over by definition
    if "start_over" not in user_data:
        user_data["start_over"] = False

    # Initialize the default settings
    if "settings" not in user_data:
        user_data["settings"] = {
            "temperature": 0.8,
            "presence_penalty": 0.0,
            "frequency_penalty": 0.0,
            "top_p": 1.0,
            "model": "default",
        }

    settings = user_data["settings"]
    text = (
            "Here are my internal settings for your conversations:\n\n"
            + "\n".join(
        [f" - {name}: {settings[key]}" for key, name in SETTINGS_NAMES.items()]
    )
            + "\n\nChoose which one you want to change or the ❓Help button to know more about these settings"
    )
    buttons = [
        [InlineKeyboardButton(text="🤖 Model", callback_data="MODEL")],
        [
            InlineKeyboardButton(text="🌡️ Temperature", callback_data="TEMPERATURE"),
            InlineKeyboardButton(text="🎲 Top probability", callback_data="TOP_P"),
        ],
        [
            InlineKeyboardButton(
                text="🃏 Presence penalty", callback_data="PRESENCE_PENALTY"
            ),
            InlineKeyboardButton(
                text="📊 Frequency penalty", callback_data="FREQUENCY_PENALTY"
            ),
        ],
        [
            InlineKeyboardButton(text="❓ Help", callback_data="HELP"),
            InlineKeyboardButton(text="🚪 Exit", callback_data="EXIT"),
        ],
    ]
    keyboard = InlineKeyboardMarkup(buttons)

    if user_data["start_over"]:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text=text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text=text, reply_markup=keyboard)

    user_data["start_over"] = False

    return SETTINGS


async def help_settings(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick help about user's settings meaning"""
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "Here's a quick explanation of the meaning of each setting you can tweak:\n\n"
        "*Temperature*\n"
        "It's a real number between 0 and 2\. The higher the temperature, the more random the output\. "
        "The lower, the more deterministic\. Higher temperature means that less likely words will be picked, "
        "which in turn means the model will be more _creative_\. Defaults to 1\.0\n\n"
        "*Top P* \(probability\)\n"
        "It's an alternative to temperature sampling\. It's a probability cutoff that defines from which subset of "
        "the vocabulary the next token will be picked\. For example, `top_p = 0.1` means that only the tokens with "
        "a cumulative probability greater than 90% will be actual candidates\. "
        "The vocabulary subset is updated at **every** generation step\. "
        "It's **not suggested** to alter both Top P and the temperature at the same time\. Defaults to 1\.0\n\n"
        "*Presence and Frequency penalty*\n"
        "These two are numbers between \-2\.0 and 2\.0\. Positive values penalize the insertion of new tokens based on "
        "either their _presence_ in the text generated so far, or their _frequency_ in the text\. "
        "Higher presence penalties will increase the likelihood of talking about new topics, while frequency penalties "
        "reduce the likelihood of verbatim repetitions of portions of text\. Defaults are 0\.0 for both",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=InlineKeyboardMarkup.from_button(
            InlineKeyboardButton(text="↩️ Back", callback_data="BACK")
        ),
    )


async def set_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Set a user's settings value"""
    user_data = ctx.user_data
    current_setting = str(update.callback_query.data).lower()
    user_data["current_setting"] = current_setting

    buttons = [
        [InlineKeyboardButton(text=model, callback_data=model)]
        for model in openai_api.MODELS
    ]
    buttons.append([InlineKeyboardButton(text="↩️ Back", callback_data="BACK")])
    keyboard = InlineKeyboardMarkup(buttons) if current_setting == "model" else None

    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        f"Okay, enter a new *{SETTINGS_NAMES[current_setting].lower()}* value "
        f"\(current is {escape_markdown(str(user_data['settings'][current_setting]))}\)\. "
        "Type /cancel to stop\.",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=keyboard,
    )

    return STORE


async def store_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Store a user's settings value"""
    user_data = ctx.user_data

    if query := update.callback_query:
        user_data["settings"][user_data["current_setting"]] = query.data

        await query.answer()
        await query.edit_message_text(
            f"The language model *{escape_markdown(query.data)}* is now the default for your conversations\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup.from_button(
                InlineKeyboardButton(text="↩️ Back", callback_data="BACK")
            ),
        )
    else:
        new_value = float(update.message.text)
        user_data["settings"][user_data["current_setting"]] = new_value

        await update.message.reply_text(
            f"The new *{user_data['current_setting']}* value is {escape_markdown(str(new_value))}\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup.from_button(
                InlineKeyboardButton(text="↩️ Back", callback_data="BACK")
            ),
        )

    del user_data["current_setting"]


async def back(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Go back to the previous menu"""
    ctx.user_data["start_over"] = True
    return await enter_settings(update, ctx)


async def exit_settings(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    """Exit user's settings menu"""
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        choice(goodbye_messages).format(user=update.effective_user.first_name)
    )

    return ConversationHandler.END


async def fallback(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle unrecognized commands"""
    await update.message.reply_text(
        f"I couldn't recognize your command `{update.message.text}`\. "
        "Would you try again? Use /help if you're unsure\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def cancel(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    user = update.effective_user
    logger.info("User %s (%s) canceled the conversation.", user.first_name, user.id)

    await update.message.reply_text(
        choice(goodbye_messages).format(user=user.first_name)
    )

    return ConversationHandler.END


def main() -> None:
    """Run the bot."""
    if not (TELEGRAM_BOT_TOKEN and OPENAI_API):
        raise RuntimeError(
            "Either Telegram Bot token or OpenAI API key are missing! Abort."
        )

    # Bot persistence
    memory = PicklePersistence(filepath=pathlib.Path("telegram-chatgpt.pickle"))

    # Create the Application and pass it your bot's token.
    application = (
        Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(memory).build()
    )

    # Basic commands: /start, /help, /admin
    application.add_handlers(
        [
            CommandHandler("start", start),
            CommandHandler("help", help_command),
            CommandHandler("admin", admin),
        ],
        group=1,
    )

    # Authorization handler
    auth_handler = ConversationHandler(
        entry_points=[CommandHandler("auth", authorize)],
        states={VERIFY: [MessageHandler(filters.TEXT & ~filters.COMMAND, verify)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(auth_handler, group=1)

    # ChatGPT conversation handler
    gpt_handler = ConversationHandler(
        entry_points=[CommandHandler("ask", start_chat)],
        states={
            QUESTION: [
                MessageHandler(
                    ~filters.COMMAND & (filters.TEXT | filters.VOICE), ask_question
                )
            ]
        },
        fallbacks=[
            CommandHandler("done", end_chat),
            CommandHandler("stop", end_chat),
            CommandHandler("cancel", end_chat),
        ],
        per_user=False
    )
    application.add_handler(gpt_handler, group=1)

    # User settings handler
    user_settings = ConversationHandler(
        entry_points=[CommandHandler("settings", enter_settings)],
        states={
            SETTINGS: [
                CallbackQueryHandler(
                    set_value,
                    pattern="^"
                            + "$|^".join(
                        [
                            "MODEL",
                            "TEMPERATURE",
                            "PRESENCE_PENALTY",
                            "FREQUENCY_PENALTY",
                            "TOP_P",
                        ]
                    )
                            + "$",
                ),
                CallbackQueryHandler(help_settings, pattern="^HELP$"),
            ],
            STORE: [
                MessageHandler(filters.Regex(r"[\d\.]+"), store_value),
                CallbackQueryHandler(
                    store_value, pattern="^" + "$|^".join(openai_api.MODELS) + "$"
                ),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(back, pattern="^BACK$"),
            CallbackQueryHandler(exit_settings, pattern="^EXIT$"),
        ],
    )
    application.add_handler(user_settings, group=1)

    # Fallback handler for unknown commands
    application.add_handler(MessageHandler(filters.COMMAND, fallback), group=1)

    # Error handler
    application.add_error_handler(error_handler)

    # Run the bot until the user presses Ctrl-C
    application.run_polling()


if __name__ == "__main__":
    main()
