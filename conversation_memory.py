"""
conversation_memory.py - Automatic conversation summarisation and context management.

Architecture
------------
Two storage layers are kept distinct:

1. **chat_store** (this module's consumer):
   Application-facing chat history – raw messages and summaries persisted to SQLite.

2. **LangGraph checkpointer** (orchestrator.py):
   LangGraph graph-state snapshots (per thread_id) – managed separately via
   SqliteCheckpointer.

This module is responsible for:
- Deciding *when* to summarise (configurable token/message threshold).
- Generating a concise summary of older messages via the LLM.
- Returning a combined "context block" (summary + recent verbatim messages)
  that the orchestrator injects into each agent invocation.
- Ensuring summarisation failures never corrupt original message history.
- Preventing infinite re-summarisation loops (tracks which messages are
  already covered by a summary).
"""

import logging
from typing import Optional

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from config import (
    GROQ_API_KEY,
    GROQ_MODEL_NAME,
    CHAT_DB_PATH,
    SUMMARISE_THRESHOLD as _DEFAULT_THRESHOLD,
    RECENT_WINDOW as _DEFAULT_WINDOW,
)

logger = logging.getLogger(__name__)

# Re-export defaults (callers can override per call)
DEFAULT_SUMMARISE_THRESHOLD = _DEFAULT_THRESHOLD
DEFAULT_RECENT_WINDOW = _DEFAULT_WINDOW


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _messages_to_lc(raw_messages: list[dict]) -> list:
    """Convert raw DB message dicts to LangChain message objects."""
    result = []
    for m in raw_messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "user":
            result.append(HumanMessage(content=content))
        elif role == "assistant":
            result.append(AIMessage(content=content))
        elif role == "system":
            result.append(SystemMessage(content=content))
        # 'tool' messages are omitted from summary context (they are internal)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Core summarisation
# ─────────────────────────────────────────────────────────────────────────────

def _generate_summary_text(
    existing_summary: Optional[str],
    messages_to_summarise: list[dict],
) -> str:
    """
    Ask the LLM to produce an updated summary that incorporates the existing
    summary (if any) and the new batch of messages.

    Deliberately does *not* commit anything to the DB – the caller is responsible
    for persisting the result (and only does so if this function succeeds).
    """
    from langchain_groq import ChatGroq
    from langchain_core.prompts import ChatPromptTemplate

    llm = ChatGroq(model=GROQ_MODEL_NAME, api_key=GROQ_API_KEY, temperature=0)

    history_lines = []
    for m in messages_to_summarise:
        role = m.get("role", "user").capitalize()
        content = m.get("content", "")
        history_lines.append(f"{role}: {content}")
    history_text = "\n".join(history_lines)

    prior_summary_section = (
        f"Existing summary of earlier messages:\n{existing_summary}\n\n"
        if existing_summary
        else ""
    )

    prompt = ChatPromptTemplate.from_template(
        """You are a conversation memory assistant for an AI-powered Invoice Automation system.

Your task is to produce a concise, updated summary that captures the key information from the conversation segment below.

{prior_summary}Conversation segment to summarise:
{history}

Write a summary that preserves:
- The user's goals and any unresolved requests.
- Relevant date ranges and invoice filters mentioned.
- Previously selected invoice groups or report parameters.
- Decisions, constraints, and important clarifications made.
- Pending actions and relevant tool outcomes (e.g. which report was generated).
- References such as "those invoices" when their meaning can be inferred.

Do NOT include exact invoice amounts, invoice IDs, or other financial figures in the summary – those must always be verified via the invoice retrieval tools. Keep the summary factual and concise (at most 300 words).

Updated summary:"""
    )

    chain = prompt | llm
    response = chain.invoke({"prior_summary": prior_summary_section, "history": history_text})
    return getattr(response, "content", str(response)).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────────────────────

