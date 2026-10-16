#!/usr/bin/env python3
"""Validate NEXORA environment variables. Prints variable NAMES only, never values.
Usage: python scripts/validate_env.py [--env-file .env]   (exit 1 on errors)"""
import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nexora.billing.domain import PlanCode, parse_price_table  # noqa: E402

REQUIRED = ["ENVIRONMENT", "DATABASE_URL", "DISCORD_BOT_TOKEN", "DISCORD_CLIENT_ID", "DISCORD_CLIENT_SECRET",
            "DISCORD_OAUTH_REDIRECT_URI", "PUBLIC_BASE_URL", "SESSION_SECRET", "OWNER_DISCORD_IDS",
            "PAYMENT_PROVIDER", "PRICES"]
SECRETS = ["DISCORD_BOT_TOKEN", "DISCORD_CLIENT_SECRET", "SESSION_SECRET", "RAZORPAY_KEY_SECRET",
           "RAZORPAY_WEBHOOK_SECRET", "DATABASE_URL", "DATABASE_MIGRATION_URL", "AI_API_KEY"]


def parse_env_file(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if v[:1] in "\"'" and v[-1:] == v[:1] and len(v) > 1:
            v = v[1:-1]
        else:
            v = re.split(r"\s+#", v, maxsplit=1)[0].strip()
        env[k.strip()] = v
    return env


def _origin(u: str) -> tuple[str, str]:
    p = urlparse(u)
    return p.scheme, p.netloc


def validate(env: dict[str, str]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warns: list[str] = []
    mode = env.get("ENVIRONMENT", "")
    if mode not in ("development", "staging", "production"):
        errors.append("ENVIRONMENT must be development|staging|production")
    strict = mode in ("staging", "production")
    for k in REQUIRED:
        if not env.get(k, "").strip():
            errors.append(f"{k} is missing or empty")
    for k, v in env.items():
        if "CHANGE_ME" in v:
            (errors if strict else warns).append(f"{k} still contains a placeholder")
    if env.get("DISCORD_CLIENT_ID") and not env["DISCORD_CLIENT_ID"].isdigit():
        errors.append("DISCORD_CLIENT_ID must be numeric")
    if env.get("OWNER_DISCORD_IDS") and not re.fullmatch(r"\d+(\s*,\s*\d+)*", env["OWNER_DISCORD_IDS"]):
        errors.append("OWNER_DISCORD_IDS must be comma-separated numeric ids")
    for k in ("DATABASE_URL", "DATABASE_MIGRATION_URL"):
        if env.get(k) and not env[k].startswith(("postgresql+asyncpg://", "postgresql://", "postgres://")):
            errors.append(f"{k} must be a PostgreSQL URL")
    if env.get("DATABASE_URL") and env.get("DATABASE_URL") == env.get("DATABASE_MIGRATION_URL"):
        warns.append("DATABASE_URL and DATABASE_MIGRATION_URL use the same role (least privilege not applied)")
    prov = env.get("PAYMENT_PROVIDER", "")
    if prov and prov not in ("razorpay", "test"):
        errors.append("PAYMENT_PROVIDER must be razorpay (or test for local development)")
    if prov == "test" and mode == "production":
        errors.append("PAYMENT_PROVIDER=test is not allowed in production")
    if prov == "razorpay":
        for k in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET"):
            if not env.get(k, "").strip():
                errors.append(f"{k} is missing or empty")
        kid = env.get("RAZORPAY_KEY_ID", "")
        if kid and "CHANGE_ME" not in kid:
            m = re.fullmatch(r"rzp_(test|live)_\w+", kid)
            if not m:
                errors.append("RAZORPAY_KEY_ID has an unexpected format")
            elif m.group(1) == "live" and mode != "production":
                errors.append("live Razorpay keys are only allowed with ENVIRONMENT=production")
            elif m.group(1) == "test" and mode == "production":
                warns.append("Razorpay TEST key in production: payments are fake")
    if env.get("PRICES"):
        try:
            table = parse_price_table(env["PRICES"])
            for plan in (PlanCode.WEEKLY, PlanCode.MONTHLY, PlanCode.YEARLY):
                if (plan, "INR") not in table:
                    warns.append(f"PRICES has no INR price for {plan.value}")
        except ValueError as e:
            errors.append(f"PRICES invalid: {e}")
    for k, lo in (("GRACE_DAYS", 0), ("MAX_PENDING_ORDERS", 1)):
        if env.get(k) and (not env[k].isdigit() or int(env[k]) < lo):
            errors.append(f"{k} must be an integer >= {lo}")
    if env.get("REMINDER_DAYS") and not re.fullmatch(r"\d+(,\d+)*", env["REMINDER_DAYS"]):
        errors.append("REMINDER_DAYS must look like 3,1")
    if env.get("SESSION_SECRET") and "CHANGE_ME" not in env["SESSION_SECRET"] and len(env["SESSION_SECRET"]) < 32:
        errors.append("SESSION_SECRET must be at least 32 characters")
    if strict:
        for k in ("PUBLIC_BASE_URL", "DISCORD_OAUTH_REDIRECT_URI"):
            if env.get(k) and not env[k].startswith("https://"):
                errors.append(f"{k} must use https in {mode}")
            if env.get(k) and "localhost" in env[k]:
                errors.append(f"{k} must not point to localhost in {mode}")
        if env.get("PUBLIC_BASE_URL") and env.get("DISCORD_OAUTH_REDIRECT_URI") and \
                _origin(env["PUBLIC_BASE_URL"]) != _origin(env["DISCORD_OAUTH_REDIRECT_URI"]):
            errors.append("DISCORD_OAUTH_REDIRECT_URI must be on the same origin as PUBLIC_BASE_URL")
        if not env.get("SENTRY_DSN"):
            warns.append("SENTRY_DSN is empty: no error monitoring")
    return errors, warns


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file")
    a = ap.parse_args()
    env = parse_env_file(a.env_file) if a.env_file else dict(os.environ)
    errors, warns = validate(env)
    for w in warns:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    print("env validation:", "FAILED" if errors else "OK")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
