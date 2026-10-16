> ADVANCED / optional VPS route. For the free route use docs/RENDER_GUIDE.md. Note: the Compose files here are untested.

# NEXORA deployment (VPS + Docker Compose + Caddy)

> Status: **NOT deployed, NOT verified.** Compose files have not been run through `docker compose config`
> (Docker was unavailable when written). Platform choice (single Ubuntu LTS VPS, >= 2 GB RAM) is an assumption.

## Local development
```bash
cp .env.example .env            # fill real dev values (own test Discord app/server)
python scripts/validate_env.py --env-file .env
docker compose up -d db redis
docker compose run --rm migrate  # explicit migration step
docker compose up -d api
python scripts/smoke_test.py --base-url http://127.0.0.1:8000 --env-file .env
```

## Production / staging (same steps, different `.env`)
1. Server: Ubuntu LTS, firewall allows only 22 (restrict to your IP), 80, 443. Create a non-root deploy user, SSH keys only.
2. DNS: A/AAAA record for `NEXORA_DOMAIN` -> server IP. Caddy fetches HTTPS certificates automatically.
3. `git clone` the repo; create `.env` (`chmod 600`). `ENVIRONMENT=production`, https URLs. Staging: separate server/domain,
   separate Discord application and separate database, `ENVIRONMENT=staging`.
4. Build and tag: `NEXORA_VERSION=2026.10.1 docker build -f docker/Dockerfile -t nexora:$NEXORA_VERSION .`
5. Validate: `python scripts/validate_env.py --env-file .env` (must print OK).
6. Back up the DB (see operations.md), then migrate **as its own step**:
   `NEXORA_VERSION=... docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm migrate`
7. Start: `... up -d db api caddy`
8. Verify: `python scripts/smoke_test.py --base-url https://$NEXORA_DOMAIN --env-file .env`
9. In Discord Developer Portal set the OAuth redirect to `https://$NEXORA_DOMAIN/auth/callback`.

## Staging and payments
Use Razorpay TEST keys and a test-mode webhook. Never point staging at the production database or Discord token.

## Graceful shutdown / Discord reconnect
Compose sends SIGTERM and waits `stop_grace_period` (30s). The app must stop accepting work, finish
in-flight interactions, close the DB pool, then exit (to be implemented in the entrypoint). discord.py
reconnects to the gateway automatically; persistent button views are re-registered on startup.

## Resource limits
Set in `docker-compose.prod.yml` (api+bot 1G, db 1G). Tune after load testing.
`DB_POOL_SIZE` x processes must stay below Postgres `max_connections`.
