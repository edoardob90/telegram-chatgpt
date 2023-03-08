# telegram-chatgpt
Personal assistant powered by ChatGPT as a Telegram bot. Uses the official [OpenAI API](https://platform.openai.com/docs/guides/chat).

### Minimal setup

- Create a Poetry environment and install the requirements
- Place your secrets in the `.env` file: `TELEGRAM_BOT_TOKEN`, `OPENAI_API` key, and the Telegram user id `ADMIN_USER_ID` of a user you want ot be able to administrate the bot
- Create a file named `.verify.json` which contains the questions & answers to verify the users. The format should be the following:

```json
[
  {"question":  "First Question",
  "answer":  "First Answer"},
  
  {"question":  "Second Question",
  "answer": "Second Answer"}
]
```

- The bot can be started with a simple command like `poetry run python main.py`
