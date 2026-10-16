# Operations runbook (untested procedures until rehearsed)

## PostgreSQL backup / restore
- Backup: `BACKUP_DIR=/var/backups/nexora AGE_RECIPIENT=age1... ./scripts/backup.sh` via daily cron; copy off-host.
- Keep 14 daily + 8 weekly + 6 monthly (adjust to data-retention.md). Dumps contain personal data: encrypt, never commit.
- **Rehearse restore monthly** into a scratch DB: `./scripts/restore.sh <dump>` (default target `nexora_restore_test`),
  then run the smoke test against a copy. A backup you never restored is unverified.
- Live restore (disaster only): stop api, `./scripts/restore.sh <dump> --into-live --confirm <dbname>`.
- Targets (proposal): RPO 24h, RTO 4h. For lower RPO enable WAL archiving / managed Postgres with PITR.

## Redis
Not used by the current code (rate limits are per-process, in-memory). Add Redis only when you run more than one API instance. Until then there is no Redis failure mode.

## Migrations
Run only as the explicit `migrate` step with the `nexora_migrator` role. The runtime role `nexora_app` has DML only.
After the first migration: `REVOKE UPDATE, DELETE ON audit_logs FROM nexora_app;` (append-only audit trail).
Prefer backward-compatible migrations (add columns/tables first, remove later) so a code rollback stays possible.

## Rollback / recovery
1. Bad release, no schema change: `NEXORA_VERSION=<previous> docker compose ... up -d api`.
2. Bad release with additive migration: roll code back; leave schema (compatible).
3. Destructive migration or data damage: stop services, restore the pre-deploy backup (taken in step 6 of deployment), redeploy previous version.
4. Server lost: new VPS, restore `.env` from your password manager, restore latest dump, redeploy, update DNS.
5. Bot token leaked: reset in Developer Portal -> update `.env` -> restart bot.

## Secret rotation (schedule quarterly or on suspicion)
Discord bot token / client secret: reset in portal, update `.env`, restart. `SESSION_SECRET`: change (logs everyone out).
DB passwords: `ALTER ROLE ... PASSWORD`, update `.env`, restart.  Razorpay: regenerate API keys / webhook secret in its dashboard. Remove ex-staff from
`OWNER_DISCORD_IDS` immediately.

## Logging, monitoring, alerts
JSON logs to stdout (`docker compose logs`; cap with Docker `max-size`). Never log tokens, UTRs in full, or message content.
Error monitoring: Sentry via `SENTRY_DSN`. Uptime: external monitor (e.g. UptimeRobot) on `https://$NEXORA_DOMAIN/readyz`
is **not** exposed by Caddy by design; monitor the public `/` or add a token-protected status route.
Alert on: API 5xx rate, bot gateway disconnected > 5 min, backup job failed, failed payment webhook
older than 24h, disk > 80%.

## Background jobs
Retry with exponential backoff + full jitter (`nexora/core/retry.py`, `JOB_MAX_RETRIES`), jobs must be idempotent
(expiry/reminders already are: reminder log has a unique constraint). After max retries: log error + alert, do not loop forever.

## Abuse protection
Per-IP and per-user limits (Redis) on login, claim submission (also max pending claims per guild), AI commands, dashboard
writes. Caddy request-size limit 1 MB. Screenshot uploads: allow only PNG/JPG/WebP, max 5 MB, verify magic bytes.

## Persistent files
Transcripts in the `transcripts` volume (`/data/transcripts`), included in backups; access only via authorized endpoints.
