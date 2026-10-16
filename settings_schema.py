"""Per-guild settings: safe defaults + strict validation. No XP/economy (excluded by owner)."""
from __future__ import annotations

import copy
from typing import Any

DEFAULTS: dict[str, dict[str, Any]] = {
    "moderation": {"antispam_enabled": False, "max_messages": 6, "per_seconds": 5,
                   "duplicate_count": 3, "mention_limit": 5, "ignored_roles": [],
                   "ignored_channels": [], "log_channel_id": None, "dm_on_action": True,
                   "warn_thresholds": [{"count": 3, "action": "timeout", "minutes": 60},
                                       {"count": 5, "action": "kick"}]},
    "welcome": {"enabled": False, "channel_id": None, "message": "Welcome {user} to {server}!"},
    "leave": {"enabled": False, "channel_id": None, "message": "{user} left {server}."},
    "autoroles": {"role_ids": []},
    "tickets": {"category_id": None, "support_role_ids": [], "max_open_per_user": 1,
                "log_channel_id": None, "transcripts": True},
    "security": {"enabled": False, "join_threshold": 10, "window_seconds": 60,
                 "alert_channel_id": None, "auto_quarantine": False,
                 "quarantine_role_id": None, "emergency_disable": False},
    "ai": {"enabled": False, "channel_ids": [], "personality":
           "You are a helpful assistant for this Discord server.", "faq": ""},
}

_B, _ID, _IDL = "bool", "id", "idlist"
RULES: dict[tuple[str, str], tuple] = {
    ("moderation", "antispam_enabled"): (_B,), ("moderation", "dm_on_action"): (_B,),
    ("moderation", "max_messages"): ("int", 2, 30), ("moderation", "per_seconds"): ("int", 2, 60),
    ("moderation", "duplicate_count"): ("int", 2, 10), ("moderation", "mention_limit"): ("int", 2, 30),
    ("moderation", "ignored_roles"): (_IDL, 25), ("moderation", "ignored_channels"): (_IDL, 50),
    ("moderation", "log_channel_id"): (_ID,), ("moderation", "warn_thresholds"): ("thresholds",),
    ("welcome", "enabled"): (_B,), ("welcome", "channel_id"): (_ID,), ("welcome", "message"): ("str", 1000),
    ("leave", "enabled"): (_B,), ("leave", "channel_id"): (_ID,), ("leave", "message"): ("str", 1000),
    ("autoroles", "role_ids"): (_IDL, 5),
    ("tickets", "category_id"): (_ID,), ("tickets", "support_role_ids"): (_IDL, 10),
    ("tickets", "max_open_per_user"): ("int", 1, 5), ("tickets", "log_channel_id"): (_ID,),
    ("tickets", "transcripts"): (_B,),
    ("security", "enabled"): (_B,), ("security", "join_threshold"): ("int", 3, 200),
    ("security", "window_seconds"): ("int", 10, 600), ("security", "alert_channel_id"): (_ID,),
    ("security", "auto_quarantine"): (_B,), ("security", "quarantine_role_id"): (_ID,),
    ("security", "emergency_disable"): (_B,),
    ("ai", "enabled"): (_B,), ("ai", "channel_ids"): (_IDL, 20),
    ("ai", "personality"): ("str", 500), ("ai", "faq"): ("str", 3000),
}


def _is_id(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 < v < 2**63


def _check(rule: tuple, v):
    kind = rule[0]
    if kind == "bool":
        return v if isinstance(v, bool) else _fail("must be true/false")
    if kind == "int":
        ok = isinstance(v, int) and not isinstance(v, bool) and rule[1] <= v <= rule[2]
        return v if ok else _fail(f"must be an integer {rule[1]}-{rule[2]}")
    if kind == "id":
        return v if v is None or _is_id(v) else _fail("must be a Discord ID or null")
    if kind == "idlist":
        ok = isinstance(v, list) and len(v) <= rule[1] and all(_is_id(x) for x in v)
        return list(dict.fromkeys(v)) if ok else _fail(f"must be a list of up to {rule[1]} IDs")
    if kind == "str":
        ok = isinstance(v, str) and len(v) <= rule[1]
        return v if ok else _fail(f"must be text up to {rule[1]} characters")
    if kind == "thresholds":
        if not isinstance(v, list) or len(v) > 5:
            _fail("must be a list of up to 5 thresholds")
        last, out = 0, []
        for t in v:
            if not isinstance(t, dict) or t.get("action") not in ("timeout", "kick", "ban"):
                _fail("each threshold needs action timeout|kick|ban")
            c = t.get("count")
            if not isinstance(c, int) or isinstance(c, bool) or not (1 <= c <= 50) or c <= last:
                _fail("threshold counts must be increasing integers 1-50")
            last = c
            item = {"count": c, "action": t["action"]}
            if t["action"] == "timeout":
                m = t.get("minutes", 60)
                if not isinstance(m, int) or isinstance(m, bool) or not (1 <= m <= 40320):
                    _fail("timeout minutes must be 1-40320")
                item["minutes"] = m
            out.append(item)
        return out
    raise AssertionError(kind)


class _Invalid(Exception):
    pass


def _fail(msg: str):
    raise _Invalid(msg)


def validate_patch(patch: Any) -> tuple[dict, list[str]]:
    """Returns (clean_patch, errors). Unknown sections/keys are errors, never silently ignored."""
    errors: list[str] = []
    clean: dict[str, dict] = {}
    if not isinstance(patch, dict):
        return {}, ["settings must be an object"]
    for sec, vals in patch.items():
        if sec not in DEFAULTS or not isinstance(vals, dict):
            errors.append(f"unknown section '{sec}'")
            continue
        for key, v in vals.items():
            rule = RULES.get((sec, key))
            if rule is None:
                errors.append(f"unknown setting '{sec}.{key}'")
                continue
            try:
                clean.setdefault(sec, {})[key] = _check(rule, v)
            except _Invalid as e:
                errors.append(f"{sec}.{key} {e}")
    return clean, errors


def merge(current: dict | None, patch: dict) -> dict:
    out = copy.deepcopy(DEFAULTS)
    for sec, vals in (current or {}).items():
        if sec in out and isinstance(vals, dict):
            out[sec].update(vals)
    for sec, vals in patch.items():
        out[sec].update(vals)
    return out


def render_template(msg: str, user: str, server: str) -> str:
    """Only {user} and {server} are substituted (no str.format -> no attribute/format injection)."""
    return msg.replace("{user}", user).replace("{server}", server)[:1900]
