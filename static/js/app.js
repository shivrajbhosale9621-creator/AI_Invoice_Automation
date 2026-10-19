/**
 * app.js — Main Application Orchestrator & View Router for Paper Invoice Design
 * Complete Authentication & Email OTP flow integrated seamlessly.
 */

import { api } from "./api.js";
import { charts } from "./charts.js";
import { initUpload } from "./upload.js";
import { initChat } from "./chat.js";

// Global App State
const state = {
  activeView: "dashboard",
  user: null,
  userId: localStorage.getItem("invoice_app_user_id") || "default_user",
  conversationId: localStorage.getItem("invoice_app_conv_id") || ("conv_" + Math.random().toString(36).substring(2, 10)),
  invoices: [],
  pendingDeleteDocId: null,
  currentStatusFilter: "all",
  searchQuery: "",
  pendingEmail: "",
  resendCountdownTimer: null,
  resendSeconds: 60,
};

function getSessionContext() {
  return {
    userId: state.user ? state.user.id : state.userId,
    conversationId: state.conversationId,
  };
}

function setConversationId(newId) {
  state.conversationId = newId;
  localStorage.setItem("invoice_app_conv_id", newId);
}

// Toast Notification Manager (Tactile Paper Toast)
export function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = `toast ${type}`;

  let stampLabel = "[INFO]";
  if (type === "success") stampLabel = "[FILED]";
  if (type === "error") stampLabel = "[ALERT]";

  toast.innerHTML = `
    <span class="stamp-badge ${type === "error" ? "failed" : (type === "success" ? "paid" : "pending")}" style="font-size:0.6rem; padding:0.1rem 0.35rem;">
      ${stampLabel}
    </span>
    <span>${escapeHtml(message)}</span>
  `;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(8px)";
    toast.style.transition = "all 0.25s ease";
    setTimeout(() => toast.remove(), 250);
  }, 4000);
}

// ─────────────────────────────────────────────────────────────────────────────
// App Initialization
// ─────────────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  setupNavigation();
  setupDeleteModal();
  setupSearchAndFilters();
  setupAuth();
  setupCustomChartsModal();

  initUpload({
    onUploadSuccess: refreshData,
    getSessionContext,
    showToast,
  });

  window.chatController = initChat({
    getSessionContext,
    setConversationId,
    showToast,
  });

  // Check auth session on boot
  await checkAuthSession();
  await checkHealth();

  setInterval(checkHealth, 60000);
});

// ─────────────────────────────────────────────────────────────────────────────
// Authentication Folio Controller
// ─────────────────────────────────────────────────────────────────────────────

export function setAuthState(user) {
  state.user = user;
  const userBadgeContainer = document.getElementById("user-badge-container");
  const userEmailDisplay = document.getElementById("user-display-email");
  const authOverlay = document.getElementById("auth-overlay");

  if (user) {
    state.userId = user.id;
    localStorage.setItem("invoice_app_user_id", user.id);
    if (userEmailDisplay) userEmailDisplay.textContent = user.email || "";
    if (userBadgeContainer) userBadgeContainer.style.display = "flex";
    if (authOverlay) authOverlay.classList.add("hidden");
  } else {
    state.userId = "default_user";
    localStorage.removeItem("invoice_app_user_id");
    if (userEmailDisplay) userEmailDisplay.textContent = "";
    if (userBadgeContainer) userBadgeContainer.style.display = "none";
    if (authOverlay) authOverlay.classList.remove("hidden");
    showAuthScreen("login");
  }
}

async function checkAuthSession() {
  try {
    const res = await api.getMe();
    if (res && res.status === "success" && res.user) {
      setAuthState(res.user);
      await refreshData();
      return;
    }
  } catch (err) {
    console.warn("User session unauthenticated, displaying login folio:", err);
  }

  // Not authenticated: cleanly reset visual state and show login screen
  setAuthState(null);
}

function showAuthScreen(screenName) {
  const screens = ["login", "signup", "verify", "forgot", "reset"];
  screens.forEach((s) => {
    const el = document.getElementById(`auth-screen-${s}`);
    if (el) el.style.display = s === screenName ? "block" : "none";
  });

  // Clear notices
  hideNotice("login-unverified-notice");
  hideNotice("login-error-notice");
  hideNotice("signup-error-notice");
  hideNotice("verify-error-stamp");
  hideNotice("verify-success-stamp");
  hideNotice("forgot-notice");
  hideNotice("reset-error-stamp");

  // Specific screen setups
  if (screenName === "verify") {
    const emailEl = document.getElementById("verify-email-display");
    if (emailEl) emailEl.textContent = state.pendingEmail || "your email";
    startResendCountdown(60);
    clearOtpBoxes();
    const firstBox = document.querySelector(".otp-digit[data-idx='0']");
    if (firstBox) firstBox.focus();
  }
}

