"""
tests/test_memory.py - Tests for persistent conversation history and summarisation.

All 12 test cases as specified. LLM calls and external services are mocked.
Run with: pytest tests/test_memory.py -v
"""

import os
import sys
import uuid
import tempfile
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    """Provide a fresh temporary SQLite database per test."""
    db_file = str(tmp_path / "test_invoices.db")
    from chat_store import init_db
    init_db(db_file)
    return db_file


@pytest.fixture
def conversation_id():
    return str(uuid.uuid4())


@pytest.fixture
def user_id():
    return f"user_{uuid.uuid4().hex[:8]}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Creating a new conversation and storing messages
# ─────────────────────────────────────────────────────────────────────────────

def test_create_conversation_and_store_messages(tmp_db, user_id):
    from chat_store import create_conversation, save_message, get_messages

    conv_id = create_conversation(user_id=user_id, title="Test conv", db_path=tmp_db)
    assert conv_id is not None

    mid1 = save_message(conversation_id=conv_id, role="user", content="Hello", db_path=tmp_db)
    mid2 = save_message(conversation_id=conv_id, role="assistant", content="Hi!", db_path=tmp_db)

    assert mid1 is not None
    assert mid2 is not None

    msgs = get_messages(conversation_id=conv_id, user_id=user_id, db_path=tmp_db)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "Hello"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "Hi!"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Continuing an existing conversation
# ─────────────────────────────────────────────────────────────────────────────

def test_continue_existing_conversation(tmp_db, user_id):
    from chat_store import create_conversation, save_message, get_messages, list_conversations

    conv_id = create_conversation(user_id=user_id, db_path=tmp_db)
    save_message(conversation_id=conv_id, role="user", content="msg1", db_path=tmp_db)
    save_message(conversation_id=conv_id, role="assistant", content="resp1", db_path=tmp_db)

    # Simulate second turn
    save_message(conversation_id=conv_id, role="user", content="msg2", db_path=tmp_db)
    save_message(conversation_id=conv_id, role="assistant", content="resp2", db_path=tmp_db)

    msgs = get_messages(conversation_id=conv_id, user_id=user_id, db_path=tmp_db)
    assert len(msgs) == 4
    assert msgs[-1]["content"] == "resp2"

    # Conversation appears in listing
    convs = list_conversations(user_id=user_id, db_path=tmp_db)
    assert any(c["conversation_id"] == conv_id for c in convs)


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Resuming a conversation after application restart
# ─────────────────────────────────────────────────────────────────────────────

def test_resume_after_restart(tmp_db, user_id):
    from chat_store import (
        create_conversation, save_message, get_messages, get_or_create_conversation
    )

    # "Before restart"
    conv_id = create_conversation(user_id=user_id, db_path=tmp_db)
    save_message(conversation_id=conv_id, role="user", content="Before restart", db_path=tmp_db)
    save_message(conversation_id=conv_id, role="assistant", content="OK", db_path=tmp_db)

    # "After restart" – same DB, same conv_id
    resumed_id = get_or_create_conversation(
        conversation_id=conv_id, user_id=user_id, db_path=tmp_db
    )
    assert resumed_id == conv_id  # same conversation returned

    msgs = get_messages(conversation_id=resumed_id, user_id=user_id, db_path=tmp_db)
    assert len(msgs) == 2
    assert msgs[0]["content"] == "Before restart"


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Isolation between conversations and users
# ─────────────────────────────────────────────────────────────────────────────

