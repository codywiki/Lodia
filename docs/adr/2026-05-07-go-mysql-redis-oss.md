# ADR: Go + MySQL + Redis + OSS Production Core

Date: 2026-05-07

## Decision

Lodia production core is migrated to:

- Go API and Go Worker
- MySQL 8.x as the primary metadata and state database
- Redis as the asynchronous job dispatch layer
- Independent object storage, with Alibaba Cloud OSS as the production target

## Context

Lodia handles high-value LLM long-horizon task data. The platform needs predictable latency, simple deployment, strong operational familiarity in China, clean horizontal scaling, and a strict separation between metadata and raw/data artifacts.

## Consequences

- Go API becomes the new backend entrypoint under `apps/api-go`.
- MySQL stores submissions, cases, jobs, assets, datasets, reviews and audit logs.
- Redis only distributes job ids; MySQL remains the durable job state source.
- OSS stores raw quarantine objects, uploaded evidence files and generated dataset artifacts.
- Raw objects are purged after successful processing by default through `LODIA_PURGE_RAW_AFTER_PROCESSING=true`.
- Legacy Python code remains in the repository temporarily as migration reference, but Compose and production docs now point to the Go core.

## Follow-ups

- Add official OSS SDK or STS credential flow for direct uploads.
- Add formal migration versioning and rollback plans for MySQL.
- Add OpenTelemetry, Prometheus metrics and structured logs.
- Split multimodal extraction into dedicated worker queues.
