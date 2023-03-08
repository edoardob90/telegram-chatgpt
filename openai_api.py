"""
OpenAI utils
"""
import os
from typing import List, Dict, Union

import openai
from openai.error import OpenAIError
import tiktoken
import dotenv


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


def num_tokens_from_messages(messages: List[Dict], model: str = "gpt-3.5-turbo-0301") -> Union[int, None]:
    """
    Returns the number of tokens used by a list of messages.
    More at: https://platform.openai.com/docs/guides/chat/introduction
    """
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")

    if model == "gpt-3.5-turbo-0301":  # note: future models may deviate from this
        num_tokens = 0
        for message in messages:
            num_tokens += 4  # every message follows{ <im_start>role/name}\n{content}<im_end>\n
            for key, value in message.items():
                num_tokens += len(encoding.encode(value))
                if key == "name":  # if there's a name, the role is omitted
                    num_tokens += -1  # role is always required and always 1 token
        num_tokens += 2  # every reply is primed with <im_start>assistant
        return num_tokens
    else:
        raise NotImplementedError(f"num_tokens_from_messages() is not presently implemented for model {model}.")


def send_request(messages: List[Dict], model: str = "gpt-3.5-turbo-0301"):
    """Prepare and send an API request"""
    # TODO: check the number of tokens < 2048 (max 4096), cut it if necessary
    # TODO: might be better to do an async request
    try:
        response = openai.ChatCompletion.create(model=model, temperature=0.8, messages=messages)
    # TODO: be more specific with the exception type (e.g., max tokens limit reached)
    except OpenAIError as err:
        raise RuntimeError("Error while performing an API request to OpenAI") from err
    else:
        return response


if __name__ == "__main__":
    dotenv.load_dotenv()
    openai.api_key = os.environ.get("OPENAI_API")
    _messages = [
        {"role": "system",
         "content": "You are a friendly physics high-school teacher."},
        {"role": "user",
         "content": "Explain the many-world interpretation of quantum mechanics"},
    ]

    print(num_tokens_from_messages(_messages))

    _response = send_request(_messages)

    print(_response["choices"][0]["message"]["content"])
