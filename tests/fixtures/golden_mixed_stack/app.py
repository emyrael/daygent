"""FastAPI + RAG connectors mixed with an unrelated health route."""

from fastapi import FastAPI
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_qdrant import QdrantVectorStore
import requests

app = FastAPI()


def retrieve_documents(query: str) -> object:
    """Fetch context from Qdrant, embeddings, and a literal HTTP host."""
    requests.get("https://api.stripe.com/v1/customers")
    OpenAIEmbeddings(model="text-embedding-3-small")
    return QdrantVectorStore(collection_name="company_docs")


def answer_question(query: str) -> object:
    """Ground an answer in retrieved docs and a literal LLM."""
    retrieve_documents(query)
    return ChatOpenAI(model="gpt-5")


@app.post("/ask")
def ask(query: str) -> object:
    """Relevant API handler on the RAG flow."""
    return answer_question(query)


@app.post("/recommend")
def recommend() -> object:
    """Second recommend() used by POST /recommend; stays via the LLM constructor."""
    return ChatOpenAI(model="gpt-5")


@app.get("/health")
def health() -> str:
    """Unrelated health route. Must be omitted from the scoped graph."""
    return "ok"
