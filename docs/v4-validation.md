# V4 validation — Dashboard and correction memory

Validated locally on September 30, 2026, against the V3 baseline `b0ac76e`.

## Automated checks

- 73 tests pass: all 64 existing V0–V3 tests and nine V4 tests.
- TypeScript and production Vite build pass.
- `git diff --check` passes.
- V4 tests cover account isolation, persistent consent, default-off capture/retrieval,
  correction deduplication, restart persistence, self-example exclusion, relevant,
  weak and conflicting retrieval, legacy coverage, unique reviewed counts, latest
  Keep state, completed/stopped/separate subscription run counts, successful action
  cycles and idempotent journal completion, independent correction clearing,
  consent/account binding and confirmation, high-confidence Laya memory routing,
  model failure, protected mail, unchanged V2 routing without memory, privacy of
  public result payloads, prompt schema, link redaction, and metadata-only backlog
  actions excluded from analyzed totals.

## Real local application checks

The running FastAPI/React application was opened at `/dashboard` using its saved
Gmail connection. Existing successful action history and inventory were displayed:
1 Trash, 1 Restore, 3 subscriptions, 2 accepted unsubscribe requests, and 0 verified
unsubscribe completions. The dashboard explicitly disclosed incomplete historical
coverage; old ephemeral scans did not become invented completed runs. The learning
control was off with zero examples.

Dashboard JSON was saved before and after an actual Uvicorn restart. All fields
were identical. The browser was reloaded to renew its session and verify the
restarted UI. The Clear learned corrections dialog named the account and described
its separate scope; it was canceled without deleting user data.

`PYTHONPATH=. .venv/bin/python scripts/validate_v4_learning.py` exercises the actual
Cleanup service, explicit correction endpoint method, normalized MIME fetch,
account-scoped SQLite store, retrieval, real Laya, real local Qwen, routing, and
persisted metrics. It uses synthetic newsletter editions and temporary storage;
no Gmail request, mailbox mutation, production consent change, or product-run
inflation occurs.

Observed held-out result:

- Original Laya probabilities: Promotional 0.6433, Informational 0.1806,
  Action Required 0.1761.
- Without memory: Qwen Promotional, high confidence → Ready to Trash.
- With the prior explicit Keep example: Qwen Keep, high confidence → Keep,
  source `memory_qwen`, example similarity 0.929.
- Qwen's reason referenced the prior explicitly kept equivalent newsletter.
- Original Laya probabilities remained intact.
- The temporary dashboard persisted one completed classification run, two unique
  reviewed messages, and two latest Keep states. Temporary data was removed.

The model check exposed ambiguity in using a generic promotional label for a
personal preference recommendation. The additional memory pass now asks for
`keep` / `trash_candidate` / `unsure`, with validated internal normalization to the
existing review schema. The ordinary V2 marketing-classification prompt remains
unchanged. Automated checks cover high-confidence Laya override; the real-model
sample exercises the ambiguous band. Neither check guarantees every similar future
email will get the same model recommendation.

## Local acceptance steps

1. Open `http://127.0.0.1:8765/dashboard` and check the coverage note and totals.
2. Complete a small Inbox cleanup scan, then return to Dashboard: Total runs grows
   by one; repeated IDs do not inflate Emails reviewed/analyzed.
3. Enable **Learning from corrections** for your account. Explicitly correct an
   unprotected result to Keep or Promotional. The saved-example count updates.
4. Scan a distinct, similar email in a later session. Inspect its prior correction
   context and original probabilities. Memory remains recommendation-only.
5. Disable learning: examples remain saved, but are neither captured nor used.
6. Confirm **Clear learned corrections**: examples disappear while review/action
   history and dashboard metrics remain. The chosen learning switch persists.

No new real unsubscribe, Trash, Restore, or production correction was performed
by the implementation validation workflow. Existing mutation safety was verified by the regression suite.
The local server remains available on port 8765. The user subsequently tested the local app. Read-only inspection confirmed three
completed classification runs, learning enabled, and one saved correction. This
confirms capture, but does not claim a later real Gmail email used that example.

The user authorized publication of the V4 notebook report and committing/pushing
the implementation on September 30, 2026. Private local databases, credentials,
and validation screenshots are excluded from the source commit.
