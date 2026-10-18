"""
flask_api.py — REST API layer for AI Invoice Automation.

Architecture
------------
- All user queries (QA, graph, report, file analysis) go through POST /api/chat → MainOrchestrator.
- Separate endpoints exist ONLY for operations that cannot go through a text chat:
    * POST /api/invoice/upload          — binary multipart file + auto OCR + index (Upload UI)
    * GET  /api/invoices                — raw dashboard data fetch (no LLM needed)
    * DELETE /api/invoice/<doc_id>      — destructive DB op + notify orchestrator
    * GET  /api/report/download/<file>  — binary Excel file stream
    * GET  /api/chat/history/<conv_id>  — conversation history for UI
    * GET  /api/chat/conversations      — conversation list for sidebar

Orchestrator session cache
--------------------------
MainOrchestrator is expensive to instantiate (4 agents + LangGraph ReAct agent).
We cache one instance per (user_id, conversation_id), shared across requests.
Cache access is protected by threading.Lock for thread safety.

System message injection
------------------------
After Upload (Upload UI) and Delete operations, a system message is injected into
chat_store so the orchestrator is aware of the data state on the next /api/chat call.
"""

import os
import uuid
import threading
import logging
import datetime

# config MUST be imported first (sets KMP_DUPLICATE_LIB_OK before numpy/chromadb)
from config import DEFAULT_INBOX_DIR, CHAT_DB_PATH

from werkzeug.utils import secure_filename
from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS

from chat_store import (
    init_db,
    save_message,
    get_messages,
    list_conversations,
    get_or_create_conversation,
)
from vector_store import get_all_invoices, get_all_invoices_with_ids, delete_invoice
from ocr_engine import OCRAgent
from orchestrator import MainOrchestrator


# ─────────────────────────────────────────────────────────────────────────────
# App Setup
# ─────────────────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app, resources={r"/api/*": {"origins": "*"}})

# Hard limit on incoming request size (covers file uploads)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff"}
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

# Ensure required directories exist at startup
os.makedirs(DEFAULT_INBOX_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("flask_api")

# Initialise DB schema on startup
init_db(CHAT_DB_PATH)


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator Session Cache
# ─────────────────────────────────────────────────────────────────────────────
_orchestrator_cache: dict = {}
_cache_lock = threading.Lock()


def get_orchestrator(conversation_id: str, user_id: str) -> MainOrchestrator:
    """
    Return a cached MainOrchestrator for this (user_id, conversation_id) pair.
    Creates a new one if not yet cached.  Thread-safe via _cache_lock.
    """
    key = f"{user_id}:{conversation_id}"
    with _cache_lock:
        if key not in _orchestrator_cache:
            logger.info("Creating new MainOrchestrator for key=%s", key)
            _orchestrator_cache[key] = MainOrchestrator(
                user_id=user_id,
                conversation_id=conversation_id,
            )
        return _orchestrator_cache[key]


# ─────────────────────────────────────────────────────────────────────────────
# Helper Utilities
# ─────────────────────────────────────────────────────────────────────────────
def allowed_file(filename: str) -> bool:
    """Return True if the file extension is in the allowed set."""
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def safe_path(filename: str, base_dir: str):
    """
    Sanitise filename and resolve to an absolute path.
    Returns None if the resolved path escapes base_dir (path traversal guard).
    """
    sanitised = secure_filename(filename)
    if not sanitised:
        return None
    resolved = os.path.abspath(os.path.join(base_dir, sanitised))
    base = os.path.abspath(base_dir)
    if not resolved.startswith(base + os.sep) and resolved != base:
        return None
    return resolved


def require_fields(data: dict, fields: list):
    """Return (True, '') if all fields present and non-empty, else (False, error_msg)."""
    for f in fields:
        if not data.get(f) or not str(data[f]).strip():
            return False, f"'{f}' is required and cannot be empty"
    return True, ""


def inject_system_message(conversation_id: str, user_id: str, content: str) -> None:
    """
    Best-effort: inject a system message into chat_store for a conversation.
    Failures are logged but never propagate to the caller.
    """
    try:
        get_or_create_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
            db_path=CHAT_DB_PATH,
        )
        save_message(
            conversation_id=conversation_id,
            role="system",
            content=content,
            db_path=CHAT_DB_PATH,
        )
        logger.info("System message injected into conversation %s", conversation_id)
    except Exception as exc:
        logger.warning("System message injection failed (non-fatal): %s", exc)


