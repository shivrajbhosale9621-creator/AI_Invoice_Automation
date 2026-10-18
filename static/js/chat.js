/**
 * chat.js — Conversational AI Copilot on Paper
 * User messages as ink-filled slips, AI responses as typed paper memos.
 */

import { api } from "./api.js";

export function initChat({ getSessionContext, setConversationId, showToast }) {
  const messagesContainer = document.getElementById("chat-messages");
  const textarea = document.getElementById("chat-textarea");
  const btnSend = document.getElementById("btn-chat-send");
  const btnAttach = document.getElementById("btn-chat-attach");
  const fileInput = document.getElementById("file-input-chat");
  const attachmentPreview = document.getElementById("chat-attachment-preview");
  const attachmentName = document.getElementById("chat-attachment-name");
  const btnRemoveAttach = document.getElementById("btn-remove-attachment");
  const btnNewChat = document.getElementById("btn-new-chat");
  const convListEl = document.getElementById("chat-conversations-list");
  const promptChips = document.querySelectorAll(".quick-prompt-chip");

  let attachedFile = null;

  // 1. Initial Load of user conversations
  loadConversations();

  // 2. New Chat Button
  if (btnNewChat) {
    btnNewChat.addEventListener("click", () => {
      const newId = "conv_" + Math.random().toString(36).substring(2, 10);
      setConversationId(newId);
      clearMessages();
      appendAssistantMessage(
        "**MEMO &bull; AI COPILOT INITIALIZED**\n\nI am connected to your invoice ledger and vector archive. You may query existing entries, request stamped Excel schedules, or attach a document for immediate analysis."
      );
      highlightActiveConversation(newId);
      showToast("Opened new ledger memo thread", "info");
    });
  }

  // 3. Quick Prompt Chips
  promptChips.forEach((chip) => {
    chip.addEventListener("click", () => {
      const prompt = chip.getAttribute("data-prompt") || chip.textContent.trim();
      if (textarea) {
        textarea.value = prompt;
        sendMessage();
      }
    });
  });

  // 4. File Attachment Handler
  if (btnAttach && fileInput) {
    btnAttach.addEventListener("click", () => fileInput.click());

    fileInput.addEventListener("change", () => {
      if (fileInput.files && fileInput.files.length > 0) {
        setAttachedFile(fileInput.files[0]);
      }
    });
  }

  if (btnRemoveAttach) {
    btnRemoveAttach.addEventListener("click", () => {
      clearAttachedFile();
    });
  }

  // 5. Send message on Enter (Shift+Enter for newline)
  if (textarea) {
    textarea.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    textarea.addEventListener("input", () => {
      textarea.style.height = "auto";
      textarea.style.height = Math.min(textarea.scrollHeight, 120) + "px";
    });
  }

  if (btnSend) {
    btnSend.addEventListener("click", () => sendMessage());
  }

  function setAttachedFile(file) {
    attachedFile = file;
    if (attachmentPreview && attachmentName) {
      attachmentName.textContent = `ATTACHED: ${file.name} [${(file.size / 1024).toFixed(1)} KB]`;
      attachmentPreview.style.display = "flex";
    }
  }

  function clearAttachedFile() {
    attachedFile = null;
    if (fileInput) fileInput.value = "";
    if (attachmentPreview) attachmentPreview.style.display = "none";
  }

  // 6. Sending Messages
  async function sendMessage() {
    const text = textarea ? textarea.value.trim() : "";
    if (!text && !attachedFile) return;

    const { userId, conversationId } = getSessionContext();
    const currentFile = attachedFile;

    // Append User Message as Ink-Filled Slip
    let userDisplay = text;
    if (currentFile) {
      userDisplay += `\n\n*[Attached Document: ${currentFile.name}]*`;
    }
    appendUserMessage(userDisplay);

    if (textarea) {
      textarea.value = "";
      textarea.style.height = "auto";
    }
    clearAttachedFile();

    // Show progressive thinking state
    const thinkingId = appendAssistantMessage("Examining ledger records & semantic index...", true);
    const thinkingStages = [
      "Examining ledger records & semantic index...",
      "Synthesizing financial entries & calculating totals...",
      "Consulting multiagent pipeline & formatting memo...",
      "Finalizing response dispatch...",
    ];
    let stageIndex = 0;
    const thinkingInterval = setInterval(() => {
      stageIndex = (stageIndex + 1) % thinkingStages.length;
      const stageTextEl = document.getElementById(`${thinkingId}-stage-text`);
      if (stageTextEl) {
        stageTextEl.style.opacity = "0.4";
        setTimeout(() => {
          if (stageTextEl) {
            stageTextEl.textContent = thinkingStages[stageIndex];
            stageTextEl.style.opacity = "1";
          }
        }, 150);
      }
    }, 2600);

    try {
      const res = await api.chat({
        message: text || "Please examine this invoice.",
        userId,
        conversationId,
        file: currentFile,
      });

      clearInterval(thinkingInterval);
      removeMessage(thinkingId);
      appendAssistantMessage(res.response || "No reply dispatched from agent.");

      if (res.conversation_id && res.conversation_id !== conversationId) {
        setConversationId(res.conversation_id);
      }

      loadConversations();
    } catch (err) {
      clearInterval(thinkingInterval);
      removeMessage(thinkingId);
      appendAssistantMessage(`[ERROR] ${err.message || "Request failed"}`);
      showToast(err.message || "Failed to reach AI agent", "error");
    }
  }

  // 7. Load Conversation History
  async function loadConversations() {
    if (!convListEl) return;
    const { userId, conversationId } = getSessionContext();

    try {
      const res = await api.getConversations(userId);
      const convs = res.conversations || [];

      convListEl.innerHTML = "";
      if (convs.length === 0) {
        convListEl.innerHTML = `
          <li style="padding: 1.5rem 1rem; text-align: center; color: var(--ink-muted); font-family: var(--font-mono); font-size: 0.72rem;">
            [ NO FILED THREADS ]
          </li>`;
        return;
      }

      convs.forEach((c) => {
        const li = document.createElement("li");
        li.className = `chat-conv-item ${c.conversation_id === conversationId ? "active" : ""}`;
        li.setAttribute("data-id", c.conversation_id);

        const dateStr = c.updated_at ? new Date(c.updated_at).toLocaleDateString() : "";
        li.innerHTML = `
          <div class="conv-item-title">${escapeHtml(c.title || "Invoice Memo")}</div>
          <div class="conv-item-date">${dateStr} &bull; ${escapeHtml(c.conversation_id.slice(0, 8))}</div>
        `;

        li.addEventListener("click", () => {
          switchConversation(c.conversation_id);
        });

        convListEl.appendChild(li);
      });
    } catch (err) {
      console.warn("Could not load conversations:", err);
    }
  }

  async function switchConversation(convId) {
    const { userId } = getSessionContext();
    setConversationId(convId);
    highlightActiveConversation(convId);

    clearMessages();
    const loadingId = appendAssistantMessage("*Retrieving transcript from archives...*", true);

    try {
      const res = await api.getChatHistory(convId, userId, 50);
      removeMessage(loadingId);

      const msgs = res.messages || [];
      if (msgs.length === 0) {
        appendAssistantMessage("**MEMO &bull; EMPTY THREAD**\n\nNo prior dispatches recorded for this session.");
        return;
      }

      msgs.forEach((m) => {
        if (m.role === "user") {
          appendUserMessage(m.content);
        } else if (m.role === "assistant") {
          appendAssistantMessage(m.content);
        } else if (m.role === "system") {
          appendSystemMessage(m.content);
        }
      });
    } catch (err) {
      removeMessage(loadingId);
      appendAssistantMessage(`Failed to retrieve archive: ${err.message}`);
    }
  }

  function highlightActiveConversation(convId) {
    if (!convListEl) return;
    const items = convListEl.querySelectorAll(".chat-conv-item");
    items.forEach((it) => {
      it.classList.toggle("active", it.getAttribute("data-id") === convId);
    });
  }

  function clearMessages() {
    if (messagesContainer) messagesContainer.innerHTML = "";
  }

  function appendUserMessage(text) {
    if (!messagesContainer) return;
    const div = document.createElement("div");
    div.className = "chat-msg user";
    div.innerHTML = `
      <div class="chat-avatar" title="User">USER</div>
      <div class="msg-body">${formatMarkdown(text)}</div>
    `;
    messagesContainer.appendChild(div);
    scrollToBottom();
  }

  function appendAssistantMessage(text, isTemporary = false) {
    if (!messagesContainer) return null;
    const id = "msg_" + Math.random().toString(36).substring(2, 9);
    const div = document.createElement("div");
    div.id = id;
    div.className = `chat-msg assistant ${isTemporary ? "is-thinking" : ""}`;

    if (isTemporary) {
      div.innerHTML = `
        <div class="chat-avatar" title="Assistant Memo">AI</div>
        <div class="msg-body">
          <div class="chat-thinking-container">
            <span class="thinking-pulse-dot"></span>
            <span class="thinking-stage-text" id="${id}-stage-text">${escapeHtml(text)}</span>
          </div>
        </div>
      `;
      messagesContainer.appendChild(div);
      scrollToBottom();
      return id;
    }

    div.innerHTML = `
      <div class="chat-avatar" title="Assistant Memo">AI</div>
      <div class="msg-body">${formatMarkdown(text)}</div>
    `;

    // Detect Excel report filename and render tear-off voucher
    const reportMatch = text.match(/([a-zA-Z0-9_\-]+\.xlsx)/);
    if (reportMatch && reportMatch[1]) {
      const filename = reportMatch[1];
      const card = createReportDownloadCard(filename);
      div.querySelector(".msg-body").appendChild(card);
    }

    // Detect Chart HTML path and render interactive frame card
    const chartRegex = /(?:\[CHART:\s*([^\]]+)\]|\[(?:[^\]]*)\]\((\/static\/charts\/[^\)]+)\))/g;
    let chartMatch;
    const detectedUrls = new Set();
    while ((chartMatch = chartRegex.exec(text)) !== null) {
      const chartUrl = (chartMatch[1] || chartMatch[2] || "").trim();
      if (chartUrl && !detectedUrls.has(chartUrl)) {
        detectedUrls.add(chartUrl);
        const card = createChartCard(chartUrl);
        div.querySelector(".msg-body").appendChild(card);
      }
    }

    messagesContainer.appendChild(div);
    scrollToBottom();
    return id;
  }

  function appendSystemMessage(text) {
    if (!messagesContainer) return;
    const div = document.createElement("div");
    div.className = "chat-msg system";
    div.style.alignSelf = "center";
    div.style.maxWidth = "90%";
    div.innerHTML = `
      <div style="background: var(--paper-card); border: 1px dashed var(--ink-muted); padding: 0.5rem 0.85rem; font-size: 0.74rem; color: var(--ink-muted); font-family: var(--font-mono);">
        [LEDGER SYSTEM LOG] ${escapeHtml(text)}
      </div>
    `;
    messagesContainer.appendChild(div);
    scrollToBottom();
  }

  function removeMessage(id) {
    if (!id) return;
    const el = document.getElementById(id);
    if (el) el.remove();
  }

  function scrollToBottom() {
    if (messagesContainer) {
      messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
  }

  function createReportDownloadCard(filename) {
    const card = document.createElement("div");
    card.className = "report-download-card";
    const downloadUrl = api.getReportDownloadUrl(filename);

    card.innerHTML = `
      <div class="report-file-meta">
        <div class="report-file-icon">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.75">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
            <polyline points="14 2 14 8 20 8"></polyline>
            <line x1="8" y1="13" x2="16" y2="13"></line>
            <line x1="8" y1="17" x2="16" y2="17"></line>
          </svg>
        </div>
        <div class="report-file-info">
          <strong>EXCEL SCHEDULE DISPATCHED</strong>
          <span>${escapeHtml(filename)}</span>
        </div>
      </div>
      <a href="${downloadUrl}" download="${filename}" class="btn-primary" style="text-decoration:none; padding: 0.35rem 0.75rem; font-size: 0.75rem;">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
          <polyline points="7 10 12 15 17 10"/>
          <line x1="12" y1="15" x2="12" y2="3"/>
        </svg>
        TEAR OFF & DOWNLOAD
      </a>
    `;
    return card;
  }

  function createChartCard(chartUrl) {
    const card = document.createElement("div");
    card.className = "report-download-card";
    card.style.flexDirection = "column";
    card.style.alignItems = "stretch";
    card.style.gap = "0.6rem";
    card.style.marginTop = "0.75rem";
    card.style.padding = "0.75rem";
    card.style.background = "var(--paper-card)";
    card.style.border = "1px solid var(--hairline)";

    card.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:center; border-bottom: 1px dashed var(--hairline); padding-bottom: 0.4rem;">
        <div style="display:flex; align-items:center; gap:0.4rem;">
          <div class="report-file-icon">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.75">
              <line x1="18" y1="20" x2="18" y2="10"></line>
              <line x1="12" y1="20" x2="12" y2="4"></line>
              <line x1="6" y1="20" x2="6" y2="14"></line>
            </svg>
          </div>
          <strong style="font-family: var(--font-mono); font-size: 0.72rem; letter-spacing: 0.04em;">VISUALIZATION SCHEDULE &bull; DISPATCHED</strong>
        </div>
        <a href="${chartUrl}" target="_blank" class="btn-secondary" style="text-decoration:none; padding: 0.2rem 0.55rem; font-size: 0.68rem; font-family: var(--font-mono);">
          EXPAND ↗
        </a>
      </div>
      <div style="width: 100%; height: 380px; background: #FDFBF5; border: 1px solid var(--hairline); border-radius: 2px; overflow: hidden;">
        <iframe src="${chartUrl}" style="width: 100%; height: 100%; border: none;" loading="lazy"></iframe>
      </div>
    `;
    return card;
  }

  function formatMarkdown(text) {
    let cleanText = (text || "")
      .replace(/\[CHART:\s*[^\]]+\]/g, "")
      .replace(/\[(?:[^\]]*)\]\(\/static\/charts\/[^\)]+\)/g, "")
      .trim();
    if (typeof marked !== "undefined" && typeof marked.parse === "function") {
      return marked.parse(cleanText);
    }
    let html = escapeHtml(cleanText)
      .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.*?)\*/g, "<em>$1</em>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\n\n/g, "</p><p>")
      .replace(/\n/g, "<br>");
    return `<p>${html}</p>`;
  }

  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  return {
    loadConversations,
    switchConversation,
  };
}
