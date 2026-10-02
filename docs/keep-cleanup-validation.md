# Keep vs Cleanup decisioning

Implemented September 30, 2026, following the notebook specification and the user's
clarifications: shared Qwen criteria, confidence-based Needs Review, fresh correction
memory, and no labeled evaluation sample/workflow.

Laya's semantic distribution is now KEEP/CLEANUP. The existing thresholds are
preserved: Cleanup >= 85%; Keep when Cleanup <= 15%; otherwise Needs Review.
Needs Review is not a third semantic category. Qwen uses personal importance and
realistic future-reference value, with genuine ambiguity and low confidence
retaining review. Shared prompts include personal, financial, medical, security,
newsletter, notification, and receipt examples, and treat input as untrusted data.
Informational/transactional or non-promotional mail is not automatically Keep.

The review API exposes its final decision separately from original Laya decision
and probabilities. UI manual labels are Keep/Cleanup, and Gmail's Promotions
category remains only a scan-scope filter. Learning still uses bounded local
examples and an extra Qwen pass; examples are now tagged keep-cleanup-v1. Legacy
untagged examples cannot enter retrieval. Existing recorded historical review
states and metrics are retained without reclassifying the past.

## Verification

- 78 automated tests pass, including the prior safety/learning/account-boundary
  regressions updated to the new contract and five new taxonomy checks.
- TypeScript and production frontend build pass; diff whitespace checks pass.
- The existing installed Laya and Qwen models were used for a synthetic local
  integration check. A held-out newsletter had KEEP 0.8274 / CLEANUP 0.1726.
  The normal Qwen path recommended CLEANUP; the correction-context path recommended
  KEEP, high confidence, citing the previously kept similar research digest.
  Original Laya probabilities remained unchanged. Temporary test data was removed.
- Three existing correction examples in the connected account were cleared using
  the confirmed, account-bound clear endpoint, as explicitly authorized by the
  user. Zero remained. Consent stayed enabled; review/action history and dashboard
  metrics were retained.
- The completed local service was restarted on port 8765 with the new taxonomy.
  Start a new scan to obtain new-contract results; old session results are not
  reused across restart. To reanalyze reviewed mail, uncheck Only emails I haven't
  reviewed rather than clearing historical records.

No 50–100-email dataset or labeling workflow was built or run. These checks
validate contract and integration, not accuracy across the user's real mailbox.
Neither underlying model nor Gmail action safety was changed. Cleanup is a
recommendation; selection, exact-count confirmation, expiry, account binding,
and write-time protected-label checks still govern every Trash action.

## User retention preferences

The user subsequently identified routine security alerts, completed ride receipts,
and Google verification codes as having no long-term value. The shared prompt now
explicitly names these Cleanup examples, while evaluating concrete unresolved
account-access problems or disputed charges separately. No account email address
was embedded in the source prompt; the security-alert example uses a placeholder.

All 78 automated tests still pass. A three-case synthetic check with the actual
installed models routed each case to Cleanup through Qwen. Laya remained uncertain
(Cleanup probabilities 0.2636, 0.3388, and 0.3085 respectively), so this is evidence
of the full pipeline applying the preference, not proof that Laya alone has learned
or confidently classified these patterns. No Gmail message was moved. Actual
message content and relevant saved corrections can still affect a new result.

## Tracking-noise diagnosis and fix

Read-only inspection of four user-reported SoFi messages confirmed that tracking
URLs occupied 84–88% of each previous 12,000-character model input. The plain-text
alternatives also contained invisible preheader padding. Link noise was consuming
the context budget before useful readable content, and Qwen's uncertain explanations
were discussing URL legitimacy rather than retention value.

Decision text now strips link targets and invisible formatting/padding and
collapses whitespace before truncation. Both Laya and ordinary/memory Qwen calls
use this cleaned input. Original Gmail headers, unsubscribe destinations, and
mailbox mutation checks are unchanged. The prompt also distinguishes unsolicited
financial product campaigns and IPO invitations from actual financial records and
explicitly keeps the task focused on retention rather than phishing analysis.

