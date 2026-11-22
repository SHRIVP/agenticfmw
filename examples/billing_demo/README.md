# billing_demo

An example pcagent pack, packaged the way a carrier would ship one: its own `pyproject.toml`,
registered under the `pcagent.packs` entry point, with tools written as plain Python functions.

```yaml
# pcagent.yaml
packs:
  - use: billing_demo          # the entry-point name
    settings:
      seed: {ACC-10001: 120.0}  # optional starting balances
```

Tools: `billing__get_balance` and `billing__list_payments` (read-only, allowed by its rules),
`billing__issue_refund` (destructive, always needs a person's approval).
