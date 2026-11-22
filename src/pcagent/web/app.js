// pcagent web UI: a thin client over the existing threads / runs / approvals API.
// Safety rule: tool arguments and results are only ever set with textContent;
// assistant Markdown is sanitized with DOMPurify before it touches innerHTML.

const $ = (id) => document.getElementById(id);
const els = {
  threads: $("threads"), conversation: $("conversation"), empty: $("empty"), scroll: $("scroll"),
  banner: $("banner"), composer: $("composer"), input: $("input"), send: $("send"), newChat: $("new-chat"),
};

const VERBS = {
  search_policies: "Searched policies", search_accounts: "Searched accounts", search_jobs: "Searched jobs",
  open_policy: "Opened the policy", start_policy_change: "Started a policy change",
  start_submission: "Started a submission", create_account: "Created an account",
  add_vehicle: "Added a vehicle", remove_vehicle: "Removed a vehicle", add_driver: "Added a driver",
  set_home_details: "Set home details", update_coverage: "Updated coverage",
  quote_policy_change: "Quoted the change", withdraw_policy_change: "Withdrew the change",
  bind_policy_change: "Bind the change", get_claim_notes: "Read the claim notes",
};
const CHIP = { allow: ["ALLOW", "allow"], require_approval: ["ASK", "ask"], deny: ["DENY", "deny"] };

const state = { thread: new URLSearchParams(location.search).get("thread"), busy: false, view: null, choices: {} };

// ---------- small helpers ----------
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
}

function textOf(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) return content.map((b) => (typeof b === "string" ? b : b.text || "")).join("");
  return content == null ? "" : String(content);
}

function markdown(text) {
  const node = el("div", "msg-ai");
  if (window.marked && window.DOMPurify) {
    node.innerHTML = DOMPurify.sanitize(marked.parse(text));
  } else {
    node.style.whiteSpace = "pre-wrap";
    node.textContent = text;
  }
  return node;
}

function pretty(value) {
  if (typeof value === "string") return value;
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

function setBusy(busy) {
  state.busy = busy;
  els.send.disabled = busy;
  els.input.disabled = busy;
  document.querySelectorAll(".approval button").forEach((b) => { b.disabled = busy; });
}

function banner(text, { error = false, action = null } = {}) {
  els.banner.replaceChildren(el("span", "", text));
  els.banner.classList.toggle("error", error);
  if (action) {
    const b = el("button", "", action.label);
    b.type = "button";
    b.addEventListener("click", action.run);
    els.banner.append(b);
  }
  els.banner.hidden = false;
}
function clearBanner() { els.banner.hidden = true; els.banner.replaceChildren(); }

function scrollDown() { els.scroll.scrollTop = els.scroll.scrollHeight; }

// ---------- API ----------
async function api(path, options = {}) {
  let res;
  try {
    res = await fetch(path, {
      ...options,
      headers: { "content-type": "application/json", ...(options.headers || {}) },
    });
  } catch {
    banner("Can't reach the pcagent server. Is `pcagent serve` running?", { error: true });
    throw new Error("unreachable");
  }
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON body */ }
  return { status: res.status, body };
}

// ---------- sidebar ----------
async function loadThreads() {
  const { status, body } = await api("/threads").catch(() => ({}));
  if (status !== 200 || !Array.isArray(body)) return;
  els.threads.replaceChildren(...body.map((t) => {
    const a = el("a", "thread" + (t.thread_id === state.thread ? " active" : ""), t.title);
    a.href = `/?thread=${encodeURIComponent(t.thread_id)}`;
    a.title = t.title;
    a.addEventListener("click", (e) => { e.preventDefault(); openThread(t.thread_id); });
    return a;
  }));
}

// ---------- rendering ----------
function decisionsById(view) {
  const map = {};
  for (const d of view.audit || []) map[d.tool_call_id] = d; // latest record wins
  for (const d of view.pending || []) map[d.tool_call_id] = d;
  return map;
}

