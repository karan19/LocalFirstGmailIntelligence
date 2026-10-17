# V3 validation — September 30, 2026

> Historical milestone notes. Current setup and limitations: [release validation](release-validation.md).


Implemented against NexusNote's **V3 — Subscription Cleanup + Restore** and
all seven answers in **Questions on V3**.

## Automated checks

- `python -m unittest discover -s tests -q`: **64 tests passed**, including all
  38 V0/V1/V2 regression tests and 26 V3 tests.
- `npm run build --prefix frontend`: **passed** (TypeScript and Vite).
- `git diff --check`: **passed**.

V3 checks exercise actual RSA/DKIM verification using synthetic mail and a public
key, required signed headers, tampering, duplicate headers, URL schemes and
credentials, private/mixed DNS destinations, pinned IP/TLS hostname behavior,
POST body and absence of cookies/authorization, redirect rejection, HTTP failures
and timeout handling. They also cover exact list/sender identity, conservative
subscription evidence, discovery pagination/stopping/partial coverage, protected
mail, confirmation expiry/selection/account binding, manual completion, pending
write recovery, partial Trash/Restore failures, legacy migration, account
separation, independent data clearing, and refreshed source-message references.

No private signing key or real message content is included in test fixtures.

## Historical acceptance checks

Earlier maintainer testing exercised sign-in, subscription discovery, one explicit
unsubscribe request, a selected-message Trash/Restore cycle, and persistence
across restart. Personal mailbox details are omitted from the public record.
These observations are not a clean-machine or broad mailbox accuracy evaluation.

## Boundaries

- No permanent delete, background unsubscribe, outgoing unsubscribe email,
  cloud model upload, arbitrary form submission, or model-threshold change.
- Body-only link discovery is optional in the spec and deferred; inventory
  discovery is header-based.
- Inventory counts are cumulative distinct observations, not live totals.
  Bounded scans disclose partial coverage; Gmail remaining counts are estimates.
- Direct one-click requires verifiable, aligned DKIM covering both unsubscribe
  headers. Missing/expired keys and unsupported messages use manual paths.
- Gmail controls recovery availability. Restore removes Trash; it does not
  promise an Inbox destination. An accepted unsubscribe is not proof of future
  non-delivery.