All 80 tests pass, including two new noise/truncation regressions. The four actual
messages were re-fetched read-only and retested locally. Cleaned bodies contained
3,431 / 6,423 / 9,669 / 3,416 characters. Laya Cleanup probabilities were 0.4346 /
0.4753 / 0.4499 / 0.4129, still requiring Qwen. Qwen returned CLEANUP with high
confidence for all four. This fixes the observed full-pipeline results; it does
not claim Laya alone became confident. No email was moved or correction saved by
this diagnostic run, and no product run summary was created for the diagnostic.

## Updated notebook revision 3 — durable personal archive

Read the updated page and implemented its core question verbatim in a compact
Laya prompt. Keep now requires durable archive value; routine receipts, alerts,
reminders, confirmations and codes are ordinarily Cleanup. Qwen shares the full
updated guidance. The earlier binary/85–15 confidence-band design is superseded
by explicit KEEP/CLEANUP/NEEDS_REVIEW choices, actual ambiguity, close competing
judgments and contradictory value rationale. Confidence alone does not force
review. A 0.10 score-margin guard and cross-check of Laya's independent value-basis
choice identify these ambiguous judgments.

The installed Laya library truncates the question head to 192 tokens. The new
instructions plus described options measured 180 tokens with its actual tokenizer,
so the complete prompt fits. This replaces long guidance that could lose important
late instructions. Models were not replaced or fine-tuned.

The output now contains decision, concise criteria-based reason, and native Laya
entropy confidence scaled to 0–100. Reasons are application summaries rather than
fabricated free-form Laya generation; this model supports discrete choices, not
open-ended explanations. Qwen generates a concise reason with numeric confidence.
The UI distinguishes final recommendation from original Laya decision scores and
uncalibrated confidence, and no longer implies the old Cleanup threshold controls
routing. Existing correction memory, consent, account separation, and action safety
remain intact. No new learned examples were cleared in this revision.

Validation: 81 tests and the production build pass. The real-model synthetic
transaction alert was Cleanup; the synthetic bank statement was Keep. The ride
receipt still needed Qwen after conflicting Laya judgments, then became Cleanup.
The four reported actual SoFi messages were fetched read-only and again all reached
Cleanup through Qwen. Laya remained ambiguous, so this is not evidence that Laya
alone is now accurate. The isolated real-model correction-memory check still
passed: prior Keep context changed a similar newsletter's Qwen recommendation to
Keep while retaining original Laya scores. No diagnostic mailbox write or product
run inflation occurred. The user-requested exclusion of an evaluation dataset and
labeling workflow remains in force.

### American Express verification-code preference

The user's explicit preference now routes the exact subject “Your American Express one-time verification code” to Cleanup candidates, even when Laya recommends Keep. Matching normalizes case, whitespace and trailing punctuation; it does not apply to other American Express messages or statements. Original model scores remain visible and the recommendation is labeled as the user's preference. STARRED/IMPORTANT protection still takes precedence. No Gmail messages are moved automatically.

Validation: 83 tests pass, including model-Keep override, protected-message precedence, exact-subject scope and no Gmail writes. Frontend production build passes. The compact Laya instruction and choices use 189 of the 192-token prompt budget.

### Current importance and full user-supplied policy

Replaced the retention instructions with the user's full prompt verbatim, shared by Laya and Qwen. Current unresolved importance is checked before durable value. Category and rationale definitions include current importance, including a compatible KEEP rationale. The previous compact-prompt configuration is superseded: question allowance 1,024 tokens, total sequence 1,536, within the encoder's supported 8,192 positions. The full policy plus choices measures 818 tokens and fits without instruction truncation.