function showNotice(id, text = "") {
  const el = document.getElementById(id);
  if (!el) return;
  if (text) {
    const desc = el.querySelector(".stamp-desc");
    if (desc) desc.textContent = text;
  }
  el.style.display = "flex";
}

function hideNotice(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = "none";
}

function startResendCountdown(seconds = 60) {
  clearInterval(state.resendCountdownTimer);
  state.resendSeconds = seconds;

  const timerSpan = document.getElementById("resend-timer-seconds");
  const resendContainer = document.getElementById("resend-container");
  const resendBtn = document.getElementById("btn-trigger-resend");

  if (resendContainer) resendContainer.style.display = "inline";
  if (resendBtn) resendBtn.style.display = "none";
  if (timerSpan) timerSpan.textContent = state.resendSeconds;

  state.resendCountdownTimer = setInterval(() => {
    state.resendSeconds--;
    if (timerSpan) timerSpan.textContent = state.resendSeconds;

    if (state.resendSeconds <= 0) {
      clearInterval(state.resendCountdownTimer);
      if (resendContainer) resendContainer.style.display = "none";
      if (resendBtn) resendBtn.style.display = "inline";
    }
  }, 1000);
}

function clearOtpBoxes() {
  document.querySelectorAll(".otp-digit").forEach((box) => {
    box.value = "";
  });
}

