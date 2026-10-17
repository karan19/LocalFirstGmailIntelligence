# Public alpha release validation

Validated October 16, 2026 on Apple Silicon macOS using a separate main-derived
checkout. The experimental Updates/Jev checkout, its downloaded mail, and its
running services were not modified or restarted.

## Release scope

This is a source-distributed, single-user **public alpha**, with a user-provided
Google Desktop OAuth client. It is not a hosted service, a verified shared OAuth
application, or a claim of measured classification accuracy. Main contains local
Laya/Qwen, optional Winnow and Explorer; it does not contain the experimental
snapshot service or Jev cleanup integration.

## Checks performed for this release

- Created a new Python 3.13.16 virtual environment without borrowing installed
  application packages or model weights. Installed Laya and all web/Explorer
  dependencies successfully; `pip check` passed. The resolved dependency set is
  retained in `constraints-macos-py313.txt` and used by the application installer.
- Fresh `npm ci` succeeded from the committed lockfile (0 vulnerabilities reported
  by npm at validation time). TypeScript and Vite production build passed.
- `./start.sh --check` passed: complete dependency install, compatibility check,
  frontend build and imports of the app/native components. It did not access Gmail,
  download model weights, or perform inference.
- **117 synthetic Python tests passed**, including existing OAuth/request-boundary,
  account isolation, protected selections, exact confirmation, partial failures,
  history/recovery, Explorer handoff and correction-learning checks. New checks
  exercise non-starting installation mode, failed preflight side effects, private
  artifact rejection, and opt-in/default-empty exact-subject preferences.
- All six application routes returned HTTP 200 in the fresh environment. An
  untrusted Origin was rejected with HTTP 403; missing OAuth configuration produced
  a setup state rather than accessing a development account.
- Started an isolated loopback server on port 8875. Browser checks verified the
  Public alpha home screen and Test 10 emails → Connect flow with an actionable
  missing-client configuration message. No real authorization was imported.
- Inspected tracked filenames for private/runtime artifacts. A limited credential
  pattern check over 98 historical main blobs found no matches for the tested
  OAuth/private-key/API-token patterns. This is not exhaustive secret detection
  or an independent security audit. Historical commit contents were not rewritten.
- Diff whitespace checks passed. GitHub Actions now encodes fresh macOS arm64
  dependency/build/import/synthetic checks. Its hosted run is pending publication;
  adding the workflow is not evidence that it has already run on GitHub.

## Earlier runtime and acceptance evidence

Historical records in `validation.json`, `v1-validation.json`, and the V2–V5 notes
cover real local model execution and limited maintainer Gmail acceptance. Those
are development observations, not testing on a separate factory-clean Mac or a
representative labeled mailbox dataset. Personal mailbox/session details have
been removed from the current public docs. Model revisions and reported test
counts in historical records describe those milestones, not the current release.

## Limits users should understand

- A fresh environment was tested on an existing development Mac with native build
  tools, not a newly provisioned physical Mac. CI has not yet run remotely.
- No new real Gmail Trash, Restore, unsubscribe, classification run or OAuth
  authorization was performed for this packaging change. Large real-mailbox
  Explorer ingestion and full current end-to-end acceptance remain user testing.
- Local model inference was not re-benchmarked in the new environment. Earlier
  synthetic runtime checks are documented separately; rerun `./doctor.sh` on
  each installation after downloading models. Optional Winnow setup was not
  downloaded/rebuilt for this release preparation.
- The 16 GiB gate is provisional. Models, hardware load, scan size and message
  length affect runtime and memory. Model scores are not deletion-safety estimates.
- Public users must create their own Google client. A future shared OAuth client
  requires separate Google publishing/verification work.
- Trash retention and unsubscribe outcomes are controlled by Gmail/providers.
  No permanent-delete endpoint or automatic mailbox action is provided.

These limits are part of the alpha's published scope, rather than claims of
completed validation. Start with the read-only 10-message test and a small
review before confirming mailbox changes.
