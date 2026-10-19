"""
vector_store.py - Unified ChromaDB Vector Database & Semantic Search Module.

Centralized interface for:
- Initializing and loading the persistent ChromaDB collection.
- Extracting normalized invoice records across all stored documents.
- Generating the unified Global Markdown Summary Matrix table.
- Performing semantic similarity search across the vector space.
- Persisting new invoice documents and deleting existing records.
- Seamless compatibility with QA Agent, Graph Agent, and Report Generator.
"""

import os
# Ensure OpenMP runtime conflict does not trigger hard abort on Windows
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import json
import uuid
import logging
from typing import Optional, List, Dict, Any

# IMPORTANT: chromadb and its onnxruntime dependency must be imported before pandas/numpy on Windows
import chromadb
from chromadb.utils import embedding_functions
import pandas as pd

from config import DEFAULT_CHROMA_PATH, DEFAULT_CHROMA_COLLECTION, DEFAULT_INDEX_PATH

logger = logging.getLogger(__name__)


# -------------------------------------------------------------
# 1. Compatibility Node Wrapper (Emulates LlamaIndex/LangChain Node)
# -------------------------------------------------------------
class ChromaNode:
	"""
	Lightweight node representation compatible with agent consumers.
	Provides .get_content(), .text, and .metadata attributes.
	"""

	def __init__(self, text: str, metadata: Optional[Dict[str, Any]] = None, id: str = ""):
		self.text = text
		self.metadata = metadata or {}
		self.id = id

	def get_content(self) -> str:
		return self.text

	def __repr__(self) -> str:
		return f"<ChromaNode id='{self.id}' text_len={len(self.text)} metadata={self.metadata}>"


# -------------------------------------------------------------
# 2. ChromaDB Client & Collection Management
# -------------------------------------------------------------
_embedding_fn = None
_clients: Dict[str, chromadb.PersistentClient] = {}


def get_embedding_function():
	"""Singleton embedding function for ChromaDB (all-MiniLM-L6-v2, 100% local, zero cost)."""
	global _embedding_fn
	if _embedding_fn is None:
		_embedding_fn = embedding_functions.DefaultEmbeddingFunction()
	return _embedding_fn


def get_chroma_client(path: str = DEFAULT_CHROMA_PATH) -> chromadb.PersistentClient:
	"""
	Get or create a persistent ChromaDB client.
	Safely falls back to DEFAULT_CHROMA_PATH if legacy invoice_index path is passed.
	"""
	if not path or "invoice_index" in path:
		path = DEFAULT_CHROMA_PATH

	resolved_path = os.path.abspath(path)
	if resolved_path not in _clients:
		os.makedirs(resolved_path, exist_ok=True)
		_clients[resolved_path] = chromadb.PersistentClient(path=resolved_path)
	return _clients[resolved_path]


def get_collection(
	path: str = DEFAULT_CHROMA_PATH,
	collection_name: str = DEFAULT_CHROMA_COLLECTION
):
	"""
	Get or create the target ChromaDB collection with the default embedding function.
	"""
	client = get_chroma_client(path)
	return client.get_or_create_collection(
		name=collection_name,
		embedding_function=get_embedding_function()
	)


def load_index(index_path: str = DEFAULT_CHROMA_PATH):
	"""
	Compatibility alias for agent entrypoints: returns the active Chroma collection.
	"""
	return get_collection(path=index_path)


import re

def parse_numeric(val: Any, default: float = 0.0) -> float:
	"""Sanitize and parse numeric currency or float values."""
	if val is None:
		return default
	if isinstance(val, (int, float)):
		return float(val)
	val_str = str(val).strip()
	if not val_str or val_str.lower() in ("n/a", "none", "null", "unknown", "-"):
		return default
	# Remove currency symbols ($, €, £, ¥, ₹), commas, spaces
	cleaned = re.sub(r"[^\d.-]", "", val_str)
	try:
		return float(cleaned)
	except (ValueError, TypeError):
		return default