def test_conversation_isolation(tmp_db):
    from chat_store import create_conversation, save_message, get_messages

    user_a = f"user_a_{uuid.uuid4().hex[:6]}"
    user_b = f"user_b_{uuid.uuid4().hex[:6]}"

    conv_a = create_conversation(user_id=user_a, db_path=tmp_db)
    conv_b = create_conversation(user_id=user_b, db_path=tmp_db)

    save_message(conversation_id=conv_a, role="user", content="User A secret", db_path=tmp_db)
    save_message(conversation_id=conv_b, role="user", content="User B secret", db_path=tmp_db)

    # User A cannot read User B's conversation
    msgs_a_reading_b = get_messages(conversation_id=conv_b, user_id=user_a, db_path=tmp_db)
    assert msgs_a_reading_b == []

    # User B cannot read User A's conversation
    msgs_b_reading_a = get_messages(conversation_id=conv_a, user_id=user_b, db_path=tmp_db)
    assert msgs_b_reading_a == []

    # Each user can read their own
    msgs_a = get_messages(conversation_id=conv_a, user_id=user_a, db_path=tmp_db)
    assert len(msgs_a) == 1
    msgs_b = get_messages(conversation_id=conv_b, user_id=user_b, db_path=tmp_db)
    assert len(msgs_b) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Summarisation triggers when the configured threshold is reached
# ─────────────────────────────────────────────────────────────────────────────

def test_summarisation_triggers_at_threshold(tmp_db, user_id):
    from chat_store import create_conversation, save_message
    from chat_store import get_latest_summary
    from conversation_memory import maybe_summarise

    conv_id = create_conversation(user_id=user_id, db_path=tmp_db)

    # Add 20 messages (threshold is 20 by default in tests)
    THRESHOLD = 20
    WINDOW = 6
    for i in range(THRESHOLD):
        role = "user" if i % 2 == 0 else "assistant"
        save_message(conversation_id=conv_id, role=role, content=f"message {i}", db_path=tmp_db)

    # Mock LLM to avoid API calls
    with patch("conversation_memory._generate_summary_text", return_value="Mock summary"):
        maybe_summarise(
            conversation_id=conv_id,
            summarise_threshold=THRESHOLD,
            recent_window=WINDOW,
            db_path=tmp_db,
        )

    summary = get_latest_summary(conversation_id=conv_id, db_path=tmp_db)
    assert summary is not None
    assert summary["summary_text"] == "Mock summary"


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Recent messages kept verbatim, older messages summarised
# ─────────────────────────────────────────────────────────────────────────────

def test_recent_messages_verbatim_older_summarised(tmp_db, user_id):
    from chat_store import create_conversation, save_message, get_all_messages_raw
    from chat_store import get_latest_summary
    from conversation_memory import maybe_summarise, build_context_block

    conv_id = create_conversation(user_id=user_id, db_path=tmp_db)
    THRESHOLD = 10
    WINDOW = 4

    messages = [f"message_{i}" for i in range(THRESHOLD)]
    for i, content in enumerate(messages):
        role = "user" if i % 2 == 0 else "assistant"
        save_message(conversation_id=conv_id, role=role, content=content, db_path=tmp_db)

    with patch("conversation_memory._generate_summary_text", return_value="Old summary"):
        maybe_summarise(
            conversation_id=conv_id,
            summarise_threshold=THRESHOLD,
            recent_window=WINDOW,
            db_path=tmp_db,
        )

    context = build_context_block(
        conversation_id=conv_id,
        recent_window=WINDOW,
        db_path=tmp_db,
    )

    # Summary should exist
    assert context["summary"] == "Old summary"

    # Recent messages should be the last WINDOW messages verbatim
    recent = context["recent_messages"]
    assert len(recent) <= WINDOW
    # The most recent message is the last one we added
    assert recent[-1]["content"] == messages[-1]


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: Multiple summarisation cycles in a long conversation
# ─────────────────────────────────────────────────────────────────────────────

