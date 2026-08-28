# Deployment guide

## Local installation

```bash
python -m pip install -e ".[dev]"
quant-factor-web --host 127.0.0.1 --port 8000
```

The interactive API documentation is available at `/api/docs`. Keep the default loopback binding for a
personal workstation.

## Docker Compose

```bash
cp .env.example .env
docker compose up --build -d
docker compose ps
```

Set `QUANT_HOST_DATA` in `.env` before startup. The dataset mount is read-only. Runtime databases and generated
artifacts persist through the `./data` and `./artifacts` bind mounts.

## Shared deployment checklist

Quantary v0.3 does not include multi-tenant authentication. Before any networked/shared deployment:

- terminate HTTPS at a trusted reverse proxy;
- require identity-aware authentication and session timeouts;
- restrict inbound networks and apply request/body/rate limits;
- store provider credentials in a secrets manager instead of process memory;
- isolate generated-code execution with CPU, memory, time, filesystem, and network limits;
- back up SQLite state and protect it as research data;
- disable API documentation when it is not needed;
- add centralized audit logs without request bodies or credentials;
- keep live broker credentials and order endpoints in a separate execution service.

## Production boundary

The web process currently combines UI/API, workflow orchestration, retrieval, and local persistence. That is
appropriate for a single-user research node. Multi-user production should separate generated-code workers and
credential storage before scaling the API horizontally.