def build_invoice_system_message(action: str, invoice_data: dict, file_name: str = "") -> str:
    """Build a human-readable system message for upload/delete events."""
    if action == "uploaded":
        lines = ["[SYSTEM] Invoice uploaded and indexed successfully."]
        if file_name:
            lines.append(f"File: {file_name}")
        for key in [
            "vendor_name", "invoice_number", "total_amount",
            "invoice_date", "due_date", "payment_status", "currency",
        ]:
            val = invoice_data.get(key)
            if val is not None:
                lines.append(f"{key.replace('_', ' ').title()}: {val}")
        lines.append(
            "This invoice is now available for queries, reports, and graph generation."
        )
        return "\n".join(lines)

    if action == "deleted":
        lines = ["[SYSTEM] Invoice deleted from the vector store."]
        for key in ["invoice_number", "vendor_name", "total_amount"]:
            val = invoice_data.get(key)
            if val is not None:
                lines.append(f"{key.replace('_', ' ').title()}: {val}")
        lines.append("This invoice is no longer available for queries.")
        return "\n".join(lines)

    return f"[SYSTEM] Invoice {action}."


# ─────────────────────────────────────────────────────────────────────────────
# Global Error Handlers — always return JSON, never HTML
# ─────────────────────────────────────────────────────────────────────────────
@app.errorhandler(400)
def bad_request(e):
    return jsonify({"status": "error", "error": str(getattr(e, "description", e))}), 400


@app.errorhandler(404)
def not_found(e):
    return jsonify({"status": "error", "error": "Endpoint not found"}), 404


@app.errorhandler(413)
def request_too_large(e):
    return jsonify({"status": "error", "error": "File too large. Maximum allowed size is 20 MB."}), 413