function setupAuth() {
  // Navigation between screens
  document.getElementById("btn-goto-signup")?.addEventListener("click", () => showAuthScreen("signup"));
  document.getElementById("btn-goto-login")?.addEventListener("click", () => showAuthScreen("login"));
  document.getElementById("btn-goto-forgot")?.addEventListener("click", () => showAuthScreen("forgot"));
  document.getElementById("btn-verify-back-login")?.addEventListener("click", () => showAuthScreen("login"));
  document.getElementById("btn-forgot-back-login")?.addEventListener("click", () => showAuthScreen("login"));
  document.getElementById("btn-reset-back-login")?.addEventListener("click", () => showAuthScreen("login"));
  document.getElementById("btn-login-go-verify")?.addEventListener("click", () => showAuthScreen("verify"));

  // Logout button
  document.getElementById("btn-logout")?.addEventListener("click", async () => {
    try {
      await api.logout();
      setAuthState(null);
      showToast("Auditor folio logged out.", "info");
    } catch (err) {
      setAuthState(null);
      showToast("Error during logout.", "error");
    }
  });

  // Session expired event from api.js interceptor
  window.addEventListener("session-expired", () => {
    setAuthState(null);
    showToast("Session expired. Please sign in again.", "info");
  });

  // Login Form
  const formLogin = document.getElementById("form-login");
  if (formLogin) {
    formLogin.addEventListener("submit", async (e) => {
      e.preventDefault();
      hideNotice("login-error-notice");
      hideNotice("login-unverified-notice");

      const email = document.getElementById("login-email")?.value.trim() || "";
      const password = document.getElementById("login-password")?.value || "";

      try {
        const res = await api.login({ email, password });
        if (res.status === "success") {
          setAuthState(res.user);
          showToast("Authorized session established.", "success");
          await refreshData();
        }
      } catch (err) {
        if (err.status === 403 && (err.data?.error === "unverified" || err.data?.detail?.error === "unverified")) {
          state.pendingEmail = email;
          showNotice("login-unverified-notice");
        } else {
          const msg = err.data?.error || err.data?.detail?.error || err.data?.detail || "Invalid email or password.";
          showNotice("login-error-notice", msg);
        }
      }
    });
  }

  // Signup Form
  const formSignup = document.getElementById("form-signup");
  if (formSignup) {
    formSignup.addEventListener("submit", async (e) => {
      e.preventDefault();
      hideNotice("signup-error-notice");

      const email = document.getElementById("signup-email")?.value.trim() || "";
      const password = document.getElementById("signup-password")?.value || "";

      try {
        const res = await api.signup({ email, password });
        if (res.status === "success") {
          state.pendingEmail = email;
          showToast("Verification code dispatched via email.", "success");
          showAuthScreen("verify");
        }
      } catch (err) {
        const msg = err.data?.error || err.data?.detail?.error || err.data?.detail || "Registration failed.";
        showNotice("signup-error-notice", msg);
      }
    });
  }

  // 6-Box OTP Auto-Advance, Backspace, and Paste
  const otpBoxes = Array.from(document.querySelectorAll(".otp-digit"));
  otpBoxes.forEach((box, idx) => {
    box.addEventListener("input", (e) => {
      const val = e.target.value.replace(/\D/g, "");
      e.target.value = val ? val[val.length - 1] : "";
      if (e.target.value && idx < otpBoxes.length - 1) {
        otpBoxes[idx + 1].focus();
      }
    });

    box.addEventListener("keydown", (e) => {
      if (e.key === "Backspace" && !box.value && idx > 0) {
        otpBoxes[idx - 1].focus();
      }
    });

    box.addEventListener("paste", (e) => {
      e.preventDefault();
      const pasted = (e.clipboardData || window.clipboardData).getData("text") || "";
      const digits = pasted.replace(/\D/g, "").slice(0, 6);
      digits.split("").forEach((d, i) => {
        if (otpBoxes[i]) otpBoxes[i].value = d;
      });
      const targetFocus = Math.min(digits.length, otpBoxes.length - 1);
      if (otpBoxes[targetFocus]) otpBoxes[targetFocus].focus();
    });
  });

  // Verify OTP Form
  const formVerify = document.getElementById("form-verify-otp");
  if (formVerify) {
    formVerify.addEventListener("submit", async (e) => {
      e.preventDefault();
      hideNotice("verify-error-stamp");

      const code = otpBoxes.map((b) => b.value.trim()).join("");
      if (code.length !== 6) {
        showNotice("verify-error-stamp", "Please enter the complete 6-digit code.");
        return;
      }

      try {
        const res = await api.verifyOtp({
          email: state.pendingEmail,
          code,
          purpose: "email_verification",
        });

        if (res.status === "success") {
          showToast("Account verified successfully.", "success");
          // Re-fetch profile to set authenticated user state
          const meRes = await api.getMe();
          setAuthState(meRes.user);
          await refreshData();
        }
      } catch (err) {
        showNotice("verify-error-stamp", "Invalid or expired authorization code.");
        clearOtpBoxes();
        if (otpBoxes[0]) otpBoxes[0].focus();
      }
    });
  }

  // Resend OTP Button
  document.getElementById("btn-trigger-resend")?.addEventListener("click", async () => {
    hideNotice("verify-error-stamp");
    hideNotice("verify-success-stamp");

    try {
      const res = await api.resendOtp({ email: state.pendingEmail, purpose: "email_verification" });
      showNotice("verify-success-stamp", "A fresh verification code has been dispatched.");
      startResendCountdown(60);
    } catch (err) {
      if (err.status === 429) {
        const remaining = err.data?.retry_after || err.data?.detail?.retry_after || 60;
        startResendCountdown(remaining);
        showNotice("verify-error-stamp", `Please wait ${remaining} seconds before resending.`);
      } else {
        showNotice("verify-error-stamp", "Could not dispatch email. Please retry shortly.");
      }
    }
  });

  // Forgot Password Form
  const formForgot = document.getElementById("form-forgot");
  if (formForgot) {
    formForgot.addEventListener("submit", async (e) => {
      e.preventDefault();
      const email = document.getElementById("forgot-email")?.value.trim() || "";
      state.pendingEmail = email;

      try {
        await api.forgotPassword({ email });
        showNotice("forgot-notice");
        setTimeout(() => {
          showAuthScreen("reset");
        }, 1500);
      } catch (err) {
        showNotice("forgot-notice");
        setTimeout(() => {
          showAuthScreen("reset");
        }, 1500);
      }
    });
  }

  // Reset Password Form
  const formReset = document.getElementById("form-reset");
  if (formReset) {
    formReset.addEventListener("submit", async (e) => {
      e.preventDefault();
      hideNotice("reset-error-stamp");

      const code = document.getElementById("reset-code")?.value.trim() || "";
      const newPassword = document.getElementById("reset-new-password")?.value || "";

      try {
        const res = await api.resetPassword({
          email: state.pendingEmail,
          code,
          new_password: newPassword,
        });
        if (res.status === "success") {
          showToast("Passphrase updated successfully. Please log in.", "success");
          showAuthScreen("login");
        }
      } catch (err) {
        showNotice("reset-error-stamp", "Invalid or expired recovery code.");
      }
    });
  }

  // Password Visibility Toggles
  const passwordToggleBtns = document.querySelectorAll(".password-toggle-btn");
  passwordToggleBtns.forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const wrapper = btn.closest(".password-input-wrapper");
      if (!wrapper) return;
      const input = wrapper.querySelector("input");
      if (!input) return;

      const isPassword = input.type === "password";
      input.type = isPassword ? "text" : "password";

      const showIcon = btn.querySelector(".eye-show");
      const hideIcon = btn.querySelector(".eye-hide");
      if (showIcon && hideIcon) {
        showIcon.style.display = isPassword ? "none" : "block";
        hideIcon.style.display = isPassword ? "block" : "none";
      }

      const label = isPassword ? "Hide passphrase" : "Show passphrase";
      btn.setAttribute("title", label);
      btn.setAttribute("aria-label", label);
    });
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// View Router
// ─────────────────────────────────────────────────────────────────────────────
function setupNavigation() {
  const navItems = document.querySelectorAll(".nav-item[data-view]");
  const views = document.querySelectorAll(".view-section");
  const breadcrumb = document.getElementById("header-breadcrumb-title");

  navItems.forEach((item) => {
    item.addEventListener("click", () => {
      const targetView = item.getAttribute("data-view");
      switchView(targetView);
    });
  });

  const btnHeaderUpload = document.getElementById("btn-header-upload");
  if (btnHeaderUpload) {
    btnHeaderUpload.addEventListener("click", () => switchView("upload"));
  }

  const btnDispatchFileInvoice = document.getElementById("btn-dispatch-file-invoice");
  if (btnDispatchFileInvoice) {
    btnDispatchFileInvoice.addEventListener("click", (e) => {
      e.preventDefault();
      switchView("upload");
    });
  }

  window.switchView = switchView;

  function switchView(viewName) {
    state.activeView = viewName;

    navItems.forEach((it) => {
      it.classList.toggle("active", it.getAttribute("data-view") === viewName);
    });

    views.forEach((v) => {
      v.classList.toggle("active", v.id === `view-${viewName}`);
    });

    if (breadcrumb) {
      const titles = {
        dashboard: "LEDGER AUDIT & ANALYTICS",
        chat: "INTELLIGENCE COPILOT MEMOS",
        upload: "INCOMING VOUCHER INTAKE",
        ledger: "GENERAL INVOICE LEDGER",
      };
      breadcrumb.textContent = titles[viewName] || "LEDGER AUDIT";
    }

    if (viewName === "dashboard") {
      setTimeout(() => charts.renderAll(state.invoices), 50);
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Data Fetching & Dashboard Calculations
// ─────────────────────────────────────────────────────────────────────────────
async function refreshData() {
  try {
    const res = await api.getInvoices(true);
    state.invoices = res.invoices || [];

    updateDashboardMetrics(state.invoices);
    charts.renderAll(state.invoices);
    renderRecentInvoices(state.invoices);
    renderLedgerTable(state.invoices);
    await loadCustomDashboardCharts();

    if (window.chatController && state.user) {
      window.chatController.loadConversations();
    }
  } catch (err) {
    if (err && (err.status === 401 || (err.message && err.message.includes("401")))) {
      setAuthState(null);
    } else {
      showToast("Failed to retrieve ledger entries.", "error");
    }
  }
}

function updateDashboardMetrics(invoices) {
  let totalSpend = 0;
  let paidCount = 0;
  let unpaidCount = 0;
  const vendors = new Set();

  invoices.forEach((inv) => {
    const amt = parseFloat(inv.total_amount) || 0;
    totalSpend += amt;

    const vendor = cleanVendorName(inv.vendor_name || inv.seller);
    if (vendor && vendor !== "Unknown Vendor" && vendor !== "Unknown" && vendor !== "N/A") {
      vendors.add(vendor);
    }

    const st = (inv.payment_status || "").toLowerCase();
    if (st.includes("paid") && !st.includes("unpaid")) {
      paidCount++;
    } else {
      unpaidCount++;
    }
  });

  const totalCount = invoices.length;
  const avgSpend = totalCount > 0 ? totalSpend / totalCount : 0;
  const paidRatio = totalCount > 0 ? Math.round((paidCount / totalCount) * 100) : 0;

  const elTotalSpend = document.getElementById("stat-total-spend");
  const elAvgSpend = document.getElementById("stat-avg-spend");
  const elTotalCount = document.getElementById("stat-total-count");
  const elPaidRatio = document.getElementById("stat-paid-ratio");
  const elVendorsCount = document.getElementById("stat-vendors-count");

  if (elTotalSpend) elTotalSpend.textContent = formatRupee(totalSpend);
  if (elAvgSpend) elAvgSpend.textContent = formatRupee(avgSpend);
  if (elTotalCount) elTotalCount.textContent = totalCount.toString();
  if (elPaidRatio) elPaidRatio.textContent = `${paidRatio}%`;
  if (elVendorsCount) elVendorsCount.textContent = vendors.size.toString();
}

function renderRecentInvoices(invoices) {
  const container = document.getElementById("recent-invoices-list");
  if (!container) return;

  const recent = [...invoices].slice(-5).reverse();

  if (recent.length === 0) {
    container.innerHTML = `
      <div style="padding:2rem; text-align:center; color:var(--ink-muted); font-family:var(--font-mono); font-size:0.8rem;">
        [EMPTY FOLIO] NO AUDITED VOUCHERS RECORDED IN LEDGER
      </div>
    `;
    return;
  }

  container.innerHTML = recent
    .map((inv) => {
      const invNum = inv.invoice_number || "N/A";
      const vendor = cleanVendorName(inv.vendor_name || inv.seller);
      const date = inv.invoice_date || "N/A";
      const amt = parseFloat(inv.total_amount) || 0;
      const statusClass = getStatusClass(inv.payment_status);
      const statusLabel = (inv.payment_status || "UNPAID").toUpperCase();

      return `
        <div class="recent-invoice-item">
          <div class="recent-inv-left">
            <span class="recent-inv-number">${escapeHtml(invNum)}</span>
            <span class="recent-inv-vendor">${escapeHtml(vendor)}</span>
          </div>
          <div style="font-family:var(--font-mono); font-size:0.75rem; color:var(--ink-muted);">
            ${escapeHtml(date)}
          </div>
          <div class="recent-inv-right">
            <span class="recent-inv-amount">${formatRupee(amt)}</span>
            <span class="stamp-badge ${statusClass}">${statusLabel}</span>
          </div>
        </div>
      `;
    })
    .join("");
}

function renderLedgerTable(invoices) {
  const tbody = document.getElementById("ledger-table-body");
  const countEl = document.getElementById("ledger-records-count");
  if (!tbody) return;

  let filtered = [...invoices];

  if (state.currentStatusFilter !== "all") {
    filtered = filtered.filter((inv) => {
      const st = (inv.payment_status || "").toLowerCase();
      if (state.currentStatusFilter === "paid") return st.includes("paid") && !st.includes("unpaid");
      if (state.currentStatusFilter === "unpaid") return !st.includes("paid") || st.includes("unpaid");
      if (state.currentStatusFilter === "overdue") return st.includes("overdue");
      return true;
    });
  }

  if (state.searchQuery) {
    const q = state.searchQuery.toLowerCase();
    filtered = filtered.filter((inv) => {
      const vendorClean = cleanVendorName(inv.vendor_name || inv.seller);
      return (
        (inv.invoice_number || "").toLowerCase().includes(q) ||
        vendorClean.toLowerCase().includes(q) ||
        (inv.buyer_name || "").toLowerCase().includes(q) ||
        (inv.file_name || "").toLowerCase().includes(q)
      );
    });
  }

  if (countEl) countEl.textContent = `${filtered.length} RECORD${filtered.length === 1 ? "" : "S"}`;

  if (filtered.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="7" style="text-align:center; padding:3rem 1rem; color:var(--ink-muted); font-family:var(--font-mono); font-size:0.82rem;">
          [NO LEDGER ENTRIES FOUND MATCHING RECORD CRITERIA]
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = filtered
    .map((inv) => {
      const docId = inv._doc_id || "";
      const invNum = inv.invoice_number || "N/A";
      const vendor = cleanVendorName(inv.vendor_name || inv.seller);
      const buyer = inv.buyer_name || inv.customer || "N/A";
      const date = inv.invoice_date || "N/A";
      const amt = parseFloat(inv.total_amount) || 0;
      const statusClass = getStatusClass(inv.payment_status);
      const statusLabel = (inv.payment_status || "UNPAID").toUpperCase();

      return `
        <tr>
          <td style="font-family:var(--font-mono); font-weight:600; color:var(--ink);">${escapeHtml(invNum)}</td>
          <td style="font-weight:600;">${escapeHtml(vendor)}</td>
          <td style="color:var(--ink-muted);">${escapeHtml(buyer)}</td>
          <td style="font-family:var(--font-mono); font-size:0.75rem;">${escapeHtml(date)}</td>
          <td style="font-family:var(--font-mono); font-weight:700; text-align:right;">
            ${formatRupee(amt)}
          </td>
          <td><span class="stamp-badge ${statusClass}">${statusLabel}</span></td>
          <td style="text-align:center;">
            <button class="btn-action-delete" data-doc-id="${escapeHtml(docId)}" data-inv-num="${escapeHtml(invNum)}" title="Expunge voucher record">
              &times;
            </button>
          </td>
        </tr>
      `;
    })
    .join("");

  tbody.querySelectorAll(".btn-action-delete").forEach((btn) => {
    btn.addEventListener("click", () => {
      const docId = btn.getAttribute("data-doc-id");
      const invNum = btn.getAttribute("data-inv-num");
      openDeleteModal(docId, invNum);
    });
  });
}

function setupSearchAndFilters() {
  const searchInput = document.getElementById("ledger-search-input");
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      state.searchQuery = e.target.value.trim();
      renderLedgerTable(state.invoices);
    });
  }

  const filterChips = document.querySelectorAll(".filter-chip[data-status]");
  filterChips.forEach((chip) => {
    chip.addEventListener("click", () => {
      filterChips.forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      state.currentStatusFilter = chip.getAttribute("data-status");
      renderLedgerTable(state.invoices);
    });
  });
}

function setupDeleteModal() {
  const modal = document.getElementById("delete-modal-overlay");
  const btnCancel = document.getElementById("btn-modal-cancel");
  const btnConfirm = document.getElementById("btn-modal-confirm");

  function closeModal() {
    if (modal) modal.classList.remove("active");
    state.pendingDeleteDocId = null;
  }

  if (btnCancel) btnCancel.addEventListener("click", closeModal);
  if (modal) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeModal();
    });
  }

  if (btnConfirm) {
    btnConfirm.addEventListener("click", async () => {
      if (!state.pendingDeleteDocId) return;
      const docId = state.pendingDeleteDocId;
      closeModal();

      try {
        showToast("Expunging entry from ledger...", "info");
        await api.deleteInvoice(docId, state.conversationId, state.userId);
        showToast("Entry deleted successfully.", "success");
        await refreshData();
      } catch (err) {
        showToast(err.message || "Failed to delete invoice.", "error");
      }
    });
  }
}

