# Support, cancellation, refunds (gateway payments)
## Paying
Customer uses `/buy` (or the dashboard) -> pays on the gateway page -> the gateway sends a signed webhook -> access starts
automatically. Nobody needs to send screenshots; screenshots/UTRs are never used to activate anything.
## Customer says "I paid but have no access"
1. Check Razorpay dashboard -> Payments: is it "captured"? 2. Check Razorpay -> Webhooks -> delivery log for that payment.
3. If the webhook failed (e.g. site was asleep), use "Resend" there; processing is idempotent so resending is safe.
4. If it was captured but cannot be matched, owner panel -> Grant (reason required), then fix the cause.
## Cancellation
No auto-renewal exists. "Cancelling" = not renewing. Access lasts until expiry (+ grace days), then paid features switch off; data is kept.
## Refunds (policy is the business owner's decision; proposed: within 48h if unusable)
Refund from the Razorpay dashboard. A full-refund webhook automatically removes that order's days. Partial refunds and
disputes are logged only; the owner decides whether to Revoke (reason required).
## Abuse / support requests
Track each request (id, server, summary, status). Never ask customers for passwords or tokens.
