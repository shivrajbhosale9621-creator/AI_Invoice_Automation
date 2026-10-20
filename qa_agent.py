"""
Agent 2: QA & Reasoning Agent powered by LangGraph.
Combines Global Markdown Summary Matrix table check with Vector Similarity Search.
"""

import os
import json
import re
from typing import TypedDict, Optional

from config import GROQ_API_KEY, GROQ_MODEL_NAME, DEFAULT_INDEX_PATH
from vector_store import (
	load_index,
	get_all_invoices,
	get_all_invoices_with_ids,
	get_all_invoices_matrix,
	delete_invoice,
	semantic_search_text,
)

import pandas as pd
from langgraph.graph import StateGraph, START, END
from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq




# -------------------------------------------------------------
# 2. Define LangGraph State Schema
# -------------------------------------------------------------
class QAState(TypedDict):
	query: str                           # User question
	index_path: str                      # Path to persisted LlamaIndex vector store
	index_matrix: str                    # Global Markdown summary table
	chat_history: Optional[list]         # Previous conversational messages
	requires_vector_search: bool        # Decided by LLM Router
	top_k: int                           # Dynamically calculated by LLM Router
	retrieval_mode: str                  # "matrix" or "vector_search"
	vector_search_prompt: Optional[str]  # Query string for vector store if needed
	retrieved_chunks: Optional[str]      # Chunks retrieved from vector store
	final_result: Optional[str]          # Final answer generated for user
	user_id: Optional[str]               # Owner of the data; scopes every read


# -------------------------------------------------------------
# 3. LangGraph Nodes
# -------------------------------------------------------------
def node_check_intent(state: QAState) -> dict:
	"""
	Node 1: Evaluates user query against Global Summary Matrix.
	Decides if question can be answered from Matrix alone or needs Vector Search,
	and dynamically calculates optimal top_k number.
	"""
	query = state["query"]
	matrix = state["index_matrix"]

	llm = ChatGroq(api_key=GROQ_API_KEY, model=GROQ_MODEL_NAME, temperature=0)

	prompt = ChatPromptTemplate.from_template(
		'''
        You are a smart Invoice QA Router.
        Analyze the User Question and the Global Summary Matrix Table below.

        Global Summary Matrix:
        {matrix}

        User Question: "{query}"

        Task:
        1. Can this question be answered completely using the Global Summary Matrix Table alone? (e.g. total spend math, list vendors, count unpaid invoices, find invoice by number/date).
        2. Or does it require deep line-item/itemized text inspection from individual original documents? (e.g. specific item serial numbers, itemized prices, warranty terms, full street addresses).
        3. If vector search IS needed, inspect the Global Summary Matrix to see how many matching invoice files exist for this specific vendor/topic, and select an optimal top_k integer (e.g. 5, 10, 15, or exact matching count).

        Return ONLY a JSON response:
        {{
            "can_answer_from_matrix": true/false,
            "top_k": integer (default 5, or exact count of matching invoices in matrix),
            "search_query": "specific search string if vector search is needed, else empty string",
            "direct_answer": "answer text if can_answer_from_matrix is true, else empty string"
        }}
        '''
	)

	chain = prompt | llm
	response = chain.invoke({"matrix": matrix, "query": query})
	content = getattr(response, "content", str(response)).strip()

	if content.startswith("```"):
		match = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
		if match:
			content = match.group(1).strip()

	try:
		parsed = json.loads(content)
		can_answer = parsed.get("can_answer_from_matrix", False)
		top_k_val = int(parsed.get("top_k", 5))
		direct_ans = parsed.get("direct_answer", "").strip()

		if can_answer and direct_ans:
			return {
				"requires_vector_search": False,
				"retrieval_mode": "matrix",
				"top_k": top_k_val,
				"final_result": direct_ans
			}
		else:
			return {
				"requires_vector_search": True,
				"retrieval_mode": "vector_search",
				"top_k": top_k_val,
				"vector_search_prompt": parsed.get("search_query", query)
			}
	except Exception:
		# Fallback to vector search if parsing fails
		return {
			"requires_vector_search": True,
			"retrieval_mode": "vector_search",
			"top_k": 5,
			"vector_search_prompt": query
		}


def node_vector_search(state: QAState) -> dict:
	"""
	Node 2: Executes Vector Similarity Search using dynamically calculated top_k.
	"""
	search_prompt = state.get("vector_search_prompt") or state["query"]
	top_k_val = state.get("top_k", 5)
	index_path = state.get("index_path", DEFAULT_INDEX_PATH)

	try:
		retrieved_text = semantic_search_text(
			query=search_prompt,
			top_k=top_k_val,
			index_path=index_path,
			user_id=state.get("user_id"),
		)
		return {"retrieved_chunks": retrieved_text}
	except Exception as e:
		return {"retrieved_chunks": f"Vector retrieval error: {str(e)}"}



