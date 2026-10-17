# V2 validation — September 30, 2026

> Historical milestone notes. Current setup and limitations: [release validation](release-validation.md).


Implemented from the notebook's **V2 — 1,000 Email Cleanup + Laya/Qwen Review**
and **Questions on V2** answers.

- All 30 automated tests pass, including V0 readiness and V1 behavior. The former
  V1-only exact route inventory was updated to retain V1 routes and prohibit a
  permanent-delete endpoint; V2 write safety has dedicated tests.
- Production frontend TypeScript check and Vite build pass. `git diff --check` passes.
- Mock Gmail scan fetched exactly 1,000 messages across two 500-message pages,
  sorted results newest first, and returned paged metadata without email bodies.
- Tests cover exact Laya threshold boundaries, Qwen high/medium/low/unsure routing,
  strict JSON validation, local-only prompt fields, model errors, protected labels,
  fresh protection checks, current-account binding, arbitrary/duplicate IDs,
  expiring confirmations, partial writes, failed-only eligibility, retry backoff,
  read-only migration, fatal authorization failures, and ephemeral run reset.
- Feedback tests verify opt-in only, manual decisions, 0700/0600 permissions,
  stopping new saves without deleting existing records, and confirmed erasure.
- Installed Qwen (`qwen3:8b`) returned valid schema JSON for a synthetic sale email
  in 3.83 seconds: promotional / high, with a short reason. Installed Laya returned
  Action Required 0.0405, Informational 0.0755, Promotional 0.884 in 1.12 seconds.
  These are single synthetic calls, not a throughput or accuracy benchmark.
- Browser checks on a separate synthetic-only server verified default Ready view,
  exact selection counts, the modal confirmation and Cancel flow, ambiguous Qwen
  explanations, manual review moving an item to Ready without writing to Gmail,
  and protected Keep items exposing neither selection nor promotional override.
- Browser setup check on port 8765 detected the existing read-only grant and
  showed feedback disabled. The real app is running at `/cleanup`.

## Pending live validation

The saved grant now includes `gmail.modify`, verified by successful Gmail
profile/list/get checks. The user must inspect results and explicitly
select/confirm any real Trash test. Initial implementation checks used synthetic
inputs; later live scan checks are recorded below. Google's unverified-app trust issue remains open in the
notebook's **Have Publishing** page. Large-scan timing, accuracy evaluation, and
16 GiB memory testing remain unverified.

## Scan-control update

The user authorized stopping the original large scan and implementing the agreed
controls. That original process was stopped; its ephemeral partial results were
not recovered. The prior live scan reached 1,000 listed, 906 fetched, and 94 skipped,
with 778 routed to Qwen; the old implementation did not retain individual skip
reasons. Before stopping, saved authorization was verified to include gmail.modify,
and separate Gmail profile/list/get checks returned HTTP 200. Live scan completion
and Trash validation remain pending.

All 36 tests now pass. Added coverage verifies category OR filtering, custom limit
validation, history exclusions refilling the requested eligible limit, account
segregation, persistent manual review, confirmed history clearing, and cooperative
stops during fetch, Laya, and Qwen. Stopped results retain completed decisions and
pending items cannot pass bulk Trash validation. The production frontend builds.
Test history uses isolated temporary directories rather than the user's dataset.

A later read-only live check scanned three Inbox messages: three fetched, zero
skipped, three Laya classifications and three Qwen reviews, yielding two Keep
and one Needs Review. Zero Gmail writes. A Social-category scan returned zero
matching Inbox messages without error. No automatic review-history marks or
training feedback were saved by either validation scan.

## Read-status filter

All 37 tests pass after adding All / Read / Unread scan options. Tests cover
combining category OR queries with the read-status filter, default All behavior,
invalid read-status rejection, and excluding fresh labels that no longer match
without writing to Gmail. Frontend TypeScript and production build pass. The
completed five-email Promotions preview was cleared by the required app restart.

## Account-aware category picker

All 38 tests and the production build pass. New coverage validates category
estimates, empty categories, read-status filtering, safe per-category failure,
Inbox-only ID listing without bodies, and keeping message IDs out of the count
response. The UI calls these Gmail categories and labels estimates as approximate,
before review-history exclusion; All Inbox remains available regardless of counts.
