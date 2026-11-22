# pcagent web UI (short design)

Date: 2026-11-22. Status: approved by Pushkar.

**Why:** a proof of concept that runs only on Pushkar's machine. He wants a browser chat for himself and for the LinkedIn recording instead of the CLI. There are no other users.

**Goals:**
- A chat page in the feel of Claude's chat. It is called "pcagent" and uses its own accent colour, with no Anthropic logo, name or branding.
- Tool calls shown inline, with their policy decisions and Jev scores.
- Approve and Reject buttons for calls that need approval.
- A conversations sidebar.
- Resume after a server restart by reloading the conversation's URL.

**Non-goals:** login, hosting, streaming replies, embedding the gwmcp PAS view, and a React or other build toolchain.

**Approaches considered:**
- (A) Static HTML, CSS and JS served by the existing FastAPI app. *Chosen.*
- (B) A separate React app. Rejected: Node tooling, a second process and CORS, all of it plumbing rather than the agent ideas being learned.
- (C) Streamlit or Gradio. Rejected: approvals and resume fight its rerun model, and it looks generic on video.

**Design:**
- **Files:** `src/pcagent/web/index.html`, `app.js` and `styles.css`. The FastAPI app serves `/` as `index.html` and `/static/*` as the other files. There is no build step. Any library is pinned and loaded from a CDN (a Markdown renderer plus DOMPurify).
- **Layout:**
  - A left sidebar with "+ New chat" and the list of conversations.
  - A centered conversation column of about 720px. User messages sit in rounded bubbles on the right; assistant text is plain, in a serif font.
  - A rounded message box pinned to the bottom.
  - A warm off-white light theme and a dark theme that follows `prefers-color-scheme`.
- **Tool calls:**
  - One collapsible row per call, showing a friendly verb and the tool name, plus a chip: ALLOW (green), ASK (amber) or DENY (red).
  - Expanding a row shows the arguments, the reason, the decision's source (rule, jev, fallback or human), and four score bars (intent, grounded, injected, impact) when Jev was used.
  - A pending call shows as a card with Approve and Reject buttons, which `POST /threads/{id}/approvals`.
  - Tool results are collapsed under their row.
- **Rendering:** assistant text is Markdown, run through DOMPurify before insertion. Tool output and arguments are shown as text only, never as HTML.
- **State:**
  - The URL `/?thread=<id>` names the conversation. On load the page calls `GET /threads/{id}` and renders `messages`, `pending` and `audit`.
  - A new chat calls `POST /threads` and then updates the URL.
- **New endpoint:** `GET /threads` returns `[{thread_id, title, updated_at}]`, newest first. The title is the first user message, cut to 60 characters. It is read from the checkpointer, so it survives restarts.
- **Errors:** an inline banner for these cases:
  - 409 "awaiting approval": show the approval card.
  - 409 "thread busy": tell the user to wait.
  - 409 "interrupted run": offer to start a new chat.
  - Server unreachable.
  A "thinking…" indicator shows while a request is in flight, and the message box is disabled during it.
- **Tests:** API tests for `GET /threads` (ordering, titles, empty list) and for the page and static routes being served. A manual browser check against the local mock, with screenshots of each demo beat saved to `docs/img/`.

**Left to the builder:** exact colours, fonts and spacing, the wording of the friendly verbs, and the JS structure.
