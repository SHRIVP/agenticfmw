"""Write an EvalReport as Markdown (for people) and JSON (for CI and other tools)."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path

from pcagent.evals import EvalReport


def summary_lines(report: EvalReport) -> list[str]:
    lines = ["| pack | suite | kind | accuracy | runs | gates | result |", "|---|---|---|---|---|---|---|"]
    for s in report.suites:
        gates = ", ".join(f"{k}={v}" for k, v in s.gates.items()) or "–"
        lines.append(f"| {s.pack} | {s.name} | {s.kind} | {s.accuracy:.0%} (min {s.min_accuracy:.0%}) | "
                     f"{s.runs} | {gates} | {'PASS' if s.passed else 'FAIL'} |")
    return lines


def write(report: EvalReport, out_dir: str | Path, tuning_logs: dict[str, Path] | None = None) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    md, js = out / f"eval-{stamp}.md", out / f"eval-{stamp}.json"
    lines = [f"# pcagent eval {stamp}", "", f"**Overall: {'PASS' if report.passed else 'FAIL'}**", "",
             *summary_lines(report)]
    for s in report.suites:
        lines += ["", f"## {s.pack} / {s.name}", ""]
        for name, summ in s.summaries.items():
            lines.append(f"- {name}: {'pass' if summ['passed'] else 'FAIL'} ({summ['detail']})")
        lines += ["", "| id | run | passed | result | details |", "|---|---|---|---|---|"]
        for r in s.rows:
            got = r.get("action") or ", ".join(r.get("proposed", [])) or "(no tools)"
            lines.append(f"| {r['id']} | {r['run']} | {'yes' if r['passed'] else 'NO'} | {got} | "
                         f"{'; '.join(r['details'].values())} |")
    for pack, log in (tuning_logs or {}).items():
        if log.exists():
            lines += ["", f"## Tuning log: {pack}", "", log.read_text()]
    md.write_text("\n".join(lines) + "\n")
    js.write_text(json.dumps({"passed": report.passed,
                              "suites": [{**dataclasses.asdict(s), "passed": s.passed} for s in report.suites]},
                             indent=2, default=str))
    return md, js
