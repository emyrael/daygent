"""Relevant RAG + FastAPI flow mixed with unrelated Python helpers."""

from fastapi import FastAPI
from langchain_openai import ChatOpenAI
from langchain_qdrant import QdrantVectorStore
import requests

app = FastAPI()


def format_date(value: str) -> str:
    """Unrelated formatter. Must be omitted from the scoped graph."""
    return value


def calculate_discount(price: float) -> float:
    """Unrelated pricing helper. Must be omitted from the scoped graph."""
    return price * 0.9


def retrieve_documents(query: str) -> object:
    """Fetch context from Qdrant and an HTTP source."""
    requests.get("https://api.stripe.com/v1/customers")
    return QdrantVectorStore(collection_name="company_docs")


def answer_question(query: str) -> object:
    """Ground an answer in retrieved docs and an LLM."""
    retrieve_documents(query)
    return ChatOpenAI(model="gpt-5")


@app.post("/ask")
def ask(query: str) -> object:
    """Relevant API handler on the RAG flow."""
    return answer_question(query)


@app.get("/health")
def health() -> str:
    """Unrelated health route. Must be omitted from the scoped graph."""
    return "ok"
