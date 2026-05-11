# Blueprints

A self-hosted [Terraform HTTP backend](https://developer.hashicorp.com/terraform/language/backend/http) that stores workspace state in PostgreSQL. Authentication and authorization are delegated to an external [Gatekeeper](https://github.com/jhparker7/gatekeeper) service.

## Features

- Full Terraform backend protocol: GET/POST/DELETE state, LOCK/UNLOCK
- Per-workspace pessimistic locking with lock-ID validation
- Bearer token and HTTP Basic auth (Basic credentials are exchanged for a token via Gatekeeper)
- Prometheus metrics at `/metrics`
- Distributed tracing via OpenTelemetry → Tempo
- Structured JSON logging via Loki

## Requirements

- Python 3.12+
- PostgreSQL
- A running [Gatekeeper](https://github.com/jhparker7/gatekeeper) instance

## Configuration

All configuration is via environment variables.

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:test@127.0.0.1:5432/blueprints` | PostgreSQL connection string |
| `GATEKEEPER_URL` | `http://localhost:8080` | Base URL of the Gatekeeper service |
| `TEMPO_ENDPOINT` | `http://localhost:4318` | OTLP/HTTP endpoint for trace export |
| `LOKI_URL` | `http://localhost:3100` | Loki push endpoint for log shipping |
| `TELEMETRY_ENABLED` | `true` | Set to `false` to disable tracing and Loki log shipping |

## Running locally

```bash
pip install -r src/requirements.txt
DATABASE_URL=postgresql://postgres:pass@localhost:5432/blueprints \
  GATEKEEPER_URL=http://localhost:8080 \
  python src/app.py
```

The server listens on port `8081`.

## Docker

```bash
docker run -p 8081:8000 \
  -e DATABASE_URL=postgresql://postgres:pass@db:5432/blueprints \
  -e GATEKEEPER_URL=http://gatekeeper:8080 \
  ghcr.io/jhparker7/blueprints:latest
```

## Terraform configuration

```hcl
terraform {
  backend "http" {
    address        = "http://blueprints:8081/state/<workspace>"
    lock_address   = "http://blueprints:8081/state/<workspace>"
    unlock_address = "http://blueprints:8081/state/<workspace>"
    username       = "user@example.com"
    password       = "your-password"
  }
}
```

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/state/{workspace}` | Fetch state (204 if none exists) |
| `POST` | `/state/{workspace}` | Store/update state |
| `DELETE` | `/state/{workspace}` | Delete state |
| `LOCK` | `/state/{workspace}` | Acquire workspace lock |
| `UNLOCK` | `/state/{workspace}` | Release workspace lock |
| `GET` | `/metrics` | Prometheus metrics |

## Testing

```bash
pip install -r src/requirements.txt

# Unit tests (no external services required)
python -m pytest tests/unit_tests/

# Integration tests (requires running Blueprints, Gatekeeper, and PostgreSQL)
python -m pytest tests/integration_tests/
```

## Release

Releases are automated via [semantic-release](https://semantic-release.gitbook.io) on push to `master`. A passing test run triggers a version bump, GitHub release, and a Docker image push to `ghcr.io/jhparker7/blueprints`.