function scoresBlock(j) {
  const grid = el("div", "scores");
  const rows = [["intent", j.intent, 1], ["grounded", j.grounded, 1], ["injected", j.injected, 1], ["impact", j.impact, 3]];
  for (const [name, value, max] of rows) {
    if (typeof value !== "number") continue;
    const bar = el("div", "bar");
    const fill = el("span");
    fill.style.width = `${Math.max(0, Math.min(1, value / max)) * 100}%`;
    bar.append(fill);
    grid.append(el("span", "label", name), bar, el("span", "num", value.toFixed(2)));
  }
  return grid;
}

function toolRow(call, decision, result) {
  const details = el("details", "tool");
  const summary = el("summary");
  summary.append(el("span", "verb", VERBS[call.name] || call.name), el("span", "name", call.name));
  if (decision) {
    const [label, cls] = CHIP[decision.action] || [decision.action, "ask"];
    const suffix = decision.source === "jev" ? " · Jev" : decision.source === "human" ? " · you" : "";
    summary.append(el("span", `chip ${cls}`, label + suffix));
  }
  details.append(summary);

  const body = el("div", "tool-body");
  body.append(el("div", "label", "Arguments"), el("pre", "", pretty(call.args || {})));
  if (decision) {
    body.append(el("div", "label", `Decision (${decision.source})`), el("div", "", decision.reason || ""));
    if (decision.judgments) body.append(el("div", "label", "Jev scores"), scoresBlock(decision.judgments));
  }
  if (result) body.append(el("div", "label", result.status === "error" ? "Result (error)" : "Result"),
    el("pre", "", textOf(result.content)));
  details.append(body);
  return details;
}

function approvalCards(pending) {
  const wrap = el("div", "");
  wrap.style.display = "grid";
  wrap.style.gap = "10px";
  for (const d of pending) {
    const card = el("div", "approval");
    const head = el("div", "head");
    head.append(el("span", "chip ask", "NEEDS APPROVAL"), el("span", "", VERBS[d.tool] || d.tool));
    card.append(head, el("div", "", d.reason || ""), el("pre", "", pretty(d.arguments || {})));
    if (d.judgments) card.append(scoresBlock(d.judgments));

    const actions = el("div", "actions");
    const reject = el("button", "btn", "Reject");
    const approve = el("button", "btn primary", "Approve");
    [reject, approve].forEach((b) => { b.type = "button"; });
    if (pending.length === 1) {
      reject.addEventListener("click", () => submitApprovals({ [d.tool_call_id]: false }));
      approve.addEventListener("click", () => submitApprovals({ [d.tool_call_id]: true }));
    } else {
      const choice = el("span", "choice", "No choice yet");
      const pick = (val) => {
        state.choices[d.tool_call_id] = val;
        choice.textContent = val ? "Will approve" : "Will reject";
        reject.classList.toggle("selected", !val);
        approve.classList.toggle("selected", val);
      };
      reject.addEventListener("click", () => pick(false));
      approve.addEventListener("click", () => pick(true));
      actions.append(choice);
    }
    actions.append(reject, approve);
    card.append(actions);
    wrap.append(card);
  }
  if (pending.length > 1) {
    const submit = el("button", "btn primary", "Submit decisions");
    submit.type = "button";
    submit.addEventListener("click", () => {
      const ids = pending.map((d) => d.tool_call_id);
      if (!ids.every((id) => id in state.choices)) {
        banner("Choose Approve or Reject for every action first.");
        return;
      }
      submitApprovals(Object.fromEntries(ids.map((id) => [id, state.choices[id]])));
    });
    const row = el("div", "actions");
    row.append(submit);
    wrap.append(row);
  }
  return wrap;
}

function render(view) {
  state.view = view;
  const nodes = [];
  const decisions = decisionsById(view);
  const results = {};
  for (const m of view.messages) if (m.type === "tool") results[m.tool_call_id] = m;

  for (const m of view.messages) {
    if (m.type === "human") nodes.push(el("div", "msg-human", textOf(m.content)));
    else if (m.type === "ai") {
      const text = textOf(m.content).trim();
      if (text) nodes.push(markdown(text));
      for (const call of m.tool_calls || []) {
        const isPending = (view.pending || []).some((p) => p.tool_call_id === call.id);
        if (!isPending) nodes.push(toolRow(call, decisions[call.id], results[call.id]));
      }
    }
  }
  if ((view.pending || []).length) nodes.push(approvalCards(view.pending));
  els.conversation.replaceChildren(...nodes);
  if (!nodes.length) els.conversation.replaceChildren(els.empty);
  scrollDown();
}

