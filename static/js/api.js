/**
 * api.js — Centralized HTTP Client for AutoInvoice Intelligence FastAPI Service
 * All fetch operations include credentials: "include" for secure httpOnly cookie authentication.
 * Includes automatic token rotation on 401 using the 7-day refresh token.
 */

const API_BASE = "";

let isRefreshing = false;
let refreshSubscribers = [];

function subscribeTokenRefresh(cb) {
  refreshSubscribers.push(cb);
}

function onRefreshed(error) {
  refreshSubscribers.forEach((cb) => cb(error));
  refreshSubscribers = [];
}

/**
 * Robust fetch wrapper:
 * - Injects credentials: "include"
 * - On 401, attempts transparent token refresh via /auth/refresh and retries
 * - Broadcasts 'session-expired' if refresh fails
 */
async function request(url, options = {}) {
  const mergedOptions = {
    ...options,
    credentials: "include",
  };

  let res = await fetch(url, mergedOptions);

  const isAuthEndpoint =
    url.includes("/auth/login") ||
    url.includes("/auth/signup") ||
    url.includes("/auth/refresh") ||
    url.includes("/auth/verify-otp") ||
    url.includes("/auth/resend-otp") ||
    url.includes("/auth/forgot-password") ||
    url.includes("/auth/reset-password");

  if (res.status === 401 && !isAuthEndpoint) {
    if (!isRefreshing) {
      isRefreshing = true;
      try {
        const refreshRes = await fetch(`${API_BASE}/auth/refresh`, {
          method: "POST",
          credentials: "include",
        });

        if (refreshRes.ok) {
          isRefreshing = false;
          onRefreshed(null);
          return await fetch(url, mergedOptions);
        } else {
          isRefreshing = false;
          onRefreshed(new Error("Refresh failed"));
          window.dispatchEvent(new CustomEvent("session-expired"));
          return res;
        }
      } catch (err) {
        isRefreshing = false;
        onRefreshed(err);
        window.dispatchEvent(new CustomEvent("session-expired"));
        return res;
      }
    } else {
      return new Promise((resolve) => {
        subscribeTokenRefresh((err) => {
          if (!err) {
            resolve(fetch(url, mergedOptions));
          } else {
            resolve(res);
          }
        });
      });
    }
  }

  return res;
}

