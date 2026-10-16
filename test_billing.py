import asyncio
import unittest
from datetime import UTC, datetime, timedelta

from nexora.billing.domain import (
    FREE, PAID, AccessState, ClaimStatus, PayMethod, PlanCode, Subscription, access_state,
    compute_new_expiry, effective_entitlements, normalize_reference, parse_price_table,
)
from nexora.billing.errors import (
    DuplicateReference, InvalidInput, InvalidState, PriceNotConfigured, ReasonRequired,
    TooManyPending, TrialAlreadyUsed,
)
from nexora.billing.memory_repo import InMemoryRepo
from nexora.billing.service import BillingConfig, BillingService

T0 = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
PRICES = parse_price_table("monthly:INR:19900,yearly:INR:199900,monthly:USD:300")


def make():
    repo = InMemoryRepo()
    return repo, BillingService(repo, BillingConfig(PRICES))


async def claim(svc, ref="UTR123456789012", guild=1, plan=PlanCode.MONTHLY, cur="INR", user=7):
    return await svc.submit_claim(guild_id=guild, user_id=user, plan=plan, method=PayMethod.UPI,
                                  currency=cur, reference=ref, screenshot_ref="s.png", now=T0)


class Domain(unittest.TestCase):
    def test_no_lifetime_plan_exists(self):
        self.assertEqual({p.value for p in PlanCode}, {"trial", "weekly", "monthly", "yearly"})
        with self.assertRaises(ValueError):
            parse_price_table("lifetime:INR:99900")

    def test_trial_cannot_be_priced(self):
        with self.assertRaises(ValueError):
            parse_price_table("trial:INR:100")

    def test_durations(self):
        self.assertEqual(compute_new_expiry(None, T0, PlanCode.TRIAL), T0 + timedelta(days=7))
        self.assertEqual(compute_new_expiry(None, T0, PlanCode.MONTHLY), T0 + timedelta(days=30))
        self.assertEqual(compute_new_expiry(None, T0, PlanCode.YEARLY), T0 + timedelta(days=365))

    def test_early_renewal_stacks_and_lapsed_restarts(self):
        cur = T0 + timedelta(days=5)
        self.assertEqual(compute_new_expiry(cur, T0, PlanCode.MONTHLY), cur + timedelta(days=30))
        self.assertEqual(compute_new_expiry(T0 - timedelta(days=9), T0, PlanCode.MONTHLY),
                         T0 + timedelta(days=30))

    def test_naive_datetime_rejected(self):
        with self.assertRaises(ValueError):
            compute_new_expiry(None, datetime(2026, 1, 1), PlanCode.MONTHLY)

    def test_states_and_entitlements(self):
        sub = Subscription(1, PlanCode.MONTHLY, T0, T0)
        self.assertEqual(access_state(sub, T0 - timedelta(seconds=1), 2), AccessState.ACTIVE)
        self.assertEqual(access_state(sub, T0 + timedelta(days=1), 2), AccessState.GRACE)
        self.assertEqual(access_state(sub, T0 + timedelta(days=2), 2), AccessState.EXPIRED)
        self.assertEqual(effective_entitlements(sub, T0 + timedelta(days=1), 2), PAID)
        self.assertEqual(effective_entitlements(sub, T0 + timedelta(days=3), 2), FREE)
        self.assertEqual(effective_entitlements(None, T0, 2), FREE)

    def test_reference_normalization(self):
        self.assertEqual(normalize_reference(" utr 1234 5678 9012 "), "UTR123456789012")
        for bad in ("", "short", "bad;drop table", "x" * 40):
            with self.assertRaises(ValueError):
                normalize_reference(bad)