@app.errorhandler(500)
def internal_error(e):
    logger.error("Internal server error: %s", e, exc_info=True)
    return jsonify({"status": "error", "error": "Internal server error"}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Frontend UI Route — GET /
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    """Serve the modern Bento Grid UI frontend."""
    return render_template("index.html")


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint 1 — GET /api/health
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/health", methods=["GET"])
def health():
    """Liveness / readiness probe for Docker / Kubernetes."""
    return jsonify({
        "status": "ok",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint 2 — POST /api/chat  (PRIMARY — all user queries go here)
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/chat", methods=["POST"])
def chat():
    """
    Main orchestrator endpoint. Accepts:
      - application/json        — standard JSON body
      - multipart/form-data     — JSON fields + optional invoice file attachment (Chat UI)

    The orchestrator decides which specialist tools to invoke based on the user message.
    When a file is attached, its saved path is appended to the message so the orchestrator
    can call extract_data_ocr with store=True or store=False as appropriate.
    """
    ct = request.content_type or ""
    if "multipart/form-data" in ct:
        data = request.form
    else:
        data = request.get_json(silent=True) or {}

    # Validate required fields
    valid, err = require_fields(data, ["user_id", "message"])
    if not valid:
        return jsonify({"status": "error", "error": err}), 400

    user_id = str(data["user_id"]).strip()
    message = str(data["message"]).strip()

    if len(message) > 4000:
        return jsonify({"status": "error", "error": "'message' must be 4000 characters or fewer"}), 400
    if len(user_id) > 128:
        return jsonify({"status": "error", "error": "'user_id' must be 128 characters or fewer"}), 400

    conversation_id = str(data.get("conversation_id") or "").strip() or str(uuid.uuid4())
    idempotency_key = str(data.get("idempotency_key") or "").strip() or None

    # Handle optional file attachment (Chat UI)
    file_context = ""
    if "file" in request.files:
        f = request.files["file"]
        if f and f.filename:
            if not allowed_file(f.filename):
                ext = os.path.splitext(f.filename)[1]
                return jsonify({
                    "status": "error",
                    "error": f"Unsupported file type '{ext}'. Allowed: pdf, png, jpg, jpeg, bmp, tiff",
                }), 400

            safe_name = secure_filename(f.filename)
            save_path = os.path.join(DEFAULT_INBOX_DIR, safe_name)
            f.save(save_path)
            logger.info("Chat file attachment saved: %s", save_path)
            file_context = f"\n[Attached file saved at: {save_path}]"

    full_message = message + file_context

    # Invoke orchestrator
    try:
        orch = get_orchestrator(conversation_id=conversation_id, user_id=user_id)
        actual_conv_id = orch.conversation_id

        response_text = orch.run(
            user_query=full_message,
            idempotency_key=idempotency_key,
        )

        return jsonify({
            "status": "success",
            "conversation_id": actual_conv_id,
            "response": response_text,
            "user_id": user_id,
        })
    except Exception as exc:
        logger.error("Chat error conv=%s: %s", conversation_id, exc, exc_info=True)
        return jsonify({"status": "error", "error": f"Agent invocation failed: {exc}"}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint 3 — POST /api/invoice/upload  (Upload UI — auto OCR + index)
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/invoice/upload", methods=["POST"])
def upload_invoice():
    """
    Upload UI flow:
      1. Validate + save the file.
      2. Run OCRAgent.process_and_index_invoice() directly (no orchestrator, no LLM tokens).
      3. If conversation_id + user_id provided, inject a system message so the orchestrator
         is aware of the new invoice on the next /api/chat call.

    Returns 422 when file saved but OCR extraction failed (distinct from 400 bad input).
    """
    if "file" not in request.files:
        return jsonify({
            "status": "error",
            "error": "No file provided. Send as multipart/form-data with field name 'file'.",
        }), 400

    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"status": "error", "error": "Empty file received."}), 400

    if not allowed_file(f.filename):
        ext = os.path.splitext(f.filename)[1]
        return jsonify({
            "status": "error",
            "error": f"Unsupported file type '{ext}'. Allowed: pdf, png, jpg, jpeg, bmp, tiff",
        }), 400

    # Sanitise and save — written to disk only after validation passes
    safe_name = secure_filename(f.filename)
    save_path = os.path.join(DEFAULT_INBOX_DIR, safe_name)
    f.save(save_path)
    logger.info("Invoice file saved for processing: %s", save_path)

    # OCR + index
    agent = OCRAgent()
    result = agent.process_and_index_invoice(save_path)

    if result.get("status") != "success":
        return jsonify({
            "status": "error",
            "error": f"File saved but OCR extraction failed: {result.get('message', 'Unknown error')}",
            "file_name": safe_name,
        }), 422

    extracted = result.get("extracted_data") or {}

    # Notify orchestrator via system message (optional)
    conv_id = str(request.form.get("conversation_id") or "").strip()
    user_id = str(request.form.get("user_id") or "").strip()
    if conv_id and user_id:
        msg = build_invoice_system_message("uploaded", extracted, safe_name)
        inject_system_message(conv_id, user_id, msg)

    return jsonify({
        "status": "success",
        "file_name": safe_name,
        "message": result.get("message", "Invoice uploaded, extracted, and indexed successfully."),
        "invoice_summary": {
            k: extracted.get(k)
            for k in [
                "vendor_name", "invoice_number", "total_amount",
                "invoice_date", "due_date", "payment_status", "currency",
            ]
            if extracted.get(k) is not None
        },
    })


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint 4 — GET /api/invoices  (Dashboard data)
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/invoices", methods=["GET"])
def get_invoices():
    """
    Return all stored invoices as structured JSON.
    Used by dashboard for KPIs, tables, and charts — no LLM required.
    Query param: include_ids=true  includes internal ChromaDB _doc_id (needed for delete).
    """
    include_ids = request.args.get("include_ids", "false").lower() == "true"
    try:
        raw = get_all_invoices_with_ids() if include_ids else get_all_invoices()
        if not include_ids:
            invoices = [{k: v for k, v in inv.items() if k != "_doc_id"} for inv in raw]
        else:
            invoices = raw
        return jsonify({
            "status": "success",
            "count": len(invoices),
            "invoices": invoices,
        })
    except Exception as exc:
        logger.error("Get invoices error: %s", exc, exc_info=True)
        return jsonify({"status": "error", "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint 5 — DELETE /api/invoice/<doc_id>
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/invoice/<doc_id>", methods=["DELETE"])
def delete_invoice_endpoint(doc_id: str):
    """
    Delete an invoice from ChromaDB by its _doc_id.
    Optionally injects a system message into the active conversation.

    Query params (for notification):
      conversation_id  — which conversation to notify
      user_id          — required if conversation_id is provided
    """
    if not doc_id or not doc_id.strip():
        return jsonify({"status": "error", "error": "doc_id is required"}), 400

    # Fetch invoice details BEFORE deleting (for system message content)
    invoice_details: dict = {}
    try:
        all_invs = get_all_invoices_with_ids()
        invoice_details = next(
            (inv for inv in all_invs if inv.get("_doc_id") == doc_id), {}
        )
    except Exception:
        pass  # non-fatal

    success = delete_invoice(doc_id=doc_id)
    if not success:
        return jsonify({"status": "error", "error": "Invoice not found."}), 404

    # Notify orchestrator (optional)
    conv_id = str(request.args.get("conversation_id") or "").strip()
    user_id = str(request.args.get("user_id") or "").strip()
    if conv_id and user_id:
        msg = build_invoice_system_message("deleted", invoice_details)
        inject_system_message(conv_id, user_id, msg)

    return jsonify({
        "status": "success",
        "message": "Invoice deleted successfully.",
        "doc_id": doc_id,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint 6 — GET /api/report/download/<filename>
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/report/download/<filename>", methods=["GET"])
def download_report(filename: str):
    """
    Serve a generated Excel report as a binary download.

    Flow:
      1. User asks /api/chat to generate a report.
      2. Orchestrator calls generate_excel tool, saves to reports/<timestamp>_<uuid>.xlsx,
         returns the filename in its response text.
      3. Frontend parses the filename and calls this endpoint.

    Security: secure_filename() + abspath guard inside REPORTS_DIR.
    """
    file_path = safe_path(filename, REPORTS_DIR)
    if not file_path:
        return jsonify({"status": "error", "error": "Invalid file path."}), 400

    if not os.path.isfile(file_path):
        return jsonify({"status": "error", "error": "Report file not found."}), 404

    return send_file(
        file_path,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=os.path.basename(file_path),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint 7 — GET /api/chat/history/<conversation_id>
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/chat/history/<conversation_id>", methods=["GET"])
def chat_history(conversation_id: str):
    """
    Return message history for a conversation (used by Chat UI).
    Requires user_id query param for user isolation.
    limit: default 50, clamped to [1, 200].
    """
    user_id = str(request.args.get("user_id") or "").strip()
    if not user_id:
        return jsonify({"status": "error", "error": "'user_id' query parameter is required"}), 400

    try:
        limit = int(request.args.get("limit", 50))
    except (ValueError, TypeError):
        limit = 50
    limit = max(1, min(limit, 200))

    try:
        messages = get_messages(
            conversation_id=conversation_id,
            user_id=user_id,
            limit=limit,
            db_path=CHAT_DB_PATH,
        )
        return jsonify({
            "status": "success",
            "conversation_id": conversation_id,
            "messages": messages,
            "count": len(messages),
        })
    except Exception as exc:
        logger.error("Chat history error conv=%s: %s", conversation_id, exc, exc_info=True)
        return jsonify({"status": "error", "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint 8 — GET /api/chat/conversations
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/chat/conversations", methods=["GET"])
def list_user_conversations():
    """
    Return all conversations for a user, most-recently-updated first.
    Used by Chat UI sidebar. Requires user_id query param.
    """
    user_id = str(request.args.get("user_id") or "").strip()
    if not user_id:
        return jsonify({"status": "error", "error": "'user_id' query parameter is required"}), 400

    try:
        convs = list_conversations(user_id=user_id, db_path=CHAT_DB_PATH)
        return jsonify({
            "status": "success",
            "user_id": user_id,
            "conversations": convs,
            "count": len(convs),
        })
    except Exception as exc:
        logger.error("List conversations error user=%s: %s", user_id, exc, exc_info=True)
        return jsonify({"status": "error", "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000, threaded=True)