def node_synthesize_answer(state: QAState) -> dict:
	"""
	Node 3: Combines retrieved vector chunks + Global Summary Matrix + chat history to produce final answer.
	"""
	query = state["query"]
	matrix = state["index_matrix"]
	chunks = state.get("retrieved_chunks", "No specific detailed chunks retrieved.")
	history = state.get("chat_history") or []

	history_text = ""
	if history:
		history_lines = []
		for msg in history[-6:]:
			role = getattr(msg, "type", "User")
			content = getattr(msg, "content", str(msg))
			history_lines.append(f"{role.capitalize()}: {content}")
		history_text = "\nPrevious Conversation:\n" + "\n".join(history_lines) + "\n"

	llm = ChatGroq(api_key=GROQ_API_KEY, model=GROQ_MODEL_NAME, temperature=0, max_tokens=1000)

	prompt = ChatPromptTemplate.from_template(
		'''
        You are an expert Invoice QA Agent.
        Answer the user's question using the Global Summary Matrix, Detailed Invoice Chunks, and Conversation History below.
        Use whatever currency is specified in the invoices ($, ₹, €, etc.) properly. Always present clean vendor names without raw dictionary syntax.

        Global Summary Matrix:
        {matrix}

        Detailed Invoice Chunks:
        {chunks}
        {history}

        User Question: "{query}"

        Answer:
        '''
	)

	chain = prompt | llm
	try:
		response = chain.invoke({"matrix": matrix, "chunks": chunks, "history": history_text, "query": query})
		answer = getattr(response, "content", str(response)).strip()
	except Exception as exc:
		err_str = str(exc).lower()
		if "429" in err_str or "rate limit" in err_str or "tokens" in err_str:
			answer = "The AI service is busy, please wait a few seconds and try again."
		else:
			answer = "The AI service encountered a temporary error while formulating the answer. Please try again."
	return {"final_result": answer}


# -------------------------------------------------------------
# 4. Conditional Edge Router
# -------------------------------------------------------------
def router_should_search(state: QAState) -> str:
	"""Decides next node based on requires_vector_search flag."""
	if state.get("requires_vector_search"):
		return "vector_search"
	return "end"


# -------------------------------------------------------------
# 5. Build & Compile LangGraph StateGraph
# -------------------------------------------------------------
def build_qa_graph():
	builder = StateGraph(QAState)

	# Add nodes
	builder.add_node("check_intent", node_check_intent)
	builder.add_node("vector_search", node_vector_search)
	builder.add_node("synthesize_answer", node_synthesize_answer)

	# Set flow edges
	builder.add_edge(START, "check_intent")
	builder.add_conditional_edges(
		"check_intent",
		router_should_search,
		{
			"vector_search": "vector_search",
			"end": END
		}
	)
	builder.add_edge("vector_search", "synthesize_answer")
	builder.add_edge("synthesize_answer", END)

	return builder.compile()


# Compile global graph runner
qa_graph_app = build_qa_graph()


# -------------------------------------------------------------
# 6. Main Entrypoint Function for QA Agent
# -------------------------------------------------------------
class QAAgent:
	"""QA & Reasoning Sub-Agent for Orchestrator."""

	def __init__(self, index_path: str = DEFAULT_INDEX_PATH, user_id: str = None):
		self.index_path = index_path
		self.user_id = str(user_id) if user_id is not None else None

	def answer(self, question: str, chat_history: list = None) -> dict:
		"""
		Main QA Agent Interface for Orchestrator.
		Returns a standardized dictionary envelope:
		{
			"status": "success" | "failed",
			"answer": str,
			"retrieval_mode": "matrix" | "vector_search" | "none",
			"error": str | None
		}
		"""
		if not os.path.exists(self.index_path) or not os.listdir(self.index_path):
			return {
				"status": "failed",
				"answer": "No invoices have been indexed yet. Please process invoices first.",
				"retrieval_mode": "none",
				"error": f"No index found at '{self.index_path}'"
			}

		try:
			index = load_index(self.index_path)
			matrix = get_all_invoices_matrix(index, user_id=self.user_id)

			initial_state: QAState = {
				"query": question,
				"index_path": self.index_path,
				"index_matrix": matrix,
				"chat_history": chat_history or [],
				"requires_vector_search": False,
				"top_k": 5,
				"retrieval_mode": "matrix",
				"user_id": self.user_id,
				"vector_search_prompt": None,
				"retrieved_chunks": None,
				"final_result": None
			}

			result_state = qa_graph_app.invoke(initial_state)

			return {
				"status": "success",
				"answer": result_state.get("final_result", "Could not generate an answer."),
				"retrieval_mode": result_state.get("retrieval_mode", "unknown"),
				"error": None
			}
		except Exception as e:
			return {
				"status": "failed",
				"answer": f"An error occurred while answering: {str(e)}",
				"retrieval_mode": "none",
				"error": str(e)
			}


def invoice_qa(query: str, index_path: str = DEFAULT_INDEX_PATH, chat_history: list = None) -> str:
	"""Backwards compatibility helper: returns string answer directly."""
	agent = QAAgent(index_path=index_path)
	res = agent.answer(question=query, chat_history=chat_history)
	return res["answer"]


def query_invoice_data(index_path: str, question: str, chat_history: list = None) -> str:
	"""Backwards compatibility helper for Streamlit UI."""
	return invoice_qa(query=question, index_path=index_path, chat_history=chat_history)
