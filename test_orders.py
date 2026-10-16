import asyncio
import hashlib
import hmac
import json
import unittest
from datetime import UTC, datetime, timedelta

from nexora.billing.domain import FREE, PAID, PlanCode, effective_entitlements, parse_price_table
from nexora.billing.errors import InvalidInput, PriceNotConfigured, TooManyPending
from nexora.billing.memory_repo import InMemoryRepo
from nexora.billing.orders import (EventKind, InvalidSignature, MalformedEvent, OrderService, OrderStatus,
                                   ProviderError, ProviderEvent)
from nexora.billing.razorpay import RazorpayProvider
from nexora.billing.service import BillingConfig, BillingService

T0 = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
PRICES = parse_price_table("weekly:INR:9900,monthly:INR:29900,yearly:INR:199900")
SECRET = "whsec_test_secret"


class FakeProvider:
    def __init__(self, fail=False): self.fail, self.n = fail, 0

    async def create_payment_link(self, order, callback_url, expire_by):
        if self.fail:
            raise ProviderError("down")
        self.n += 1
        return f"plink_{self.n}", f"https://rzp.io/i/{self.n}"


def setup(fail=False):
    repo = InMemoryRepo()
    return repo, OrderService(repo, BillingConfig(PRICES), FakeProvider(fail), "https://x.test/billing/thanks")


def paid(order, event_id="e1", amount=None, currency="INR", payment="pay_1", ref=None):
    return ProviderEvent(event_id, EventKind.PAID, order.id, ref or order.provider_ref, payment,
                         order.amount_minor if amount is None else amount, currency)


async def checkout(svc, plan=PlanCode.MONTHLY, guild=1):
    return await svc.create_checkout(guild_id=guild, user_id=7, plan=plan, currency="INR", now=T0)


class Prices(unittest.TestCase):
    def test_requested_prices(self):
        self.assertEqual(PRICES[(PlanCode.WEEKLY, "INR")], 9900)    # Rs 99 / 7 days
        self.assertEqual(PRICES[(PlanCode.MONTHLY, "INR")], 29900)  # Rs 299 / 30 days
        self.assertEqual(PRICES[(PlanCode.YEARLY, "INR")], 199900)  # Rs 1999 / 365 days


class Checkout(unittest.IsolatedAsyncioTestCase):
    async def test_amount_comes_from_server_config(self):
        repo, svc = setup()
        o = await checkout(svc, PlanCode.YEARLY)
        self.assertEqual((o.amount_minor, o.status), (199900, OrderStatus.CREATED))
        self.assertTrue(o.short_url.startswith("https://"))

    async def test_creating_link_grants_nothing(self):
        repo, svc = setup()
        await checkout(svc)
        self.assertEqual(repo.subs, {})

    async def test_invalid_requests(self):
        _, svc = setup()
        with self.assertRaises(InvalidInput):
            await svc.create_checkout(guild_id=1, user_id=7, plan=PlanCode.TRIAL, currency="INR", now=T0)
        with self.assertRaises(PriceNotConfigured):
            await svc.create_checkout(guild_id=1, user_id=7, plan=PlanCode.MONTHLY, currency="USD", now=T0)

    async def test_provider_failure_cancels_order(self):
        repo, svc = setup(fail=True)
        with self.assertRaises(ProviderError):
            await checkout(svc)
        self.assertEqual([o.status for o in repo.orders.values()], [OrderStatus.CANCELLED])

    async def test_checkout_spam_limited(self):
        _, svc = setup()
        with self.assertRaises(TooManyPending):
            for _ in range(20):
                await checkout(svc)