export const api = {
  /**
   * Liveness / Health check
   */
  async getHealth() {
    try {
      const res = await request(`${API_BASE}/api/health`);
      return await res.json();
    } catch (err) {
      console.error("Health probe failed:", err);
      return { status: "error", error: err.message };
    }
  },

  // ─────────────────────────────────────────────────────────────────────────────
  // Authentication Endpoints
  // ─────────────────────────────────────────────────────────────────────────────

  /**
   * Get active user session info from access token cookie
   */
  async getMe() {
    const res = await request(`${API_BASE}/auth/me`, {
      method: "GET",
    });
    const data = await res.json();
    if (!res.ok) {
      throw { status: res.status, data };
    }
    return data;
  },

  /**
   * Register a new user
   */
  async signup({ email, password }) {
    const res = await request(`${API_BASE}/auth/signup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw { status: res.status, data };
    }
    return data;
  },

  /**
   * Verify 6-digit OTP code
   */
  async verifyOtp({ email, code, purpose = "email_verification" }) {
    const res = await request(`${API_BASE}/auth/verify-otp`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, code, purpose }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw { status: res.status, data };
    }
    return data;
  },

  /**
   * Resend OTP code
   */
  async resendOtp({ email, purpose = "email_verification" }) {
    const res = await request(`${API_BASE}/auth/resend-otp`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, purpose }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw { status: res.status, data };
    }
    return data;
  },

  /**
   * Log in user
   */
  async login({ email, password }) {
    const res = await request(`${API_BASE}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw { status: res.status, data };
    }
    return data;
  },

  /**
   * Log out user
   */
  async logout() {
    try {
      const res = await request(`${API_BASE}/auth/logout`, {
        method: "POST",
      });
      return await res.json();
    } catch (err) {
      console.error("Logout error:", err);
    }
  },

  /**
   * Request password reset code
   */
  async forgotPassword({ email }) {
    const res = await request(`${API_BASE}/auth/forgot-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw { status: res.status, data };
    }
    return data;
  },

  /**
   * Reset password with OTP
   */
  async resetPassword({ email, code, new_password }) {
    const res = await request(`${API_BASE}/auth/reset-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, code, new_password }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw { status: res.status, data };
    }
    return data;
  },

  /**
   * Rotate and refresh auth tokens
   */
  async refreshToken() {
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    });
    const data = await res.json();
    if (!res.ok) {
      throw { status: res.status, data };
    }
    return data;
  },

  // ─────────────────────────────────────────────────────────────────────────────
  // Invoice & Chat Endpoints
  // ─────────────────────────────────────────────────────────────────────────────

  /**
   * Fetch all invoices for active user from ChromaDB & SQLite
   * @param {boolean} includeIds - Include internal _doc_id for deletions
   */
  async getInvoices(includeIds = true) {
    try {
      const res = await request(`${API_BASE}/api/invoices?include_ids=${includeIds}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to load invoices");
      return data;
    } catch (err) {
      console.error("getInvoices error:", err);
      throw err;
    }
  },

  /**
   * Delete an invoice by _doc_id
   */
  async deleteInvoice(docId, conversationId = "", userId = "") {
    try {
      const params = new URLSearchParams();
      if (conversationId) params.append("conversation_id", conversationId);
      if (userId) params.append("user_id", userId);

      const qs = params.toString() ? `?${params.toString()}` : "";
      const res = await request(`${API_BASE}/api/invoice/${encodeURIComponent(docId)}${qs}`, {
        method: "DELETE",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Deletion failed");
      return data;
    } catch (err) {
      console.error("deleteInvoice error:", err);
      throw err;
    }
  },

  /**
   * Dedicated Upload Center: Fast OCR + Indexing
   */
  async uploadInvoice(file, conversationId = "", userId = "") {
    try {
      const formData = new FormData();
      formData.append("file", file);
      if (conversationId) formData.append("conversation_id", conversationId);
      if (userId) formData.append("user_id", userId);

      const res = await request(`${API_BASE}/api/invoice/upload`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Upload failed");
      return data;
    } catch (err) {
      console.error("uploadInvoice error:", err);
      throw err;
    }
  },

  /**
   * Main Conversational Agent: Handles both text and optional file attachment
   */
  async chat({ message, userId, conversationId, file = null, idempotencyKey = null }) {
    try {
      let body;
      let headers = {};

      if (file) {
        body = new FormData();
        if (userId) body.append("user_id", userId);
        body.append("message", message);
        if (conversationId) body.append("conversation_id", conversationId);
        if (idempotencyKey) body.append("idempotency_key", idempotencyKey);
        body.append("file", file);
      } else {
        headers["Content-Type"] = "application/json";
        body = JSON.stringify({
          user_id: userId,
          message: message,
          conversation_id: conversationId,
          idempotency_key: idempotencyKey,
        });
      }

      const res = await request(`${API_BASE}/api/chat`, {
        method: "POST",
        headers,
        body,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Chat agent request failed");
      return data;
    } catch (err) {
      console.error("chat error:", err);
      throw err;
    }
  },

  /**
   * Fetch conversation list for the active user
   */
  async getConversations(userId) {
    try {
      const res = await request(`${API_BASE}/api/chat/conversations`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to load conversations");
      return data;
    } catch (err) {
      console.error("getConversations error:", err);
      throw err;
    }
  },

  /**
   * Fetch message history for a specific conversation
   */
  async getChatHistory(conversationId, userId, limit = 50) {
    try {
      const res = await request(
        `${API_BASE}/api/chat/history/${encodeURIComponent(conversationId)}?limit=${limit}`
      );
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to load chat history");
      return data;
    } catch (err) {
      console.error("getChatHistory error:", err);
      throw err;
    }
  },

  /**
   * Build report download URL
   */
  getReportDownloadUrl(filename) {
    return `${API_BASE}/api/report/download/${encodeURIComponent(filename)}`;
  },

  /**
   * Get all pinned dashboard custom charts for current user
   */
  async getDashboardCharts() {
    const res = await request(`${API_BASE}/api/dashboard/charts`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to load custom charts");
    return data;
  },

  /**
   * Create and pin a new custom chart from prompt
   */
  async createDashboardChart(prompt, title = "") {
    const res = await request(`${API_BASE}/api/dashboard/charts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, title }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to generate custom chart");
    return data;
  },

  /**
   * Delete a pinned chart
   */
  async deleteDashboardChart(chartId) {
    const res = await request(`${API_BASE}/api/dashboard/charts/${encodeURIComponent(chartId)}`, {
      method: "DELETE",
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to delete custom chart");
    return data;
  },
};