def maybe_summarise(
    conversation_id: str,
    summarise_threshold: int = DEFAULT_SUMMARISE_THRESHOLD,
    recent_window: int = DEFAULT_RECENT_WINDOW,
    db_path: Optional[str] = None,
) -> None:
    """
    Check whether the conversation has grown past the threshold and, if so,
    summarise the older portion.

    Summarisation is triggered only when:
    - total messages >= summarise_threshold
    - the number of messages *not yet covered by a summary* is large enough to
      justify a new summary cycle (prevents re-summarising the same messages).

    The function is idempotent; calling it multiple times on a conversation
    below the threshold is a no-op.

    Failures during LLM call are logged and silently suppressed so that the
    caller's original messages are never lost.
    """
    from chat_store import get_all_messages_raw, get_latest_summary, save_summary

    _db = db_path or CHAT_DB_PATH

    all_msgs = get_all_messages_raw(conversation_id, _db)
    total = len(all_msgs)

    if total < summarise_threshold:
        return  # Below threshold – nothing to do

    latest_summary = get_latest_summary(conversation_id, _db)

    # Determine which messages are already covered by the existing summary
    covered_up_to_id = latest_summary.get("summarized_up_to_message_id") if latest_summary else None
    if covered_up_to_id:
        covered_ids = [m["message_id"] for m in all_msgs]
        try:
            covered_idx = covered_ids.index(covered_up_to_id)
        except ValueError:
            covered_idx = -1
        unsummarised = all_msgs[covered_idx + 1:]
    else:
        unsummarised = all_msgs

    # How many unsummarised messages sit *before* the recent window?
    messages_to_cover = unsummarised[: max(0, len(unsummarised) - recent_window)]

    if not messages_to_cover:
        return  # Everything fits within the recent window already

    # Avoid thrashing – only summarise if we have a meaningful new batch
    if len(messages_to_cover) < max(2, recent_window // 2):
        return

    existing_summary_text = latest_summary["summary_text"] if latest_summary else None
    last_covered_msg = messages_to_cover[-1]

    try:
        new_summary = _generate_summary_text(existing_summary_text, messages_to_cover)
    except Exception as exc:
        logger.error(
            "Summarisation failed for conversation %s: %s – original messages preserved.",
            conversation_id,
            exc,
        )
        return  # Graceful degradation – never corrupt history

    # Atomic save – only now do we write to the DB
    try:
        save_summary(
            conversation_id=conversation_id,
            summary_text=new_summary,
            summarized_up_to_message_id=last_covered_msg["message_id"],
            metadata={"messages_covered": len(messages_to_cover)},
            db_path=_db,
        )
    except Exception as exc:
        logger.error("Failed to persist summary for %s: %s", conversation_id, exc)


def build_context_block(
    conversation_id: str,
    recent_window: int = DEFAULT_RECENT_WINDOW,
    db_path: Optional[str] = None,
) -> dict:
    """
    Return a structured context block for the orchestrator.

    The returned dict contains:
    - ``summary``: str   – the latest accumulated summary (empty string if none).
    - ``recent_messages``: list[dict]  – the most recent *recent_window* raw DB messages.
    - ``lc_messages``: list  – LangChain message objects combining summary (as system
      context) and recent messages, ready to pass into the LangGraph agent.

    The current user message is NOT included here; the orchestrator appends it.
    """
    from chat_store import get_all_messages_raw, get_latest_summary

    _db = db_path or CHAT_DB_PATH

    latest_summary = get_latest_summary(conversation_id, _db)
    summary_text = latest_summary["summary_text"] if latest_summary else ""

    all_msgs = get_all_messages_raw(conversation_id, _db)
    recent_msgs = all_msgs[-recent_window:] if len(all_msgs) > recent_window else all_msgs

    # Build LangChain message list for the agent
    lc_messages = []
    if summary_text:
        lc_messages.append(
            SystemMessage(
                content=(
                    "The following is a summary of earlier conversation history. "
                    "Use it for context but always verify financial facts via tools:\n\n"
                    + summary_text
                )
            )
        )
    lc_messages.extend(_messages_to_lc(recent_msgs))

    return {
        "summary": summary_text,
        "recent_messages": recent_msgs,
        "lc_messages": lc_messages,
    }
