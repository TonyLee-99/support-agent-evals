"""Docs required by the build rules exist, are non-trivial, and Simulated docs say so in the title."""

import sys
from pathlib import Path

D = Path(__file__).resolve().parents[1] / "docs"
need = ["PRD.md", "DISCOVERY.md", "ACCEPTANCE.md", "ROLLOUT.md", "RUNBOOK.md", "INCIDENTS.md", "EVAL.md",
        "FEEDBACK_TO_PRODUCT.md", "DESIGN.md"]
ok = all((D / n).exists() and len((D / n).read_text()) > 600 for n in need)
ok &= "Simulated" in (D / "DISCOVERY.md").read_text().splitlines()[0]
ok &= len(list((D / "decisions").glob("0*.md"))) >= 4
ok &= "postmortem" in (D / "INCIDENTS.md").read_text().lower()
print("OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
