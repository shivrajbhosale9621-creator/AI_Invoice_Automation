"""
Report Generator & Agentic Excel Export Module powered by LangGraph.
Analyzes user queries against invoice metadata, dynamically decides retrieval needs,
extracts itemized or summary records, and generates Excel reports at a specified output path.
"""

import os
import re
import json
import datetime as dt
from typing import TypedDict, Optional, List, Dict, Any

from config import GROQ_MODEL_NAME, GROQ_API_KEY, DEFAULT_INDEX_PATH
from vector_store import (
	load_index,
	get_all_invoices,
	get_all_invoices_matrix,
	semantic_search_nodes,
)

import pandas as pd
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, START, END




# -------------------------------------------------------------
# 2. LangGraph State Schema
# -------------------------------------------------------------
class EXState(TypedDict):
	query: str
	index_path: str
	output_path: str
	index_matrix: str
	date: str
	requires_vector_search: bool
	matched_invoice_ids: list[str]
	top_k: int
	vector_search_prompt: Optional[str]
	retrieved_chunks: Optional[list]
	needs_line_items: bool
	excel_columns: list[str]
	excel_rows: list[dict]
	final_result: Optional[str]
	status: str
	record_count: int
	error: Optional[str]
	user_id: Optional[str]


# -------------------------------------------------------------
# 3. Decision Prompt & Intent Node
# -------------------------------------------------------------
REPORT_ROUTER_PROMPT = ChatPromptTemplate.from_template(
	"""You are the Retrieval & Reporting Decision Agent in an AI Invoice Automation system.

Analyze the user's request and the Invoice Summary Matrix to determine the data required for generating an Excel report.

Current Date: {date}
User Request: "{query}"

Invoice Summary Matrix (Top-level metadata):
{matrix}

TASKS:
1. Identify all matching invoice IDs/numbers from the matrix that satisfy the user's criteria (date ranges, vendors, payment status, etc.). If the user asks for all invoices, include all invoice numbers from the matrix.
2. Determine if the request can be fulfilled using ONLY the top-level matrix metadata (invoice_number, vendor_name, buyer_name, invoice_date, total_amount, currency, payment_status).
3. Set `requires_vector_search` to true ONLY if deep line-item details (individual products, item descriptions, quantities, unit prices, line taxes) or unindexed invoice text are needed.
4. If line items are required, set `needs_line_items` to true and provide a focused `vector_search_prompt`.
5. Set `top_k` to the number of matched invoices (or reasonable number covering them, default 5).
6. List the recommended column headers for the Excel report in `excel_columns`.

Return ONLY a valid JSON object with no markdown backticks and no extra commentary:
{{
    "matched_invoice_ids": ["INV-001", "INV-002"],
    "requires_vector_search": false,
    "vector_search_prompt": "",
    "top_k": 5,
    "excel_columns": ["invoice_number", "vendor_name", "invoice_date", "total_amount", "payment_status"],
    "needs_line_items": false,
    "reason": "Top-level metadata contains all requested columns"
}}
"""
)


def check_data_enough(state: EXState) -> dict:
	"""
	Node 1: Evaluates user query and summary matrix.
	Decides if vector search is needed or if matrix records are sufficient.
	"""
	query = state.get("query", "")
	matrix = state.get("index_matrix", "")
	date = state.get("date") or dt.datetime.now().strftime("%Y-%m-%d")

	llm = ChatGroq(api_key=GROQ_API_KEY, model=GROQ_MODEL_NAME, temperature=0)
	chain = REPORT_ROUTER_PROMPT | llm

	try:
		response = chain.invoke({"matrix": matrix, "query": query, "date": date})
		content = getattr(response, "content", str(response)).strip()

		if content.startswith("```"):
			match = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
			if match:
				content = match.group(1).strip()

		parsed = json.loads(content)
		req_search = bool(parsed.get("requires_vector_search", False))
		matched_ids = [str(x).strip("# ") for x in parsed.get("matched_invoice_ids", [])]
		top_k_val = int(parsed.get("top_k", max(len(matched_ids), 5)))

		return {
			"requires_vector_search": req_search,
			"matched_invoice_ids": matched_ids,
			"top_k": top_k_val if top_k_val > 0 else 5,
			"vector_search_prompt": parsed.get("vector_search_prompt") or query,
			"excel_columns": parsed.get("excel_columns", []),
			"needs_line_items": bool(parsed.get("needs_line_items", False))
		}
	except Exception:
		return {
			"requires_vector_search": False,
			"matched_invoice_ids": [],
			"top_k": 5,
			"vector_search_prompt": query,
			"excel_columns": [],
			"needs_line_items": False
		}


