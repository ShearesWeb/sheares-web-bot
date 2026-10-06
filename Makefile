PYTHON_BIN ?= python3.12
VENV := .venv
PYTHON := $(VENV)/bin/python

# Everything that differs per developer or environment lives in .env; see .env.example.
-include .env
export

.PHONY: install dev test

install:
	$(PYTHON_BIN) -m venv $(VENV)
	$(PYTHON) -m pip install -r requirements-dev.txt

dev: .env
	$(PYTHON) src/app.py

test:
	$(PYTHON) -m pytest