class Webhooks(unittest.IsolatedAsyncioTestCase):
    async def test_verified_payment_grants_exact_duration(self):
        repo, svc = setup()
        for plan, days in ((PlanCode.WEEKLY, 7), (PlanCode.MONTHLY, 30), (PlanCode.YEARLY, 365)):
            r = InMemoryRepo(); s = OrderService(r, BillingConfig(PRICES), FakeProvider(), "u")
            o = await checkout(s, plan)
            self.assertEqual(await s.handle_event(paid(o), T0), "granted")
            self.assertEqual(r.subs[1].expires_at, T0 + timedelta(days=days))
            self.assertEqual(effective_entitlements(r.subs[1], T0, 2), PAID)

    async def test_replayed_event_is_ignored(self):
        repo, svc = setup()
        o = await checkout(svc)
        self.assertEqual(await svc.handle_event(paid(o), T0), "granted")
        self.assertEqual(await svc.handle_event(paid(o), T0 + timedelta(hours=1)), "duplicate")
        self.assertEqual(repo.subs[1].expires_at, T0 + timedelta(days=30))

    async def test_second_event_id_for_same_order_does_not_double_grant(self):
        repo, svc = setup()
        o = await checkout(svc)
        await svc.handle_event(paid(o, "e1"), T0)
        self.assertEqual(await svc.handle_event(paid(o, "e2"), T0), "already_paid")
        self.assertEqual(repo.subs[1].expires_at, T0 + timedelta(days=30))

    async def test_simultaneous_duplicate_callbacks(self):
        repo, svc = setup()
        o = await checkout(svc)
        res = await asyncio.gather(*[svc.handle_event(paid(o), T0) for _ in range(10)])
        self.assertEqual(res.count("granted"), 1)
        self.assertEqual(res.count("duplicate"), 9)
        self.assertEqual(sum(a["action"] == "payment.verified" for a in repo.audit), 1)

    async def test_wrong_amount_and_currency_rejected(self):
        repo, svc = setup()
        o = await checkout(svc)
        self.assertEqual(await svc.handle_event(paid(o, "a", amount=100), T0), "rejected_amount")
        self.assertEqual(await svc.handle_event(paid(o, "b", currency="USD"), T0), "rejected_currency")
        self.assertEqual(repo.subs, {})
        self.assertEqual(await svc.handle_event(paid(o, "c"), T0), "granted")  # real payment still works

    async def test_wrong_reference_and_unknown_order(self):
        repo, svc = setup()
        o = await checkout(svc)
        self.assertEqual(await svc.handle_event(paid(o, "a", ref="plink_other"), T0), "rejected_reference")
        bad = ProviderEvent("b", EventKind.PAID, "no-such-order", "x", "pay_9", 100, "INR")
        self.assertEqual(await svc.handle_event(bad, T0), "unknown_order")
        self.assertEqual(repo.subs, {})

    async def test_same_payment_cannot_pay_two_orders(self):
        repo, svc = setup()
        a, b = await checkout(svc), await checkout(svc)
        await svc.handle_event(paid(a, "e1", payment="pay_X"), T0)
        self.assertEqual(await svc.handle_event(paid(b, "e2", payment="pay_X"), T0), "rejected_duplicate_payment")

    async def test_delayed_event_after_link_expired_still_grants_verified_payment(self):
        repo, svc = setup()
        o = await checkout(svc)
        await svc.handle_event(ProviderEvent("x", EventKind.EXPIRED, o.id), T0)
        self.assertEqual(repo.orders[o.id].status, OrderStatus.EXPIRED)
        late = T0 + timedelta(days=2)
        self.assertEqual(await svc.handle_event(paid(o, "late"), late), "granted")
        self.assertEqual(repo.subs[1].expires_at, late + timedelta(days=30))  # from verified time

    async def test_simultaneous_renewals_stack(self):
        repo, svc = setup()
        a, b = await checkout(svc), await checkout(svc)
        await asyncio.gather(svc.handle_event(paid(a, "e1", payment="p1"), T0),
                             svc.handle_event(paid(b, "e2", payment="p2"), T0))
        self.assertEqual(repo.subs[1].expires_at, T0 + timedelta(days=60))

    async def test_expiry_then_renewal_restores_access(self):
        repo, svc = setup()
        o = await checkout(svc, PlanCode.WEEKLY)
        await svc.handle_event(paid(o, "e1", payment="p1"), T0)
        later = T0 + timedelta(days=20)
        self.assertEqual(effective_entitlements(repo.subs[1], later, 2), FREE)
        o2 = await svc.create_checkout(guild_id=1, user_id=7, plan=PlanCode.MONTHLY, currency="INR", now=later)
        await svc.handle_event(paid(o2, "e2", payment="p2"), later)
        self.assertEqual(effective_entitlements(repo.subs[1], later, 2), PAID)

    async def test_full_refund_removes_that_orders_days_only(self):
        repo, svc = setup()
        a, b = await checkout(svc), await checkout(svc)
        await svc.handle_event(paid(a, "e1", payment="p1"), T0)
        await svc.handle_event(paid(b, "e2", payment="p2"), T0)
        r = ProviderEvent("r1", EventKind.REFUNDED, payment_id="p2", amount_minor=b.amount_minor)
        self.assertEqual(await svc.handle_event(r, T0 + timedelta(hours=1)), "refunded")
        self.assertEqual(repo.subs[1].expires_at, T0 + timedelta(days=30))
        self.assertEqual(repo.orders[b.id].status, OrderStatus.REFUNDED)

    async def test_partial_refund_and_dispute_need_owner_decision(self):
        repo, svc = setup()
        o = await checkout(svc)
        await svc.handle_event(paid(o, "e1", payment="p1"), T0)
        self.assertEqual(await svc.handle_event(ProviderEvent("r", EventKind.REFUNDED, payment_id="p1", amount_minor=100), T0), "partial_refund")
        self.assertEqual(await svc.handle_event(ProviderEvent("d", EventKind.DISPUTED, payment_id="p1"), T0), "disputed")
        self.assertEqual(repo.subs[1].expires_at, T0 + timedelta(days=30))  # unchanged

    async def test_failed_payment_event_changes_nothing(self):
        repo, svc = setup()
        o = await checkout(svc)
        await svc.handle_event(ProviderEvent("c", EventKind.CANCELLED, o.id), T0)
        self.assertEqual(repo.subs, {})

    async def test_owner_manual_grant_and_revoke_still_audited(self):
        repo, _ = setup()
        b = BillingService(repo, BillingConfig(PRICES))
        await b.manual_grant(guild_id=5, plan=PlanCode.WEEKLY, owner_id=1, reason="giveaway winner", now=T0)
        await b.revoke(guild_id=5, owner_id=1, reason="abuse", now=T0)
        self.assertEqual([a["action"] for a in repo.audit], ["grant.manual", "grant.revoked"])