# -------------------------------------------------------------
# 4. Data Gathering Nodes
# -------------------------------------------------------------
def prepare_matrix_rows(state: EXState) -> dict:
	"""
	Node 2A: Extracts structured rows directly from stored invoice metadata.
	Used when vector search is not required.
	"""
	index_path = state.get("index_path", DEFAULT_INDEX_PATH)
	invoices = get_all_invoices(index_path=index_path, user_id=state.get("user_id"))
	matched_ids = set(state.get("matched_invoice_ids", []))

	rows = []
	for inv in invoices:
		inv_num = str(inv.get("invoice_number", "")).strip("# ")
		# If specific IDs were matched, filter by them; if none matched (e.g. all or loose match), include all
		if matched_ids and inv_num not in matched_ids:
			continue

		rows.append({
			"invoice_number": inv.get("invoice_number", "N/A"),
			"vendor_name": inv.get("vendor_name", "Unknown"),
			"buyer_name": inv.get("buyer_name", "N/A"),
			"invoice_date": inv.get("invoice_date", "N/A"),
			"due_date": inv.get("due_date", "N/A"),
			"payment_status": inv.get("payment_status", "Unpaid"),
			"payment_method": inv.get("payment_method", "N/A"),
			"purchase_order_number": inv.get("purchase_order_number", "N/A"),
			"currency": inv.get("currency", "USD"),
			"subtotal": inv.get("subtotal", 0.0),
			"tax": inv.get("tax", 0.0),
			"discount": inv.get("discount", 0.0),
			"total_amount": inv.get("total_amount", 0.0)
		})

	# If strict filtering produced nothing but invoices exist, fallback to all invoices
	if not rows and invoices and not matched_ids:
		for inv in invoices:
			rows.append({
				"invoice_number": inv.get("invoice_number", "N/A"),
				"vendor_name": inv.get("vendor_name", "Unknown"),
				"buyer_name": inv.get("buyer_name", "N/A"),
				"invoice_date": inv.get("invoice_date", "N/A"),
				"due_date": inv.get("due_date", "N/A"),
				"payment_status": inv.get("payment_status", "Unpaid"),
				"currency": inv.get("currency", "USD"),
				"total_amount": inv.get("total_amount", 0.0)
			})

	return {"excel_rows": rows}


def vector_search(state: EXState) -> dict:
	"""
	Node 2B: Executes Vector Similarity Search using dynamically calculated top-k.
	"""
	search_prompt = state.get("vector_search_prompt") or state.get("query", "")
	top_k_val = state.get("top_k", 5)
	index_path = state.get("index_path", DEFAULT_INDEX_PATH)

	try:
		nodes = semantic_search_nodes(query=search_prompt, top_k=top_k_val, index_path=index_path, user_id=state.get("user_id"))
		return {"retrieved_chunks": nodes}
	except Exception:
		return {"retrieved_chunks": []}


