/* Kumbuka web app. Vanilla JS, no build step. */
(() => {
  "use strict";

  // ---------------------------------------------------------------- state
  const $ = (id) => document.getElementById(id);
  // Where the API lives. Empty = same origin (served by FastAPI). On GitHub Pages,
  // static/config.js sets window.KUMBUKA_API_BASE to the Render URL.
  const API_BASE = String(window.KUMBUKA_API_BASE || "").replace(/\/+$/, "");
  const KEY = "kumbuka-id";
  const LEGACY = ["memory-agent-id"];
  const S = {
    uid: null,
    config: null,
    conversations: [],
    current: null,          // conversation id
    memories: [],
    memFilter: null,
    memQuery: "",
    freshIds: new Set(),
    busy: false,
    abort: null,
    tab: "memories",
  };
  const CAT_LABEL = {
    preference: "Preference", stack: "Stack", project: "Project",
    goal: "Goal", constraint: "Constraint", personal: "Personal",
  };
  const STARTERS = [
    { t: "Introduce your stack", d: "I build APIs with FastAPI and Postgres, and deploy on Fly.io." },
    { t: "Share how you like answers", d: "I prefer short answers with a code example first, explanation after." },
    { t: "Describe what you're building", d: "I'm working on a CLI tool that summarises noisy server logs." },
    { t: "See what Kumbuka knows", d: "What do you remember about me?" },
  ];
  const ICON = {
    copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
    edit: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16v4z"/><path d="M13.5 6.5l4 4"/></svg>',
    trash: '<svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>',
    pin: '<svg viewBox="0 0 24 24"><path d="M9 4h6l-1 6 3 3H7l3-3-1-6zM12 13v7"/></svg>',
    spark: '<svg viewBox="0 0 24 24"><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6"/></svg>',
    chev: '<svg class="chev" viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg>',
    mark: '<svg viewBox="0 0 32 32"><path d="M11 8v16M11 16l9-8M14 13l7 11" stroke="#fff" stroke-width="3" stroke-linecap="round" fill="none"/><circle cx="24" cy="8" r="3" fill="var(--ember-bright)"/></svg>',
  };

  // ---------------------------------------------------------------- utils
  function newId() {
    if (crypto.randomUUID) return crypto.randomUUID();
    return "u" + Math.random().toString(36).slice(2) + Date.now().toString(36);
  }
  function h(tag, attrs = {}, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "html") el.innerHTML = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) if (c != null) el.append(c.nodeType ? c : document.createTextNode(c));
    return el;
  }
  const qs = (o) => new URLSearchParams({ user_id: S.uid, ...o }).toString();
  async function api(path, opts = {}) {
    const res = await fetch(API_BASE + path, {
      ...opts,
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    });
    let data = null;
    try { data = await res.json(); } catch { /* empty body */ }
    if (!res.ok) {
      const detail = data && data.detail;
      const msg = typeof detail === "string" ? detail : Array.isArray(detail) ? detail[0]?.msg : `Request failed (${res.status})`;
      const err = new Error(msg || `Request failed (${res.status})`);
      err.status = res.status;
      throw err;
    }
    return data;
  }
  function ago(ts) {
    if (!ts) return "";
    const s = Date.now() / 1000 - ts;
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    if (s < 86400 * 7) return `${Math.floor(s / 86400)} d ago`;
    return new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  function fmtMs(ms) {
    if (ms == null) return "–";
    return ms < 10 ? ms.toFixed(1) : Math.round(ms).toString();
  }
  function debounce(fn, ms) {
    let t;
    return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  }
  function catVar(c) { return `var(--c-${CAT_LABEL[c] ? c : "personal"})`; }

  function toast(text, { action, onAction, error, timeout = 4200 } = {}) {
    const el = h("div", { class: "toast" + (error ? " error" : ""), role: error ? "alert" : "status" }, h("span", {}, text));
    if (action) el.append(h("button", { type: "button", onclick: () => { onAction(); el.remove(); } }, action));
    $("toasts").append(el);
    setTimeout(() => el.remove(), timeout);
  }

  function confirmDialog(title, body, okLabel = "Delete") {
    return new Promise((resolve) => {
      const d = $("confirm");
      $("confirmTitle").textContent = title;
      $("confirmBody").textContent = body;
      $("confirmOk").textContent = okLabel;
      d.returnValue = "";
      d.showModal();
      d.addEventListener("close", () => resolve(d.returnValue === "ok"), { once: true });
    });
  }

  // ---------------------------------------------------------------- identity & theme
  function initIdentity() {
    let id = localStorage.getItem(KEY);
    for (const k of LEGACY) if (!id) id = localStorage.getItem(k);
    if (!id || !/^[A-Za-z0-9_-]{3,64}$/.test(id)) id = newId();
    localStorage.setItem(KEY, id);
    S.uid = id;
  }
  function applyTheme(choice) {
    const root = document.documentElement;
    if (choice === "light" || choice === "dark") root.dataset.theme = choice;
    else delete root.dataset.theme;
    localStorage.setItem("kumbuka-theme", choice);
    document.querySelectorAll("[data-theme-choice]").forEach((b) =>
      b.setAttribute("aria-checked", String(b.dataset.themeChoice === choice)));
  }

  // ---------------------------------------------------------------- layout
  const app = $("app");
  const wide = () => window.matchMedia("(min-width: 1181px)").matches;
  function setMemoryOpen(open) {
    if (wide()) {
      app.classList.toggle("memory-closed", !open);
      app.classList.remove("memory-open");
      localStorage.setItem("kumbuka-memory-open", open ? "1" : "0");
    } else {
      app.classList.toggle("memory-open", open);
    }
    $("toggleMemory").setAttribute("aria-expanded", String(open));
    syncScrim();
    if (open) refreshMemories();
  }
  function memoryIsOpen() {
    return wide() ? !app.classList.contains("memory-closed") : app.classList.contains("memory-open");
  }
  function setSidebarOpen(open) { app.classList.toggle("sidebar-open", open); syncScrim(); }
  function syncScrim() {
    const show = app.classList.contains("sidebar-open") || (!wide() && app.classList.contains("memory-open"));
    $("scrim").hidden = !show;
  }

  // ---------------------------------------------------------------- conversations
  async function loadConversations() {
    try { S.conversations = await api(`/api/conversations?${qs()}`); }
    catch (e) { S.conversations = []; toast(e.message, { error: true }); }
    renderConversations();
  }

  function groupOf(c) {
    if (c.pinned) return "Pinned";
    const d = new Date(c.updated_at * 1000), now = new Date();
    const day = 86400000;
    const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    if (d.getTime() >= startToday) return "Today";
    if (d.getTime() >= startToday - day) return "Yesterday";
    if (d.getTime() >= startToday - 7 * day) return "Previous 7 days";
    return "Older";
  }

  function renderConversations() {
    const list = $("convList");
    const q = $("convSearch").value.trim().toLowerCase();
    const items = S.conversations.filter((c) =>
      !q || c.title.toLowerCase().includes(q) || (c.last_message || "").toLowerCase().includes(q));
    list.replaceChildren();
    if (!items.length) {
      list.append(h("div", { class: "conv-empty" }, q ? "No chats match that search." : "Your chats will appear here."));
      return;
    }
    let group = null;
    for (const c of items) {
      const g = groupOf(c);
      if (g !== group) { list.append(h("div", { class: "conv-group" }, g)); group = g; }
      const row = h("div", {
        class: "conv" + (c.id === S.current ? " active" : ""), role: "link", tabindex: "0",
        "aria-current": c.id === S.current ? "page" : null, title: c.last_message || c.title,
        onclick: (e) => { if (!e.target.closest("button,input")) openConversation(c.id); },
        onkeydown: (e) => { if (e.key === "Enter" && e.target === row) openConversation(c.id); },
      });
      const text = h("div", { class: "conv-text" }, h("span", { class: "conv-title" }, c.title));
      row.append(text);
      if (c.pinned) row.append(h("span", { html: ICON.pin.replace("<svg", '<svg class="conv-pin"') }));
      row.append(h("div", { class: "conv-actions" },
        h("button", { type: "button", "aria-label": c.pinned ? "Unpin chat" : "Pin chat", title: c.pinned ? "Unpin" : "Pin", html: ICON.pin,
          onclick: () => patchConversation(c.id, { pinned: !c.pinned }) }),
        h("button", { type: "button", "aria-label": "Rename chat", title: "Rename", html: ICON.edit, onclick: () => startRename(row, c) }),
        h("button", { type: "button", "aria-label": "Delete chat", title: "Delete", html: ICON.trash, onclick: () => deleteConversation(c) }),
      ));
      list.append(row);
    }
  }

  function startRename(row, c) {
    const input = h("input", { class: "rename", value: c.title, "aria-label": "Chat name", maxlength: "120" });
    const text = row.querySelector(".conv-text");
    text.replaceChildren(input);
    input.focus(); input.select();
    let done = false;
    const finish = async (save) => {
      if (done) return; done = true;
      const v = input.value.trim();
      if (save && v && v !== c.title) await patchConversation(c.id, { title: v });
      else renderConversations();
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); });
    input.addEventListener("blur", () => finish(true));
  }

  async function patchConversation(id, body) {
    try {
      const c = await api(`/api/conversations/${id}?${qs()}`, { method: "PATCH", body: JSON.stringify(body) });
      const i = S.conversations.findIndex((x) => x.id === id);
      if (i >= 0) S.conversations[i] = { ...S.conversations[i], ...c, pinned: !!c.pinned };
      S.conversations.sort((a, b) => (b.pinned - a.pinned) || (b.updated_at - a.updated_at));
      renderConversations();
      if (id === S.current) $("convTitle").textContent = c.title;
    } catch (e) { toast(e.message, { error: true }); }
  }

  async function deleteConversation(c) {
    const ok = await confirmDialog("Delete this chat?", `“${c.title}” will be removed. Memories Kumbuka saved from it stay in Memory.`);
    if (!ok) return;
    try {
      await api(`/api/conversations/${c.id}?${qs()}`, { method: "DELETE" });
      S.conversations = S.conversations.filter((x) => x.id !== c.id);
      if (S.current === c.id) newChat();
      renderConversations();
      toast("Chat deleted");
    } catch (e) { toast(e.message, { error: true }); }
  }

  async function openConversation(id) {
    if (S.busy) stopStreaming();
    setSidebarOpen(false);
    try {
      const conv = await api(`/api/conversations/${id}?${qs()}`);
      S.current = id;
      history.replaceState(null, "", `#c/${id}`);
      $("convTitle").textContent = conv.title;
      clearSuggestions();
      renderThread(conv);
      renderConversations();
      focusInput();
    } catch (e) {
      toast(e.status === 404 ? "That chat no longer exists." : e.message, { error: true });
      newChat();
    }
  }

  function newChat() {
    if (S.busy) stopStreaming();
    S.current = null;
    history.replaceState(null, "", location.pathname);
    $("convTitle").textContent = "New chat";
    $("thread").replaceChildren($("empty") || buildEmpty());
    $("empty").hidden = false;
    clearSuggestions();
    renderConversations();
    setSidebarOpen(false);
    focusInput();
  }

  // ---------------------------------------------------------------- thread rendering
  function buildEmpty() { return document.querySelector("#empty"); }

  function renderStarters() {
    const box = $("starters");
    box.replaceChildren(...STARTERS.map((s) =>
      h("button", { type: "button", class: "starter", onclick: () => send(s.d) }, h("strong", {}, s.t), h("span", {}, s.d))));
  }

  function renderThread(conv) {
    const t = $("thread");
    const empty = $("empty");
    empty.hidden = true;
    t.replaceChildren(empty);
    const folded = conv.summarized_messages || 0;
    conv.messages.forEach((m, i) => {
      if (m.role === "user") t.append(userMsg(m.content));
      else t.append(botMsg(m.content, m.meta || {}, m.id).el);
      if (folded && i === folded - 1) t.append(foldNote(folded));
    });
    scrollToEnd(true);
  }

  function foldNote(n) {
    return h("div", { class: "fold-note", title: "Older turns are sent to the model as a compact summary" },
      `${n} earlier messages compressed into a summary`);
  }

  function userMsg(text) {
    return h("div", { class: "msg user" }, h("div", { class: "bubble" }, text));
  }

  function traceEl(recalled, metrics, pending) {
    const el = h("div", { class: "trace" + (pending ? " searching" : "") });
    const head = h("button", { type: "button", class: "trace-head", "aria-expanded": "false" });
    const items = h("div", { class: "trace-items" });
    el.append(head, items);
    head.addEventListener("click", () => {
      el.classList.toggle("open");
      head.setAttribute("aria-expanded", String(el.classList.contains("open")));
    });
    el.update = (rec, met, isPending) => {
      el.classList.toggle("searching", !!isPending);
      if (isPending) { head.replaceChildren("Searching memory…"); el.hidden = false; return; }
      const n = rec.length;
      const ms = met && met.lookup_ms != null ? ` · ${fmtMs(met.lookup_ms)} ms lookup` : "";
      if (!n) { el.hidden = true; return; }
      el.hidden = false;
      head.innerHTML = ICON.chev;
      head.append(h("strong", {}, `Recalled ${n} ${n === 1 ? "memory" : "memories"}`), h("span", {}, ms));
      items.replaceChildren(...rec.map((m) => h("div", { class: "recall-item" },
        h("span", { class: "score", title: "Similarity" }, m.score != null ? Math.round(m.score * 100) + "%" : ""),
        h("span", { class: "dot", style: `--cat:${catVar(m.category)}`, title: CAT_LABEL[m.category] }),
        h("span", {}, m.content))));
    };
    el.update(recalled || [], metrics, pending);
    return el;
  }

  function botMsg(text, meta, messageId) {
    const content = h("div", { class: "content" });
    const trace = traceEl(meta.recalled || [], meta.metrics, false);
    const after = h("div", { class: "after" });
    const body = h("div", {}, trace, content, after);
    const el = h("div", { class: "msg bot" }, h("div", { class: "avatar", html: ICON.mark }), body);
    const view = { el, content, trace, after, text: text || "", meta };
    if (text) content.innerHTML = renderMarkdown(text);
    if (meta && (meta.metrics || meta.saved)) renderAfter(view);
    return view;
  }

  function renderAfter(view, { remembering = false } = {}) {
    const { after, meta } = view;
    after.replaceChildren();
    for (const s of meta.saved || []) after.append(savedChip(s));
    if (remembering) after.append(h("span", { class: "remembering" }, h("span", { class: "typing" }, h("i"), h("i"), h("i")), "Remembering"));
    const m = meta.metrics || {};
    const line = h("div", { class: "meta-line" });
    if (m.has_summary && m.saved_pct > 0) {
      line.append(h("span", { class: "stat good", title: `Sent ≈${m.prompt_tokens} tokens instead of ≈${m.baseline_tokens}: older turns were compressed into a summary` },
        `Prompt ${Math.round(m.saved_pct)}% smaller`));
    }
    if (view.text) {
      const copy = h("button", { type: "button", "aria-label": "Copy reply", title: "Copy reply", html: ICON.copy });
      copy.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(view.text); copy.innerHTML = ICON.check; setTimeout(() => (copy.innerHTML = ICON.copy), 1400); }
        catch { toast("Couldn't copy to the clipboard.", { error: true }); }
      });
      line.append(h("span", { class: "msg-actions" }, copy));
    }
    after.append(line);
  }

  function savedChip(s) {
    const chip = h("span", { class: "saved-chip", title: s.content },
      h("span", { html: ICON.spark }), h("span", { class: "txt" }, s.content));
    const undo = h("button", { type: "button", "aria-label": `Forget: ${s.content}` }, "Forget");
    undo.addEventListener("click", async () => {
      try {
        await api(`/api/memories/${s.id}?${qs()}`, { method: "DELETE" });
        chip.classList.add("removed"); undo.remove();
        refreshMemories();
        toast("Forgotten");
      } catch (e) { toast(e.status === 404 ? "Already forgotten." : e.message, { error: true }); chip.classList.add("removed"); undo.remove(); }
    });
    chip.append(undo);
    return chip;
  }

  function scrollToEnd(force) {
    const t = $("thread");
    const near = t.scrollHeight - t.scrollTop - t.clientHeight < 160;
    if (force || near) t.scrollTop = t.scrollHeight;
  }

  // ---------------------------------------------------------------- sending
  function focusInput() { if (window.matchMedia("(pointer:fine)").matches) $("input").focus(); }

  function setBusy(b) {
    S.busy = b;
    $("stopBtn").hidden = !b;
    $("sendBtn").hidden = b;
    updateSendState();
  }

  function updateSendState() {
    const v = $("input").value;
    $("sendBtn").disabled = S.busy || !v.trim();
    const n = v.length;
    $("charCount").textContent = n > 6000 ? `${n} / 8000` : "";
  }

  function autosize() {
    const ta = $("input");
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 220) + "px";
  }

  function stopStreaming() {
    if (S.abort) S.abort.abort();
  }

  async function send(text) {
    text = (text ?? $("input").value).trim();
    if (!text || S.busy) return;
    $("input").value = ""; autosize();
    clearSuggestions();
    $("empty").hidden = true;

    const t = $("thread");
    t.append(userMsg(text));
    const view = botMsg("", {}, null);
    view.trace.update([], null, true);
    view.content.innerHTML = '<span class="typing" aria-label="Kumbuka is thinking"><i></i><i></i><i></i></span>';
    t.append(view.el);
    scrollToEnd(true);
    setBusy(true);

    const ctrl = new AbortController();
    S.abort = ctrl;
    let raw = "";
    let frame = 0;
    let finished = false;
    const meta = { recalled: [], saved: [], metrics: {} };
    let savedArrived = false;
    const paint = () => {
      frame = 0;
      view.content.innerHTML = renderMarkdown(raw);
      const last = view.content.lastElementChild;
      if (last && !finished) (last.querySelector("code:last-child") || last).classList.add("caret");
      scrollToEnd(false);
    };

    try {
      const res = await fetch(API_BASE + "/api/chat/stream", {
        method: "POST", signal: ctrl.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: S.uid, message: text, conversation_id: S.current }),
      });
      if (!res.ok) {
        let msg = `Request failed (${res.status})`;
        try { const d = await res.json(); if (typeof d.detail === "string") msg = d.detail; } catch {}
        throw Object.assign(new Error(msg), { status: res.status });
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
          const line = chunk.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          let ev;
          try { ev = JSON.parse(line.slice(6)); } catch { continue; }
          handle(ev);
        }
      }
    } catch (e) {
      if (e.name === "AbortError") {
        finished = true;
        if (raw) { paint(); view.after.replaceChildren(h("span", { class: "remembering" }, "Stopped")); }
        else view.el.remove();
      } else {
        showError(view, e.message || "Couldn't reach Kumbuka. Check that the server is running.", text);
      }
    } finally {
      setBusy(false);
      S.abort = null;
    }

    function handle(ev) {
      switch (ev.type) {
        case "start":
          if (!S.current || S.current !== ev.conversation_id) {
            S.current = ev.conversation_id;
            history.replaceState(null, "", `#c/${ev.conversation_id}`);
          }
          $("convTitle").textContent = ev.title;
          upsertConversation({ id: ev.conversation_id, title: ev.title, updated_at: Date.now() / 1000, pinned: false, last_message: text });
          break;
        case "recall":
          meta.recalled = ev.memories || [];
          meta.metrics = { ...meta.metrics, ...(ev.timings || {}) };
          view.trace.update(meta.recalled, meta.metrics, false);
          break;
        case "context":
          meta.metrics = { ...meta.metrics, ...ev.metrics };
          break;
        case "token":
          raw += ev.text;
          if (!frame) frame = requestAnimationFrame(paint);
          if (!savedArrived && !view.rememberingShown) { view.rememberingShown = true; renderAfter(view, { remembering: true }); }
          break;
        case "saved":
          savedArrived = true;
          meta.saved = ev.memories || [];
          view.meta = meta;
          renderAfter(view, { remembering: false });
          if (meta.saved.length) onMemoriesSaved(meta.saved);
          break;
        case "compressed":
          break;
        case "done": {
          finished = true;
          if (frame) cancelAnimationFrame(frame);
          raw = ev.reply;
          paint();
          view.text = ev.reply;
          view.meta = { recalled: ev.recalled, saved: ev.saved, metrics: ev.metrics };
          view.trace.update(ev.recalled, ev.metrics, false);
          renderAfter(view);
          if (ev.compression) {
            const notes = $("thread").querySelectorAll(".fold-note");
            notes.forEach((n) => n.remove());
          }
          touchConversation(ev.conversation_id);
          if (memoryIsOpen()) { if (S.tab === "insights") renderInsights(); else refreshMemories(); }
          fetchSuggestions(text, ev.reply, ev.conversation_id);
          break;
        }
        case "error":
          finished = true;
          showError(view, ev.message, text);
          break;
      }
    }
  }

  function showError(view, message, text) {
    view.trace.hidden = true;
    view.content.replaceChildren(h("div", { class: "error-box", role: "alert" },
      h("span", {}, message),
      h("button", { type: "button", class: "btn small", onclick: () => { view.el.previousElementSibling?.remove(); view.el.remove(); send(text); } }, "Try again")));
    view.after.replaceChildren();
  }

  function upsertConversation(c) {
    const i = S.conversations.findIndex((x) => x.id === c.id);
    if (i >= 0) S.conversations[i] = { ...S.conversations[i], ...c, pinned: S.conversations[i].pinned };
    else S.conversations.unshift(c);
    S.conversations.sort((a, b) => (b.pinned - a.pinned) || (b.updated_at - a.updated_at));
    renderConversations();
  }
  function touchConversation(id) {
    const c = S.conversations.find((x) => x.id === id);
    if (c) { c.updated_at = Date.now() / 1000; upsertConversation(c); }
  }

  // ---------------------------------------------------------------- suggestions
  function clearSuggestions() { $("suggestions").replaceChildren(); }
  async function fetchSuggestions(message, reply, cid) {
    try {
      const d = await api("/api/suggest", { method: "POST", body: JSON.stringify({ user_id: S.uid, message, reply: reply.slice(0, 19000) }) });
      if (S.busy || S.current !== cid) return;
      $("suggestions").replaceChildren(...(d.suggestions || []).map((s) =>
        h("button", { type: "button", class: "suggestion", onclick: () => send(s) }, s)));
    } catch { /* optional */ }
  }

  // ---------------------------------------------------------------- memories
  function onMemoriesSaved(rows) {
    rows.forEach((r) => { S.freshIds.add(r.id); setTimeout(() => S.freshIds.delete(r.id), 60000); });
    const c = $("memCount");
    c.classList.remove("bump"); void c.offsetWidth; c.classList.add("bump");
    refreshMemories();
  }

  async function refreshMemories() {
    try {
      const params = {};
      if (S.memQuery) params.q = S.memQuery;
      const [all, shown] = await Promise.all([
        api(`/api/memories?${qs()}`),
        S.memQuery || S.memFilter ? api(`/api/memories?${qs({ ...params, ...(S.memFilter ? { category: S.memFilter } : {}) })}`) : null,
      ]);
      S.memories = all;
      $("memCount").textContent = all.length;
      renderCatFilter();
      renderMemories(shown || all);
    } catch (e) {
      $("memList").replaceChildren(h("li", { class: "mem-empty" }, h("strong", {}, "Memory is unavailable"), e.message));
    }
  }

  function renderCatFilter() {
    const counts = {};
    S.memories.forEach((m) => (counts[m.category] = (counts[m.category] || 0) + 1));
    const cats = (S.config?.categories || Object.keys(CAT_LABEL));
    const box = $("catFilter");
    box.replaceChildren(
      h("button", { type: "button", class: "cat-chip", "aria-pressed": String(!S.memFilter), onclick: () => { S.memFilter = null; refreshMemories(); } },
        "All", h("span", { class: "n" }, String(S.memories.length))),
      ...cats.filter((c) => counts[c] || S.memFilter === c).map((c) =>
        h("button", { type: "button", class: "cat-chip", "aria-pressed": String(S.memFilter === c), style: `--cat:${catVar(c)}`,
          onclick: () => { S.memFilter = S.memFilter === c ? null : c; refreshMemories(); } },
          h("span", { class: "dot" }), CAT_LABEL[c], h("span", { class: "n" }, String(counts[c] || 0)))),
    );
  }

  function renderMemories(rows) {
    const list = $("memList");
    if (!rows.length) {
      list.replaceChildren(h("li", { class: "mem-empty" },
        S.memQuery || S.memFilter
          ? [h("strong", {}, "Nothing matches"), "Try a different search or category."]
          : [h("strong", {}, "No memories yet"), "Tell Kumbuka about your tools, projects and preferences, and they'll show up here."]));
      return;
    }
    list.replaceChildren(...rows.map(memItem));
  }

  function memItem(m) {
    const li = h("li", { class: "mem" + (S.freshIds.has(m.id) ? " fresh" : ""), style: `--cat:${catVar(m.category)}` });
    const text = h("div", { class: "mem-text" }, m.content);
    const sel = h("select", { "aria-label": "Category" },
      ...Object.keys(CAT_LABEL).map((c) => h("option", { value: c, selected: c === m.category }, CAT_LABEL[c])));
    sel.addEventListener("change", () => updateMemory(m.id, { category: sel.value }));
    const bits = [];
    if (m.score != null && S.memQuery) bits.push(h("span", { class: "mem-score", title: "Similarity to your search" }, Math.round(m.score * 100) + "%"));
    const used = m.recall_count ? `recalled ${m.recall_count}×` : "not recalled yet";
    bits.push(h("span", { title: new Date(m.created_at * 1000).toLocaleString() }, `${ago(m.created_at)} · ${used}`));
    const tools = h("div", { class: "mem-tools" },
      h("button", { type: "button", "aria-label": "Edit memory", title: "Edit", html: ICON.edit, onclick: () => editMemory(li, m) }),
      h("button", { type: "button", class: "del", "aria-label": "Forget memory", title: "Forget", html: ICON.trash, onclick: () => forgetMemory(m) }));
    li.append(text, h("div", { class: "mem-foot" }, h("span", { class: "mem-cat" }, h("span", { class: "dot" }), sel), ...bits, tools));
    return li;
  }

  function editMemory(li, m) {
    const ta = h("textarea", { rows: "2", maxlength: "500", "aria-label": "Edit memory" });
    ta.value = m.content;
    const save = h("button", { type: "button", class: "btn small primary" }, "Save");
    const cancel = h("button", { type: "button", class: "btn small" }, "Cancel");
    const wrap = h("div", {}, ta, h("div", { class: "mem-edit-row" }, cancel, save));
    li.querySelector(".mem-text").replaceWith(wrap);
    ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
    cancel.onclick = () => refreshMemories();
    save.onclick = () => { const v = ta.value.trim(); if (v.length >= 3) updateMemory(m.id, { content: v }); };
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); save.click(); }
      if (e.key === "Escape") { e.stopPropagation(); cancel.click(); }
    });
  }

  async function updateMemory(id, body) {
    try {
      await api(`/api/memories/${id}?${qs()}`, { method: "PATCH", body: JSON.stringify(body) });
      toast(body.content ? "Memory updated" : `Moved to ${CAT_LABEL[body.category]}`);
    } catch (e) { toast(e.message, { error: true }); }
    refreshMemories();
  }

  async function forgetMemory(m) {
    try {
      await api(`/api/memories/${m.id}?${qs()}`, { method: "DELETE" });
      refreshMemories();
      toast("Memory forgotten", {
        action: "Undo",
        onAction: async () => {
          try { await api(`/api/memories?${qs()}`, { method: "POST", body: JSON.stringify({ content: m.content, category: m.category }) }); }
          catch (e) { toast(e.message, { error: true }); }
          refreshMemories();
        },
      });
    } catch (e) { toast(e.message, { error: true }); }
  }

  async function forgetEverything() {
    const n = S.memories.length;
    if (!n) { toast("There's nothing to forget yet."); return; }
    const ok = await confirmDialog("Forget everything?", `Kumbuka will permanently delete all ${n} memories about you. Your chats stay.`, "Forget everything");
    if (!ok) return;
    try {
      const r = await api(`/api/memories?${qs({ confirm: "true" })}`, { method: "DELETE" });
      S.freshIds.clear();
      refreshMemories();
      toast(`Forgot ${r.deleted} memories`);
    } catch (e) { toast(e.message, { error: true }); }
  }

  // ---------------------------------------------------------------- insights
  function sparkline(values) {
    if (!values.length) return h("p", { class: "sub" }, "Appears after your first message.");
    const W = 300, H = 56, P = 4;
    const max = Math.max(...values, 1), min = 0;
    const step = values.length > 1 ? (W - 2 * P) / (values.length - 1) : 0;
    const pts = values.map((v, i) => [P + i * step, H - P - ((v - min) / (max - min || 1)) * (H - 2 * P)]);
    const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    const area = d + ` L ${pts[pts.length - 1][0].toFixed(1)} ${H - P} L ${pts[0][0].toFixed(1)} ${H - P} Z`;
    const svg = `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Lookup latency over the last ${values.length} messages, max ${fmtMs(max)} ms"><path class="area" d="${area}"/><path class="line" d="${d}" vector-effect="non-scaling-stroke"/></svg>`;
    return h("div", { html: svg });
  }

  async function renderInsights() {
    const box = $("insights");
    let s;
    try { s = await api(`/api/stats?${qs()}`); }
    catch (e) { box.replaceChildren(h("p", { class: "sub" }, e.message)); return; }
    const kpi = (value, unit, label) => h("div", { class: "kpi" },
      h("div", { class: "kpi-value" }, value, unit ? h("small", {}, unit) : null), h("div", { class: "kpi-label" }, label));
    const comp = s.prompt_saved_pct.long_conversations;
    box.replaceChildren(
      h("div", { class: "kpis" },
        kpi(String(s.memories), "", "memories kept about you"),
        kpi(fmtMs(s.lookup_ms.p50), "ms", "median vector lookup"),
        kpi(comp != null ? Math.round(comp) + "" : "–", comp != null ? "%" : "", "smaller prompts once a chat is compressed"),
        kpi(s.tokens_saved >= 1000 ? (s.tokens_saved / 1000).toFixed(1) : String(s.tokens_saved), s.tokens_saved >= 1000 ? "k" : "", "tokens saved by compression"),
      ),
      h("section", { class: "insight-block" },
        h("h3", {}, "Memory lookup speed"),
        h("p", { class: "sub" }, s.lookup_ms.p95 != null ? `Last ${s.lookup_ms.recent.length} messages · p95 ${fmtMs(s.lookup_ms.p95)} ms (Qdrant search, excluding embedding)` : "Qdrant search time per message"),
        sparkline(s.lookup_ms.recent)),
      h("section", { class: "insight-block" },
        h("h3", {}, "What Kumbuka knows"),
        s.memories ? h("div", { class: "bars" }, ...Object.entries(s.by_category).filter(([, n]) => n).map(([c, n]) =>
          h("div", { class: "bar-row", style: `--cat:${catVar(c)}` },
            h("span", {}, CAT_LABEL[c]),
            h("div", { class: "bar-track" }, h("div", { class: "bar-fill", style: `width:${(100 * n) / Math.max(...Object.values(s.by_category))}%` })),
            h("span", { class: "n" }, String(n))))) : h("p", { class: "sub" }, "Nothing yet.")),
      s.most_recalled.length ? h("section", { class: "insight-block" },
        h("h3", {}, "Most useful memories"),
        ...s.most_recalled.map((m) => h("div", { class: "top-mem" }, h("span", {}, m.content), h("span", { class: "times" }, `${m.recall_count}×`)))) : null,
      h("section", { class: "insight-block how" },
        h("h3", {}, "How memory works"),
        h("ol", {},
          h("li", {}, "Each message is embedded and matched against your memories in Qdrant; the closest ones are added to the prompt."),
          h("li", {}, "While Kumbuka answers, it extracts lasting facts you shared and stores new ones, skipping near-duplicates."),
          h("li", {}, `After ${S.config?.history_window ?? 8} messages, older turns are folded into a running summary so long chats stay fast and focused.`))),
    );
  }

  function setTab(tab) {
    S.tab = tab;
    document.querySelectorAll(".tab").forEach((b) => {
      const on = b.dataset.tab === tab;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", String(on));
    });
    $("tab-memories").hidden = tab !== "memories";
    $("tab-insights").hidden = tab !== "insights";
    if (tab === "insights") renderInsights(); else refreshMemories();
  }

  // ---------------------------------------------------------------- settings
  function openSettings() {
    $("userKey").value = S.uid;
    $("newKey").value = "";
    applyTheme(localStorage.getItem("kumbuka-theme") || "system");
    $("settings").showModal();
  }

  async function switchKey() {
    const v = $("newKey").value.trim();
    if (!/^[A-Za-z0-9_-]{3,64}$/.test(v)) { toast("That doesn't look like a memory key.", { error: true }); return; }
    localStorage.setItem(KEY, v);
    S.uid = v;
    $("settings").close();
    S.freshIds.clear();
    newChat();
    await Promise.all([loadConversations(), refreshMemories()]);
    toast("Switched to that memory key");
  }

  // ---------------------------------------------------------------- get the app
  let installPrompt = null;
  const isStandalone = () =>
    window.matchMedia("(display-mode: standalone)").matches ||
    window.matchMedia("(display-mode: window-controls-overlay)").matches ||
    navigator.standalone === true;
  const SHARE_ICON = '<span class="glyph"><svg viewBox="0 0 24 24"><path d="M12 15V3M8 7l4-4 4 4M6 11H5v10h14V11h-1"/></svg></span>';

  function installSteps() {
    const ua = navigator.userAgent;
    const ios = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    const safari = /Safari/.test(ua) && !/Chrome|Chromium|CriOS|Edg|OPR|Firefox|FxiOS/.test(ua);
    const firefox = /Firefox|FxiOS/.test(ua);
    const edge = /Edg\//.test(ua);
    const android = /Android/.test(ua);
    if (ios) return [`Tap Share ${SHARE_ICON} in the browser toolbar.`, "Scroll down and tap <strong>Add to Home Screen</strong>.", "Tap <strong>Add</strong>. Kumbuka appears on your home screen."];
    if (safari) return ["In the menu bar, choose <strong>File → Add to Dock</strong>.", "Click <strong>Add</strong>. Kumbuka opens from your Dock like any other app."];
    if (firefox && android) return ["Open the browser menu <strong>⋮</strong>.", "Tap <strong>Install</strong> (or <strong>Add to Home screen</strong>)."];
    if (firefox) return ["Firefox on desktop can't install web apps.", "Open this page in <strong>Chrome</strong>, <strong>Edge</strong> or <strong>Safari</strong> to install it, or run Kumbuka on your computer with the steps below."];
    if (edge) return ["Open the menu <strong>…</strong> at the top right.", "Choose <strong>Apps → Install Kumbuka</strong>."];
    return ["Click the install icon at the right end of the address bar.", "Or open the menu <strong>⋮</strong> and choose <strong>Cast, save and share → Install page as app</strong>."];
  }

  function renderGetApp() {
    const installed = isStandalone();
    $("installNow").hidden = installed || !installPrompt;
    $("installDone").hidden = !installed;
    const steps = $("installSteps");
    steps.hidden = installed || !!installPrompt;
    if (!steps.hidden) steps.innerHTML = installSteps().map((t) => `<li>${t}</li>`).join("");
    // Already running a local copy: no need to explain how to run one.
    // Phones can't run the local version.
    const phone = /Android|iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    $("runLocally").hidden = phone || /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
  }

  function initGetApp() {
    $("getApp").hidden = isStandalone();
    window.addEventListener("beforeinstallprompt", (e) => {
      e.preventDefault();
      installPrompt = e;
      if ($("getAppDialog").open) renderGetApp();
    });
    window.addEventListener("appinstalled", () => {
      installPrompt = null;
      $("getApp").hidden = true;
      if ($("getAppDialog").open) $("getAppDialog").close();
      toast("Kumbuka is installed. You'll find it with your other apps.");
    });
    $("getApp").addEventListener("click", () => { renderGetApp(); $("getAppDialog").showModal(); });
    $("installNow").addEventListener("click", async () => {
      if (!installPrompt) return;
      installPrompt.prompt();
      const { outcome } = await installPrompt.userChoice;
      installPrompt = null;
      if (outcome !== "accepted") renderGetApp();
    });
    $("getAppDialog").addEventListener("click", async (e) => {
      const b = e.target.closest("[data-copy]");
      if (!b) return;
      try {
        await navigator.clipboard.writeText($(b.dataset.copy).textContent);
        b.textContent = "Copied";
        setTimeout(() => (b.textContent = "Copy"), 1400);
      } catch { toast("Couldn't copy to the clipboard.", { error: true }); }
    });
    if ("serviceWorker" in navigator && window.isSecureContext) {
      navigator.serviceWorker.register("sw.js").catch(() => { /* app still works without it */ });
    }
  }

  // ---------------------------------------------------------------- boot
  // A free-tier host can take ~a minute to wake up; keep the user informed while it does.
  async function waitForServer() {
    const started = Date.now();
    let warned = false;
    for (;;) {
      try {
        return await api("/api/config");
      } catch (e) {
        if (e.status) throw e;                       // server answered with an error
        if (Date.now() - started > 90000) throw e;   // give up after 90 s
        if (!warned) {
          warned = true;
          $("engineInfo").textContent = "Waking server…";
          toast("Waking up the server. This can take up to a minute.", { timeout: 9000 });
        }
        await new Promise((r) => setTimeout(r, 3000));
      }
    }
  }

  async function boot() {
    initIdentity();
    initGetApp();
    applyTheme(localStorage.getItem("kumbuka-theme") || "system");
    renderStarters();
    if (wide() && localStorage.getItem("kumbuka-memory-open") === "0") setMemoryOpen(false);
    else if (!wide()) $("toggleMemory").setAttribute("aria-expanded", "false");

    try {
      S.config = await waitForServer();
      const demo = S.config.demo;
      $("demoBadge").hidden = !demo;
      $("demoNote").hidden = !demo || localStorage.getItem("kumbuka-demo-dismissed") === "1";
      $("engineInfo").textContent = `${demo ? "Demo" : S.config.model} · ${S.config.vector_backend === "qdrant" ? "Qdrant" : "in-memory"}`;
      $("engineInfo").title = `Model: ${S.config.model}\nEmbeddings: ${S.config.embed_model}\nVector store: ${S.config.vector_backend} (${S.config.vector_location})`;
      $("addMemCat").replaceChildren(...S.config.categories.map((c) => h("option", { value: c }, CAT_LABEL[c] || c)));
    } catch {
      $("engineInfo").textContent = "Server offline";
      toast("Can't reach the Kumbuka server. Start it and reload.", { error: true, timeout: 8000 });
    }
    $("exportMem").href = `${API_BASE}/api/memories/export?${qs()}`;

    await Promise.all([loadConversations(), refreshMemories()]);
    const m = location.hash.match(/^#c\/([A-Za-z0-9_-]+)$/);
    if (m) openConversation(m[1]); else focusInput();
  }

  // ---------------------------------------------------------------- events
  $("composer").addEventListener("submit", (e) => { e.preventDefault(); send(); });
  $("input").addEventListener("input", () => { autosize(); updateSendState(); });
  $("input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  });
  $("stopBtn").addEventListener("click", stopStreaming);
  $("newChat").addEventListener("click", newChat);
  $("convSearch").addEventListener("input", renderConversations);
  $("toggleMemory").addEventListener("click", () => setMemoryOpen(!memoryIsOpen()));
  $("closeMemory").addEventListener("click", () => setMemoryOpen(false));
  $("openSidebar").addEventListener("click", () => setSidebarOpen(true));
  document.querySelectorAll("[data-close-drawer]").forEach((b) => b.addEventListener("click", () => setSidebarOpen(false)));
  $("scrim").addEventListener("click", () => { setSidebarOpen(false); if (!wide()) setMemoryOpen(false); });
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
  $("memSearch").addEventListener("input", debounce((e) => { S.memQuery = e.target.value.trim(); refreshMemories(); }, 250));
  $("forgetAll").addEventListener("click", forgetEverything);
  $("forgetAll2").addEventListener("click", () => { $("settings").close(); forgetEverything(); });
  $("clearChats").addEventListener("click", async () => {
    $("settings").close();
    if (!(await confirmDialog("Delete all chats?", "Every conversation will be removed. Memories stay unless you forget them too.", "Delete all chats"))) return;
    try { await api(`/api/conversations?${qs()}`, { method: "DELETE" }); S.conversations = []; newChat(); toast("All chats deleted"); }
    catch (e) { toast(e.message, { error: true }); }
  });
  $("addMemForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const content = $("addMemText").value.trim();
    if (content.length < 3) return;
    try {
      const row = await api(`/api/memories?${qs()}`, { method: "POST", body: JSON.stringify({ content, category: $("addMemCat").value }) });
      $("addMemText").value = "";
      S.freshIds.add(row.id);
      toast("Memory saved");
      refreshMemories();
    } catch (err) { toast(err.message, { error: true }); }
  });
  $("addMemText").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("addMemForm").requestSubmit(); }
  });
  $("dismissDemo").addEventListener("click", () => { localStorage.setItem("kumbuka-demo-dismissed", "1"); $("demoNote").hidden = true; });
  $("openSettings").addEventListener("click", openSettings);
  $("copyKey").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(S.uid); toast("Memory key copied"); }
    catch { $("userKey").select(); }
  });
  $("useKey").addEventListener("click", switchKey);
  document.querySelectorAll("[data-theme-choice]").forEach((b) => b.addEventListener("click", () => applyTheme(b.dataset.themeChoice)));

  // copy buttons inside rendered code blocks
  $("thread").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-copy-code]");
    if (!btn) return;
    const code = btn.closest(".code").querySelector("code").innerText;
    try { await navigator.clipboard.writeText(code); btn.innerHTML = ICON.check + "Copied"; setTimeout(() => (btn.innerHTML = ICON.copy + "Copy"), 1400); }
    catch { toast("Couldn't copy to the clipboard.", { error: true }); }
  });

  document.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); newChat(); }
    else if (mod && e.key === ".") { e.preventDefault(); setMemoryOpen(!memoryIsOpen()); }
    else if (e.key === "Escape") {
      if (document.querySelector("dialog[open]")) return;
      if (S.busy) stopStreaming();
      else if (app.classList.contains("sidebar-open")) setSidebarOpen(false);
      else if (!wide() && app.classList.contains("memory-open")) setMemoryOpen(false);
    } else if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) && !document.querySelector("dialog[open]")) {
      e.preventDefault(); $("input").focus();
    }
  });
  window.addEventListener("resize", debounce(() => {
    if (wide()) { app.classList.remove("memory-open", "sidebar-open"); syncScrim(); }
  }, 150));

  boot();
})();