def normalize_invoice_data(raw_data: dict, file_name: str = "") -> dict:
	"""
	Robust multi-schema normalizer for invoice dictionaries extracted by LLMs or OCR.
	Handles nested 'fields', varied vendor/customer synonyms, currency strings, and missing fields.
	"""
	if not isinstance(raw_data, dict):
		raw_data = {}

	fields = raw_data.get("fields") if isinstance(raw_data.get("fields"), dict) else {}

	def find_str(*keys, default=""):
		for k in keys:
			v = raw_data.get(k)
			if v is not None and str(v).strip() and str(v).strip().lower() not in ("none", "null", "n/a"):
				return str(v).strip()
			v_f = fields.get(k)
			if v_f is not None and str(v_f).strip() and str(v_f).strip().lower() not in ("none", "null", "n/a"):
				return str(v_f).strip()
		return default

	def find_float(*keys, default=0.0):
		for k in keys:
			if k in raw_data and raw_data[k] is not None:
				amt = parse_numeric(raw_data[k], default=default)
				if amt != 0.0:
					return amt
			if k in fields and fields[k] is not None:
				amt = parse_numeric(fields[k], default=default)
				if amt != 0.0:
					return amt
		return default

	# 1. Invoice Number
	inv_num = find_str(
		"invoice_number", "invoice_no", "inv_number", "inv_no",
		"invoice_id", "bill_number", "bill_no", "receipt_no",
		"order_number", "order_no", "invoice_num", default=""
	)
	if not inv_num:
		if file_name:
			base = os.path.splitext(os.path.basename(file_name))[0]
			inv_num = base
		else:
			inv_num = "N/A"

	# 2. Vendor / Seller
	vendor = find_str(
		"vendor_name", "vendor", "seller", "seller_name",
		"company", "company_name", "merchant", "biller",
		"from", "issuer", "store", "store_name", default="Unknown Vendor"
	)

	# 3. Buyer / Customer
	buyer = find_str(
		"buyer_name", "buyer", "customer", "customer_name",
		"client", "client_name", "bill_to", "recipient", default="N/A"
	)

	# 4. Dates
	inv_date = find_str(
		"invoice_date", "date", "issue_date", "billing_date",
		"created_date", "inv_date", "order_date", default="N/A"
	)
	due_date = find_str(
		"due_date", "payment_due", "payment_due_date", "expiry_date", default=""
	)

	# 5. Addresses
	vendor_addr = find_str("vendor_address", "seller_address", "company_address", default="")
	buyer_addr = find_str("buyer_address", "customer_address", "client_address", "shipping_address", default="")

	# 6. Line Items
	line_items = (
		raw_data.get("line_items") or
		raw_data.get("items") or
		fields.get("line_items") or
		fields.get("items") or
		[]
	)
	if isinstance(line_items, dict):
		line_items = [line_items]
	elif not isinstance(line_items, list):
		line_items = []

	# 7. Financial amounts
	subtotal = find_float("subtotal", "sub_total", "amount_before_tax", "net_amount", default=0.0)
	tax = find_float("tax", "vat", "gst", "tax_amount", "total_tax", default=0.0)
	discount = find_float("discount", "discount_amount", "rebate", default=0.0)
	total = find_float(
		"total_amount", "grand_total", "total", "amount_due",
		"final_total", "net_total", "balance_due", "total_due", "amount", default=0.0
	)

	# Fallback: if total is still 0.0, attempt to sum line items
	if total == 0.0 and line_items:
		items_sum = 0.0
		for it in line_items:
			if isinstance(it, dict):
				it_total = parse_numeric(it.get("total") or it.get("total_amount") or it.get("amount") or 0.0)
				if it_total > 0:
					items_sum += it_total
				else:
					qty = parse_numeric(it.get("quantity") or it.get("qty") or 1.0)
					price = parse_numeric(it.get("unit_price") or it.get("price") or 0.0)
					items_sum += qty * price
		if items_sum > 0:
			total = round(items_sum, 2)

	if subtotal == 0.0 and total > 0 and tax == 0.0:
		subtotal = total

	currency = find_str("currency", "currency_code", "currency_symbol", default="USD")
	payment_status = find_str("payment_status", "status", default="Unpaid")

	return {
		"invoice_number": inv_num,
		"vendor_name": vendor,
		"vendor_address": vendor_addr,
		"buyer_name": buyer,
		"buyer_address": buyer_addr,
		"invoice_date": inv_date,
		"due_date": due_date,
		"total_amount": total,
		"subtotal": subtotal,
		"tax": tax,
		"discount": discount,
		"currency": currency,
		"payment_status": payment_status,
		"line_items": line_items,
		"items": line_items,
	}