def extract_vector_rows(state: EXState) -> dict:
	"""
	Node 3: Extracts itemized or detailed line-item rows from retrieved chunks
	and matched invoices in the docstore.
	"""
	index_path = state.get("index_path", DEFAULT_INDEX_PATH)
	invoices = get_all_invoices(index_path=index_path, user_id=state.get("user_id"))
	matched_ids = set(state.get("matched_invoice_ids", []))
	retrieved_nodes = state.get("retrieved_chunks") or []

	# Build a lookup of relevant invoice dictionaries
	inv_map = {}
	for inv in invoices:
		inv_num = str(inv.get("invoice_number", "")).strip("# ")
		inv_map[inv_num] = inv

	# Also inspect retrieved nodes for JSON content
	for node in retrieved_nodes:
		try:
			data = json.loads(node.get_content())
			inv_num = str(data.get("invoice_number", "")).strip("# ")
			if inv_num and inv_num not in inv_map:
				inv_map[inv_num] = data
		except Exception:
			pass

	# Filter down to matched IDs if present, otherwise use all found
	target_invoices = []
	if matched_ids:
		for mid in matched_ids:
			if mid in inv_map:
				target_invoices.append(inv_map[mid])
	if not target_invoices:
		target_invoices = list(inv_map.values())

	rows = []
	needs_items = state.get("needs_line_items", True)

	for inv in target_invoices:
		items = inv.get("items", [])
		if needs_items and items:
			for item in items:
				rows.append({
					"invoice_number": inv.get("invoice_number", "N/A"),
					"vendor_name": inv.get("vendor_name", "Unknown"),
					"buyer_name": inv.get("buyer_name", "N/A"),
					"invoice_date": inv.get("invoice_date", "N/A"),
					"payment_status": inv.get("payment_status", "Unpaid"),
					"item_description": str(item),
					"currency": inv.get("currency", "USD"),
					"total_amount": inv.get("total_amount", 0.0)
				})
		else:
			rows.append({
				"invoice_number": inv.get("invoice_number", "N/A"),
				"vendor_name": inv.get("vendor_name", "Unknown"),
				"buyer_name": inv.get("buyer_name", "N/A"),
				"invoice_date": inv.get("invoice_date", "N/A"),
				"payment_status": inv.get("payment_status", "Unpaid"),
				"currency": inv.get("currency", "USD"),
				"items": "; ".join(items) if items else "N/A",
				"total_amount": inv.get("total_amount", 0.0)
			})

	return {"excel_rows": rows}


# -------------------------------------------------------------
# 5. Excel Generation Node
# -------------------------------------------------------------
def node_create_excel(state: EXState) -> dict:
	"""
	Node 4: Writes the extracted rows into an Excel file at the specified output_path.
	"""
	rows = state.get("excel_rows", [])
	output_path = state.get("output_path") or os.path.join(os.getcwd(), "invoice_report.xlsx")

	if not rows:
		msg = f"No matching invoices found for '{state.get('query')}'. Excel report was not created."
		return {
			"final_result": msg,
			"status": "failed",
			"record_count": 0,
			"error": "No matching records found"
		}

	try:
		# Ensure destination directory exists
		out_dir = os.path.dirname(os.path.abspath(output_path))
		if out_dir:
			os.makedirs(out_dir, exist_ok=True)

		df = pd.DataFrame(rows)

		# Filter / reorder columns if user requested specific columns and they exist
		desired_columns = state.get("excel_columns") or []
		if desired_columns:
			matched_cols = [c for c in desired_columns if c in df.columns]
			if matched_cols:
				df = df[matched_cols]

		df.to_excel(output_path, index=False, engine="openpyxl")
		msg = f"Excel report with {len(df)} records created for '{state.get('query')}' at '{output_path}'."
		return {
			"final_result": msg,
			"status": "success",
			"record_count": len(df),
			"error": None
		}
	except Exception as e:
		return {
			"final_result": f"Failed to create Excel report at '{output_path}': {str(e)}",
			"status": "failed",
			"record_count": 0,
			"error": str(e)
		}


# -------------------------------------------------------------
# 6. LangGraph Routing & Construction
# -------------------------------------------------------------
def router_should_search(state: EXState) -> str:
	"""Decides next node based on requires_vector_search flag."""
	if state.get("requires_vector_search"):
		return "vector_search"
	else:
		return "prepare_matrix_rows"


