(() => {
  const state = {
    view: "agents",
    agents: [],
    selectedAgentId: null,
    conversations: [],
    selectedSessionId: null,
    convFilter: "all",
    saveTimer: null,
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { detail: text };
    }
    if (!res.ok) {
      const msg =
        (data && data.detail && (data.detail.message || data.detail)) ||
        res.statusText;
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    return data;
  }

  function estimateTokens(text) {
    const words = (text || "").trim().split(/\s+/).filter(Boolean).length;
    return Math.max(0, Math.round(words * 1.3));
  }

  function initials(name) {
    return (name || "A")
      .split(/\s+/)
      .map((p) => p[0])
      .join("")
      .slice(0, 2)
      .toUpperCase();
  }

  function formatTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    if (sameDay) {
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
    return d.toLocaleDateString([], { month: "short", day: "numeric" });
  }

  function setView(view) {
    state.view = view;
    $$(".nav-item").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.view === view);
    });
    $$(".view").forEach((el) => {
      el.classList.toggle("active", el.id === `view-${view}`);
    });
    if (view === "conversations") loadConversations();
    if (view === "agents") loadAgents();
  }

  async function loadAgents() {
    state.agents = await api("/api/v1/agents");
    $("#agentCountLabel").textContent = `All Agents (${state.agents.length})`;
    renderAgentList();
    fillTestAgentSelect();
    if (!state.selectedAgentId && state.agents[0]) {
      state.selectedAgentId = state.agents[0].id;
    }
    renderAgentDetail();
  }

  function renderAgentList() {
    const q = ($("#globalSearch").value || "").trim().toLowerCase();
    const list = $("#agentList");
    const filtered = state.agents.filter((a) => {
      if (!q) return true;
      return (
        a.name.toLowerCase().includes(q) ||
        (a.description || "").toLowerCase().includes(q)
      );
    });
    if (!filtered.length) {
      list.innerHTML = `<div class="empty-state">No agents found</div>`;
      return;
    }
    list.innerHTML = filtered
      .map(
        (a) => `
      <button type="button" class="agent-card ${
        a.id === state.selectedAgentId ? "active" : ""
      }" data-id="${a.id}">
        <div class="row">
          <span class="status-dot ${a.enabled ? "" : "off"}"></span>
          <strong>${escapeHtml(a.name)}</strong>
        </div>
        <p>${escapeHtml(a.description || "No description")}</p>
      </button>`
      )
      .join("");
    $$(".agent-card", list).forEach((btn) => {
      btn.addEventListener("click", () => {
        state.selectedAgentId = btn.dataset.id;
        renderAgentList();
        renderAgentDetail();
      });
    });
  }

  function selectedAgent() {
    return state.agents.find((a) => a.id === state.selectedAgentId) || null;
  }

  function renderAgentDetail() {
    const agent = selectedAgent();
    const panel = $("#agentDetail");
    if (!agent) {
      panel.innerHTML = `<div class="empty-state">Select an agent to edit prompts</div>`;
      return;
    }
    const tokens = estimateTokens(agent.system_prompt);
    panel.innerHTML = `
      <div class="detail-head">
        <div>
          <h2>${escapeHtml(agent.name)} <span class="badge">Admin</span></h2>
          <p>${escapeHtml(agent.description || "")}</p>
        </div>
        <label class="toggle">
          <input type="checkbox" id="agentEnabled" ${agent.enabled ? "checked" : ""} />
          Enabled
        </label>
      </div>
      <div class="tabs">
        <button type="button" class="tab active" data-tab="prompt">Prompt</button>
        <button type="button" class="tab" data-tab="voice">Voice Settings</button>
        <button type="button" class="tab" data-tab="tools">Tools <span class="tiny">Admin</span></button>
        <button type="button" class="tab" data-tab="advanced">Advanced Settings</button>
      </div>
      <div class="prompt-box" data-pane="prompt">
        <h3>Prompt Configuration <button type="button" class="btn ghost sm" id="testThisAgent">Test in Conversations</button></h3>
        <label class="field-label" for="firstMessage">First Message (Opening Gambit)</label>
        <textarea id="firstMessage" class="field" rows="2">${escapeHtml(
          agent.first_message || ""
        )}</textarea>
        <label class="field-label" for="systemPrompt">Conversation Prompt (System Role)</label>
        <textarea id="systemPrompt" class="field prompt-area">${escapeHtml(
          agent.system_prompt || ""
        )}</textarea>
        <div class="meta-row">
          <span>Est. Cost: $0.00/min (testing)</span>
          <span id="tokenCount">${tokens} Tokens</span>
        </div>
        <div class="save-row">
          <span id="saveStatus" class="save-status">Auto-save is enabled for drafts.</span>
          <div style="display:flex;gap:0.4rem">
            <button type="button" class="btn ghost sm" id="deleteAgentBtn">Delete</button>
            <button type="button" class="btn yellow sm" id="saveAgentBtn">Save now</button>
          </div>
        </div>
      </div>
      <div class="prompt-box" data-pane="voice" hidden>
        <p class="muted">Voice uses the shared ElevenLabs voice from <code>sabrah-ai/.env</code> for all agents in this MVP.</p>
      </div>
      <div class="prompt-box" data-pane="tools" hidden>
        <p class="muted">Tools are the existing Sabrah travel tools (search, book, cancel, refund). Per-agent tool toggles can come later.</p>
      </div>
      <div class="prompt-box" data-pane="advanced" hidden>
        <p class="muted">Agent id: <code>${escapeHtml(agent.id)}</code></p>
        <p class="muted">Updated: ${escapeHtml(agent.updated_at || "")}</p>
      </div>
    `;

    $$(".tab", panel).forEach((tab) => {
      tab.addEventListener("click", () => {
        $$(".tab", panel).forEach((t) => t.classList.remove("active"));
        tab.classList.add("active");
        $$("[data-pane]", panel).forEach((pane) => {
          pane.hidden = pane.dataset.pane !== tab.dataset.tab;
        });
      });
    });

    $("#agentEnabled").addEventListener("change", () => scheduleSave());
    $("#firstMessage").addEventListener("input", () => scheduleSave());
    $("#systemPrompt").addEventListener("input", () => {
      $("#tokenCount").textContent = `${estimateTokens(
        $("#systemPrompt").value
      )} Tokens`;
      scheduleSave();
    });
    $("#saveAgentBtn").addEventListener("click", () => saveAgent(true));
    $("#deleteAgentBtn").addEventListener("click", deleteSelectedAgent);
    $("#testThisAgent").addEventListener("click", () => {
      setView("conversations");
      $("#testAgentSelect").value = agent.id;
      startTestConversation();
    });
  }

  function scheduleSave() {
    const status = $("#saveStatus");
    if (status) {
      status.textContent = "Saving draft…";
      status.className = "save-status";
    }
    clearTimeout(state.saveTimer);
    state.saveTimer = setTimeout(() => saveAgent(false), 650);
  }

  async function saveAgent(manual) {
    const agent = selectedAgent();
    if (!agent) return;
    const payload = {
      name: agent.name,
      description: agent.description,
      enabled: $("#agentEnabled")?.checked ?? agent.enabled,
      first_message: $("#firstMessage")?.value ?? agent.first_message,
      system_prompt: $("#systemPrompt")?.value ?? agent.system_prompt,
    };
    try {
      const updated = await api(`/api/v1/agents/${agent.id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      const idx = state.agents.findIndex((a) => a.id === agent.id);
      state.agents[idx] = updated;
      const status = $("#saveStatus");
      if (status) {
        status.textContent = manual ? "Saved." : "Auto-saved.";
        status.className = "save-status ok";
      }
      renderAgentList();
    } catch (err) {
      const status = $("#saveStatus");
      if (status) {
        status.textContent = err.message || "Save failed";
        status.className = "save-status err";
      }
    }
  }

  async function deleteSelectedAgent() {
    const agent = selectedAgent();
    if (!agent) return;
    if (!confirm(`Delete agent “${agent.name}”?`)) return;
    try {
      await api(`/api/v1/agents/${agent.id}`, { method: "DELETE" });
      state.selectedAgentId = null;
      await loadAgents();
    } catch (err) {
      alert(err.message || "Delete failed");
    }
  }

  function fillTestAgentSelect() {
    const sel = $("#testAgentSelect");
    sel.innerHTML = state.agents
      .map(
        (a) =>
          `<option value="${a.id}">${escapeHtml(a.name)}</option>`
      )
      .join("");
  }

  async function loadConversations() {
    const data = await api("/api/v1/conversations");
    state.conversations = data.conversations || [];
    $("#convCountLabel").textContent = `CONVERSATIONS (${data.count || 0})`;
    renderConvList();
    if (state.selectedSessionId) {
      const still = state.conversations.find(
        (c) => c.session_id === state.selectedSessionId
      );
      if (still) renderChat(still);
    }
  }

  function filteredConversations() {
    const q = ($("#convSearch").value || "").trim().toLowerCase();
    return state.conversations.filter((c) => {
      if (state.convFilter === "unread" && !c.unread) return false;
      if (state.convFilter === "booked" && !c.booked) return false;
      if (!q) return true;
      return (
        (c.customer_name || "").toLowerCase().includes(q) ||
        (c.snippet || "").toLowerCase().includes(q)
      );
    });
  }

  function renderConvList() {
    const list = $("#convList");
    const rows = filteredConversations();
    if (!rows.length) {
      list.innerHTML = `<div class="empty-state">No conversations yet. Start a test.</div>`;
      return;
    }
    list.innerHTML = rows
      .map(
        (c) => `
      <button type="button" class="conv-item ${
        c.session_id === state.selectedSessionId ? "active" : ""
      }" data-id="${c.session_id}">
        <strong>${escapeHtml(c.customer_name || "Guest")}</strong>
        <span class="time">${formatTime(c.updated_at)}</span>
        <span class="snippet">${escapeHtml(c.snippet || "")}</span>
        ${
          c.unread
            ? `<span class="badge-count">1</span>`
            : `<span></span>`
        }
      </button>`
      )
      .join("");
    $$(".conv-item", list).forEach((btn) => {
      btn.addEventListener("click", () => {
        state.selectedSessionId = btn.dataset.id;
        const conv = state.conversations.find(
          (c) => c.session_id === state.selectedSessionId
        );
        renderConvList();
        renderChat(conv);
      });
    });
  }

  function renderChat(conv) {
    const input = $("#chatInput");
    const send = $(".send-btn");
    if (!conv) {
      $("#chatName").textContent = "Select a conversation";
      $("#chatPhone").textContent = "";
      $("#chatThread").innerHTML =
        `<div class="empty-state">Start a test conversation from the left</div>`;
      input.disabled = true;
      send.disabled = true;
      return;
    }
    $("#chatName").textContent = conv.customer_name || "Guest";
    $("#chatPhone").textContent = conv.customer_phone || conv.session_id.slice(0, 8);
    input.disabled = false;
    send.disabled = false;

    const msgs = conv.messages || [];
    $("#chatThread").innerHTML = msgs.length
      ? msgs
          .map(
            (m) => `
        <div class="bubble ${m.role}">
          ${escapeHtml(m.content || "")}
          <span class="ts">${formatTime(m.timestamp)}</span>
        </div>`
          )
          .join("")
      : `<div class="empty-state">No messages yet</div>`;
    $("#chatThread").scrollTop = $("#chatThread").scrollHeight;

    $("#customerDetails").innerHTML = `
      <div><dt>Name</dt><dd>${escapeHtml(conv.customer_name || "—")}</dd></div>
      <div><dt>Email</dt><dd>${escapeHtml(conv.customer_email || "—")}</dd></div>
      <div><dt>Phone</dt><dd>${escapeHtml(conv.customer_phone || "—")}</dd></div>
    `;
    $("#customerSource").textContent = conv.source || "—";
    const agent = state.agents.find((a) => a.id === conv.agent_id);
    $("#customerAgent").textContent = agent ? agent.name : conv.agent_id || "—";
  }

  async function startTestConversation() {
    const agentId = $("#testAgentSelect").value;
    if (!agentId) {
      await loadAgents();
    }
    const id = $("#testAgentSelect").value;
    const agent = state.agents.find((a) => a.id === id);
    const created = await api("/api/v1/sessions", {
      method: "POST",
      body: JSON.stringify({
        agent_id: id,
        customer_name: `Test · ${agent?.name || "Agent"}`,
        source: "Admin Console",
      }),
    });
    state.selectedSessionId = created.session_id;
    await loadConversations();
    const conv = state.conversations.find(
      (c) => c.session_id === state.selectedSessionId
    );
    renderChat(conv);
  }

  async function sendChat(e) {
    e.preventDefault();
    const text = ($("#chatInput").value || "").trim();
    if (!text || !state.selectedSessionId) return;
    $("#chatInput").value = "";
    const conv = state.conversations.find(
      (c) => c.session_id === state.selectedSessionId
    );
    const agentId = conv?.agent_id;
    try {
      await api("/api/v1/chat/text", {
        method: "POST",
        body: JSON.stringify({
          session_id: state.selectedSessionId,
          message: text,
          agent_id: agentId,
        }),
      });
      await loadConversations();
      const updated = state.conversations.find(
        (c) => c.session_id === state.selectedSessionId
      );
      renderChat(updated);
    } catch (err) {
      alert(err.message || "Chat failed");
    }
  }

  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function wire() {
    $$(".nav-item").forEach((btn) => {
      btn.addEventListener("click", () => setView(btn.dataset.view));
    });
    $$("[data-goto]").forEach((btn) => {
      btn.addEventListener("click", () => setView(btn.dataset.goto));
    });
    $("#globalSearch").addEventListener("input", renderAgentList);
    $("#convSearch").addEventListener("input", renderConvList);
    $$("#convFilters .chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        $$("#convFilters .chip").forEach((c) => c.classList.remove("active"));
        chip.classList.add("active");
        state.convFilter = chip.dataset.filter;
        renderConvList();
      });
    });
    $("#newTestBtn").addEventListener("click", startTestConversation);
    $("#chatForm").addEventListener("submit", sendChat);

    const dialog = $("#addAgentDialog");
    $("#addAgentBtn").addEventListener("click", () => dialog.showModal());
    $("#cancelAddAgent").addEventListener("click", () => dialog.close());
    $("#addAgentForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const body = {
        name: fd.get("name"),
        description: fd.get("description") || "",
        first_message: fd.get("first_message") || "",
        system_prompt:
          fd.get("system_prompt") ||
          "You are a helpful Sabrah travel assistant. Reply in English only.",
        enabled: true,
      };
      try {
        const created = await api("/api/v1/agents", {
          method: "POST",
          body: JSON.stringify(body),
        });
        dialog.close();
        e.target.reset();
        state.selectedAgentId = created.id;
        await loadAgents();
      } catch (err) {
        alert(err.message || "Create failed");
      }
    });
  }

  wire();
  loadAgents().catch((err) => {
    console.error(err);
    $("#agentList").innerHTML = `<div class="empty-state">${escapeHtml(
      err.message || "Failed to load agents"
    )}</div>`;
  });
})();