function openDeleteModal(docId, invNum) {
  const modal = document.getElementById("delete-modal-overlay");
  const textEl = document.getElementById("delete-modal-inv-num");
  if (!modal) return;

  state.pendingDeleteDocId = docId;
  if (textEl) textEl.textContent = invNum ? `"${invNum}"` : "this record";
  modal.classList.add("active");
}

async function checkHealth() {
  const dot = document.getElementById("health-status-dot");
  const text = document.getElementById("health-status-text");
  try {
    const res = await api.getHealth();
    if (res.status === "ok") {
      if (dot) dot.style.backgroundColor = "var(--stamp-green)";
      if (text) text.textContent = "LEDGER LIVE";
    } else {
      throw new Error();
    }
  } catch {
    if (dot) dot.style.backgroundColor = "var(--stamp-red)";
    if (text) text.textContent = "OFFLINE";
  }
}

function getStatusClass(status) {
  const st = (status || "").toLowerCase();
  if (st.includes("paid") && !st.includes("unpaid")) return "paid";
  if (st.includes("overdue")) return "overdue";
  return "unpaid";
}

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// ─────────────────────────────────────────────────────────────────────────────
// Pinned Custom Charts Folio Controller
// ─────────────────────────────────────────────────────────────────────────────

async function loadCustomDashboardCharts() {
  const grid = document.getElementById("dashboard-custom-charts-grid");
  const countBadge = document.getElementById("custom-charts-count-badge");
  if (!grid) return;

  try {
    const res = await api.getDashboardCharts();
    const chartsList = res.charts || [];

    if (countBadge) {
      countBadge.textContent = `${chartsList.length} PINNED`;
    }

    if (chartsList.length === 0) {
      grid.innerHTML = `
        <div class="custom-charts-empty-state" id="custom-charts-empty">
          <div style="font-family: var(--font-mono); font-size: 0.76rem; color: var(--ink-muted); margin-bottom: 0.35rem;">
            [NO CUSTOM GRAPHS PINNED TO LEDGER]
          </div>
          <p style="font-size: 0.78rem; color: var(--ink-muted); max-width: 480px; margin: 0 auto 0.85rem auto; line-height: 1.4;">
            Generate any custom analysis (e.g. major vendors, payment trends, line-item costs) and pin it here. Your custom charts remain permanently saved across sessions.
          </p>
          <button class="btn-secondary" id="btn-empty-create-chart" style="font-size: 0.72rem; padding: 0.35rem 0.75rem;">
            + PIN FIRST GRAPH TO DASHBOARD
          </button>
        </div>
      `;
      const btnEmpty = document.getElementById("btn-empty-create-chart");
      if (btnEmpty) {
        btnEmpty.addEventListener("click", openCustomChartModal);
      }
      return;

    }

    grid.innerHTML = chartsList
      .map((item) => {
        const id = item.id;
        const title = escapeHtml(item.title || "Custom Analytics Dispatch");
        const prompt = escapeHtml(item.prompt || "");
        const chartPath = item.chart_path;
        const dateStr = item.created_at ? new Date(item.created_at).toLocaleDateString() : "";

        return `
          <div class="custom-chart-card" id="card-${id}">
            <div class="custom-chart-header">
              <div>
                <div class="custom-chart-title">${title}</div>
                <div class="custom-chart-prompt" title="${prompt}">PROMPT: ${prompt} &bull; ${dateStr}</div>
              </div>
              <div class="custom-chart-actions">
                <a href="${chartPath}" target="_blank" class="btn-secondary" style="text-decoration:none; padding:0.25rem 0.55rem; font-size:0.68rem; font-family:var(--font-mono);">
                  EXPAND ↗
                </a>
                <button type="button" class="btn-secondary btn-delete-custom-chart" data-id="${id}" style="padding:0.25rem 0.55rem; font-size:0.68rem; color:var(--stamp-red); border-color:var(--hairline); font-family:var(--font-mono);">
                  DELETE ✕
                </button>
              </div>
            </div>
            <div class="custom-chart-frame-wrapper">
              <iframe src="${chartPath}" loading="lazy"></iframe>
            </div>
          </div>
        `;
      })
      .join("");

    // Attach delete listeners with two-step tactile confirmation
    grid.querySelectorAll(".btn-delete-custom-chart").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const chartId = btn.getAttribute("data-id");
        if (!btn.dataset.confirming) {
          btn.dataset.confirming = "true";
          btn.textContent = "CONFIRM? ✕";
          btn.style.color = "#fff";
          btn.style.backgroundColor = "var(--stamp-red)";
          btn.style.borderColor = "var(--stamp-red)";

          // Reset if not clicked within 4 seconds
          setTimeout(() => {
            if (btn && btn.dataset.confirming) {
              delete btn.dataset.confirming;
              btn.textContent = "DELETE ✕";
              btn.style.color = "var(--stamp-red)";
              btn.style.backgroundColor = "transparent";
              btn.style.borderColor = "var(--hairline)";
            }
          }, 4000);
          return;
        }

        btn.disabled = true;
        btn.textContent = "DELETING...";
        try {
          await api.deleteDashboardChart(chartId);
          showToast("Custom graph unpinned from dashboard", "info");
          await loadCustomDashboardCharts();
        } catch (err) {
          showToast(err.message || "Failed to remove chart", "error");
          delete btn.dataset.confirming;
          btn.disabled = false;
          btn.textContent = "DELETE ✕";
          btn.style.color = "var(--stamp-red)";
          btn.style.backgroundColor = "transparent";
          btn.style.borderColor = "var(--hairline)";
        }
      });
    });
  } catch (err) {
    console.warn("Could not load custom dashboard charts:", err);
  }
}

