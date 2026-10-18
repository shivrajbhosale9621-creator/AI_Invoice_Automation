"""
Live endpoint test suite for Flask REST API.
Tests every endpoint, checks response codes, JSON schemas, edge cases, error handling,
security guards, and orchestrator / OCR integration.
"""

import os
import sys
import json
import time
import uuid
import requests

BASE_URL = "http://127.0.0.1:5000"

results = []

def record(test_name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    results.append({"name": test_name, "status": status, "detail": detail})
    print(f"[{status}] {test_name}" + (f" - {detail}" if detail else ""))


def run_all_tests():
    print("=" * 60)
    print("STARTING LIVE FLASK API ENDPOINT VALIDATION")
    print(f"Target: {BASE_URL}")
    print("=" * 60)

    # -------------------------------------------------------------
    # 1. Health Probe
    # -------------------------------------------------------------
    try:
        r = requests.get(f"{BASE_URL}/api/health", timeout=5)
        passed = (r.status_code == 200 and r.json().get("status") == "ok" and "timestamp" in r.json())
        record("1. GET /api/health", passed, f"Status: {r.status_code}, Body: {r.text}")
    except Exception as e:
        record("1. GET /api/health", False, f"Exception: {e}")

    # -------------------------------------------------------------
    # 2. Get Invoices (Standard)
    # -------------------------------------------------------------
    try:
        r = requests.get(f"{BASE_URL}/api/invoices", timeout=10)
        data = r.json()
        passed = (r.status_code == 200 and data.get("status") == "success" and isinstance(data.get("invoices"), list))
        # Ensure _doc_id is NOT leaked when include_ids=false
        has_doc_id = False
        if data.get("invoices"):
            has_doc_id = any("_doc_id" in inv for inv in data["invoices"])
        passed = passed and not has_doc_id
        record("2. GET /api/invoices (default, no internal IDs)", passed, f"Count: {data.get('count')}, _doc_id excluded: {not has_doc_id}")
    except Exception as e:
        record("2. GET /api/invoices", False, f"Exception: {e}")

    # -------------------------------------------------------------
    # 3. Get Invoices (include_ids=true)
    # -------------------------------------------------------------
    try:
        r = requests.get(f"{BASE_URL}/api/invoices?include_ids=true", timeout=10)
        data = r.json()
        passed = (r.status_code == 200 and data.get("status") == "success")
        has_doc_id = False
        sample_doc_id = None
        if data.get("invoices"):
            has_doc_id = all("_doc_id" in inv for inv in data["invoices"])
            sample_doc_id = data["invoices"][0].get("_doc_id")
        passed = passed and has_doc_id
        record("3. GET /api/invoices?include_ids=true", passed, f"Count: {data.get('count')}, _doc_id included: {has_doc_id}")
    except Exception as e:
        record("3. GET /api/invoices?include_ids=true", False, f"Exception: {e}")

    # -------------------------------------------------------------
    # 4. Chat Conversations List (Validation & Functionality)
    # -------------------------------------------------------------
    # 4a. Missing user_id -> 400
    try:
        r = requests.get(f"{BASE_URL}/api/chat/conversations", timeout=5)
        passed = (r.status_code == 400 and r.json().get("status") == "error")
        record("4a. GET /api/chat/conversations (missing user_id -> 400)", passed, f"Status: {r.status_code}, error: {r.json().get('error')}")
    except Exception as e:
        record("4a. GET /api/chat/conversations (missing user_id)", False, f"Exception: {e}")

    # 4b. With valid user_id -> 200
    test_user_id = f"test_user_{uuid.uuid4().hex[:6]}"
    try:
        r = requests.get(f"{BASE_URL}/api/chat/conversations?user_id={test_user_id}", timeout=5)
        passed = (r.status_code == 200 and r.json().get("status") == "success" and isinstance(r.json().get("conversations"), list))
        record("4b. GET /api/chat/conversations (valid user_id -> 200)", passed, f"Count: {r.json().get('count')}")
    except Exception as e:
        record("4b. GET /api/chat/conversations (valid user_id)", False, f"Exception: {e}")

    # -------------------------------------------------------------
    # 5. Chat History (Validation & Limit Clamping)
    # -------------------------------------------------------------
    dummy_conv_id = str(uuid.uuid4())
    # 5a. Missing user_id -> 400
    try:
        r = requests.get(f"{BASE_URL}/api/chat/history/{dummy_conv_id}", timeout=5)
        passed = (r.status_code == 400 and r.json().get("status") == "error")
        record("5a. GET /api/chat/history/<conv_id> (missing user_id -> 400)", passed, f"Status: {r.status_code}")
    except Exception as e:
        record("5a. GET /api/chat/history (missing user_id)", False, f"Exception: {e}")

    # 5b. Valid user_id -> 200 (empty history for new conv)
    try:
        r = requests.get(f"{BASE_URL}/api/chat/history/{dummy_conv_id}?user_id={test_user_id}", timeout=5)
        passed = (r.status_code == 200 and r.json().get("status") == "success" and r.json().get("messages") == [])
        record("5b. GET /api/chat/history/<conv_id> (new conv -> 200 empty)", passed, f"Messages: {r.json().get('count')}")
    except Exception as e:
        record("5b. GET /api/chat/history (new conv)", False, f"Exception: {e}")

    # 5c. Limit clamping
    try:
        r = requests.get(f"{BASE_URL}/api/chat/history/{dummy_conv_id}?user_id={test_user_id}&limit=9999", timeout=5)
        passed = (r.status_code == 200 and r.json().get("status") == "success")
        record("5c. GET /api/chat/history/<conv_id> (limit=9999 clamped)", passed, f"Status: {r.status_code}")
    except Exception as e:
        record("5c. GET /api/chat/history (clamping)", False, f"Exception: {e}")

    # -------------------------------------------------------------
    # 6. POST /api/chat Validation
    # -------------------------------------------------------------
    # 6a. Missing body fields -> 400
    try:
        r = requests.post(f"{BASE_URL}/api/chat", json={}, timeout=5)
        passed = (r.status_code == 400 and r.json().get("status") == "error")
        record("6a. POST /api/chat (empty JSON body -> 400)", passed, f"Error: {r.json().get('error')}")
    except Exception as e:
        record("6a. POST /api/chat (empty body)", False, f"Exception: {e}")

    # 6b. Message too long (> 4000 chars) -> 400
    try:
        r = requests.post(f"{BASE_URL}/api/chat", json={"user_id": test_user_id, "message": "X" * 4005}, timeout=5)
        passed = (r.status_code == 400 and "4000 characters" in r.json().get("error", ""))
        record("6b. POST /api/chat (message > 4000 chars -> 400)", passed, f"Error: {r.json().get('error')}")
    except Exception as e:
        record("6b. POST /api/chat (too long message)", False, f"Exception: {e}")

    # -------------------------------------------------------------
    # 7. POST /api/chat End-to-End Execution (Orchestrator + QA Tool)
    # -------------------------------------------------------------
    chat_conv_id = f"test_conv_{uuid.uuid4().hex[:8]}"
    chat_response_text = ""
    try:
        payload = {
            "user_id": test_user_id,
            "conversation_id": chat_conv_id,
            "message": "How many total invoices are currently in the system? Give a brief answer.",
        }
        print("-> Sending query to /api/chat (invoking orchestrator)...")
        start_t = time.time()
        r = requests.post(f"{BASE_URL}/api/chat", json=payload, timeout=60)
        elapsed = time.time() - start_t
        data = r.json()
        chat_response_text = data.get("response", "")
        passed = (r.status_code == 200 and data.get("status") == "success" and bool(chat_response_text) and data.get("conversation_id") == chat_conv_id)
        record("7a. POST /api/chat (orchestrator QA query -> 200)", passed, f"Elapsed: {elapsed:.2f}s, Response: {chat_response_text[:80]}...")
    except Exception as e:
        record("7a. POST /api/chat (QA query)", False, f"Exception: {e}")

    # 7b. Check history persistence for that conversation
    try:
        r = requests.get(f"{BASE_URL}/api/chat/history/{chat_conv_id}?user_id={test_user_id}", timeout=5)
        data = r.json()
        msgs = data.get("messages", [])
        passed = (r.status_code == 200 and len(msgs) >= 2)
        record("7b. GET /api/chat/history after chat (persisted user + assistant messages)", passed, f"Message count: {len(msgs)}")
    except Exception as e:
        record("7b. GET /api/chat/history after chat", False, f"Exception: {e}")

    # 7c. Check conversation appears in /api/chat/conversations
    try:
        r = requests.get(f"{BASE_URL}/api/chat/conversations?user_id={test_user_id}", timeout=5)
        data = r.json()
        convs = data.get("conversations", [])
        found = any(c.get("conversation_id") == chat_conv_id for c in convs)
        record("7c. GET /api/chat/conversations (conversation listed for user)", found, f"Total convs for user: {len(convs)}")
    except Exception as e:
        record("7c. GET /api/chat/conversations after chat", False, f"Exception: {e}")

    # 7d. Idempotency Key test
    try:
        idem_key = f"idem_{uuid.uuid4().hex}"
        p1 = {"user_id": test_user_id, "conversation_id": chat_conv_id, "message": "Say exactly: PING-123", "idempotency_key": idem_key}
        r1 = requests.post(f"{BASE_URL}/api/chat", json=p1, timeout=60)
        resp1 = r1.json().get("response")

        # Second request with SAME idempotency key
        r2 = requests.post(f"{BASE_URL}/api/chat", json=p1, timeout=60)
        resp2 = r2.json().get("response")
        passed = (resp1 == resp2 and bool(resp1))
        record("7d. POST /api/chat (idempotency key deduplication)", passed, f"Same cached response: {resp1 == resp2}")
    except Exception as e:
        record("7d. POST /api/chat (idempotency)", False, f"Exception: {e}")

    # -------------------------------------------------------------
    # 8. POST /api/chat Multipart with Attached Invoice File
    # -------------------------------------------------------------
    sample_pdf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "invoices_to_process", "INV-001.pdf")
    if os.path.exists(sample_pdf):
        try:
            print("-> Testing /api/chat multipart file attachment...")
            with open(sample_pdf, "rb") as f:
                files = {"file": ("INV-001.pdf", f, "application/pdf")}
                data = {
                    "user_id": test_user_id,
                    "conversation_id": chat_conv_id,
                    "message": "What is the invoice number and buyer or vendor on this attached invoice? Do not store it.",
                }
                r = requests.post(f"{BASE_URL}/api/chat", data=data, files=files, timeout=90)
            res_data = r.json()
            passed = (r.status_code == 200 and res_data.get("status") == "success" and bool(res_data.get("response")))
            record("8. POST /api/chat multipart with file attachment", passed, f"Response: {res_data.get('response', '')[:80]}...")
        except Exception as e:
            record("8. POST /api/chat multipart with file", False, f"Exception: {e}")
    else:
        record("8. POST /api/chat multipart with file", False, f"Sample PDF not found at {sample_pdf}")

    # -------------------------------------------------------------
    # 9. POST /api/invoice/upload (Upload UI - Direct OCR + Indexing)
    # -------------------------------------------------------------
    # 9a. No file in upload -> 400
    try:
        r = requests.post(f"{BASE_URL}/api/invoice/upload", timeout=5)
        passed = (r.status_code == 400 and r.json().get("status") == "error")
        record("9a. POST /api/invoice/upload (no file -> 400)", passed, f"Error: {r.json().get('error')}")
    except Exception as e:
        record("9a. POST /api/invoice/upload (no file)", False, f"Exception: {e}")

    # 9b. Disallowed file type -> 400
    try:
        files = {"file": ("malicious.exe", b"MZDummyContent", "application/octet-stream")}
        r = requests.post(f"{BASE_URL}/api/invoice/upload", files=files, timeout=5)
        passed = (r.status_code == 400 and "Unsupported file type" in r.json().get("error", ""))
        record("9b. POST /api/invoice/upload (invalid extension -> 400)", passed, f"Error: {r.json().get('error')}")
    except Exception as e:
        record("9b. POST /api/invoice/upload (invalid extension)", False, f"Exception: {e}")

    # 9c. Valid upload with system message injection
    upload_conv_id = f"upload_conv_{uuid.uuid4().hex[:8]}"
    if os.path.exists(sample_pdf):
        try:
            print("-> Testing /api/invoice/upload with valid PDF & system message injection...")
            with open(sample_pdf, "rb") as f:
                # Name it uniquely to avoid collision
                test_upload_name = f"test_upload_{uuid.uuid4().hex[:6]}.pdf"
                files = {"file": (test_upload_name, f, "application/pdf")}
                data = {
                    "conversation_id": upload_conv_id,
                    "user_id": test_user_id,
                }
                r = requests.post(f"{BASE_URL}/api/invoice/upload", files=files, data=data, timeout=90)
            res_data = r.json()
            passed = (r.status_code == 200 and res_data.get("status") == "success" and "invoice_summary" in res_data)
            record("9c. POST /api/invoice/upload (valid PDF -> 200 + extracted summary)", passed, f"Summary: {res_data.get('invoice_summary')}")

            # 9d. Verify system message was injected into chat history
            time.sleep(1)
            rh = requests.get(f"{BASE_URL}/api/chat/history/{upload_conv_id}?user_id={test_user_id}", timeout=5)
            hdata = rh.json()
            sys_msgs = [m for m in hdata.get("messages", []) if m.get("role") == "system"]
            passed_sys = (len(sys_msgs) > 0 and "[SYSTEM] Invoice uploaded" in sys_msgs[0].get("content", ""))
            record("9d. POST /api/invoice/upload injected system message in chat_store", passed_sys, f"System messages: {len(sys_msgs)}")
        except Exception as e:
            record("9c. POST /api/invoice/upload (valid PDF)", False, f"Exception: {e}")

    # -------------------------------------------------------------
    # 10. GET /api/report/download/<filename>
    # -------------------------------------------------------------
    # 10a. Path traversal attack attempt -> 400
    try:
        r = requests.get(f"{BASE_URL}/api/report/download/..%2F..%2Fconfig.py", timeout=5)
        passed = (r.status_code in (400, 404))
        record("10a. GET /api/report/download path traversal guard (../../config.py -> 400/404)", passed, f"Status: {r.status_code}")
    except Exception as e:
        record("10a. GET /api/report/download path traversal", False, f"Exception: {e}")

    # 10b. Non-existent file -> 404
    try:
        r = requests.get(f"{BASE_URL}/api/report/download/nonexistent_report_123.xlsx", timeout=5)
        passed = (r.status_code == 404 and r.json().get("status") == "error")
        record("10b. GET /api/report/download (missing file -> 404)", passed, f"Status: {r.status_code}")
    except Exception as e:
        record("10b. GET /api/report/download (missing file)", False, f"Exception: {e}")

    # 10c. Generate report via orchestrator / reports dir and download
    reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports")
    os.makedirs(reports_dir, exist_ok=True)
    test_report_name = f"test_report_{uuid.uuid4().hex[:6]}.xlsx"
    test_report_path = os.path.join(reports_dir, test_report_name)
    with open(test_report_path, "wb") as f:
        f.write(b"dummy excel binary content")

    try:
        r = requests.get(f"{BASE_URL}/api/report/download/{test_report_name}", timeout=5)
        content_type = r.headers.get("Content-Type", "")
        disposition = r.headers.get("Content-Disposition", "")
        passed = (r.status_code == 200 and "spreadsheetml" in content_type and "attachment" in disposition and r.content == b"dummy excel binary content")
        record("10c. GET /api/report/download/<file> (valid file stream)", passed, f"Content-Type: {content_type}, Disposition: {disposition}")
    except Exception as e:
        record("10c. GET /api/report/download/<file>", False, f"Exception: {e}")
    finally:
        time.sleep(0.5)
        try:
            if os.path.exists(test_report_path):
                os.remove(test_report_path)
        except Exception:
            pass

    # -------------------------------------------------------------
    # 11. DELETE /api/invoice/<doc_id>
    # -------------------------------------------------------------
    # 11a. Non-existent doc_id -> 404
    try:
        r = requests.delete(f"{BASE_URL}/api/invoice/non-existent-doc-id-99999", timeout=5)
        passed = (r.status_code == 404 and r.json().get("status") == "error")
        record("11a. DELETE /api/invoice/<doc_id> (non-existent doc_id -> 404)", passed, f"Status: {r.status_code}")
    except Exception as e:
        record("11a. DELETE /api/invoice (non-existent)", False, f"Exception: {e}")

    # 11b. Delete valid doc_id with system message injection
    # First fetch invoices with IDs
    try:
        r_invs = requests.get(f"{BASE_URL}/api/invoices?include_ids=true", timeout=10)
        inv_list = r_invs.json().get("invoices", [])
        # Find an invoice to delete (ideally one of our test uploads if possible, or check last)
        if inv_list:
            # Let's delete the last invoice
            target_to_delete = inv_list[-1]
            target_id = target_to_delete["_doc_id"]
            del_conv_id = f"del_conv_{uuid.uuid4().hex[:8]}"

            rdel = requests.delete(
                f"{BASE_URL}/api/invoice/{target_id}?conversation_id={del_conv_id}&user_id={test_user_id}",
                timeout=10
            )
            passed_del = (rdel.status_code == 200 and rdel.json().get("status") == "success" and rdel.json().get("doc_id") == target_id)
            record("11b. DELETE /api/invoice/<doc_id> (valid doc_id -> 200)", passed_del, f"Status: {rdel.status_code}, Deleted ID: {target_id}")

            # Verify system message was injected
            time.sleep(1)
            rh = requests.get(f"{BASE_URL}/api/chat/history/{del_conv_id}?user_id={test_user_id}", timeout=5)
            hdata = rh.json()
            sys_msgs = [m for m in hdata.get("messages", []) if m.get("role") == "system"]
            passed_sys_del = (len(sys_msgs) > 0 and "[SYSTEM] Invoice deleted" in sys_msgs[0].get("content", ""))
            record("11c. DELETE /api/invoice injected system message in chat_store", passed_sys_del, f"System messages: {len(sys_msgs)}")
        else:
            record("11b. DELETE /api/invoice/<doc_id>", False, "No invoices available to delete")
    except Exception as e:
        record("11b. DELETE /api/invoice/<doc_id>", False, f"Exception: {e}")

    # -------------------------------------------------------------
    # 12. Global Error Handling
    # -------------------------------------------------------------
    # 12a. 404 for unknown endpoint
    try:
        r = requests.get(f"{BASE_URL}/api/unknown_route_xyz", timeout=5)
        passed = (r.status_code == 404 and r.json().get("status") == "error" and "not found" in r.json().get("error", "").lower())
        record("12a. 404 Global Error Handler (JSON response)", passed, f"Status: {r.status_code}, Body: {r.text}")
    except Exception as e:
        record("12a. 404 Handler", False, f"Exception: {e}")

    # -------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------
    print("=" * 60)
    total = len(results)
    passed_count = sum(1 for r in results if r["status"] == "PASS")
    failed_count = sum(1 for r in results if r["status"] == "FAIL")
    print(f"TEST RESULTS: {passed_count}/{total} PASSED ({failed_count} FAILED)")
    print("=" * 60)
    for r in results:
        status_symbol = "[+]" if r["status"] == "PASS" else "[-]"
        print(f" {status_symbol} [{r['status']}] {r['name']}")
    print("=" * 60)

    return failed_count == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