function showThinking(extraHuman) {
  const nodes = [...els.conversation.children].filter((n) => n !== els.empty);
  if (extraHuman) nodes.push(el("div", "msg-human", extraHuman));
  const t = el("div", "thinking");
  t.append(el("span", "", "Thinking"), el("span", "dots"));
  nodes.push(t);
  els.conversation.replaceChildren(...nodes);
  scrollDown();
}

// ---------- actions ----------
async function openThread(id, { push = true } = {}) {
  clearBanner();
  state.thread = id;
  state.choices = {};
  if (push) history.pushState(null, "", `/?thread=${encodeURIComponent(id)}`);
  const { status, body } = await api(`/threads/${encodeURIComponent(id)}`).catch(() => ({}));
  if (status === 404) {
    els.conversation.replaceChildren(els.empty);
    banner("Conversation not found.", { error: true, action: { label: "New chat", run: newChat } });
  } else if (status === 200) {
    render(body);
  }
  loadThreads();
}

function newChat() {
  clearBanner();
  state.thread = null;
  state.view = null;
  state.choices = {};
  history.pushState(null, "", "/");
  els.conversation.replaceChildren(els.empty);
  loadThreads();
  els.input.focus();
}

function handleConflict(body) {
  const detail = body && body.detail;
  if (detail === "thread busy") banner("Still working on the last message. Try again in a moment.");
  else if (detail === "interrupted run") {
    banner("This conversation was interrupted and can't continue.", { error: true, action: { label: "Start a new chat", run: newChat } });
  } else if (detail === "awaiting approval") banner("Approve or reject the pending action first.");
  else banner(detail || "Request rejected.", { error: true });
}

async function sendMessage(text) {
  if (state.busy || !text.trim()) return;
  clearBanner();
  setBusy(true);
  try {
    if (!state.thread) {
      const { status, body } = await api("/threads", { method: "POST" });
      if (status !== 200) { banner("Couldn't start a conversation.", { error: true }); return; }
      state.thread = body.thread_id;
      history.replaceState(null, "", `/?thread=${encodeURIComponent(state.thread)}`);
    }
    els.input.value = "";
    autosize();
    showThinking(text);
    const { status, body } = await api(`/threads/${encodeURIComponent(state.thread)}/runs`, {
      method: "POST", body: JSON.stringify({ message: text }),
    });
    if (status === 409) handleConflict(body);
    else if (status !== 200) banner(`Server error (${status}).`, { error: true });
  } catch { /* banner already shown */ } finally {
    setBusy(false);
    if (state.thread) await openThread(state.thread, { push: false });
  }
}

async function submitApprovals(choices) {
  if (state.busy) return;
  clearBanner();
  setBusy(true);
  const decisions = Object.fromEntries(Object.entries(choices).map(([id, approved]) => [id, { approved, note: "" }]));
  try {
    showThinking();
    const { status, body } = await api(`/threads/${encodeURIComponent(state.thread)}/approvals`, {
      method: "POST", body: JSON.stringify({ decisions }),
    });
    if (status === 409) handleConflict(body);
    else if (status !== 200) banner(`Server error (${status}).`, { error: true });
  } catch { /* banner already shown */ } finally {
    setBusy(false);
    await openThread(state.thread, { push: false });
  }
}

// ---------- composer ----------
function autosize() {
  els.input.style.height = "auto";
  els.input.style.height = `${Math.min(els.input.scrollHeight, 200)}px`;
}

els.input.addEventListener("input", autosize);
els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    sendMessage(els.input.value);
  }
});
els.composer.addEventListener("submit", (e) => { e.preventDefault(); sendMessage(els.input.value); });
els.newChat.addEventListener("click", newChat);
document.querySelectorAll(".suggestion").forEach((b) => b.addEventListener("click", () => sendMessage(b.textContent)));
window.addEventListener("popstate", () => {
  const id = new URLSearchParams(location.search).get("thread");
  if (id) openThread(id, { push: false }); else newChat();
});

// ---------- start ----------
if (state.thread) openThread(state.thread, { push: false }); else loadThreads();