export function openCustomChartModal() {
  const modal = document.getElementById("modal-custom-chart");
  const promptInput = document.getElementById("input-custom-chart-prompt");
  const titleInput = document.getElementById("input-custom-chart-title");
  const statusNotice = document.getElementById("custom-chart-status-notice");
  if (!modal) return;
  modal.style.display = "flex";
  if (statusNotice) statusNotice.style.display = "none";
  if (promptInput) {
    promptInput.value = "";
    promptInput.focus();
  }
  if (titleInput) titleInput.value = "";
}

export function closeCustomChartModal() {
  const modal = document.getElementById("modal-custom-chart");
  if (!modal) return;
  modal.style.display = "none";
}

window.openCustomChartModal = openCustomChartModal;
window.closeCustomChartModal = closeCustomChartModal;

function setupCustomChartsModal() {
  const modal = document.getElementById("modal-custom-chart");
  const btnOpen = document.getElementById("btn-open-custom-chart-modal");
  const btnEmpty = document.getElementById("btn-empty-create-chart");
  const btnClose = document.getElementById("btn-close-chart-modal");
  const btnCancel = document.getElementById("btn-cancel-custom-chart");
  const btnSubmit = document.getElementById("btn-submit-custom-chart");
  const spinner = document.getElementById("btn-submit-chart-spinner");
  const promptInput = document.getElementById("input-custom-chart-prompt");
  const titleInput = document.getElementById("input-custom-chart-title");
  const statusNotice = document.getElementById("custom-chart-status-notice");
  const templateChips = document.querySelectorAll(".chart-template-chip");

  if (btnOpen) btnOpen.addEventListener("click", openCustomChartModal);
  if (btnEmpty) btnEmpty.addEventListener("click", openCustomChartModal);
  if (btnClose) btnClose.addEventListener("click", closeCustomChartModal);
  if (btnCancel) btnCancel.addEventListener("click", closeCustomChartModal);


  // Quick template chips
  templateChips.forEach((chip) => {
    chip.addEventListener("click", () => {
      const p = chip.getAttribute("data-prompt");
      if (promptInput && p) {
        promptInput.value = p;
        if (titleInput) titleInput.value = chip.textContent.trim();
        promptInput.focus();
      }
    });
  });

  // Modal Submit
  if (btnSubmit) {
    btnSubmit.addEventListener("click", async () => {
      const prompt = promptInput ? promptInput.value.trim() : "";
      const title = titleInput ? titleInput.value.trim() : "";

      if (!prompt) {
        if (statusNotice) {
          statusNotice.style.display = "block";
          statusNotice.style.color = "var(--stamp-red)";
          statusNotice.textContent = "[REQUIRED] Please specify an analytical prompt for the chart.";
        }
        return;
      }

      btnSubmit.disabled = true;
      if (spinner) spinner.style.display = "inline";
      if (statusNotice) {
        statusNotice.style.display = "block";
        statusNotice.style.color = "var(--stamp-amber)";
        statusNotice.textContent = "ANALYZING LEDGER & DRAFTING VISUALIZATION WITH AI... (Takes ~5-10s)";
      }

      try {
        const res = await api.createDashboardChart(prompt, title);
        if (res.status === "success") {
          showToast("Custom graph pinned to dashboard folio!", "success");
          closeCustomChartModal();
          await loadCustomDashboardCharts();
        } else {
          throw new Error(res.error || "Failed to generate chart.");
        }
      } catch (err) {
        if (statusNotice) {
          statusNotice.style.display = "block";
          statusNotice.style.color = "var(--stamp-red)";
          statusNotice.textContent = `[FAILED] ${err.message || "Could not generate chart. Please adjust prompt."}`;
        }
      } finally {
        btnSubmit.disabled = false;
        if (spinner) spinner.style.display = "none";
      }
    });
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Robust Vendor Name Cleaner & Currency Formatter
// ─────────────────────────────────────────────────────────────────────────────
export function cleanVendorName(raw) {
  if (!raw) return "Unknown Vendor";
  if (typeof raw === "object") {
    return raw.name || raw.vendor_name || raw.company_name || raw.seller || raw.title || "Unknown Vendor";
  }
  let str = String(raw).trim();
  if (!str || str.toLowerCase() === "unknown" || str.toLowerCase() === "unknown vendor" || str.toLowerCase() === "n/a") {
    return "Unknown Vendor";
  }
  if ((str.startsWith("{") && str.endsWith("}")) || (str.startsWith("(") && str.endsWith(")"))) {
    try {
      const normalized = str.replace(/'/g, '"');
      const parsed = JSON.parse(normalized);
      if (typeof parsed === "object" && parsed !== null) {
        return parsed.name || parsed.vendor_name || parsed.company_name || parsed.seller || "Unknown Vendor";
      }
    } catch (e) {
      const match = str.match(/['"](?:name|vendor_name|company|seller)['"]\s*:\s*['"]([^'"]+)['"]/i);
      if (match && match[1]) return match[1].trim();
    }
  }
  return str;
}

export function formatRupee(amount) {
  const num = parseFloat(amount) || 0;
  return `₹${num.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
