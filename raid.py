from __future__ import annotations

from collections import deque


class JoinRateMonitor:
    """Triggers once when >= threshold joins happen within window seconds, then stays quiet for cooldown."""

    def __init__(self, threshold: int, window: int, cooldown: int = 300):
        self.threshold, self.window, self.cooldown = threshold, window, cooldown
        self._joins: deque[float] = deque()
        self._quiet_until = 0.0

    def record(self, now: float) -> bool:
        self._joins.append(now)
        while self._joins and now - self._joins[0] > self.window:
            self._joins.popleft()
        if len(self._joins) >= self.threshold and now >= self._quiet_until:
            self._quiet_until = now + self.cooldown
            return True
        return False


def plan_response(policy: dict, triggered: bool) -> list[str]:
    """Only explicitly enabled, REVERSIBLE actions. Never bans/kicks. emergency_disable wins."""
    if not triggered or policy.get("emergency_disable") or not policy.get("enabled"):
        return []
    actions = ["alert"]
    if policy.get("auto_quarantine") and policy.get("quarantine_role_id"):
        actions.append("quarantine")  # reversible: role can be removed with /security release
    return actions


def advisory_note(account_age_days: int) -> str | None:
    """Account age is an unreliable signal: it may be LOGGED for staff, never used to punish."""
    return f"new account ({account_age_days}d) - advisory only" if account_age_days < 7 else None
