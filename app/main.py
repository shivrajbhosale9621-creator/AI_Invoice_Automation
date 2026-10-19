import os
import uuid
import datetime
import time
import logging
import threading
import asyncio
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Request, Response, Depends, HTTPException, status, UploadFile, File, Form, Query
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from werkzeug.utils import secure_filename

# Centralized config (sets environment variables before ChromaDB/ONNX)
from config import DEFAULT_INBOX_DIR, CHAT_DB_PATH
from app.core.config import settings
from app.core.rate_limiter import limiter, rate_limit_handler
from app.core.security import get_current_user
from app.models.user import User
from app.routers.auth import router as auth_router

from chat_store import (
    init_db,
    save_message,
    get_messages,
    list_conversations,
    get_or_create_conversation,
    add_dashboard_chart,
    list_dashboard_charts,
    delete_dashboard_chart,
)
from vector_store import (
    get_all_invoices,
    get_all_invoices_with_ids,
    delete_invoice,
)
from ocr_engine import OCRAgent
from graph_agent import GraphAgent
from orchestrator import MainOrchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fastapi_app")

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff"}
REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")

# Ensure required directories exist at startup
os.makedirs(DEFAULT_INBOX_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

# Initialise chat history tables in SQLite
init_db(CHAT_DB_PATH)

# Orchestrator session cache
_orchestrator_cache: Dict[str, MainOrchestrator] = {}
_cache_lock = threading.Lock()


def get_orchestrator(conversation_id: str, user_id: str) -> MainOrchestrator:
    """Return a cached MainOrchestrator for (user_id, conversation_id) pair."""
    key = f"{user_id}:{conversation_id}"
    with _cache_lock:
        if key not in _orchestrator_cache:
            logger.info("Instantiating MainOrchestrator for key=%s", key)
            _orchestrator_cache[key] = MainOrchestrator(
                user_id=user_id,
                conversation_id=conversation_id,
            )
        return _orchestrator_cache[key]


def allowed_file(filename: str) -> bool:
    """Return True if the file extension is allowed."""
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def safe_path(filename: str, base_dir: str) -> Optional[str]:
    """Sanitise filename and ensure it stays inside base_dir."""
    sanitised = secure_filename(filename)
    if not sanitised:
        return None
    resolved = os.path.abspath(os.path.join(base_dir, sanitised))
    base = os.path.abspath(base_dir)
    if not resolved.startswith(base + os.sep) and resolved != base:
        return None
    return resolved


def inject_system_message(conversation_id: str, user_id: str, content: str) -> None:
    """Best-effort system message injection into chat_store."""
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
    """Build human-readable system notification text."""
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
        lines.append("This invoice is now available for queries, reports, and graph generation.")
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
# FastAPI Lifespan Handler (Startup Warmup & Graceful Teardown)
# ─────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup warmup: Pre-load RapidOCR ONNX model in thread pool so cold-start overhead is zero
    t0 = time.time()
    logger.info("Starting RapidOCR ONNX model warmup...")
    try:
        from ocr_engine import get_ocr_instance
        await asyncio.to_thread(get_ocr_instance)
        warmup_dur = round(time.time() - t0, 2)
        logger.info("RapidOCR model warmup completed in %ss", warmup_dur)
    except Exception as exc:
        logger.warning("RapidOCR model warmup encountered error (non-fatal): %s", exc)

    # Ensure database schema is created and up-to-date (users, otp_codes, refresh_tokens, invoices)
    try:
        from alembic.config import Config
        from alembic import command
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
        logger.info("Database schema initialized via Alembic migrations (head).")
    except Exception as mig_err:
        logger.warning("Alembic migration notice (attempting Base.metadata fallback): %s", mig_err)
        try:
            from app.core.database import async_engine, Base
            import app.models  # ensure models are registered
            async with async_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Database tables verified via Base.metadata.create_all.")
        except Exception as fallback_err:
            logger.error("Failed to initialize database tables: %s", fallback_err)

    yield


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI Application Setup
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="AutoInvoice Intelligence Ledger",
    description="FastAPI service for AI Invoice Automation with Brevo-powered Email OTP Authentication.",
    version="2.0.0",
    lifespan=lifespan,
)