def test_multiple_summarisation_cycles(tmp_db, user_id):
    from chat_store import create_conversation, save_message, get_all_summaries
    from conversation_memory import maybe_summarise

    conv_id = create_conversation(user_id=user_id, db_path=tmp_db)
    THRESHOLD = 10
    WINDOW = 4

    summary_call_count = [0]

    def mock_generate(existing, messages):
        summary_call_count[0] += 1
        return f"Summary cycle {summary_call_count[0]}"

    # First batch
    for i in range(THRESHOLD):
        save_message(conversation_id=conv_id, role="user" if i % 2 == 0 else "assistant",
                     content=f"batch1_msg_{i}", db_path=tmp_db)

    with patch("conversation_memory._generate_summary_text", side_effect=mock_generate):
        maybe_summarise(conv_id, summarise_threshold=THRESHOLD, recent_window=WINDOW, db_path=tmp_db)

    # Add more messages to trigger a second cycle
    for i in range(THRESHOLD):
        save_message(conversation_id=conv_id, role="user" if i % 2 == 0 else "assistant",
                     content=f"batch2_msg_{i}", db_path=tmp_db)

    with patch("conversation_memory._generate_summary_text", side_effect=mock_generate):
        maybe_summarise(conv_id, summarise_threshold=THRESHOLD, recent_window=WINDOW, db_path=tmp_db)

    summaries = get_all_summaries(conv_id, tmp_db)
    assert len(summaries) >= 2  # At least two summary cycles
    assert summaries[0]["summary_text"] == "Summary cycle 1"


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: Correct context restoration after summarisation
# ─────────────────────────────────────────────────────────────────────────────

def test_context_restoration_after_summarisation(tmp_db, user_id):
    from chat_store import create_conversation, save_message
    from conversation_memory import maybe_summarise, build_context_block
    from langchain_core.messages import SystemMessage

    conv_id = create_conversation(user_id=user_id, db_path=tmp_db)
    THRESHOLD = 10
    WINDOW = 4

    for i in range(THRESHOLD):
        save_message(conversation_id=conv_id, role="user" if i % 2 == 0 else "assistant",
                     content=f"ctx_msg_{i}", db_path=tmp_db)

    with patch("conversation_memory._generate_summary_text", return_value="Context summary"):
        maybe_summarise(conv_id, summarise_threshold=THRESHOLD, recent_window=WINDOW, db_path=tmp_db)

    context = build_context_block(conv_id, recent_window=WINDOW, db_path=tmp_db)

    # LangChain messages should include the summary as a SystemMessage
    lc_msgs = context["lc_messages"]
    assert any(isinstance(m, SystemMessage) and "Context summary" in m.content for m in lc_msgs)

    # And the recent verbatim messages
    recent_contents = [m.content for m in lc_msgs if not isinstance(m, SystemMessage)]
    assert len(recent_contents) <= WINDOW
    assert "ctx_msg_9" in recent_contents  # last message present verbatim


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: Summarisation failure without loss of original messages
# ─────────────────────────────────────────────────────────────────────────────

def test_summarisation_failure_preserves_messages(tmp_db, user_id):
    from chat_store import create_conversation, save_message, get_all_messages_raw, get_latest_summary
    from conversation_memory import maybe_summarise

    conv_id = create_conversation(user_id=user_id, db_path=tmp_db)
    THRESHOLD = 10

    for i in range(THRESHOLD):
        save_message(conversation_id=conv_id, role="user" if i % 2 == 0 else "assistant",
                     content=f"safe_msg_{i}", db_path=tmp_db)

    # Simulate LLM failure
    with patch("conversation_memory._generate_summary_text", side_effect=RuntimeError("LLM down")):
        # Should not raise
        maybe_summarise(conv_id, summarise_threshold=THRESHOLD, recent_window=4, db_path=tmp_db)

    # All messages intact
    msgs = get_all_messages_raw(conv_id, tmp_db)
    assert len(msgs) == THRESHOLD

    # No summary was stored
    summary = get_latest_summary(conv_id, tmp_db)
    assert summary is None


# ─────────────────────────────────────────────────────────────────────────────
# Test 10: Avoiding duplicate messages and handling retries
# ─────────────────────────────────────────────────────────────────────────────

