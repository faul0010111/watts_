PY ?= python3
export PYTHONPATH := .:tests

.PHONY: help demo test lint bench dashboard experiments experiment report calibrate parity accept clean docker-up docker-down policy-test

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

demo:            ## Run the end-to-end pipeline on the digital twin
	$(PY) watts.py demo

test:            ## Run the full test suite
	$(PY) -m unittest discover -s tests -v

bench:           ## Run the strategy benchmark (writes benchmarks/results/)
	$(PY) watts.py bench

dashboard:       ## Build the Command Center HTML
	$(PY) watts.py dashboard

experiments:     ## Run all nine experiments (writes experiments/results/)
	@for f in experiments/exp*.py; do echo "== $$f"; $(PY) $$f >/dev/null || exit 1; done
	@echo "results in experiments/results/"

experiment:      ## Run one experiment: make experiment EXP=exp03_dynamic_batching
	$(PY) experiments/$(EXP).py

report:          ## Generate the full report for a fixed seed
	$(PY) watts.py demo --seed $(or $(SEED),42)

calibrate:       ## Run the calibration loop (against the twin, and the report says so)
	$(PY) watts.py calibrate

accept:          ## Check the sixteen V2 acceptance criteria
	$(PY) tools/acceptance.py

parity:          ## Regenerate the parity vectors and check both sides agree
	$(PY) tools/generate_parity_vectors.py
	$(PY) -m unittest tests.test_java_parity tests.test_policy_parity -v

policy-test:     ## Evaluate the Rego policies with OPA (requires opa on PATH)
	opa fmt --list policies/
	opa eval -d policies/ 'data.watts' --format pretty >/dev/null && echo "policies load cleanly"

lint:            ## Byte-compile everything as a cheap syntax check
	$(PY) -m compileall -q services simulation benchmarks experiments sdk apps tests tools watts.py

docker-up:       ## Start the local stack (Kafka, TimescaleDB, Redis, OPA, Prometheus, Grafana)
	docker compose -f infrastructure/docker-compose.yml up -d

docker-down:
	docker compose -f infrastructure/docker-compose.yml down -v

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	rm -rf apps/dashboard/dist reports
