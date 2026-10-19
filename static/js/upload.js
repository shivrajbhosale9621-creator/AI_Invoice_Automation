/**
 * upload.js — Upload Center with Processing Docket & Ink Receipt Stages
 */

import { api } from "./api.js";

export function initUpload({ onUploadSuccess, getSessionContext, showToast }) {
  const dropzone = document.getElementById("dropzone-card");
  const fileInput = document.getElementById("file-input-upload");
  const processingDocket = document.getElementById("processing-docket");
  const resultCard = document.getElementById("extraction-result-card");
  const btnUploadAnother = document.getElementById("btn-upload-another");

  if (!dropzone || !fileInput) return;

  dropzone.addEventListener("click", () => fileInput.click());

  ["dragenter", "dragover"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.add("dragover");
    });
  });

  ["dragleave", "drop"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.remove("dragover");
    });
  });

  dropzone.addEventListener("drop", (e) => {
    const files = Array.from(e.dataTransfer.files || []);
    if (files.length > 0) {
      handleFiles(files);
    }
  });

  fileInput.addEventListener("change", (e) => {
    const files = Array.from(fileInput.files || []);
    if (files.length > 0) {
      handleFiles(files);
    }
  });

  if (btnUploadAnother) {
    btnUploadAnother.addEventListener("click", () => {
      if (resultCard) resultCard.style.display = "none";
      if (processingDocket) processingDocket.style.display = "none";
      dropzone.style.display = "block";
      fileInput.value = "";
    });
  }

  async function handleFiles(files) {
    const allowed = [".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff"];
    const validFiles = [];

    for (const file of files) {
      const ext = "." + file.name.split(".").pop().toLowerCase();
      if (!allowed.includes(ext)) {
        showToast(`Skipping '${file.name}': unsupported file type '${ext}'.`, "error");
        continue;
      }
      if (file.size > 20 * 1024 * 1024) {
        showToast(`Skipping '${file.name}': exceeds 20MB limit.`, "error");
        continue;
      }
      validFiles.push(file);
    }

    if (validFiles.length === 0) {
      fileInput.value = "";
      return;
    }

    // Show processing docket
    dropzone.style.display = "none";
    if (resultCard) resultCard.style.display = "none";
    if (processingDocket) processingDocket.style.display = "block";

    let lastRes = null;
    let lastFilename = "";
    let processedCount = 0;

    for (let i = 0; i < validFiles.length; i++) {
      const file = validFiles[i];
      const batchLabel = validFiles.length > 1 ? ` (${i + 1} of ${validFiles.length})` : "";

      const docketFileName = document.getElementById("docket-file-name");
      if (docketFileName) docketFileName.textContent = `${file.name}${batchLabel}`;

      // Reset stages
      resetDocketStages();

      // Start elapsed timer
      const startTime = Date.now();
      const timerInterval = setInterval(() => {
        const elapsedSec = ((Date.now() - startTime) / 1000).toFixed(1);
        const timerEl = document.getElementById("docket-timer");
        if (timerEl) timerEl.textContent = `ELAPSED: ${elapsedSec}s`;
      }, 100);

      // Stage 1: Received immediately
      setStageStatus("stage-received", "completed", "+0.1s");

      // Stages 2 to 5: Indeterminate state with spinners
      setStageStatus("stage-ocr", "active");
      setStageStatus("stage-extracted", "active");
      setStageStatus("stage-validated", "active");
      setStageStatus("stage-filed", "active");
      setIndeterminateProgress(true);

      const { userId, conversationId } = getSessionContext();

      try {
        showToast(`Processing invoice ${i + 1}/${validFiles.length}: "${file.name}"...`, "info");
        const res = await api.uploadInvoice(file, conversationId, userId);

        clearInterval(timerInterval);
        setIndeterminateProgress(false);
        setProgress(100);

        const totalElapsed = ((Date.now() - startTime) / 1000).toFixed(2);
        const timerEl = document.getElementById("docket-timer");
        if (timerEl) timerEl.textContent = `COMPLETED IN ${totalElapsed}s`;

        const timings = res.timings || {};
        const tPre = Number(timings.preprocess_s || 0.2);
        const tOcr = Number(timings.ocr_s || 2.4);
        const tStruct = Number(timings.structuring_s || 5.0);
        const tTotal = Number(timings.total_s || totalElapsed);

        setStageStatus("stage-received", "completed", `+${tPre.toFixed(1)}s`);
        setStageStatus("stage-ocr", "completed", `+${(tPre + tOcr).toFixed(1)}s`);
        setStageStatus("stage-extracted", "completed", `+${(tPre + tOcr + tStruct).toFixed(1)}s`);
        setStageStatus("stage-validated", "completed", `+${Math.min(tTotal, tPre + tOcr + tStruct + 0.1).toFixed(1)}s`);
        setStageStatus("stage-filed", "completed", `+${tTotal.toFixed(1)}s`);

        showToast(`Filed "${file.name}" in ledger (${tTotal.toFixed(1)}s)`, "success");
        lastRes = res;
        lastFilename = file.name;
        processedCount++;

        // If batch upload, briefly pause to show docket completion before next
        if (i < validFiles.length - 1) {
          await new Promise((r) => setTimeout(r, 600));
        }
      } catch (err) {
        clearInterval(timerInterval);
        setIndeterminateProgress(false);
        showDocketError(err.message || `Failed to process ${file.name}`);
        showToast(`Error processing "${file.name}": ${err.message}`, "error");
        if (i < validFiles.length - 1) {
          await new Promise((r) => setTimeout(r, 1200));
        }
      }
    }

    fileInput.value = "";

    // Refresh ledger data
    if (typeof onUploadSuccess === "function") {
      onUploadSuccess();
    }

    // Render structured result after brief visual confirmation
    if (lastRes) {
      setTimeout(() => {
        renderExtractionResult(lastRes, lastFilename);
      }, 700);
    }
  }

  function resetDocketStages() {
    setIndeterminateProgress(false);
    ["received", "ocr", "extracted", "validated", "filed"].forEach((stage) => {
      setStageStatus(`stage-${stage}`, "pending", "--:--");
    });
    setProgress(0);
    const errBox = document.getElementById("docket-error-box");
    if (errBox) errBox.style.display = "none";
  }

  function setStageStatus(elementId, status, timeText = "") {
    const row = document.getElementById(elementId);
    if (!row) return;

    const stampEl = row.querySelector(".docket-stage-stamp");
    const timeEl = row.querySelector(".docket-stage-time");

    if (stampEl) {
      if (status === "completed") {
        stampEl.className = "docket-stage-stamp stamp-badge paid";
        stampEl.textContent = "OK \u2713";
      } else if (status === "active") {
        stampEl.className = "docket-stage-stamp stamp-badge pending is-loading";
        stampEl.innerHTML = `<span class="docket-inline-spinner"></span> IN PROGRESS`;
      } else if (status === "failed") {
        stampEl.className = "docket-stage-stamp stamp-badge failed";
        stampEl.textContent = "FAILED";
      } else {
        stampEl.className = "docket-stage-stamp stamp-badge";
        stampEl.style.color = "var(--ink-muted)";
        stampEl.style.borderColor = "var(--hairline)";
        stampEl.textContent = "PENDING";
      }
    }

    if (timeEl) {
      if (timeText) {
        timeEl.textContent = timeText;
      } else if (status === "active") {
        timeEl.textContent = "PROCESSING...";
      } else {
        timeEl.textContent = "--:--";
      }
    }
  }

  function setProgress(pct) {
    const fill = document.getElementById("docket-progress-fill");
    if (fill) fill.style.width = `${pct}%`;
  }

  function setIndeterminateProgress(enable) {
    const fill = document.getElementById("docket-progress-fill");
    if (!fill) return;
    if (enable) {
      fill.classList.add("is-indeterminate");
    } else {
      fill.classList.remove("is-indeterminate");
    }
  }

  function showDocketError(reason) {
    setStageStatus("stage-ocr", "failed");
    setStageStatus("stage-extracted", "failed");
    setStageStatus("stage-validated", "failed");
    setStageStatus("stage-filed", "failed");

    const errBox = document.getElementById("docket-error-box");
    const errMsg = document.getElementById("docket-error-msg");
    if (errBox && errMsg) {
      errMsg.textContent = reason;
      errBox.style.display = "block";
    }
  }

  function renderExtractionResult(res, filename) {
    if (!resultCard) return;

    const summary = res.invoice_summary || {};
    const setText = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val || "N/A";
    };

    setText("res-filename", filename);
    setText("res-inv-num", summary.invoice_number);
    const vendorClean = summary.vendor_name ? (typeof summary.vendor_name === 'object' ? (summary.vendor_name.name || "Unknown") : String(summary.vendor_name)) : "Unknown";
    setText("res-vendor", vendorClean);
    setText("res-date", summary.invoice_date);
    setText("res-amount", summary.total_amount ? `₹${Number(summary.total_amount).toLocaleString("en-IN", { minimumFractionDigits: 2 })}` : "₹0.00");
    setText("res-status", summary.payment_status || "Unpaid");

    if (processingDocket) processingDocket.style.display = "none";
    resultCard.style.display = "block";
  }
}
