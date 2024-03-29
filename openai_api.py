"""
OpenAI utils
"""
import os
from typing import List, Dict, Union, Any
import pathlib
import logging
import asyncio

import openai
from openai.error import OpenAIError
import tiktoken
import dotenv
from pydub import AudioSegment

MODELS = [
    "gpt-3.5-turbo",
    "gpt-3.5-turbo-0301",  # Snapshot of gpt-3.5-turbo from March 1st 2023. Expires June 1st 2023
    "gpt-4",
    "gpt-4-0314",  # Snapshot of gpt-4 from March 14th 2023. Expires June 14th 2023
]

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


def num_tokens_from_messages(
        messages: List[Dict], model: str = "gpt-3.5-turbo-0301"
) -> Union[int, None]:
    """
    Returns the number of tokens used by a list of messages.
    More at: https://platform.openai.com/docs/guides/chat/introduction
    """
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")

    if model.startswith("gpt-3.5"):  # note: future models may deviate from this
        num_tokens = 0
        for message in messages:
            num_tokens += (
                4  # every message follows{ <im_start>role/name}\n{content}<im_end>\n
            )
            for key, value in message.items():
                num_tokens += len(encoding.encode(value))
                if key == "name":  # if there's a name, the role is omitted
                    num_tokens += -1  # role is always required and always 1 token
        num_tokens += 2  # every reply is primed with <im_start>assistant
        return num_tokens
    else:
        raise NotImplementedError(
            f"num_tokens_from_messages() is not presently implemented for model {model}."
        )


async def chat_completion(messages: List[Dict], model: str = None, **kwargs) -> Any:
    """Prepare and send a Chat API request"""
    if not model or model == "default":
        model = "gpt-3.5-turbo"

    try:
        response = await openai.ChatCompletion.acreate(
            model=model, messages=messages, **kwargs
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


async def main():
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
    asyncio.run(main())
