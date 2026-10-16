#!/usr/bin/env python3
"""NEXORA deployment smoke test. READ-ONLY: only unauthenticated GET requests, no payments,
no Discord calls, no writes. Endpoint paths below are the planned API contract (assumed).
Sends only GETs plus one deliberately forged, unsigned webhook that must be refused.
Usage: python scripts/smoke_test.py --base-url https://nexora.example.com [--env-file .env]
Exit code 1 if any check FAILS (SKIP does not fail)."""
import argparse
import asyncio
import json
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.validate_env import SECRETS, parse_env_file, validate  # noqa: E402

results: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    print(f"[{status}] {name}" + (f" - {detail}" if detail else ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def get(url: str, timeout: float):
    try:
        with _opener.open(urllib.request.Request(url, method="GET"), timeout=timeout) as r:
            return r.status, dict(r.headers), r.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read(200_000).decode("utf-8", "replace")


def check_env(env: dict) -> None:
    errors, _ = validate(env)
    record("FAIL" if errors else "PASS", "environment variables", "; ".join(errors[:5]))


def check_db(env: dict, timeout: float) -> None:
    url = env.get("DATABASE_URL", "")
    p = urlparse(url.replace("postgresql+asyncpg://", "postgresql://"))
    if not p.hostname:
        return record("FAIL", "database connectivity", "DATABASE_URL has no host")
    try:
        socket.create_connection((p.hostname, p.port or 5432), timeout=timeout).close()
    except OSError as e:
        return record("FAIL", "database TCP connect", type(e).__name__)
    try:
        import asyncpg  # type: ignore
    except ImportError:
        return record("SKIP", "database SELECT 1", "asyncpg not installed (TCP reachable only)")

    async def q():
        c = await asyncpg.connect(url.replace("+asyncpg", ""), timeout=timeout)
        try:
            return await c.fetchval("SELECT 1")
        finally:
            await c.close()
    try:
        ok = asyncio.run(q()) == 1
        record("PASS" if ok else "FAIL", "database SELECT 1")
    except Exception as e:  # noqa: BLE001
        record("FAIL", "database SELECT 1", type(e).__name__)


def check_migrations(env: dict) -> None:
    if not shutil.which("alembic") or not Path("alembic.ini").exists():
        return record("SKIP", "migrations at head", "alembic or alembic.ini not available")
    try:
        cur = subprocess.run(["alembic", "current"], capture_output=True, text=True, timeout=60, env={**env})
        head = subprocess.run(["alembic", "heads"], capture_output=True, text=True, timeout=60, env={**env})
        c, h = cur.stdout.split(" ")[0:1], head.stdout.split(" ")[0:1]
        record("PASS" if c and c == h else "FAIL", "migrations at head", f"current={c} head={h}")
    except Exception as e:  # noqa: BLE001
        record("FAIL", "migrations at head", type(e).__name__)


def post_forged_webhook(url: str, timeout: float) -> int | None:
    """Sends a deliberately FAKE, unsigned webhook. A correct server must refuse it (4xx). No side effects."""
    req = urllib.request.Request(url, data=b'{"event":"payment_link.paid"}', method="POST",
                                 headers={"Content-Type": "application/json", "X-Razorpay-Signature": "0" * 64,
                                          "X-Razorpay-Event-Id": "smoke-test"})
    try:
        with _opener.open(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001
        return None


def check_http(base: str, env: dict, timeout: float) -> None:
    base = base.rstrip("/")
    secret_values = [env[k] for k in SECRETS if len(env.get(k, "")) >= 12]
    bodies: list[str] = []

    def req(path):
        try:
            s, h, b = get(base + path, timeout)
            bodies.append(b)
            return s, h, b
        except Exception as e:  # noqa: BLE001
            return None, {}, type(e).__name__

    s, _, b = req("/healthz")
    record("PASS" if s == 200 else "FAIL", "GET /healthz", f"status={s}")
    s, _, b = req("/readyz")
    ready = s == 200
    try:
        ready = ready and json.loads(b).get("status", "ok") == "ok"
    except json.JSONDecodeError:
        pass
    record("PASS" if ready else "FAIL", "GET /readyz", f"status={s}")
    for path in ("/api/v1/guilds", "/api/v1/owner/overview", "/api/v1/billing/claims"):
        s, _, _ = req(path)
        record("PASS" if s in (401, 403, 404) else "FAIL", f"unauthenticated {path} denied", f"status={s}")
    s, _, b = req("/api/v1/billing/plans")
    if s == 200:
        bad = "lifetime" in b.lower()
        record("FAIL" if bad else "PASS", "plans list has no lifetime plan")
    else:
        record("FAIL", "GET /api/v1/billing/plans", f"status={s}")
    code = post_forged_webhook(base + "/api/v1/webhooks/razorpay", timeout)
    record("PASS" if code in (400, 401, 403, 503) else "FAIL", "forged webhook is rejected", f"status={code}")
    s, _, _ = req("/definitely-not-a-route")
    record("PASS" if s == 404 else "FAIL", "unknown route returns 404", f"status={s}")
    s, h, _ = req("/")
    hl = {k.lower() for k in h}
    missing = [x for x in ("x-content-type-options",) if x not in hl]
    if base.startswith("https://") and "strict-transport-security" not in hl:
        missing.append("strict-transport-security")
    record("FAIL" if missing else "PASS", "security headers", ",".join(missing))
    leaked = [i for i, v in enumerate(secret_values) if any(v in b for b in bodies)]
    record("FAIL" if leaked else "PASS", "responses do not contain configured secrets")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--env-file")
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--skip-db", action="store_true")
    a = ap.parse_args()
    env = parse_env_file(a.env_file) if a.env_file else {}
    if env:
        check_env(env)
        if not a.skip_db:
            check_db(env, a.timeout)
            check_migrations(env)
    else:
        record("SKIP", "env/db/migration checks", "no --env-file given")
    check_http(a.base_url, env, a.timeout)
    fails = [r for r in results if r[0] == "FAIL"]
    print(f"\n{len(results) - len(fails)} ok/skipped, {len(fails)} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
