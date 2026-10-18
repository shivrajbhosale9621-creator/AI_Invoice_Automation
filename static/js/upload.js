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
    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      handleFileUpload(files[0]);
    }
  });

  fileInput.addEventListener("change", (e) => {
    if (fileInput.files && fileInput.files.length > 0) {
      handleFileUpload(fileInput.files[0]);
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

  async function handleFileUpload(file) {
    // Validate file extension
    const allowed = [".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff"];
    const ext = "." + file.name.split(".").pop().toLowerCase();
    if (!allowed.includes(ext)) {
      showToast(`Unsupported file type '${ext}'. Please upload PDF or image.`, "error");
      return;
    }

    // Validate size (max 20MB)
    if (file.size > 20 * 1024 * 1024) {
      showToast("File exceeds the 20MB maximum limit.", "error");
      return;
    }

    const { userId, conversationId } = getSessionContext();

    // Show processing docket
    dropzone.style.display = "none";
    if (resultCard) resultCard.style.display = "none";
    if (processingDocket) processingDocket.style.display = "block";

    const docketFileName = document.getElementById("docket-file-name");
    if (docketFileName) docketFileName.textContent = file.name;

    // Reset stages
    resetDocketStages();

    // Start elapsed timer
    const startTime = Date.now();
    const timerInterval = setInterval(() => {
      const elapsedSec = ((Date.now() - startTime) / 1000).toFixed(1);
      const timerEl = document.getElementById("docket-timer");
      if (timerEl) timerEl.textContent = `ELAPSED: ${elapsedSec}s`;
    }, 100);

    // Stage 1: Received
    setStageStatus("stage-received", "completed", "+0.1s");
    setProgress(20);

    // Stage 2: OCR Recognition
    setStageStatus("stage-ocr", "active");
    setProgress(35);

    // In-flight progressive visual tracker
    const stageCheckpoints = [
      setTimeout(() => {
        setStageStatus("stage-ocr", "completed", "+2.5s");
        setStageStatus("stage-extracted", "active");
        setProgress(60);
      }, 2500),
      setTimeout(() => {
        setStageStatus("stage-extracted", "completed", "+8.5s");
        setStageStatus("stage-validated", "active");
        setProgress(80);
      }, 8500),
      setTimeout(() => {
        setStageStatus("stage-validated", "completed", "+11.0s");
        setStageStatus("stage-filed", "active");
        setProgress(92);
      }, 11000),
    ];

    try {
      showToast(`Filing "${file.name}" in ledger...`, "info");
      const res = await api.uploadInvoice(file, conversationId, userId);

      stageCheckpoints.forEach(clearTimeout);
      clearInterval(timerInterval);

      const totalElapsed = ((Date.now() - startTime) / 1000).toFixed(2);
      const timerEl = document.getElementById("docket-timer");
      if (timerEl) timerEl.textContent = `COMPLETED IN ${totalElapsed}s`;

      const timings = res.timings || {};
      const tPre = Number(timings.preprocess_s || 0.2);
      const tOcr = Number(timings.ocr_s || 2.4);
      const tStruct = Number(timings.structuring_s || 5.0);
      const tTotal = Number(timings.total_s || totalElapsed);

      // Stamp true server timings
      setStageStatus("stage-received", "completed", `+${tPre.toFixed(1)}s`);
      setStageStatus("stage-ocr", "completed", `+${(tPre + tOcr).toFixed(1)}s`);
      setStageStatus("stage-extracted", "completed", `+${(tPre + tOcr + tStruct).toFixed(1)}s`);
      setStageStatus("stage-validated", "completed", `+${Math.min(tTotal, tPre + tOcr + tStruct + 0.1).toFixed(1)}s`);
      setStageStatus("stage-filed", "completed", `+${tTotal.toFixed(1)}s`);
      setProgress(100);

      showToast(`Successfully filed invoice "${file.name}" in ${tTotal.toFixed(1)}s!`, "success");

      // Render structured result after brief visual confirmation
      setTimeout(() => {
        renderExtractionResult(res, file.name);
      }, 600);

      // Notify parent to refresh dashboard KPIs & table
      if (typeof onUploadSuccess === "function") {
        onUploadSuccess();
      }
    } catch (err) {
      clearInterval(timerInterval);
      stageCheckpoints.forEach(clearTimeout);
      showDocketError(err.message || "Invoice processing failed.");
      showToast(err.message || "Invoice processing failed.", "error");
    } finally {
      fileInput.value = "";
    }
  }

  function resetDocketStages() {
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
        stampEl.className = "docket-stage-stamp stamp-badge pending";
        stampEl.textContent = "IN PROGRESS";
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

    if (timeEl && timeText) {
      timeEl.textContent = timeText;
    }
  }

  function setProgress(pct) {
    const fill = document.getElementById("docket-progress-fill");
    if (fill) fill.style.width = `${pct}%`;
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
    setText("res-vendor", summary.vendor_name);
    setText("res-date", summary.invoice_date);
    setText("res-amount", summary.total_amount ? `$${Number(summary.total_amount).toLocaleString(undefined, { minimumFractionDigits: 2 })}` : "$0.00");
    setText("res-status", summary.payment_status || "Unpaid");

    if (processingDocket) processingDocket.style.display = "none";
    resultCard.style.display = "block";
  }
}
