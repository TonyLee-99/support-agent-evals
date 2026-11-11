# Scenario provenance

| file | author | how | count |
|---|---|---|---|
| `self_50.jsonl` | me (project author) | written by hand against `tests/fixtures/retail_small.json` | 50 |
| `codex_50.jsonl` | ChatGPT via `codex exec` (codex-cli 0.164.0), 2026-11-11 | blind: run in a directory containing only `codex_brief.md`, the retail policy and the 30-user fixture; it did not see the agent prompt, the agent code or `self_50.jsonl` (its command log shows reads of the brief files and of the owner's generic build-rules file only) | 50 |

The codex file is committed byte-for-byte as produced (sha256 in `holdout.json`). Any later correction of a codex
item is recorded below rather than edited silently.

Holdout: 20 ids per author, `random.Random(11).sample`, chosen before any agent ran on those scenarios
(`holdout.json`). Exception: S001 and S015 were used in a harness smoke test before the holdout was drawn; both are
in the dev split.

## Corrections
(none)