class Service(unittest.IsolatedAsyncioTestCase):
    async def test_claim_does_not_grant_access(self):
        repo, svc = make()
        await claim(svc)
        self.assertNotIn(1, repo.subs)

    async def test_approve_grants_exactly_once_under_concurrency(self):
        repo, svc = make()
        c = await claim(svc)
        results = await asyncio.gather(*[svc.approve(c.id, 99, T0) for _ in range(10)])
        self.assertEqual(sum(1 for r in results if not r.already_approved), 1)
        self.assertEqual(repo.subs[1].expires_at, T0 + timedelta(days=30))
        self.assertEqual(sum(1 for a in repo.audit if a["action"] == "claim.approved"), 1)

    async def test_duplicate_reference_rejected_even_concurrently(self):
        repo, svc = make()
        res = await asyncio.gather(claim(svc, guild=1), claim(svc, guild=2), return_exceptions=True)
        self.assertEqual(sum(isinstance(r, DuplicateReference) for r in res), 1)

    async def test_rejected_reference_can_be_resubmitted(self):
        _, svc = make()
        c = await claim(svc)
        await svc.reject(c.id, 99, T0, "not received")
        again = await claim(svc)
        self.assertEqual(again.status, ClaimStatus.PENDING)

    async def test_invalid_inputs(self):
        _, svc = make()
        with self.assertRaises(PriceNotConfigured):
            await claim(svc, cur="EUR")
        with self.assertRaises(InvalidInput):
            await claim(svc, plan=PlanCode.TRIAL)
        with self.assertRaises(InvalidInput):
            await claim(svc, ref="x")

    async def test_pending_limit(self):
        _, svc = make()
        for i in range(3):
            await claim(svc, ref=f"REF0000000{i}")
        with self.assertRaises(TooManyPending):
            await claim(svc, ref="REF00000009")

    async def test_cannot_approve_rejected_or_reject_approved(self):
        _, svc = make()
        a, b = await claim(svc, ref="AAAAAAAA1111"), await claim(svc, ref="BBBBBBBB2222")
        await svc.reject(a.id, 99, T0, "no")
        with self.assertRaises(InvalidState):
            await svc.approve(a.id, 99, T0)
        await svc.approve(b.id, 99, T0)
        with self.assertRaises(InvalidState):
            await svc.reject(b.id, 99, T0, "late")

    async def test_renewal_after_expiry_restores_access(self):
        repo, svc = make()
        first = await claim(svc, ref="AAAAAAAA1111")
        await svc.approve(first.id, 99, T0)
        later = T0 + timedelta(days=40)
        self.assertEqual(effective_entitlements(repo.subs[1], later, 2), FREE)
        second = await claim(svc, ref="BBBBBBBB2222")
        await svc.approve(second.id, 99, later)
        self.assertEqual(effective_entitlements(repo.subs[1], later, 2), PAID)

    async def test_yearly_duration(self):
        repo, svc = make()
        c = await claim(svc, plan=PlanCode.YEARLY)
        await svc.approve(c.id, 99, T0)
        self.assertEqual(repo.subs[1].expires_at, T0 + timedelta(days=365))

    async def test_trial_once_per_guild_and_per_owner(self):
        _, svc = make()
        await svc.start_trial(guild_id=1, owner_discord_id=10, now=T0)
        with self.assertRaises(TrialAlreadyUsed):
            await svc.start_trial(guild_id=1, owner_discord_id=11, now=T0)
        with self.assertRaises(TrialAlreadyUsed):
            await svc.start_trial(guild_id=2, owner_discord_id=10, now=T0)

    async def test_manual_grant_and_revoke_need_reason_and_are_audited(self):
        repo, svc = make()
        with self.assertRaises(ReasonRequired):
            await svc.manual_grant(guild_id=1, plan=PlanCode.MONTHLY, owner_id=99, reason=" ", now=T0)
        await svc.manual_grant(guild_id=1, plan=PlanCode.MONTHLY, owner_id=99, reason="giveaway", now=T0)
        await svc.revoke(guild_id=1, owner_id=99, reason="abuse", now=T0 + timedelta(days=1))
        self.assertEqual(effective_entitlements(repo.subs[1], T0 + timedelta(days=4), 2), FREE)
        self.assertEqual([a["action"] for a in repo.audit], ["grant.manual", "grant.revoked"])

    async def test_failed_transaction_rolls_back(self):
        repo, svc = make()
        c = await claim(svc)
        with self.assertRaises(RuntimeError):
            async with repo.transaction() as tx:
                await tx.upsert_subscription(Subscription(1, PlanCode.YEARLY, T0, T0))
                raise RuntimeError("boom")
        self.assertNotIn(1, repo.subs)
        self.assertEqual(repo.claims[c.id].status, ClaimStatus.PENDING)

    async def test_reminders_sent_once_and_rerun_safe(self):
        repo, svc = make()
        c = await claim(svc)
        await svc.approve(c.id, 99, T0)
        t = T0 + timedelta(days=27, hours=1)  # ~2.96 days left -> "3 day" reminder
        self.assertEqual([d for _, d in await svc.due_reminders(t)], [3])
        self.assertEqual(await svc.due_reminders(t), [])
        t2 = T0 + timedelta(days=29, hours=1)  # <1 day left -> "1 day" reminder
        self.assertEqual([d for _, d in await svc.due_reminders(t2)], [1])
