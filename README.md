# Currency Rates API

[![CI](https://github.com/GlowyStar/currency-rates-api/actions/workflows/ci.yml/badge.svg)](https://github.com/GlowyStar/currency-rates-api/actions/workflows/ci.yml)

A small service that serves ECB reference exchange rates over REST and gRPC. A worker
pulls daily rates from the free [Frankfurter API](https://frankfurter.dev), derives the
cross rate of every currency pair and keeps the history in ClickHouse; PostgreSQL holds
the currency catalog and the API clients. History requests are downsampled inside
ClickHouse to at most `max_points` averaged buckets, so a chart never gets more points
than it can draw, whether it covers ten days or ten years. Python 3.12, FastAPI, grpcio, SQLAlchemy 2.0 async,
Alembic, ClickHouse, uv, Docker Compose; typed with mypy in strict mode.

## Architecture

```mermaid
flowchart LR
    frankfurter["Frankfurter API<br/>(ECB reference rates)"]
    worker["Ingestion worker"]
    postgres[("PostgreSQL<br/>catalog, API clients")]
    clickhouse[("ClickHouse<br/>rate history")]
    rest["REST API<br/>(FastAPI)"]
    grpc["gRPC server"]
    services["Services<br/>(shared rules)"]

    frankfurter -- "HTTP with retries" --> worker
    worker -- "currency catalog" --> postgres
    worker -- "cross rates" --> clickhouse
    rest --> services
    grpc --> services
    services -- "codes, API keys" --> postgres
    services -- "latest, downsampled history" --> clickhouse
```

One image runs three processes: `api` (REST), `grpc` and `worker`. A one-shot `migrate`
job applies the Alembic migrations and creates the ClickHouse table before any of them
starts, so no process races another for the schema.

## Why two databases

- **PostgreSQL** keeps small relational data that must stay consistent: the currency
  catalog (primary key, ISO code check) and the API clients (unique name, unique key
  hash). Rows change rarely and are read by key.
- **ClickHouse** keeps the rate history: append-mostly rows of `(base, quote, date, rate)`,
  about 225 thousand a year for the 30 ECB currencies (870 ordered pairs a day). Columnar
  storage and aggregation make the downsampling a single `GROUP BY` over the primary key
  range, and `ReplacingMergeTree` makes loading the same days again harmless.

## Design decisions

| Decision | Why |
| --- | --- |
| Frankfurter v2 with `providers=ECB` | Free and keyless; v1 is deprecated. Restricting to the ECB keeps the meaning of "reference rate" instead of a blend of providers. |
| Cross rates derived by the worker | The ECB publishes rates against EUR only. One upstream request per sync instead of one per base, and every read stays a lookup of one pair. |
| Cross rates rounded to 6 significant digits | The ECB publishes five or six; more digits would be noise. |
| `ReplacingMergeTree(ingested_at)` ordered by `(base, quote, date)`, reads with `FINAL` | Re-ingestion is idempotent, and a corrected rate replaces the old one. |
| The worker reloads from the last stored day | Incremental sync without any state besides the data; a day fetched before the ECB published it gets completed on the next run. |
| Backfill in windows of 365 days | A long backfill never holds more than a year of rows in memory. |
| Bucket width computed in Python, SQL constant with bound parameters | The bucket arithmetic is unit tested, and no value is ever formatted into SQL. |
| API keys stored as SHA-256 of 256-bit random tokens | A leaked table is useless and lookup is one unique index; slow password hashes are for low-entropy secrets. |
| gRPC stubs committed next to `rates.proto` | The runtime image and the tests need no protoc step; CI regenerates the stubs and fails on any drift. |
| Services shared by REST and gRPC | Validation and rules live once; transports only translate requests and errors. |
| Retries with exponential backoff on transport errors, 429 and 5xx only | A 4xx means the request is wrong, and repeating it cannot help. |

## API

REST (OpenAPI docs at `http://localhost:8000/docs`):

| Method | Path | Auth | Returns |
| --- | --- | --- | --- |
| GET | `/health` | none | `{"postgres": true, "clickhouse": true}`, 503 if a database is down |
| GET | `/v1/currencies` | none | The currency catalog |
| GET | `/v1/rates/latest?base=USD&symbols=EUR,GBP` | `X-API-Key` | Rates of the last published day; all currencies when `symbols` is empty |
| GET | `/v1/rates/history?base=USD&symbol=EUR&start=2025-10-21&end=2026-10-21&max_points=200` | `X-API-Key` | Average rate per bucket, at most `max_points` (1 to 1000, default 200) buckets; `end` defaults to today, `start` to a year before `end` |

gRPC, service `currency_rates.v1.RatesService` from
[`rates.proto`](src/currency_rates/proto/rates.proto), API key in the `x-api-key` metadata:

| RPC | Kind | Returns |
| --- | --- | --- |
| `Latest(LatestRequest)` | unary | `LatestResponse` with a map of rates |
| `History(HistoryRequest)` | server streaming | `HistoryPoint` per bucket |

Errors: unknown currency or invalid parameters give 422 / `INVALID_ARGUMENT`, a base
without stored rates gives 404 / `NOT_FOUND`, a missing or wrong key gives 401 /
`UNAUTHENTICATED`.

## Run

```bash
docker compose up --build -d
docker compose logs worker          # the first backfill of a year takes a few seconds
API_KEY=$(docker compose exec -T api python -m currency_rates.create_api_key demo)
```

The key is printed once; only its hash is stored. If ports 5432 or 8123 are taken on
your machine, start with `POSTGRES_HOST_PORT=55432 CLICKHOUSE_HOST_PORT=58123 docker compose up --build -d`.

```bash
curl -H "X-API-Key: $API_KEY" "localhost:8000/v1/rates/latest?base=USD&symbols=EUR,GBP,JPY"
# {"base":"USD","date":"2026-10-21","rates":{"EUR":0.890155,"GBP":0.746172,"JPY":157.878}}

curl -H "X-API-Key: $API_KEY" "localhost:8000/v1/rates/history?base=USD&symbol=EUR&max_points=4"
# {"base":"USD","symbol":"EUR","bucket_days":92,"points":[{"date":"2025-10-21","rate":0.8593...},...]}
```

With [grpcurl](https://github.com/fullstorydev/grpcurl) (`brew install grpcurl`):

```bash
grpcurl -plaintext -import-path src/currency_rates/proto -proto rates.proto \
  -H "x-api-key: $API_KEY" -d '{"base": "USD", "symbols": ["EUR", "GBP"]}' \
  localhost:50051 currency_rates.v1.RatesService/Latest

grpcurl -plaintext -import-path src/currency_rates/proto -proto rates.proto \
  -H "x-api-key: $API_KEY" -d '{"base": "USD", "symbol": "EUR", "max_points": 3}' \
  localhost:50051 currency_rates.v1.RatesService/History
```

## Test

```bash
uv sync
docker compose up -d postgres clickhouse
uv run pytest                # unit and integration tests
uv run pytest tests/unit     # no databases needed
uv run ruff check && uv run ruff format --check && uv run mypy
```

Integration tests create and use their own `rates_test` databases, so the demo data stays
untouched. They expect the compose credentials; point them elsewhere with
`TEST_POSTGRES_DSN`, `TEST_CLICKHOUSE_HOST` and `TEST_CLICKHOUSE_PORT`. HTTP calls to
Frankfurter are replaced by an `httpx.MockTransport` in every test.

After a change of `rates.proto`, regenerate the stubs:

```bash
uv run python -m grpc_tools.protoc -I src --python_out=src --pyi_out=src \
  --grpc_python_out=src src/currency_rates/proto/rates.proto
```

## Configuration

Environment variables (or a `.env` file, see [`.env.example`](.env.example)):

| Variable | Default | Meaning |
| --- | --- | --- |
| `POSTGRES_DSN` | required | SQLAlchemy URL, `postgresql+asyncpg://user:password@host:5432/db` |
| `CLICKHOUSE_HOST` | `localhost` | ClickHouse host |
| `CLICKHOUSE_PORT` | `8123` | ClickHouse HTTP port |
| `CLICKHOUSE_USER` | `default` | ClickHouse user |
| `CLICKHOUSE_PASSWORD` | required | ClickHouse password |
| `CLICKHOUSE_DATABASE` | `default` | ClickHouse database |
| `FRANKFURTER_URL` | `https://api.frankfurter.dev` | Upstream API |
| `BACKFILL_DAYS` | `365` | History loaded when ClickHouse is empty |
| `POLL_INTERVAL_SECONDS` | `3600` | Pause between two syncs of the worker |
| `GRPC_PORT` | `50051` | Port of the gRPC server |

## Layout

```
src/currency_rates/
  api/rest.py, api/grpc_server.py   transports: request parsing, error mapping
  services/                         rules shared by both transports, API keys
  storage/postgres.py               SQLAlchemy models and queries
  storage/clickhouse.py             schema, inserts, latest and history queries
  ingestion/frankfurter.py          HTTP client with retries
  ingestion/worker.py               sync loop, cross rates
  migrate.py, create_api_key.py     one-shot commands
  proto/                            rates.proto and its stubs
migrations/                         Alembic
tests/unit, tests/integration
```
