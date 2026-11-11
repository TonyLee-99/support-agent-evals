"""Fail unless tau2-bench is at the pinned commit, MIT-licensed, and the retail domain files are byte-identical."""

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAU2 = ROOT / "third_party" / "tau2-bench"
PINNED = "c06b07dd7c4d4ec61ba6ff9bcdf0b15dd7ef36d4"


def main() -> int:
    if not TAU2.exists():
        print("third_party/tau2-bench missing; run make init")
        return 1
    head = subprocess.run(["git", "-C", str(TAU2), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    ok = head == PINNED
    print(f"commit {head} pinned={ok}")
    lic = (TAU2 / "LICENSE").read_text().splitlines()[0].strip()
    ok &= lic == "MIT License"
    print(f"license: {lic}")
    for line in (ROOT / "scripts" / "tau2_pristine.sha256").read_text().splitlines():
        rel, digest = line.split()
        actual = hashlib.sha256((TAU2 / rel).read_bytes()).hexdigest()
        same = actual == digest
        ok &= same
        print(f"{'OK ' if same else 'MODIFIED'} {rel}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
