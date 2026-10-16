import unittest

from nexora.core.config import Settings, _async_url


class Config(unittest.TestCase):
    def test_neon_url_is_converted_for_asyncpg(self):
        u = _async_url("postgresql://u:p@ep-1.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require")
        self.assertEqual(u, "postgresql+asyncpg://u:p@ep-1.us-east-2.aws.neon.tech/neondb?ssl=require")

    def test_other_forms(self):
        self.assertEqual(_async_url("postgres://u:p@h/db"), "postgresql+asyncpg://u:p@h/db")
        self.assertEqual(_async_url("postgresql+asyncpg://u:p@h/db"), "postgresql+asyncpg://u:p@h/db")

    def test_settings_from_env(self):
        s = Settings.from_env({"DATABASE_URL": "postgres://u:p@h/db", "PRICES": "weekly:INR:9900",
                               "PUBLIC_BASE_URL": "https://x.onrender.com", "OWNER_DISCORD_IDS": "1, 2"})
        self.assertEqual((s.owner_discord_ids, s.secure_cookies, s.run_bot, s.auto_migrate, s.keepalive),
                         (frozenset({1, 2}), True, True, True, True))
        self.assertFalse(Settings.from_env({"DATABASE_URL": "x", "PRICES": "weekly:INR:9900", "RUN_BOT": "false"}).run_bot)


if __name__ == "__main__":
    unittest.main()
