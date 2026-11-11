"""Copy the cache records a command needs into cache/ci (committed), so CI can replay it offline.

  uv run python scripts/export_ci_cache.py -- uv run python -m support_evals.flywheel.regress run --version v2
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE, CI = ROOT / "data/cache", ROOT / "cache/ci"


def main() -> int:
    cmd = sys.argv[sys.argv.index("--") + 1:]
    trace = Path(tempfile.mktemp(suffix=".keys"))
    r = subprocess.run(cmd, cwd=ROOT, env={**os.environ, "SE_LLM_BACKEND": "replay", "SE_CACHE_TRACE": str(trace)})
    keys = set(trace.read_text().split()) if trace.exists() else set()
    CI.mkdir(parents=True, exist_ok=True)
    have = set()
    for f in CI.glob("*.jsonl"):
        have |= {json.loads(line)["key"] for line in f.read_text().splitlines() if line.strip()}
    added = 0
    for f in LIVE.glob("*.jsonl"):
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec["key"] in keys and rec["key"] not in have:
                with open(CI / f.name, "a") as out:
                    out.write(line + "\n")
                have.add(rec["key"])
                added += 1
    print(f"command exit {r.returncode}; {len(keys)} keys used; {added} records added to cache/ci")
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
