"""Second `recommend` implementation for ambiguous-name tests."""

from langchain_openai import ChatOpenAI


def recommend() -> object:
    """Literal LLM constructor in a second module named recommend."""
    return ChatOpenAI(model="gpt-5")
