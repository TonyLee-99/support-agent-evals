"""Run each feature's verification command; the only writer of feature_list.json state."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FL = ROOT / "feature_list.json"


def main() -> int:
    feats = json.loads(FL.read_text())
    only = set(sys.argv[1:])
    for f in feats:
        if only and f["id"] not in only:
            continue
        if f["state"] == "dropped":
            print(f"{f['id']} dropped")
            continue
        r = subprocess.run(f["verification"], shell=True, cwd=ROOT, capture_output=True, text=True, timeout=1800,
                           env={**os.environ, "SE_LLM_BACKEND": "replay"})
        f["state"] = "passing" if r.returncode == 0 else "failing"
        tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]
        f["evidence"] = f"exit {r.returncode}: {tail[0][:200]}"
        print(f"{f['id']} {f['state']:8} {f['evidence'][:120]}")
    FL.write_text(json.dumps(feats, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
