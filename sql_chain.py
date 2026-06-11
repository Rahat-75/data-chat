"""
Text-to-SQL pipeline: question → SQL → execute → natural-language answer.
"""

import time
from collections.abc import Callable
from typing import Any, TypeVar

from langchain_community.utilities import SQLDatabase
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI

from config import get_google_api_key
from store_db import (
    clean_sql,
    execute_select,
    get_sqlalchemy_engine,
    summarize_rows_for_context,
)

try:
    from langchain.chains import create_sql_query_chain
except ModuleNotFoundError:
    from langchain_classic.chains import create_sql_query_chain

AVAILABLE_LLM_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemma-4-31b-it",
]
DEFAULT_LLM_MODEL = AVAILABLE_LLM_MODELS[0]

RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}
MAX_API_RETRIES = 4
RETRY_BASE_DELAY_SEC = 2.0

T = TypeVar("T")

SAMPLE_QUESTIONS = [
    "What was the total revenue in 2024?",
    "Which product category generated the most revenue?",
    "Who are the top 5 customers by total spending?",
    "How many orders were placed in March 2025?",
    "Which city has the highest number of customers?",
    "What products were never ordered?",
]

def _to_text(value: Any) -> str:
    """Normalize LangChain / Gemini responses to a plain string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if hasattr(value, "content"):
        return _to_text(value.content)
    if isinstance(value, list):
        parts: list[str] = []
        for block in value:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or ""))
            elif hasattr(block, "text"):
                parts.append(str(block.text))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(value)


EXPLAIN_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a data analyst assistant.\n"
            "The user asked a question about an online store database.\n"
            "A SQL query was run and you receive the question, SQL, and result rows.\n"
            "Explain the answer clearly in plain English.\n"
            "Use bullet points when listing multiple items.\n"
            "If the result is empty, say no matching data was found.\n"
            "Do not invent numbers that are not in the result.\n"
            "Keep the answer concise (under 120 words unless listing many items).",
        ),
        (
            "human",
            "Question: {question}\n\n"
            "SQL:\n{sql}\n\n"
            "Result ({row_count} rows):\n{result_summary}",
        ),
    ]
)


def get_llm(model: str = DEFAULT_LLM_MODEL, temperature: float = 0.0) -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(model=model, temperature=temperature)


def _is_retryable_api_error(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if status in RETRYABLE_HTTP_STATUS:
        return True
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "503",
            "429",
            "unavailable",
            "high demand",
            "rate limit",
            "overloaded",
            "resource exhausted",
        )
    )


def _invoke_with_retry(fn: Callable[[], T], max_attempts: int = MAX_API_RETRIES) -> T:
    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if not _is_retryable_api_error(exc) or attempt >= max_attempts - 1:
                raise
            time.sleep(RETRY_BASE_DELAY_SEC * (2 ** attempt))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("invoke_with_retry failed without an exception")


def _models_to_try(primary: str) -> list[str]:
    models = [primary]
    for model in AVAILABLE_LLM_MODELS:
        if model not in models:
            models.append(model)
    return models


def _api_error_response(exc: BaseException | None, explanation: str | None = None) -> dict[str, Any]:
    return {
        "sql": "",
        "rows": [],
        "row_count": 0,
        "explanation": explanation or (
            "The AI service is temporarily unavailable (high demand or rate limits). "
            "Wait a moment and try again, or pick a different model in Settings."
        ),
        "error": str(exc) if exc else "Unknown API error",
        "api_error": True,
    }


def _error_response(exc: BaseException) -> dict[str, Any]:
    if _is_retryable_api_error(exc):
        return _api_error_response(exc)

    msg = str(exc).lower()
    if any(
        token in msg
        for token in ("api key", "api_key", "401", "403", "credential", "permission denied", "unauthorized")
    ):
        return _api_error_response(
            exc,
            "GOOGLE_API_KEY is missing or invalid. Copy `.env.example` to `.env` in the "
            "data-chat folder and add your key from https://aistudio.google.com/app/apikey",
        )
    if "404" in msg or ("model" in msg and "not found" in msg):
        return _api_error_response(
            exc,
            "The selected model is not available. Open Settings and try another model.",
        )
    return _api_error_response(exc, f"Request failed: {exc}")


def get_sql_database() -> SQLDatabase:
    return SQLDatabase(get_sqlalchemy_engine())


def build_sql_query_chain(llm: ChatGoogleGenerativeAI, db: SQLDatabase | None = None):
    database = db or get_sql_database()
    return create_sql_query_chain(llm, database)


def _format_history(history: list[dict]) -> str:
    if not history:
        return ""
    lines = []
    for item in history[-3:]:
        lines.append(f"Q: {item['question']}")
        if item.get("sql"):
            lines.append(f"SQL: {item['sql']}")
        if item.get("summary"):
            lines.append(f"Result: {item['summary']}")
    return "\n".join(lines)


def _question_with_history(question: str, history: list[dict]) -> str:
    context = _format_history(history)
    if not context:
        return question
    return (
        "Use the conversation context below if the current question refers to it.\n\n"
        f"Context:\n{context}\n\n"
        f"Current question: {question}"
    )


def _retry_sql_with_error(
    llm: ChatGoogleGenerativeAI,
    db: SQLDatabase,
    question: str,
    bad_sql: str,
    error: str,
) -> str:
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "Fix the SQLite SELECT query. Return ONLY the corrected SQL, no markdown.",
            ),
            (
                "human",
                "Schema:\n{schema}\n\n"
                "Question: {question}\n\n"
                "Failed SQL:\n{bad_sql}\n\n"
                "Error: {error}",
            ),
        ]
    )
    schema = db.get_table_info()
    msg = prompt.format_messages(
        schema=schema,
        question=question,
        bad_sql=bad_sql,
        error=error,
    )
    response = _invoke_with_retry(lambda: llm.invoke(msg))
    return clean_sql(_to_text(response))


def _ask_data_with_llm(
    question: str,
    llm: ChatGoogleGenerativeAI,
    history: list[dict] | None = None,
    max_rows: int = 100,
) -> dict[str, Any]:
    database = get_sql_database()
    chain = build_sql_query_chain(llm, database)

    enriched_question = _question_with_history(question, history or [])
    raw_sql = _invoke_with_retry(lambda: chain.invoke({"question": enriched_question}))
    sql = clean_sql(_to_text(raw_sql))

    rows, error = execute_select(sql, max_rows=max_rows)
    if error:
        fixed = _retry_sql_with_error(llm, database, question, sql, error)
        sql = clean_sql(fixed)
        rows, error = execute_select(sql, max_rows=max_rows)

    if error:
        return {
            "sql": sql,
            "rows": [],
            "row_count": 0,
            "explanation": f"I could not run the query. Error: {error}",
            "error": error,
            "api_error": False,
        }

    summary = summarize_rows_for_context(rows)
    explain_chain = EXPLAIN_PROMPT | llm
    explanation = _invoke_with_retry(
        lambda: explain_chain.invoke(
            {
                "question": question,
                "sql": sql,
                "row_count": len(rows),
                "result_summary": summary,
            }
        )
    )
    return {
        "sql": sql,
        "rows": rows,
        "row_count": len(rows),
        "explanation": _to_text(explanation).strip(),
        "error": None,
        "api_error": False,
    }


def ask_data(
    question: str,
    history: list[dict] | None = None,
    max_rows: int = 100,
    model: str | None = None,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """
    Full pipeline: generate SQL, execute safely, explain results.

    Returns dict with keys: sql, rows, row_count, explanation, error, api_error
    """
    if not get_google_api_key():
        return _api_error_response(
            None,
            "GOOGLE_API_KEY is not set. Copy `.env.example` to `.env` in the data-chat folder "
            "and add your API key from https://aistudio.google.com/app/apikey",
        )

    primary = model or DEFAULT_LLM_MODEL
    last_exc: BaseException | None = None
    for model_name in _models_to_try(primary):
        try:
            active_llm = get_llm(model=model_name, temperature=temperature)
            return _ask_data_with_llm(question, active_llm, history, max_rows)
        except Exception as exc:
            if _is_retryable_api_error(exc):
                last_exc = exc
                continue
            return _error_response(exc)

    return _api_error_response(last_exc)
