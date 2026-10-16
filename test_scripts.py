import contextlib
import io
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import smoke_test, validate_env  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def make_server(plans_body='{"plans":["weekly","monthly","yearly"]}', secret_leak="", accept_forged=False):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _send(self, code, body="{}"):
            self.send_response(code)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_POST(self):
            self._send(400 if self.path.endswith("/webhooks/razorpay") and not self.server.accept_forged else 200)

        def do_GET(self):
            routes = {"/healthz": (200, "ok"), "/readyz": (200, '{"status":"ok"}'),
                      "/api/v1/guilds": (401, "{}"), "/api/v1/owner/overview": (403, "{}"),
                      "/api/v1/billing/claims": (401, "{}"), "/": (200, secret_leak or "hi"),
                      "/api/v1/billing/plans": (200, plans_body)}
            self._send(*routes.get(self.path, (404, "{}")))
    srv = HTTPServer(("127.0.0.1", 0), H)
    srv.accept_forged = accept_forged
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def run_smoke(*args):
    smoke_test.results.clear()
    out = io.StringIO()
    old = sys.argv
    sys.argv = ["smoke_test.py", *args]
    try:
        with contextlib.redirect_stdout(out):
            code = smoke_test.main()
    finally:
        sys.argv = old
    return code, out.getvalue()


GOOD_ENV = {
    "ENVIRONMENT": "production", "DATABASE_URL": "postgresql+asyncpg://app:pw@db:5432/n",
    "DATABASE_MIGRATION_URL": "postgresql+asyncpg://mig:pw@db:5432/n", "REDIS_URL": "redis://:x@redis:6379/0",
    "DISCORD_BOT_TOKEN": "abc.def.ghi", "DISCORD_CLIENT_ID": "123", "DISCORD_CLIENT_SECRET": "s3cr3t",
    "DISCORD_OAUTH_REDIRECT_URI": "https://nexora.example.com/auth/callback",
    "PUBLIC_BASE_URL": "https://nexora.example.com", "SESSION_SECRET": "a" * 40,
    "OWNER_DISCORD_IDS": "111", "PAYMENT_PROVIDER": "razorpay", "RAZORPAY_KEY_ID": "rzp_live_abc123",
    "RAZORPAY_KEY_SECRET": "ks", "RAZORPAY_WEBHOOK_SECRET": "ws",
    "PRICES": "weekly:INR:9900,monthly:INR:29900,yearly:INR:199900", "SENTRY_DSN": "https://x@sentry.io/1",
}


class EnvValidation(unittest.TestCase):
    def test_good_env_passes(self):
        self.assertEqual(validate_env.validate(GOOD_ENV)[0], [])

    def test_catches_problems(self):
        bad = dict(GOOD_ENV, PUBLIC_BASE_URL="http://x.com", SESSION_SECRET="short",
                   PRICES="lifetime:INR:1", DATABASE_MIGRATION_URL=GOOD_ENV["DATABASE_URL"],
                   OWNER_DISCORD_IDS="abc", DISCORD_BOT_TOKEN="CHANGE_ME")
        errs, warns = validate_env.validate(bad)
        text = " | ".join(errs)
        for needle in ("PUBLIC_BASE_URL", "SESSION_SECRET", "PRICES", "OWNER_DISCORD_IDS", "DISCORD_BOT_TOKEN"):
            self.assertIn(needle, text)
        self.assertTrue(any("same role" in w for w in warns))

    def test_render_style_env_is_accepted(self):
        env = {k: v for k, v in GOOD_ENV.items() if k != "DATABASE_MIGRATION_URL"}
        env["DATABASE_URL"] = "postgresql://u:p@dpg-x.oregon-postgres.render.com/db"
        self.assertEqual(validate_env.validate(env)[0], [])

    def test_payment_key_rules(self):
        self.assertTrue(any("live Razorpay" in e for e in validate_env.validate(dict(GOOD_ENV, ENVIRONMENT="staging", PUBLIC_BASE_URL="https://s.x.com", DISCORD_OAUTH_REDIRECT_URI="https://s.x.com/cb"))[0]))
        self.assertTrue(any("not allowed in production" in e for e in validate_env.validate(dict(GOOD_ENV, PAYMENT_PROVIDER="test"))[0]))
        self.assertTrue(any("RAZORPAY_KEY_SECRET" in e for e in validate_env.validate({k: v for k, v in GOOD_ENV.items() if k != "RAZORPAY_KEY_SECRET"})[0]))
        self.assertTrue(any("TEST key" in w for w in validate_env.validate(dict(GOOD_ENV, RAZORPAY_KEY_ID="rzp_test_abc"))[1]))

    def test_missing_required(self):
        errs = validate_env.validate({})[0]
        self.assertTrue(any("DATABASE_URL" in e for e in errs))

    def test_env_example_parses_and_dev_only_warns(self):
        env = dict(validate_env.parse_env_file(str(ROOT / ".env.example")), ENVIRONMENT="development")
        errors, warns = validate_env.validate(env)
        self.assertEqual(errors, [])           # placeholders are only warnings in development
        self.assertTrue(warns)
        # and it must fail if someone sets production with placeholders
        self.assertTrue(validate_env.validate(dict(env, ENVIRONMENT="production"))[0])

    def test_no_real_secrets_in_example(self):
        text = (ROOT / ".env.example").read_text()
        for marker in ("sk_live", "ghp_", "xoxb-"):
            self.assertNotIn(marker, text)


class SmokeTest(unittest.TestCase):
    def test_passes_against_healthy_stub(self):
        srv = make_server()
        code, out = run_smoke("--base-url", f"http://127.0.0.1:{srv.server_port}")
        srv.shutdown()
        self.assertEqual(code, 0, out)

    def test_fails_if_lifetime_plan_present(self):
        srv = make_server(plans_body='{"plans":["monthly","Lifetime"]}')
        code, out = run_smoke("--base-url", f"http://127.0.0.1:{srv.server_port}")
        srv.shutdown()
        self.assertEqual(code, 1)
        self.assertIn("lifetime", out)

    def test_fails_if_secret_leaked_in_response(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("SESSION_SECRET=super-secret-value-123456\n")
        srv = make_server(secret_leak="debug: super-secret-value-123456")
        code, out = run_smoke("--base-url", f"http://127.0.0.1:{srv.server_port}",
                              "--env-file", f.name, "--skip-db")
        srv.shutdown()
        self.assertEqual(code, 1)
        self.assertIn("do not contain configured secrets", out)

    def test_fails_if_forged_webhook_accepted(self):
        srv = make_server(accept_forged=True)
        code, out = run_smoke("--base-url", f"http://127.0.0.1:{srv.server_port}")
        srv.shutdown()
        self.assertEqual(code, 1)
        self.assertIn("forged webhook", out)

    def test_fails_when_unreachable(self):
        code, _ = run_smoke("--base-url", "http://127.0.0.1:9", "--timeout", "1")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()


class DbUrl(unittest.TestCase):
    def test_neon_and_render_urls(self):
        from nexora.core.config import _async_url
        self.assertEqual(_async_url("postgres://u:p@h/db"), "postgresql+asyncpg://u:p@h/db")
        self.assertEqual(_async_url("postgresql://u:p@ep-1.neon.tech/db?sslmode=require&channel_binding=require"),
                         "postgresql+asyncpg://u:p@ep-1.neon.tech/db?ssl=require")
        self.assertEqual(_async_url("postgresql+asyncpg://u:p@h/db"), "postgresql+asyncpg://u:p@h/db")
