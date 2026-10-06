PYTHON_BIN ?= python3.12
VENV := .venv
PYTHON := $(VENV)/bin/python

.PHONY: install test

install:
	$(PYTHON_BIN) -m venv $(VENV)
	$(PYTHON) -m pip install -r requirements-dev.txt

test:
	$(PYTHON) -m pytest
