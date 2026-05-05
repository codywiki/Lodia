.PHONY: api-test api-dev web-dev up

api-test:
	PYTHONPATH=apps/api python3 -m unittest discover apps/api/tests

api-dev:
	cd apps/api && uvicorn main:app --reload --host 0.0.0.0 --port 8000

web-dev:
	cd apps/web && npm install && npm run dev

up:
	docker compose up --build