def signed(payload: dict, secret=SECRET, event_id="evt_1"):
    body = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id}


PAID_PAYLOAD = {"event": "payment_link.paid", "payload": {
    "payment_link": {"entity": {"id": "plink_1", "reference_id": "order-1", "status": "paid"}},
    "payment": {"entity": {"id": "pay_1", "status": "captured", "amount": 29900, "currency": "INR"}}}}


class RazorpayAdapter(unittest.TestCase):
    p = RazorpayProvider("rzp_test_x", "secret", SECRET)

    def test_valid_signature_parses(self):
        ev = self.p.parse_webhook(*signed(PAID_PAYLOAD))
        self.assertEqual((ev.kind, ev.order_id, ev.amount_minor, ev.currency, ev.payment_id),
                         (EventKind.PAID, "order-1", 29900, "INR", "pay_1"))

    def test_forged_signatures_rejected(self):
        body, h = signed(PAID_PAYLOAD)
        for bad in ({**h, "X-Razorpay-Signature": "0" * 64}, {"X-Razorpay-Event-Id": "e"}, {**h, "X-Razorpay-Signature": ""}):
            with self.assertRaises(InvalidSignature):
                self.p.parse_webhook(body, bad)
        with self.assertRaises(InvalidSignature):  # signed with the wrong secret
            self.p.parse_webhook(*signed(PAID_PAYLOAD, secret="attacker"))
        with self.assertRaises(InvalidSignature):  # body tampered after signing
            self.p.parse_webhook(body.replace(b"29900", b"100"), h)
        with self.assertRaises(InvalidSignature):  # empty configured secret never validates
            RazorpayProvider("k", "s", "").parse_webhook(*signed(PAID_PAYLOAD, secret=""))

    def test_malformed_and_unknown_events(self):
        with self.assertRaises(MalformedEvent):
            self.p.parse_webhook(*signed({"event": "payment_link.paid", "payload": {}}))
        with self.assertRaises(MalformedEvent):
            self.p.parse_webhook(*signed(PAID_PAYLOAD, event_id=""))
        self.assertEqual(self.p.parse_webhook(*signed({"event": "something.else"})).kind, EventKind.IGNORED)

    def test_uncaptured_payment_is_not_paid(self):
        d = json.loads(json.dumps(PAID_PAYLOAD)); d["payload"]["payment"]["entity"]["status"] = "authorized"
        self.assertEqual(self.p.parse_webhook(*signed(d)).kind, EventKind.IGNORED)

    def test_refund_and_expiry_events(self):
        r = {"event": "refund.processed", "payload": {"refund": {"entity": {"payment_id": "pay_1", "amount": 29900, "currency": "INR"}}}}
        self.assertEqual(self.p.parse_webhook(*signed(r)).kind, EventKind.REFUNDED)
        e = {"event": "payment_link.expired", "payload": {"payment_link": {"entity": {"id": "p", "reference_id": "o"}}}}
        self.assertEqual(self.p.parse_webhook(*signed(e)).kind, EventKind.EXPIRED)


if __name__ == "__main__":
    unittest.main()
