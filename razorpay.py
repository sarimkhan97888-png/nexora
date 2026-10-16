"""Razorpay adapter (Payment Links + signed webhooks). Written from knowledge of Razorpay's docs and NOT
yet tested against Razorpay's sandbox: verify with test keys (rzp_test_...) before using live keys."""
from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping

from nexora.billing.orders import (EventKind, InvalidSignature, MalformedEvent, Order, ProviderError,
                                   ProviderEvent)

API = "https://api.razorpay.com/v1"


class RazorpayProvider:
    def __init__(self, key_id: str, key_secret: str, webhook_secret: str):
        self.key_id, self.key_secret, self.webhook_secret = key_id, key_secret, webhook_secret

    async def create_payment_link(self, order: Order, callback_url: str, expire_by: int) -> tuple[str, str]:
        import aiohttp
        payload = {"amount": order.amount_minor, "currency": order.currency, "accept_partial": False,
                   "reference_id": order.id, "description": f"NEXORA {order.plan.value} plan",
                   "notes": {"guild_id": str(order.guild_id), "plan": order.plan.value, "order_id": order.id},
                   "callback_url": callback_url, "callback_method": "get", "expire_by": expire_by,
                   "reminder_enable": False}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15),
                                             auth=aiohttp.BasicAuth(self.key_id, self.key_secret)) as s:
                async with s.post(f"{API}/payment_links", json=payload) as r:
                    data = await r.json()
                    if r.status not in (200, 201):
                        raise ProviderError(f"razorpay status {r.status}")
            return data["id"], data["short_url"]
        except (aiohttp.ClientError, KeyError, ValueError, TimeoutError) as e:
            raise ProviderError("razorpay request failed") from e

    def parse_webhook(self, body: bytes, headers: Mapping[str, str]) -> ProviderEvent:
        h = {k.lower(): v for k, v in headers.items()}
        sig = h.get("x-razorpay-signature", "")
        good = hmac.new(self.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        if not self.webhook_secret or not sig or not hmac.compare_digest(sig, good):
            raise InvalidSignature()
        event_id = h.get("x-razorpay-event-id", "")
        if not event_id:
            raise MalformedEvent("missing event id")
        try:
            data = json.loads(body)
            name, pl = data["event"], data.get("payload", {})
            ent = lambda k: (pl.get(k) or {}).get("entity") or {}  # noqa: E731
            if name == "payment_link.paid":
                link, pay = ent("payment_link"), ent("payment")
                if not link or not pay:
                    raise MalformedEvent("paid event without entities")
                if link.get("status") != "paid" or pay.get("status") != "captured":
                    return ProviderEvent(event_id, EventKind.IGNORED)
                return ProviderEvent(event_id, EventKind.PAID, link["reference_id"], link["id"],
                                     pay["id"], int(pay["amount"]), str(pay["currency"]))
            if name in ("payment_link.expired", "payment_link.cancelled"):
                link = ent("payment_link")
                kind = EventKind.EXPIRED if name.endswith("expired") else EventKind.CANCELLED
                return ProviderEvent(event_id, kind, link.get("reference_id"), link.get("id"))
            if name == "refund.processed":
                ref = ent("refund")
                return ProviderEvent(event_id, EventKind.REFUNDED, payment_id=ref["payment_id"],
                                     amount_minor=int(ref["amount"]), currency=ref.get("currency"))
            if name == "payment.dispute.created":
                return ProviderEvent(event_id, EventKind.DISPUTED, payment_id=ent("dispute").get("payment_id"))
            return ProviderEvent(event_id, EventKind.IGNORED)
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as e:
            raise MalformedEvent(str(e)) from None