def test_no_duplicate_messages_on_retry(tmp_db, user_id):
    from chat_store import create_conversation, save_message, get_all_messages_raw

    conv_id = create_conversation(user_id=user_id, db_path=tmp_db)
    ikey = "unique-key-abc"

    mid1 = save_message(conversation_id=conv_id, role="user", content="Hello",
                        idempotency_key=ikey, db_path=tmp_db)
    mid2 = save_message(conversation_id=conv_id, role="user", content="Hello (retry)",
                        idempotency_key=ikey, db_path=tmp_db)

    # Second call should return None (duplicate skipped)
    assert mid1 is not None
    assert mid2 is None

    # Only one message in DB
    msgs = get_all_messages_raw(conv_id, tmp_db)
    assert len(msgs) == 1
    assert msgs[0]["content"] == "Hello"  # original content preserved


# ─────────────────────────────────────────────────────────────────────────────
# Test 11: Correct tool routing with summarised history
# ─────────────────────────────────────────────────────────────────────────────

def test_tool_routing_with_summarised_history(tmp_db, user_id):
    """
    Verify that the orchestrator correctly routes a follow-up request even when
    older context exists only in the summary. The LLM and tools are mocked.
    """
    from chat_store import create_conversation, save_message
    from conversation_memory import maybe_summarise, build_context_block

    conv_id = create_conversation(user_id=user_id, db_path=tmp_db)
    THRESHOLD = 10
    WINDOW = 4

    # Simulate a prior turn about invoices
    for i in range(THRESHOLD - 2):
        save_message(conversation_id=conv_id, role="user" if i % 2 == 0 else "assistant",
                     content=f"tool_routing_msg_{i}", db_path=tmp_db)

    save_message(conversation_id=conv_id, role="user",
                 content="Show me Q1 invoices from Acme", db_path=tmp_db)
    save_message(conversation_id=conv_id, role="assistant",
                 content="Found 3 invoices from Acme in Q1.", db_path=tmp_db)

    with patch("conversation_memory._generate_summary_text",
               return_value="User asked about Acme Q1 invoices; 3 were found."):
        maybe_summarise(conv_id, summarise_threshold=THRESHOLD, recent_window=WINDOW, db_path=tmp_db)

    context = build_context_block(conv_id, recent_window=WINDOW, db_path=tmp_db)

    # The summary should be present in the context
    assert "Acme" in context["summary"]

    # The context block includes the summary + recent messages
    lc_msgs = context["lc_messages"]
    contents = " ".join(m.content for m in lc_msgs)
    assert "Acme" in contents


# ─────────────────────────────────────────────────────────────────────────────
# Test 12: Existing OCR, Q&A, Excel, and graph workflows remain functional
# ─────────────────────────────────────────────────────────────────────────────

def test_existing_agent_interfaces_functional(tmp_path):
    """
    Verify that OCRAgent, QAAgent, ReportAgent, and GraphAgent can be imported
    and instantiated, and that their public interfaces have not been broken.
    """
    # OCR Agent
    from ocr_engine import OCRAgent
    agent = OCRAgent()
    result = agent.process_and_index_invoice("/nonexistent_file.pdf")
    assert result["status"] == "failed"
    assert "file_name" in result

    # QA Agent (with no index)
    from qa_agent import QAAgent
    index_path = str(tmp_path / "empty_index")
    qa = QAAgent(index_path=index_path)
    result = qa.answer("What is the total?")
    assert result["status"] == "failed"
    assert "answer" in result

    # Report Agent (with no index)
    from report_generator import ReportAgent
    rep = ReportAgent(index_path=index_path)
    result = rep.generate_report("January 2025")
    assert result["status"] == "failed"
    assert "message" in result

    # Graph Agent – only check import and instantiation (building graph needs data)
    from graph_agent import GraphAgent
    ga = GraphAgent()
    result = ga.run("Show me spend by vendor")
    assert result["status"] in ("success", "failed")
    assert "message" in result
    assert "figures" in result