def build_ex_graph():
	"""Build and compile the LangGraph workflow for report generation."""
	builder = StateGraph(EXState)

	builder.add_node("check_intent", check_data_enough)
	builder.add_node("prepare_matrix_rows", prepare_matrix_rows)
	builder.add_node("vector_search", vector_search)
	builder.add_node("extract_vector_rows", extract_vector_rows)
	builder.add_node("create_excel", node_create_excel)

	builder.add_edge(START, "check_intent")
	builder.add_conditional_edges(
		"check_intent",
		router_should_search,
		{
			"vector_search": "vector_search",
			"prepare_matrix_rows": "prepare_matrix_rows"
		}
	)
	builder.add_edge("prepare_matrix_rows", "create_excel")
	builder.add_edge("vector_search", "extract_vector_rows")
	builder.add_edge("extract_vector_rows", "create_excel")
	builder.add_edge("create_excel", END)

	return builder.compile()


ex_graph_app = build_ex_graph()


# -------------------------------------------------------------
# 7. Public Interfaces
# -------------------------------------------------------------
class ReportAgent:
	"""Excel Reporting Sub-Agent for Orchestrator."""

	def __init__(self, index_path: str = DEFAULT_INDEX_PATH, user_id: str = None):
		self.index_path = index_path
		self.user_id = str(user_id) if user_id is not None else None

	def generate_report(self, query: str = "", output_path: str = "invoice_report.xlsx") -> dict:
		"""
		Main Excel Report Agent Interface for Orchestrator.
		Returns a standardized dictionary envelope:
		{
			"status": "success" | "failed",
			"message": str,
			"file_path": str | None,
			"record_count": int,
			"columns": list,
			"error": str | None
		}
		"""
		if not os.path.exists(self.index_path) or not os.listdir(self.index_path):
			return {
				"status": "failed",
				"message": "No invoices found in storage. Please process invoices first.",
				"file_path": None,
				"record_count": 0,
				"columns": [],
				"error": f"No index found at '{self.index_path}'"
			}

		try:
			index = load_index(self.index_path)
			matrix = get_all_invoices_matrix(index, user_id=self.user_id)

			initial_state: EXState = {
				"query": query,
				"index_path": self.index_path,
				"output_path": output_path,
				"index_matrix": matrix,
				"date": dt.datetime.now().strftime("%Y-%m-%d"),
				"requires_vector_search": False,
				"matched_invoice_ids": [],
				"top_k": 5,
				"vector_search_prompt": None,
				"retrieved_chunks": None,
				"needs_line_items": False,
				"excel_columns": [],
				"excel_rows": [],
				"final_result": None,
				"status": "failed",
				"record_count": 0,
				"error": None,
				"user_id": self.user_id
			}

			result_state = ex_graph_app.invoke(initial_state)

			return {
				"status": result_state.get("status", "failed"),
				"message": result_state.get("final_result", "Excel generation finished."),
				"file_path": output_path if result_state.get("status") == "success" else None,
				"record_count": result_state.get("record_count", 0),
				"columns": result_state.get("excel_columns", []),
				"error": result_state.get("error")
			}
		except Exception as e:
			return {
				"status": "failed",
				"message": f"Failed to generate report: {str(e)}",
				"file_path": None,
				"record_count": 0,
				"columns": [],
				"error": str(e)
			}


def create_invoice_excel_report(
	index_path: str = DEFAULT_INDEX_PATH,
	period_text: str = "",
	output_path: str = "invoice_report.xlsx"
) -> str:
	"""Backwards compatibility helper: returns human-readable string directly."""
	agent = ReportAgent(index_path=index_path)
	res = agent.generate_report(query=period_text, output_path=output_path)
	return res["message"]


# Backwards compatibility aliases
create_excel = create_invoice_excel_report
invoice_qa = create_invoice_excel_report