Validation: 84 tests pass; frontend production build passes. A real local Laya smoke check classified a synthetic overdue-payment action as KEEP with current_importance rationale. This single check is not an accuracy evaluation. No Gmail writes were performed. An active scan prevented restarting the service during this change; the running process continues with its previously loaded policy until restart.

### Dual classifiers and manual Qwen review

Added a localhost-only Winnow 12B Q8 typed-decision adapter, using the same full retention instructions and reason choices as Laya. Scans can select Laya, Winnow or both. Both mode evaluates the same message snapshot; disagreement or a missing classifier result routes to Needs Review. The API keeps original per-model scores, rationale and wall time. The UI includes an aggregate comparison and per-email model cards; disagreement is not an accuracy metric.

Added explicit Qwen forwarding for unprotected Keep and Needs Review messages. Manual mode skips automatic Qwen inference, including memory-triggered inference; relevant corrections are retrieved when the user explicitly forwards. Failed manual reviews preserve the previous recommendation. Qwen decisions do not capture learning, mark emails reviewed or perform Gmail actions. Starred/Important exclusions, exact selection validation and mutual exclusion with scans/Trash operations remain enforced.

Validation: 88 tests pass, including local-only adapter request, unavailable runtime, paired disagreement, manual Qwen transition while preserving original model judgments, and rejection of protected/duplicate selections. Frontend production build passes. Native Winnow runtime built with its native test suite passing. Runtime source is pinned in README. No Gmail writes were performed.

Live runtime smoke checks completed on the M4 Pro/64 GB Mac after verified Q8 download and native build: overdue-payment action was KEEP/current_importance for both classifiers. A synthetic verification code was NEEDS_REVIEW/current_importance for Laya and CLEANUP/transient_message for Winnow. Winnow classification took approximately 4.3 and 4.2 seconds respectively. These two synthetic cases are runtime checks, not an accuracy or throughput evaluation. Both local services are running (application 8765, Winnow 8091). Gmail connection verification is waiting for macOS Keychain approval after repair of Python removed by Homebrew cleanup; no credentials were exposed or cleared.

### Independent post-fetch classifier branches (supersedes combined UI flow)

The UI now fetches first with no classification, then offers separate Laya and Winnow buttons. Each classifier operates on its own deep copy of the same bounded fetched snapshot and keeps separate recommendations, original model judgments and Qwen results. Viewing a branch selects its own results and actions. Model disagreements are reported in comparison, without merging recommendations. Manual Qwen targets a named branch; mailbox Trash/Restore state is shared and protected checks remain enforced. Classification is serialized; the snapshot is not fetched again per model. Fetch-only operations do not inflate classification-run metrics.

Validation: 90 tests pass and production UI build passes. New tests verify zero inference during fetch, no additional Gmail fetch during independent classification, branch-specific Qwen changes without mutation of the other branch or source snapshot, original comparison retention, rejection of duplicate classification, and shared Trash eligibility. The previous combined run was stopped before service restart at the user's request.

Live end-to-end verification after restart fetched two Gmail messages without inference (zero classified, no branches), then used the UI's separate Laya and Winnow controls. Both branches completed using the existing snapshot; Qwen remained idle. Each model panel exposes its own Keep/Needs Review forwarding buttons, with per-item controls available in the branch view. No Gmail writes or new correction captures were made during verification.

### Single-question Winnow thresholds

Removed Winnow's rationale question and conflict guard. Its model output is now binary KEEP/CLEANUP. Application routing uses Cleanup probability: >0.60 Cleanup candidates; <0.20 Keep; [0.20,0.60] automatic Qwen review. Explicit preferences and protected labels remain enforced. Corrections are injected only into eligible intermediate-band review. Laya and its manual Qwen branch remain independent. No automatic Trash writes were added.

Validation: 92 tests and production UI build pass. Tests cover strict threshold boundaries, 74% Cleanup routing, a single binary request, and automatic Qwen only for the intermediate unprotected message, with no Gmail writes. Unresolved Qwen/error results remain reviewable rather than being silently converted to a deletion recommendation.
