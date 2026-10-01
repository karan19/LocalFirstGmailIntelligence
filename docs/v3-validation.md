# V3 validation — September 30, 2026

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

## Live Gmail and browser validation

The local application was tested through its browser UI with the already granted
`gmail.modify` permission. No additional OAuth scope was requested.

1. A read-only 50-message discovery scan completed with zero fetch failures,
   finding four subscription messages in three groups. The UI correctly reported
   partial coverage and an approximate remaining candidate count. Actual local
   DKIM verification enabled one-click capability.
2. The user explicitly authorized unsubscribing the LinkedIn newsletter from
   `editors-noreply@linkedin.com`. Its authenticated HTTPS endpoint returned
   **HTTP 200**. The UI and journal record **Request accepted; unsubscribe not
   verified**, without claiming the sender has permanently stopped delivery.
3. The exact-sender subscription-evidence backlog scan found **six matching
   messages**, **zero protected**, and exhausted the requested candidate scope.
4. The user separately authorized moving **one** selected message to Gmail Trash
   and restoring that same message immediately. The exact-count confirmation,
   write revalidation and Trash action succeeded.
5. The recovery view fetched Gmail state and displayed the selected message as
   **In Trash**. Its explicit Restore confirmation and `untrash` succeeded.
   An independent metadata read verified the selected message's `TRASH` label
   was absent after recovery.
6. Five pre-existing V2 Trash records were imported, fetched and shown as
   recoverable. These messages were not modified.
7. The backend was restarted. Three subscription records, the accepted request,
   successful Trash/Restore actions and five legacy recovery records survived.
   A fresh read-only backlog scan confirmed six matches and refreshed the
   inventory's observed count, preserving the unsubscribe outcome. Ephemeral
   confirmation tokens and previous scan state were discarded on restart.

Browser evidence is stored privately under `.local/validation/v3/`, excluded from
Git. It includes the unsubscribe confirmation, one-message Trash confirmation,
and the resulting live action history.

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
