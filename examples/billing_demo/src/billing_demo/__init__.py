"""billing_demo: an example pcagent pack whose tools are plain Python functions.

A carrier would wrap its own billing API the same way; here the "system" is an
in-memory ledger so the demo runs anywhere.
"""

from __future__ import annotations

from pathlib import Path

from pcagent.packs import Pack

PROMPT = "Billing: balances and refunds for policy accounts. Refunds over 500 need a reason from the user."

SEED = {"ACC-10001": 120.00, "ACC-10003": 0.00}


def make_pack(settings: dict) -> Pack:
    balances: dict[str, float] = dict(settings.get("seed") or SEED)
    payments: dict[str, list[str]] = {acc: [] for acc in balances}

    pack = Pack(
        "billing",
        prompt=PROMPT,
        rules="rules.yaml",
        evals="evals",
        labels={"get_balance": "Looked up the balance", "list_payments": "Listed payments",
                "issue_refund": "Issued a refund"},
        judge_hints=["After looking up a balance, the system may suggest listing payments."],
        base_dir=Path(__file__).parent,
    )

    def _account(account_number: str) -> str:
        if account_number not in balances:
            raise ValueError(f"unknown account {account_number}")
        return account_number

    @pack.tool(read_only=True)
    def get_balance(account_number: str) -> str:
        """Current billing balance for an account, e.g. ACC-10001."""
        return f"{_account(account_number)} balance: {balances[account_number]:.2f}"

    @pack.tool(read_only=True)
    def list_payments(account_number: str) -> str:
        """Payments and refunds recorded on an account."""
        items = payments[_account(account_number)]
        return "\n".join(items) if items else f"No payments recorded on {account_number}."

    @pack.tool(destructive=True)
    def issue_refund(account_number: str, amount: float, reason: str) -> str:
        """Refund an amount to the account. Moves money; needs a person's approval."""
        _account(account_number)
        if amount <= 0:
            raise ValueError("amount must be positive")
        balances[account_number] -= amount
        payments[account_number].append(f"refund {amount:.2f}: {reason}")
        return f"Refunded {amount:.2f} to {account_number}. New balance {balances[account_number]:.2f}."

    return pack