# -------------------------------------------------------------
# 3. Extracting Invoices & Data Normalization
# -------------------------------------------------------------
def get_all_invoices(index=None, index_path: str = DEFAULT_CHROMA_PATH, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
	"""
	Retrieve all invoice records stored in ChromaDB as a list of normalized dicts.
	Handles both top-level and nested 'fields' structures gracefully.
	Optionally filters by user_id for strict user isolation.
	"""
	col = index if hasattr(index, "get") else get_collection(path=index_path)
	where_filter = {"user_id": str(user_id)} if user_id else None
	try:
		data = col.get(where=where_filter) if where_filter else col.get()
	except Exception as exc:
		logger.warning("Failed to fetch with where filter %s, falling back to all: %s", where_filter, exc)
		try:
			data = col.get()
		except Exception as inner_exc:
			logger.error("Failed to fetch documents from ChromaDB collection: %s", inner_exc)
			return []

	ids = data.get("ids") or []
	docs = data.get("documents") or []
	metadatas = data.get("metadatas") or []

	invoices = []
	for doc_id, doc_text, meta in zip(ids, docs, metadatas):
		meta = meta or {}
		# Enforce user filtering in memory as well
		if user_id:
			doc_user = meta.get("user_id")
			if doc_user and str(doc_user) != str(user_id):
				continue

		try:
			parsed = json.loads(doc_text) if doc_text else {}
			if not isinstance(parsed, dict):
				parsed = {}
		except Exception:
			parsed = {}

		# Merge metadata into parsed dict
		for k, v in meta.items():
			parsed.setdefault(k, v)

		file_name = meta.get("file_name") or parsed.get("file_name", "")
		normalized = normalize_invoice_data(parsed, file_name=file_name)

		# Preserve all original parsed keys, overlaid with normalized values
		res_dict = {**parsed, **normalized}
		res_dict["_doc_id"] = doc_id
		res_dict["file_name"] = file_name

		invoices.append(res_dict)

	return invoices


def get_all_invoices_with_ids(index_path: str = DEFAULT_CHROMA_PATH, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
	"""Retrieve all invoice records along with their ChromaDB node IDs, optionally filtered by user_id."""
	return get_all_invoices(index_path=index_path, user_id=user_id)


def get_all_invoices_df(index=None, index_path: str = DEFAULT_CHROMA_PATH, user_id: Optional[str] = None) -> pd.DataFrame:
	"""
	Convert stored ChromaDB invoices into a clean pandas DataFrame with standardized columns.
	"""
	invoices = get_all_invoices(index=index, index_path=index_path, user_id=user_id)
	if not invoices:
		return pd.DataFrame(columns=[
			"invoice_number", "vendor_name", "buyer_name",
			"invoice_date", "total_amount", "currency", "payment_status"
		])

	records = []
	for inv in invoices:
		norm = normalize_invoice_data(inv, file_name=inv.get("file_name", ""))
		inv_num = norm["invoice_number"]
		vendor = norm["vendor_name"]
		buyer = norm["buyer_name"]
		date = norm["invoice_date"]
		amount = norm["total_amount"]
		currency = norm["currency"]
		status = norm["payment_status"]

		records.append({
			"invoice_number": str(inv_num).strip(),
			"vendor_name": str(vendor).strip(),
			"buyer_name": str(buyer).strip(),
			"invoice_date": str(date).strip(),
			"total_amount": float(amount),
			"currency": str(currency).strip(),
			"payment_status": str(status).strip(),
		})

	return pd.DataFrame(records)


def get_all_invoices_matrix(index=None, index_path: str = DEFAULT_CHROMA_PATH) -> str:
	"""
	Build a single unified Markdown Summary Matrix table across all invoices.
	Used by QA Agent, Graph Agent, and Report Generator to perform rapid exact math.
	"""
	df = get_all_invoices_df(index=index, index_path=index_path)
	if df.empty:
		return "No invoices found in storage."
	return df.to_markdown(index=False)


# -------------------------------------------------------------
# 4. Semantic Similarity Search
# -------------------------------------------------------------
def semantic_search_nodes(
	query: str,
	top_k: int = 5,
	index=None,
	index_path: str = DEFAULT_CHROMA_PATH
) -> List[ChromaNode]:
	"""
	Perform semantic similarity search on ChromaDB and return retrieved node objects.
	"""
	col = index if hasattr(index, "query") else get_collection(path=index_path)
	count = col.count()
	if count == 0:
		return []

	n_results = max(1, min(top_k, count))
	try:
		res = col.query(query_texts=[query], n_results=n_results)
	except Exception as exc:
		logger.error("ChromaDB query error for '%s': %s", query, exc)
		return []

	ids_list = (res.get("ids") or [[]])[0]
	docs_list = (res.get("documents") or [[]])[0]
	metas_list = (res.get("metadatas") or [[]])[0]

	nodes = []
	for doc_id, doc_text, meta in zip(ids_list, docs_list, metas_list):
		nodes.append(ChromaNode(text=doc_text, metadata=meta or {}, id=doc_id))

	return nodes


def semantic_search_text(
	query: str,
	top_k: int = 5,
	index=None,
	index_path: str = DEFAULT_CHROMA_PATH
) -> str:
	"""
	Perform semantic similarity search on ChromaDB and return formatted text snippets.
	"""
	nodes = semantic_search_nodes(query=query, top_k=top_k, index=index, index_path=index_path)
	if not nodes:
		return ""

	chunks = []
	for n in nodes:
		file_name = n.metadata.get("file_name", "Invoice Document")
		chunks.append(f"Source: {file_name}\n{n.get_content()}")

	return "\n\n---\n\n".join(chunks)


# -------------------------------------------------------------
# 5. Storage & Persistence Modifications
# -------------------------------------------------------------
def save_invoice_to_vector_store(
	structured_json: dict,
	file_path: str,
	index_path: str = DEFAULT_CHROMA_PATH,
	user_id: Optional[str] = None,
):
	"""
	Package structured invoice JSON into ChromaDB with metadata and persist to disk.
	Associates with user_id for strict multi-user tenancy and synchronizes with SQLite invoices table.
	"""
	col = get_collection(path=index_path)

	file_name = os.path.basename(file_path)
	norm = normalize_invoice_data(structured_json, file_name=file_name)

	inv_num = norm["invoice_number"]
	seller = norm["vendor_name"]
	buyer = norm["buyer_name"]
	date = norm["invoice_date"]
	grand_total = norm["total_amount"]
	currency = norm["currency"]
	status = norm["payment_status"]

	# Prepare clean primitive metadata for ChromaDB (strings, floats, ints, bools only)
	metadata = {
		"document_type": str(structured_json.get("document_type", "invoice")),
		"file_name": file_name,
		"invoice_number": str(inv_num).strip(),
		"vendor_name": str(seller).strip(),
		"buyer_name": str(buyer).strip(),
		"invoice_date": str(date).strip(),
		"total_amount": float(grand_total),
		"currency": str(currency).strip(),
		"payment_status": str(status).strip(),
		"user_id": str(user_id) if user_id else "",
	}

	# Deterministic or unique doc_id
	if inv_num and str(inv_num).strip() not in ("N/A", "Unknown", ""):
		clean_inv = str(inv_num).strip().replace(" ", "_").replace("/", "_").replace("\\", "_")
		doc_id = f"inv_{clean_inv}"
	else:
		doc_id = f"inv_{uuid.uuid4().hex[:12]}"

	doc_text = json.dumps({**structured_json, **norm}, indent=2)

	col.upsert(
		documents=[doc_text],
		metadatas=[metadata],
		ids=[doc_id]
	)

	# Synchronize with SQLite invoices table if user_id provided
	if user_id:
		try:
			import sqlite3
			from config import CHAT_DB_PATH
			conn = sqlite3.connect(CHAT_DB_PATH)
			cursor = conn.cursor()
			u_clean = str(user_id).replace("-", "")
			cursor.execute("""
				INSERT INTO invoices (
					user_id, file_name, vendor_name, vendor_address,
					buyer_name, buyer_address, invoice_number, invoice_date,
					items, subtotal, tax, discount, total_amount
				) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
				ON CONFLICT(file_name) DO UPDATE SET
					user_id=excluded.user_id,
					vendor_name=excluded.vendor_name,
					invoice_number=excluded.invoice_number,
					total_amount=excluded.total_amount,
					items=excluded.items,
					invoice_date=excluded.invoice_date,
					subtotal=excluded.subtotal,
					tax=excluded.tax,
					discount=excluded.discount
			""", (
				u_clean,
				file_name,
				str(seller).strip(),
				str(norm.get("vendor_address") or "").strip(),
				str(buyer).strip(),
				str(norm.get("buyer_address") or "").strip(),
				str(inv_num).strip(),
				str(date).strip(),
				json.dumps(norm.get("line_items", [])),
				float(norm.get("subtotal") or 0.0),
				float(norm.get("tax") or 0.0),
				float(norm.get("discount") or 0.0),
				float(grand_total),
			))
			conn.commit()
			conn.close()
		except Exception as exc:
			logger.warning("Could not persist to SQLite invoices table: %s", exc)

	return col


def delete_invoice(doc_id: str, index_path: str = DEFAULT_CHROMA_PATH, user_id: Optional[str] = None) -> bool:
	"""
	Delete a single invoice from ChromaDB by its ID.
	Optionally validates user_id ownership before deletion.
	Returns True if deleted, False if document was not found or error occurred.
	"""
	try:
		col = get_collection(path=index_path)
		existing = col.get(ids=[doc_id])
		if not existing or not existing.get("ids"):
			return False

		# Check ownership if user_id supplied
		if user_id:
			metas = existing.get("metadatas") or [{}]
			doc_user = metas[0].get("user_id") if metas else None
			if doc_user and str(doc_user) != str(user_id):
				return False

		col.delete(ids=[doc_id])

		# Also clean up from SQLite invoices table
		try:
			import sqlite3
			from config import CHAT_DB_PATH
			conn = sqlite3.connect(CHAT_DB_PATH)
			file_name = (existing.get("metadatas") or [{}])[0].get("file_name", "")
			if file_name:
				conn.execute("DELETE FROM invoices WHERE file_name = ?", (file_name,))
				conn.commit()
			conn.close()
		except Exception:
			pass

		return True
	except Exception as exc:
		logger.error("Failed to delete document %s from ChromaDB: %s", doc_id, exc)
		return False
