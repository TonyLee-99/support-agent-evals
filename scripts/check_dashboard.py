"""Dashboard is built from results/ (rebuild and compare) and screenshots exist."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from support_evals.dashboard.build import build  # noqa: E402

site = ROOT / "docs/site/index.html"
before = site.read_text() if site.exists() else ""
after = build().read_text()
ok = before == after and all((ROOT / "docs/img" / f).exists() for f in ("dashboard.png", "widget.png"))
for key in ('"tau2"', '"taxonomy"', '"judge"', '"replays"'):
    ok &= key in after
print("OK" if ok else "FAIL (dashboard stale or screenshots missing)")
sys.exit(0 if ok else 1)
