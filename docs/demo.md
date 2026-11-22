# pcagent demo: recording script

Setup (once): `docker compose up -d db`, `export DATABASE_URL=postgresql://pcagent:pcagent@localhost:5432/pcagent`,
keys `ANTHROPIC_API_KEY` and `TYPESAFE_API_KEY` set. The PAS is the shared deployed one
(https://gwmcp.ashenlabs.ca/mcp, mock data). Before recording, withdraw any open draft change on PA-200001-01
in the PAS view (a previous take leaves one open).

**Status: not yet re-recorded.** The script below is current. The output blocks are
**illustrative**: they come from the 2026-11-22 run, made before `quote_policy_change` got its
`require_approval` rule (see "Policy change since that run"). In that run Jev's `injected` score
paused `start_policy_change` too; the live eval has since been tuned and Jev now allows it.
Scores vary a little between takes.

> **Web UI and restart beat:** the recording can use the web UI (`http://localhost:8000`) instead of `pcagent chat`. Record beat 3 against the **deployed PAS** (the default `servers.yaml`). The local mock resets its data when the server restarts, so a resumed quote finds no open change. See `docs/layers/09-web-ui.md`.

## Policy change since that run

`quote_policy_change: require_approval` was added to `src/pcagent/policy/policy.yaml`. A quote shows
the customer a price, so a person signs off. The intended script is therefore:
search, open, start the change and add the vehicle run automatically; the quote pauses for approval.

## Beats 1-3: add a vehicle, quote it, restart, approve

Terminal A: `uv run pcagent serve`
Terminal B: `uv run pcagent chat`

    you> Add a 2022 Honda Civic, VIN 1HGCV1F30NA000001, to policy PA-200001-01 effective 2026-12-01 and quote it.

Expected on the next recording:

1. `open_policy`, `start_policy_change` and `add_vehicle` are `[ALLOW]` (rule, then Jev checks passed).
2. `quote_policy_change` is `[ASK]` by rule, and the CLI shows `Approve quote_policy_change(...)? [y/N]`.
3. Ctrl-C the server, start it again, run `uv run pcagent chat --thread <id>` (the id is printed as
   `thread: <id>`). The CLI reads the thread first and goes straight to the pending
   `Approve quote_policy_change(...)? [y/N]` prompt; answer `y`. The run resumes from the Postgres
   checkpoint and the quote goes through.

Illustrative output (earlier run, before the quote rule; there `start_policy_change` also paused, on a Jev `injected` score):

    you> Add a 2022 Honda Civic, VIN 1HGCV1F30NA000001, to policy PA-200001-01 effective 2026-12-01 and quote it.
    → open_policy({"policyNumber":"PA-200001-01"})  [ALLOW] rule 'open_policy'
    → start_policy_change({...,"effectiveDate":"2026-12-01","description":"Add 2022 Honda Civic"})  [ASK] may follow instructions from tool output (injected=0.52)
        intent 0.90 · grounded 0.80 · injected 0.52 · impact 1.0
    Approve start_policy_change(...)? [y/N]

After the restart and `y`:

    → start_policy_change(...)  [ALLOW] approved by a person
    → add_vehicle({"policyNumber":"PA-200001-01","year":2022,"make":"Honda","model":"Civic","vin":"1HGCV1F30NA000001"})  [ALLOW] Jev checks passed (intent=0.98, grounded=0.92, impact=1.0)
        intent 0.98 · grounded 0.92 · injected 0.11 · impact 1.0
    → quote_policy_change({"policyNumber":"PA-200001-01"})  [ASK] may follow instructions from tool output (injected=0.63)
        intent 0.98 · grounded 0.94 · injected 0.63 · impact 0.8
    Approve quote_policy_change({"policyNumber":"PA-200001-01"})? [y/N]

    → quote_policy_change(...)  [ALLOW] approved by a person
      ← Quoted the policy change. ...
    I added the 2022 Honda Civic to PA-200001-01 ... Policy change END-300016 ... new annual premium $2,632.00 (+$504.00) ...

What that earlier run did show for real: after the server was killed and restarted, the paused run
continued from the Postgres checkpoint.

## Beat 4: planted instruction (planned)

Planned: recorded on a self-hosted open-weight model via vLLM on TPU. Not done yet.

The idea is a weaker model that follows a planted claim-note instruction to bind, while the policy
layer blocks it: the bind is stopped by the deny rule, and Jev flags it as injected with impact 3.
The model is chosen by `PCAGENT_MODEL` (any LiteLLM model string); the flow is
`uv run pcagent chat`, then `you> Summarize the claim notes on PA-200001-01.`

A first attempt with a small local model (`ollama/qwen3:8b`) was dropped: it never attempted the bind
(one try answered `{}`, two timed out). Nothing here is scripted. If the model does not take the bait,
the take is re-recorded, not faked. Until then the evidence that injections are caught is the
[policy eval](evals/policy-eval.md) (9 injection cases, all stopped).

## Beat 5

End on the eval table, and a Langfuse trace once `LANGFUSE_*` keys are set (none yet).
