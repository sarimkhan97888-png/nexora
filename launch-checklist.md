# Before real customers
- [ ] Follow docs/RENDER_GUIDE.md fully with Razorpay TEST keys first; make a test payment and see /plan become active
- [ ] Replay the same webhook from Razorpay (Resend) and confirm access is NOT extended twice
- [ ] Test refund in test mode; confirm days are removed
- [ ] Switch to rzp_live keys + a live-mode webhook (webhooks are separate for test and live)
- [ ] Lawyer review of privacy policy / terms / refund policy
- [ ] Install dependencies locally, run unit tests, add PostgreSQL integration tests
- [ ] Test every slash command on a private server (permissions, role hierarchy, ticket persistence after restart)
- [ ] Decide when to leave the free tier (sleeping instance = bot goes offline)
