# OceanKind device — workstation targets. Nothing here runs on the Pi.
#
#   make venv   one-off: a local virtualenv with the production requirements
#   make test   every offline check: compile, smoke, conformance, soak
#
# These are the tests the docs cite as proof of the contracted work. They were
# broken for a month (upload_status renamed 2026-09-23) because nothing ran them.
# Run `make test` before every commit; it needs no hardware, Azure or Twilio.

PY       ?= .venv/bin/python
TOOLS     = raspberry-pi/tools
SRC       = raspberry-pi/src

.PHONY: venv test compile smoke conformance soak lint-sh

venv:
	python3 -m venv .venv
	.venv/bin/pip install --disable-pip-version-check -q -r raspberry-pi/requirements.txt

test: compile smoke conformance soak
	@echo "DEVICE TESTS: ALL PASS"

compile:
	PYTHONDONTWRITEBYTECODE=1 $(PY) -m compileall -q $(SRC) $(TOOLS) tools
	@for f in raspberry-pi/scripts/*.sh $(TOOLS)/*.sh; do bash -n $$f || exit 1; done

smoke:
	PYTHONDONTWRITEBYTECODE=1 $(PY) $(TOOLS)/phase1_smoke_test.py

conformance:
	PYTHONDONTWRITEBYTECODE=1 $(PY) $(TOOLS)/v2_conformance_test.py

soak:
	PYTHONDONTWRITEBYTECODE=1 $(PY) $(TOOLS)/pipeline_soak_test.py

# Optional: shellcheck if installed.
lint-sh:
	@command -v shellcheck >/dev/null && shellcheck raspberry-pi/scripts/*.sh $(TOOLS)/*.sh || echo "shellcheck not installed"
