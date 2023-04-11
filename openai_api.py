"""
OpenAI utils
"""
import os
from typing import List, Dict, Union, Any
from collections import namedtuple, deque
import pathlib
import logging
import asyncio

import openai
from openai.error import OpenAIError
import tiktoken
import dotenv
from pydub import AudioSegment

OpenAIModel = namedtuple("OpenAIModel", ["id", "max_tokens", "variants"], defaults=(None,))

MODELS = {
    "GPT-3.5": OpenAIModel(
        "gpt-3.5-turbo",
        4000,
        ("gpt-3.5-turbo-0301",),  # Snapshot of gpt-3.5-turbo from March 1st 2023. Expires June 1st 2023
    ),
    "GPT-4": OpenAIModel(
        "gpt-4",
        8000,
        ("gpt-4-0314",),  # Snapshot of gpt-4 from March 14th 2023. Expires June 14th 2023
    )
}

# Logging'
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.DEBUG
)
logger = logging.getLogger(__name__)


def set_api_key(api_key: str = None) -> None:
    """Set OpenAI API key"""
    if api_key:
        openai.api_key = api_key


def num_tokens_from_string(string: str, model: str = "gpt-3.5-turbo-0301") -> int:
    """Returns the number of tokens in a text string."""
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding(model)
    return len(encoding.encode(string))


def build_messages_to_send(messages: List[Dict], model: OpenAIModel) -> List[Dict]:
    """Collect the messages to send to the API, handling the model's tokens limit"""
    if model.id == "gpt-3.5-turbo":
        tokens_per_message = 4
        tokens_per_name = -1
    elif model.id == "gpt-4":
        tokens_per_message = 3
        tokens_per_name = 1
    else:
        msg = "Unknown model, cannot estimate the number of tokens. Did you spell the model's name right?"
        logger.error(msg)
        raise NotImplementedError(msg)

    tokens_count = 0
    messages_to_send = deque()

    # Count the tokens in the message history
    for message in reversed(messages):
        tokens_count += tokens_per_message
        for key, value in message.items():
            tokens_count += num_tokens_from_string(value, model.id)
            if key == "name":
                tokens_count += tokens_per_name

        # Check if we have filled the model's context plus a buffer
        # The buffer is models max tokens / 2
        if (tokens_count + model.max_tokens // 2) > model.max_tokens:
            break

        messages_to_send.appendleft(message)

    return list(messages_to_send)


async def chat_completion(messages: List[Dict], model: str = None, **kwargs) -> Any:
    """Prepare and send a Chat API request"""
    try:
        openai_model = MODELS[model]
    except KeyError:
        openai_model = MODELS["GPT-3.5"]

    messages_to_send = build_messages_to_send(messages, openai_model)

    try:
        response = await openai.ChatCompletion.acreate(
            model=openai_model.id, messages=messages_to_send, **kwargs
        )
    # TODO: be more specific with the exception type (e.g., rate-limit has been reached)
    except OpenAIError:
        logger.error("Error while performing a Chat API request")
        raise
    else:
        return response


async def transcribe_audio(filepath: Union[str, pathlib.Path], **kwargs) -> Dict:
    """Transcribe & translate audio file to English text"""
    filepath = pathlib.Path(filepath) if isinstance(filepath, str) else filepath
    try:
        with filepath.open("rb") as file:
            response = await openai.Audio.atranscribe(
                model="whisper-1", file=file, **kwargs
            )
    except FileNotFoundError:
        logger.error(f"File '{filepath.name}' cannot be found")
        raise
    except OpenAIError:
        logger.error("Error while performing an audio translation API request")
        raise
    else:
        return response


async def _main():
    logger.addHandler(logging.StreamHandler())

    dotenv.load_dotenv()
    openai.api_key = os.environ.get("OPENAI_API")

    audio_file = pathlib.Path("audio/one.ogg")

    AudioSegment.from_ogg(audio_file).export("./audio/one.mp3", format="mp3")
    audio_text = await transcribe_audio("audio/one.mp3")

    print(audio_text)

    _messages = [
        {"role": "system", "content": "You are a friendly high-school teacher."},
        {"role": "user", "content": audio_text["text"]},
    ]

    _response = await chat_completion(_messages)

    print(_response["choices"][0]["message"]["content"])


if __name__ == "__main__":
    asyncio.run(_main())
