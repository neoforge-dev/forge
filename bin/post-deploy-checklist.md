# Post-Deploy Checklist

Run this after every Railway deploy of IS or VC backends.

---

## 1. Automated Smoke Tests

```bash
./bin/deploy-smoke-test.sh all
```

Expected: all checks PASS. If any FAIL, check Railway logs before continuing.

To test a single product:
```bash
./bin/deploy-smoke-test.sh is
./bin/deploy-smoke-test.sh vc
```

To test against localhost during development:
```bash
./bin/deploy-smoke-test.sh is --base-url http://localhost:8000
./bin/deploy-smoke-test.sh vc --base-url http://localhost:8001
```

---

## 2. Manual Verification

- [ ] Create test user via signup form on the live app
- [ ] Confirm welcome email arrives (check spam if needed)
- [ ] Log in with the test user and verify the dashboard loads

---

## 3. Stripe Payment Flow

- [ ] Trigger a test payment using Stripe test card `4242 4242 4242 4242`
- [ ] Verify the $1 charge appears in Stripe Dashboard (test mode)
- [ ] Issue a refund from Stripe Dashboard
- [ ] Confirm webhook events show `payment_intent.succeeded` and `charge.refunded`

---

## 4. SEO

- [ ] Submit updated sitemap to Google Search Console for `codeswiftr.com`
- [ ] Submit updated sitemap to Google Search Console for `brandfocus.ai`

---

## 5. Go-to-Market

- [ ] Post first LinkedIn post announcing the product (use scheduled post if off-hours)
- [ ] DM first 5 bootcamp instructor targets with the outreach template

---

## Notes

- Smoke test script: `bin/deploy-smoke-test.sh`
- Individual product scripts: `bin/smoke-test-is.sh`, `bin/smoke-test-vc.sh`
- Connection refused on smoke tests = backend not deployed yet (expected before deploy)
