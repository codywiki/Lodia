.PHONY: go-format go-format-check go-tidy go-test go-docker-test api-dev worker-dev api-test web-dev web-build compose-config image-build deploy-smoke production-smoke ci up

GO ?= go

go-format:
	cd apps/api-go && $(GO) fmt ./...

go-format-check:
	cd apps/api-go && test -z "$$(gofmt -l .)"

go-tidy:
	cd apps/api-go && $(GO) mod tidy && $(GO) fmt ./...

go-test:
	cd apps/api-go && $(GO) test ./...

go-docker-test:
	docker run --rm -v "$$(pwd)/apps/api-go:/src" -w /src golang:1.22-alpine sh -c "go mod tidy && gofmt -w . && go test ./..."

api-test: go-test

api-dev:
	cd apps/api-go && $(GO) run ./cmd/api

worker-dev:
	cd apps/api-go && $(GO) run ./cmd/worker

web-dev:
	cd apps/web && npm install && npm run dev

web-build:
	cd apps/web && npm ci && npm run build

compose-config:
	docker compose config --quiet
	docker compose --env-file .env.production.example -f docker-compose.prod.yml config --quiet
	docker compose --env-file .env.production.example -f docker-compose.prod.yml -f docker-compose.build.yml config --quiet

image-build:
	docker compose --env-file .env.production.example -f docker-compose.prod.yml -f docker-compose.build.yml build api web

deploy-smoke:
	bash scripts/deploy_smoke.sh

production-smoke:
	bash scripts/go_smoke.sh

ci: go-docker-test web-build compose-config

up:
	docker compose up --build
