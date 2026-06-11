"""
Streamlit UI for DataChat (text-to-SQL).

Run: streamlit run app.py
"""

import streamlit as st

import chat_db
from config import get_google_api_key, ROOT
from sql_chain import (
    AVAILABLE_LLM_MODELS,
    DEFAULT_LLM_MODEL,
    SAMPLE_QUESTIONS,
    ask_data,
)
from store_db import ensure_store_db, get_table_row_counts

chat_db.init_db()
ensure_store_db()

st.set_page_config(
    page_title="DataChat",
    page_icon="💬",
    layout="wide",
    initial_sidebar_state="expanded",
)

if "messages" not in st.session_state:
    st.session_state.messages = []
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None


def inject_styles():
    st.markdown(
        """
        <style>
            #MainMenu, footer, .stDeployButton {visibility: hidden;}
            .stApp { background-color: #212121; }
            section[data-testid="stSidebar"] {
                background-color: #171717 !important;
                border-right: 1px solid #2f2f2f;
            }
            section[data-testid="stSidebar"] button[kind="primary"] {
                background-color: #2563eb !important;
                border-color: #2563eb !important;
            }
            .main .block-container { max-width: 100%; padding-top: 1rem; padding-bottom: 2rem; }
            .chat-container { max-width: 52rem; margin: 0 auto; }
            .welcome-title {
                text-align: center; font-size: 1.6rem; font-weight: 500; color: #ececec;
                margin: 3rem 0 0.4rem;
            }
            .welcome-sub {
                text-align: center; color: #8e8e8e; font-size: 0.9rem; margin-bottom: 1.25rem;
            }
            .sample-questions { max-width: 38rem; margin: 0 auto 0.75rem; }
            .sample-questions .stButton > button {
                width: 100%; font-size: 0.8rem; text-align: left; white-space: normal;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def start_new_chat():
    st.session_state.messages = []
    st.session_state.conversation_id = None


def load_chat(conversation_id: int):
    st.session_state.conversation_id = conversation_id
    st.session_state.messages = chat_db.load_messages(conversation_id)


def _short_label(text: str, max_len: int = 34) -> str:
    cleaned = " ".join(text.strip().split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3] + "..."


def render_sidebar():
    with st.sidebar:
        st.markdown("### DataChat")

        if not get_google_api_key():
            st.error(
                "GOOGLE_API_KEY not found. Copy `.env.example` to `.env` in this folder "
                f"(`{ROOT}`) and add your key."
            )

        if st.button("New chat", use_container_width=True):
            start_new_chat()
            st.rerun()

        st.markdown("**Recent chats**")
        for conv in chat_db.list_conversations(limit=15):
            label = _short_label(chat_db.get_display_title(conv["id"], conv["title"]))
            is_active = st.session_state.conversation_id == conv["id"]
            if st.button(
                label,
                key=f"chat_{conv['id']}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
            ):
                load_chat(conv["id"])
                st.rerun()

        st.markdown("---")
        counts = get_table_row_counts()
        st.caption(
            "Online store DB: "
            + ", ".join(f"{t} ({n})" for t, n in counts.items())
        )

        llm_model = DEFAULT_LLM_MODEL
        temperature = 0.0

        with st.expander("Settings", expanded=False):
            llm_model = st.selectbox("Model", AVAILABLE_LLM_MODELS, index=0)
            temperature = st.slider("Temperature", 0.0, 1.0, 0.0, 0.05)

            if st.session_state.conversation_id and st.button("Delete this chat", use_container_width=True):
                chat_db.delete_conversation(st.session_state.conversation_id)
                start_new_chat()
                st.rerun()

            if st.button("Re-seed sample data", use_container_width=True):
                from seed_data import seed_database

                seed_database()
                st.toast("Database re-seeded.")
                st.rerun()

        return llm_model, temperature


def render_empty_state():
    st.markdown('<p class="welcome-title">Ask your data anything</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="welcome-sub">Natural language → SQL → results. Sample online store with customers, products, and orders.</p>',
        unsafe_allow_html=True,
    )


def render_sample_questions():
    st.markdown('<div class="sample-questions">', unsafe_allow_html=True)
    for i, q in enumerate(SAMPLE_QUESTIONS):
        if st.button(q, key=f"sample_{i}"):
            st.session_state.pending_question = q
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


def render_message(msg: dict):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sql"):
            with st.expander("SQL query", expanded=False):
                st.code(msg["sql"], language="sql")
        if msg.get("rows"):
            with st.expander(f"Result ({len(msg['rows'])} rows)", expanded=True):
                st.dataframe(msg["rows"], use_container_width=True, hide_index=True)


def ensure_conversation() -> int:
    if st.session_state.conversation_id is not None:
        return st.session_state.conversation_id
    cid = chat_db.create_conversation("New chat")
    st.session_state.conversation_id = cid
    return cid


def submit_user_message(prompt: str) -> None:
    conv_id = ensure_conversation()
    conv = chat_db.get_conversation(conv_id)
    if conv and conv["title"] == "New chat" and not st.session_state.messages:
        chat_db.update_conversation_title(conv_id, _short_label(prompt, 48))
    chat_db.add_message(conv_id, "user", prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})


def run_pending_generation() -> None:
    gen = st.session_state.pop("generating")
    conv_id = st.session_state.conversation_id
    history = chat_db.get_history_for_sql(conv_id) if conv_id else []

    with st.chat_message("assistant"):
        with st.spinner("Querying data..."):
            result = ask_data(
                gen["prompt"],
                model=gen["llm_model"],
                temperature=gen["temperature"],
                history=history,
            )

        if result.get("api_error"):
            st.warning(result["explanation"])
            if result.get("error"):
                with st.expander("Technical details", expanded=False):
                    st.code(result["error"])
        else:
            st.markdown(result["explanation"])
            if result.get("sql"):
                with st.expander("SQL query", expanded=False):
                    st.code(result["sql"], language="sql")
            if result.get("rows"):
                with st.expander(f"Result ({result['row_count']} rows)", expanded=True):
                    st.dataframe(result["rows"], use_container_width=True, hide_index=True)

    chat_db.add_message(
        conv_id,
        "assistant",
        result["explanation"],
        sql_text=result["sql"],
        rows=result["rows"],
    )
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": result["explanation"],
            "sql": result["sql"],
            "rows": result["rows"],
        }
    )
    st.rerun()


def main():
    inject_styles()
    llm_model, temperature = render_sidebar()

    with st.container():
        st.markdown('<div class="chat-container">', unsafe_allow_html=True)

        for msg in st.session_state.messages:
            render_message(msg)

        if st.session_state.get("generating"):
            run_pending_generation()

        has_messages = bool(st.session_state.messages)
        if not has_messages and not st.session_state.get("generating"):
            render_empty_state()
            render_sample_questions()

        prompt = st.session_state.pop("pending_question", None)
        if prompt is None:
            prompt = st.chat_input("Ask about customers, products, orders, revenue...")

        if prompt:
            submit_user_message(prompt)
            st.session_state.generating = {
                "prompt": prompt,
                "llm_model": llm_model,
                "temperature": temperature,
            }
            st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
