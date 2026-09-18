"""Environment-backed model name must stay unresolved."""

import os

from langchain_openai import ChatOpenAI


def load_dynamic_model() -> object:
    """Constructor whose model comes from the environment, not a literal."""
    return ChatOpenAI(model=os.getenv("MODEL"))
