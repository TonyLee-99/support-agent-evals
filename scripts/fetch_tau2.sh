#!/usr/bin/env bash
# Clone tau2-bench at the pinned commit (shallow) into third_party/. Idempotent.
set -euo pipefail
PINNED=c06b07dd7c4d4ec61ba6ff9bcdf0b15dd7ef36d4
DIR="$(cd "$(dirname "$0")/.." && pwd)/third_party/tau2-bench"
if [ -d "$DIR/.git" ]; then
  echo "tau2-bench present at $(git -C "$DIR" rev-parse --short HEAD)"; exit 0
fi
mkdir -p "$DIR"
git -C "$DIR" init -q
git -C "$DIR" remote add origin https://github.com/sierra-research/tau2-bench
git -C "$DIR" fetch -q --depth 1 origin "$PINNED"
git -C "$DIR" checkout -q FETCH_HEAD
echo "tau2-bench checked out at $PINNED"
