PYTHON  ?= python

DBML    := input.dbml
SQL     := output.sql

.PHONY: help venv run tests clean

help:
	@echo "Cibles disponibles :"
	@echo "  make venv   - cree le venv (utilise PYTHON, defaut: python)"
	@echo "  make run    - genere $(SQL) a partir de $(DBML) (le venv doit etre active)"
	@echo "  make tests  - lance la suite de tests unitaires (le venv doit etre active)"
	@echo "  make clean  - supprime les fichiers __pycache__"

venv:
	$(PYTHON) -m venv venv

run:
	$(PYTHON) dbml2sql.py $(DBML) $(SQL)

tests:
	$(PYTHON) -m unittest discover -s tests -v

clean:
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