# Attach slowapi rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom generic HTTP exception handler for clean JSON format
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "error": exc.detail},
    )


# Mount Authentication Router under /auth
app.include_router(auth_router)

# Mount Static assets
static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
templates_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# ─────────────────────────────────────────────────────────────────────────────
# Root Frontend Route — GET /
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root_index():
    """Serve the single-page Paper Invoice ledger frontend."""
    index_file = os.path.join(templates_dir, "index.html")
    if os.path.exists(index_file):
        with open(index_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h2>AutoInvoice UI missing index.html</h2>")


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint 1 — GET /api/health and GET /health
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/health")
@app.get("/health")
async def health():
    """Liveness probe."""
    return {
        "status": "ok",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint 2 — POST /api/chat (Protected, scoped to current_user.id)
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/chat")
async def chat(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """
    Main orchestrator endpoint. Accepts JSON or multipart/form-data.
    Protected by get_current_user: strictly scoped to current_user.id.
    """
    user_id = str(current_user.id)
    ct = request.headers.get("content-type", "")

    conversation_id = ""
    idempotency_key = None
    message = ""
    save_path = None

    if "multipart/form-data" in ct:
        form = await request.form()
        message = str(form.get("message") or "").strip()
        conversation_id = str(form.get("conversation_id") or "").strip()
        idempotency_key = str(form.get("idempotency_key") or "").strip() or None

        file_obj = form.get("file")
        if file_obj and hasattr(file_obj, "filename") and file_obj.filename:
            if not allowed_file(file_obj.filename):
                ext = os.path.splitext(file_obj.filename)[1]
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported file type '{ext}'. Allowed: pdf, png, jpg, jpeg, bmp, tiff",
                )
            safe_name = secure_filename(file_obj.filename)
            save_path = os.path.join(DEFAULT_INBOX_DIR, safe_name)
            content = await file_obj.read()
            with open(save_path, "wb") as f:
                f.write(content)
            logger.info("Chat file attachment saved: %s", save_path)
    else:
        try:
            data = await request.json()
        except Exception:
            data = {}
        message = str(data.get("message") or "").strip()
        conversation_id = str(data.get("conversation_id") or "").strip()
        idempotency_key = str(data.get("idempotency_key") or "").strip() or None

    if not message:
        raise HTTPException(status_code=400, detail="'message' is required and cannot be empty")
    if len(message) > 4000:
        raise HTTPException(status_code=400, detail="'message' must be 4000 characters or fewer")

    if not conversation_id:
        conversation_id = str(uuid.uuid4())

    file_context = f"\n[Attached file saved at: {save_path}]" if save_path else ""
    full_message = message + file_context

    try:
        orch = get_orchestrator(conversation_id=conversation_id, user_id=user_id)
        actual_conv_id = orch.conversation_id
        response_text = await asyncio.to_thread(
            orch.run,
            user_query=full_message,
            idempotency_key=idempotency_key,
        )
        return {
            "status": "success",
            "conversation_id": actual_conv_id,
            "response": response_text,
            "user_id": user_id,
        }
    except Exception as exc:
        logger.error("Chat error conv=%s: %s", conversation_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Agent invocation failed: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint 3 — POST /api/invoice/upload (Protected, scoped to current_user.id)
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/invoice/upload")
async def upload_invoice(
    file: UploadFile = File(...),
    conversation_id: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
):
    """
    Upload and index invoice directly into ChromaDB and SQLite.
    Scoped strictly to current_user.id.
    """
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    if not allowed_file(file.filename):
        ext = os.path.splitext(file.filename)[1]
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: pdf, png, jpg, jpeg, bmp, tiff",
        )

    safe_name = secure_filename(file.filename)
    save_path = os.path.join(DEFAULT_INBOX_DIR, safe_name)
    file_bytes = await file.read()
    with open(save_path, "wb") as f:
        f.write(file_bytes)

    logger.info("Invoice file saved for processing: %s (user=%s)", save_path, current_user.id)

    # Process invoice asynchronously in worker thread to prevent event loop starvation
    agent = OCRAgent()
    result = await asyncio.to_thread(
        agent.process_and_index_invoice,
        save_path,
        user_id=str(current_user.id),
    )

    if result.get("status") != "success":
        return JSONResponse(
            status_code=422,
            content={
                "status": "error",
                "error": f"File saved but OCR extraction failed: {result.get('message', 'Unknown error')}",
                "file_name": safe_name,
                "timings": result.get("timings", {}),
            },
        )

    extracted = result.get("extracted_data") or {}

    # Notify orchestrator via system message if conversation_id provided
    if conversation_id:
        msg = build_invoice_system_message("uploaded", extracted, safe_name)
        inject_system_message(conversation_id, str(current_user.id), msg)

    return {
        "status": "success",
        "file_name": safe_name,
        "message": result.get("message", "Invoice uploaded, extracted, and indexed successfully."),
        "timings": result.get("timings", {}),
        "invoice_summary": {
            k: extracted.get(k)
            for k in [
                "vendor_name", "invoice_number", "total_amount",
                "invoice_date", "due_date", "payment_status", "currency",
            ]
            if extracted.get(k) is not None
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint 4 — GET /api/invoices (Protected, scoped to current_user.id)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/invoices")
async def get_user_invoices(
    include_ids: bool = Query(False),
    current_user: User = Depends(get_current_user),
):
    """
    Return all stored invoices for the current authenticated user.
    Used by dashboard for metrics, tables, and charts.
    """
    user_id = str(current_user.id)
    try:
        raw = get_all_invoices_with_ids(user_id=user_id) if include_ids else get_all_invoices(user_id=user_id)
        if not include_ids:
            invoices = [{k: v for k, v in inv.items() if k != "_doc_id"} for inv in raw]
        else:
            invoices = raw
        return {
            "status": "success",
            "count": len(invoices),
            "invoices": invoices,
        }
    except Exception as exc:
        logger.error("Get invoices error for user %s: %s", user_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint 5 — DELETE /api/invoice/{doc_id} (Protected, checks ownership)
# ─────────────────────────────────────────────────────────────────────────────
@app.delete("/api/invoice/{doc_id}")
async def delete_user_invoice(
    doc_id: str,
    conversation_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
):
    """
    Delete an invoice owned by current_user.id.
    """
    user_id = str(current_user.id)
    if not doc_id or not doc_id.strip():
        raise HTTPException(status_code=400, detail="doc_id is required")

    invoice_details: dict = {}
    try:
        all_invs = get_all_invoices_with_ids(user_id=user_id)
        invoice_details = next((inv for inv in all_invs if inv.get("_doc_id") == doc_id), {})
    except Exception:
        pass

    success = delete_invoice(doc_id=doc_id, user_id=user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Invoice not found.")

    if conversation_id:
        msg = build_invoice_system_message("deleted", invoice_details)
        inject_system_message(conversation_id, user_id, msg)

    return {
        "status": "success",
        "message": "Invoice deleted successfully.",
        "doc_id": doc_id,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint 6 — GET /api/report/download/{filename} (Protected)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/report/download/{filename}")
async def download_report(
    filename: str,
    current_user: User = Depends(get_current_user),
):
    """Serve a generated Excel report file as binary download."""
    file_path = safe_path(filename, REPORTS_DIR)
    if not file_path:
        raise HTTPException(status_code=400, detail="Invalid file path.")

    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Report file not found.")

    return FileResponse(
        path=file_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=os.path.basename(file_path),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint 7 — GET /api/chat/history/{conversation_id} (Protected)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/chat/history/{conversation_id}")
async def chat_history(
    conversation_id: str,
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
):
    """Return chronological message history for user's conversation."""
    user_id = str(current_user.id)
    try:
        messages = get_messages(
            conversation_id=conversation_id,
            user_id=user_id,
            limit=limit,
            db_path=CHAT_DB_PATH,
        )
        return {
            "status": "success",
            "conversation_id": conversation_id,
            "messages": messages,
            "count": len(messages),
        }
    except Exception as exc:
        logger.error("Chat history error conv=%s: %s", conversation_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint 8 — GET /api/chat/conversations (Protected)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/chat/conversations")
async def list_user_conversations(
    current_user: User = Depends(get_current_user),
):
    """Return all conversations for the authenticated user."""
    user_id = str(current_user.id)
    try:
        convs = list_conversations(user_id=user_id, db_path=CHAT_DB_PATH)
        return {
            "status": "success",
            "user_id": user_id,
            "conversations": convs,
            "count": len(convs),
        }
    except Exception as exc:
        logger.error("List conversations error user=%s: %s", user_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint 9 — GET /api/dashboard/charts (Protected)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/dashboard/charts")
async def get_user_dashboard_charts(
    current_user: User = Depends(get_current_user),
):
    """Retrieve all saved custom charts for the authenticated user."""
    user_id = str(current_user.id)
    try:
        charts = list_dashboard_charts(user_id=user_id, db_path=CHAT_DB_PATH)
        return {"status": "success", "charts": charts, "count": len(charts)}
    except Exception as exc:
        logger.error("Failed to list dashboard charts for user %s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint 10 — POST /api/dashboard/charts (Protected)
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/dashboard/charts")
async def create_user_dashboard_chart(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """
    Generate a custom chart from a natural language prompt and pin it permanently
    to the user's dashboard.
    """
    user_id = str(current_user.id)
    try:
        data = await request.json()
    except Exception:
        data = {}

    prompt = str(data.get("prompt") or "").strip()
    title = str(data.get("title") or "").strip()

    if not prompt:
        raise HTTPException(status_code=400, detail="'prompt' is required and cannot be empty")
    if len(prompt) > 1000:
        raise HTTPException(status_code=400, detail="'prompt' must be 1000 characters or fewer")

    try:
        agent = GraphAgent()
        result = await asyncio.to_thread(agent.run, prompt)
        status_val = result.get("status")
        chart_paths = result.get("chart_paths") or []

        if status_val != "success" or not chart_paths:
            error_msg = result.get("message") or result.get("error") or "Failed to generate visualization from prompt."
            return JSONResponse(
                status_code=422,
                content={"status": "error", "error": error_msg},
            )

        if not title:
            # Format prompt cleanly as title
            title = prompt[:50].title()

        saved = add_dashboard_chart(
            user_id=user_id,
            title=title,
            prompt=prompt,
            chart_path=chart_paths[0],
            db_path=CHAT_DB_PATH,
        )
        return {"status": "success", "chart": saved}
    except Exception as exc:
        logger.error("Error creating dashboard chart user=%s: %s", user_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Chart generation failed: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint 11 — DELETE /api/dashboard/charts/{chart_id} (Protected)
# ─────────────────────────────────────────────────────────────────────────────
@app.delete("/api/dashboard/charts/{chart_id}")
async def delete_user_dashboard_chart(
    chart_id: str,
    current_user: User = Depends(get_current_user),
):
    """Delete a pinned custom chart from the user's dashboard."""
    user_id = str(current_user.id)
    try:
        deleted = delete_dashboard_chart(chart_id=chart_id, user_id=user_id, db_path=CHAT_DB_PATH)
        if not deleted:
            raise HTTPException(status_code=404, detail="Chart not found or does not belong to user")
        return {"status": "success", "message": "Chart deleted successfully", "chart_id": chart_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error deleting dashboard chart %s: %s", chart_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=5000, reload=False)
