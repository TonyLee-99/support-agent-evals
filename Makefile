# Sample sizes are knobs: `make tau2 N=40 K=4` scales the frozen test subset (N <= 40) and the trials.
N ?= 10
K ?= 2
BACKEND ?= anthropic
SCEN_N ?= 10
VERSION ?= v2

.PHONY: init check lint types test widget gate verify tau2 dev ablations scenarios flywheel regress dashboard screenshots reproduce

init:
	./scripts/fetch_tau2.sh
	uv sync --all-extras
	cd web/widget && npm ci --silent

lint:
	uv run ruff check src tests scripts

types:
	uv run mypy

test:
	SE_LLM_BACKEND=replay uv run pytest -q

widget:
	cd web/widget && npm run --silent check

gate:
	SE_LLM_BACKEND=replay uv run python -m support_evals.flywheel.regress gate --version $(VERSION)

# Offline: replay backend only, zero live LLM calls.
check: lint types test widget gate

verify:
	uv run python scripts/verify_features.py

# ---- live paths (LLM calls; outputs land in results/) ----
tau2:
	uv run python -m support_evals.bench.run --agent llm_agent --split test --n $(N) --trials $(K) --backend $(BACKEND) --run-id llm_agent_test$(N)_api
	uv run python -m support_evals.bench.run --agent policy_agent --split test --n $(N) --trials $(K) --backend $(BACKEND) --run-id policy_agent_test$(N)_api
	uv run python -m support_evals.report tau2

dev:
	uv run python -m support_evals.bench.run --agent llm_agent --split dev --n $(N) --run-id llm_agent_dev$(N)
	uv run python -m support_evals.bench.run --agent policy_agent --split dev --n $(N) --run-id policy_agent_dev$(N)

ablations:
	for a in planner arg_check confirm_gate identity_gate handoff; do \
	  uv run python -m support_evals.bench.run --agent policy_agent__minus_$$a --split dev --n 10 --run-id policy_agent__minus_$${a}_dev10; done
	uv run python -m support_evals.report ablations

scenarios:
	for v in baseline v1 v2; do \
	  SE_LLM_BACKEND=claude_cli uv run python -m support_evals.scenarios --version $$v --split all --n $(SCEN_N) --run-id scen_$${v}_20; \
	  SE_LLM_BACKEND=claude_cli uv run python -m support_evals.report judge scen_$${v}_20; done
	uv run python -m support_evals.report scenarios

flywheel:
	SE_LLM_BACKEND=claude_cli uv run python -m support_evals.flywheel.label --tau2-runs llm_agent_dev10,policy_agent_dev10 --scenario-runs scen_baseline_20,scen_v1_20
	uv run python -m support_evals.flywheel.cluster
	uv run python -m support_evals.flywheel.regress generate

regress:
	for v in v1 v2 broken; do SE_LLM_BACKEND=claude_cli uv run python -m support_evals.flywheel.regress run --version $$v; done

dashboard:
	uv run python -m support_evals.dashboard.build

screenshots: dashboard
	uv run python scripts/screenshots.py

reproduce: tau2 dev scenarios flywheel regress dashboard
