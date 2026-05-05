.PHONY: api-install api-test api-dev web-dev up

.venv:
	python3 -m venv .venv

api-install: .venv
	.venv/bin/python -m pip install -r apps/api/requirements.txt

api-test: api-install
	PYTHONPATH=apps/api .venv/bin/python -m unittest discover apps/api/tests

api-dev: api-install
	cd apps/api && ../../.venv/bin/uvicorn main:app --reload --host 0.0.0.0 --port 8000

web-dev:
	cd apps/web && npm install && npm run dev

up:
	docker compose up --build
