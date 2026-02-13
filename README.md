# DevOps Order System — CI/CD Hands-on Exercise

```mermaid
flowchart LR
  Client["Client"] -->|HTTP| Gateway["NGINX Gateway\n(gateway)"]
  Gateway -->|reverse-proxy| Order["order-service\nHTTP:5000"]
  Order -->|POST /reserve| InventoryAPI["inventory-service\nHTTP:5001"]
  InventoryAPI -->|atomic Lua reserve| Redis[("Redis (inventory-db)")]
  Order -->|insert order| Postgres[("Postgres (order-db)")]
  Order -->|publish `order_created`| Rabbit[("RabbitMQ")]
  Rabbit -->|consume event| InventoryConsumer["inventory-service\nconsumer (read-only)"]
  Order -->|metrics| Prom["Prometheus"]
  InventoryAPI -->|metrics| Prom
  Prom --> Graf["Grafana"]
```

What this application does
- Accepts HTTP order requests through an NGINX gateway to `order-service`.
- `order-service` attempts a stock reservation via `inventory-service`'s `/reserve` endpoint before inserting an order into Postgres.
- `inventory-service` performs atomic reservations using Redis + a small Lua script to avoid negative stock.
- Successful orders publish an `order_created` event to RabbitMQ; services expose Prometheus metrics.

Motivation
- This repository is a hands-on DevOps/CI-CD exercise. It is intended to teach and let you experiment with:
  - Docker & Docker Compose for reproducible local stacks
  - Unit and service tests with `pytest`
  - Building CI pipelines (GitHub Actions) that run tests before build/publish
  - Basic operational concerns: secrets handling, logs, metrics, and simple design tradeoffs

Repository layout
- `order-service/` — Flask API to create orders, reserve inventory, persist to Postgres, and publish events
- `inventory-service/` — Flask API to reserve stock and view inventory (Redis-backed)
- `gateway/` — NGINX config (HTTP entry point for the demo)
- `monitoring/` — Prometheus config
- `docker-compose.yml` — local stack orchestration for hands-on testing
- `.github/workflows/ci-cd.yml` — CI pipeline (tests → build → push)

Quickstart (minimal, reproducible)

Prerequisites
- Docker and Docker Compose (v2) installed and running.

1) Create a local `.env` file by copying the example and edit values (DO NOT COMMIT):

```bash
# Unix / macOS:
cp .env.example .env

# Windows (PowerShell):
Copy-Item .env.example .env
```

If you accidentally committed a `.env` with secrets, untrack it and commit the cleanup:

```bash
# Stop tracking the file but keep it locally
git rm --cached .env
git add .gitignore .env.example
git commit -m "Add .env.example and stop tracking .env"
```

To remove `.env` from repository history (rewrites history; coordinate with your team), use `git filter-repo` or the BFG Repo-Cleaner. Example with `git filter-repo`:

```bash
# install git-filter-repo first (system package / pip)
git filter-repo --path .env --invert-paths
# then force-push rewritten history
git push --force --all
```

2) Start the stack (from the repository root):

```bash
docker compose up -d
```

3) (Optional) Run the tests for each service locally:

```bash
cd order-service && pytest -q
cd ../inventory-service && pytest -q
```

4) Smoke test — create an order (curl example):

```bash
curl -sS -X POST http://localhost:8080/orders \
  -H 'Content-Type: application/json' \
  -d '{"item":"widget","quantity":1}'
```

5) Inspect runtime state (examples):

```bash
# List running containers
docker compose ps

# Query Postgres orders table
docker compose exec -e PGPASSWORD=$POSTGRES_PASSWORD order-db \
  psql -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT * FROM orders;"

# Read inventory from Redis
docker compose exec inventory-db redis-cli GET widget
```

Notes
- If you change Postgres credentials or the `.env` defaults, recreate volumes to reinitialize the DB:

```bash
docker compose down -v
docker compose up -d
```

CI / GitHub Actions
- Workflow: `.github/workflows/ci-cd.yml` — runs tests for each service and, on success, builds and (optionally) pushes images.
- To enable image publishing, configure GitHub Secrets: `DOCKER_HUB_USERNAME` and `DOCKER_HUB_ACCESS_TOKEN` (or other registry credentials).

Design choices (summary)
- Current stack choice: Redis + Lua for fast, atomic reservations; Postgres for long-term order storage; RabbitMQ for events.
- Why Redis: provides low-latency atomic operations and is useful for demonstrating race-free reservations in a hands-on exercise.
- Why Postgres: single source of truth for orders and relational queries.
- Alternatives: implement reservations in Postgres using transactional row locking (`SELECT ... FOR UPDATE`) for stronger consistency at the cost of contention/latency.
- Recommendation: keep the Redis-based reservation for the exercise (fast feedback), and document or add an optional Postgres transactional implementation for comparison.

Service functional notes
- `order-service`: validates input, calls `inventory-service` `/reserve`, inserts order on success, and publishes `order_created` to RabbitMQ.
- `inventory-service`: `POST /reserve` performs atomic initialize-and-decrement with Lua; `GET /inventory/<item>` returns current stock.

Troubleshooting & support
- Tail logs:

```bash
docker compose logs -f order-service inventory-service order-db rabbitmq
```

- If tests fail in CI, check that the Actions workflow installs each service's dependencies and that required services are reachable during tests.

Next steps / exercises
- Add more unit tests and mark slow integration tests so CI runs fast.
- Harden CI: add caching, linters, and pinned dependency versions.
- Add a deployment job (Kubernetes, ECS, etc.) to complete the full CI→CD flow.

This README is the canonical guide for this repository and contains the service-level functional descriptions.
