# Experimental Updates snapshot branch

This branch tests a persistent Gmail Updates download product. Public GitHub
contains code, schema and synthetic tests only. Originals, OAuth configuration,
tokens, checkpoints and Gmail IDs remain private. Optional local redaction
produces private review copies. Local BERTopic discovers groups from originals.
Optional Jev scoring sends original subjects and bodies to TypeSafe when explicitly
started. Saved cleanup candidates can be labeled or moved to Gmail Trash through
explicit UI actions. This is not permanent deletion; private copies remain.
See the Jev cleanup section below before starting cloud scoring or mailbox changes.

## Architecture and scope

- UI server: `snapshot.ui_server`, loopback port 8766. It imports no Gmail,
  credential or private database modules. It proxies a fixed command/status API.
- Private service: `snapshot.private_service`, loopback port 9000. Owns OAuth
  (state plus PKCE), separate macOS Keychain namespace, Gmail and SQLite.
- Default storage: `~/CleanUpFolderService`, beside `~/workspace`, outside this
  project. This matches the folder selected for this experiment.
  Set `--directory` if the protected launch uses a different boundary.
- Category: Updates throughout Inbox and archive; Spam and Trash excluded.
  Numeric count defaults to 8,700. “All” exhausts pagination without a scan cap.
  The downloader retries temporary network failures, Gmail rate limits (403 or
  429), and server errors with exponential backoff. It honors numeric Retry-After
  delays up to 60 seconds; longer waits pause with an actionable error. Expired
  access tokens are refreshed automatically, and messages deleted after listing
  are counted as skipped. Persistent failures retain the checkpoint and a safe
  error category/HTTP status, without storing provider error bodies or credentials.
- Originals are Gmail `format=raw` responses, including MIME attachment content,
  persisted in `mailbox.sqlite3`. They are not loose `.eml` files or normalized,
  truncated classifier text. Status returns counts and job progress. In regular
  session mode, an explicit topic-view request also returns keywords and bounded
  original subject/text examples. It never returns credentials or Gmail IDs.
  Protected-session mode rejects requests for original topic examples.

SQLite stores messages keyed by account/message ID, jobs, and pending IDs.
Each page commits pending work and its next-page token together; each message
commits immediately. Pause lets the current request finish. Resume drains saved
pending work before listing more. Authentication is refreshed and the account
is rechecked before each resumed job. Previously stored originals are reused.
This is an archive download, not a live label synchronization system.

Coverage is separate from dates: a sample never claims all Updates coverage.
“Complete” means an unbounded listing exhausted with no unresolved or skipped
IDs. Gmail can change during or after listing, so this does not claim a point-in-
time consistent or permanently current mailbox snapshot. Changed categories,
404s and network failures can prevent completeness. Expired pagination tokens
currently require starting a new download; stored originals are deduplicated.

## Protected launch prerequisite

For an explicitly chosen regular Codex session, run
`.venv/bin/python -m snapshot.launch --private-service --regular-session`.
This opts into service operation without claiming protected-agent isolation.
The coding agent can potentially access private storage in this mode; the
service keeps mail out of status responses. Topic examples are available to
your local browser on explicit request in regular-session mode. The protected workflow
below remains the default and requires its isolation attestation.

A sibling folder and permissions **do not isolate processes running as the same
OS user**. This code does not implement CodexGate or an OS sandbox. The existing
protected Codex launch must deny reads of the private folder and credentials,
including SQLite journals, process environments and access through other tools.

The CodexGate broker dummy test established that approved Read/write grants can
read and write this folder. That is a permission integration test, **not mailbox
isolation**. Before using it for mail, revoke every temporary grant for the
folder and remove any Read or Read/write launch allowance covering it (including
an allowed ancestor). Launch-rule changes require restarting protected Codex.
Do not grant the mailbox directory to Codex after real mail arrives. Alternatively,
choose another private storage directory outside every allowed ancestor and use
`--directory`; a child of a granted folder is not isolated from that grant.

Before real mail, use a trusted terminal outside protected Codex to create a
dummy folder at the intended private location and a file containing only a
random synthetic marker. Ask protected Codex to try reading that dummy file and
writing another dummy file there. Both must be denied by the OS boundary. Check
the private service can read/write those fixtures from the trusted terminal.
Verify inherited protection does not prevent the trusted service from accessing
its own storage. Repeat after changing the boundary or storage path. Do not use
real credentials or mail to test isolation.

Only after that check, set `GMAIL_SNAPSHOT_ISOLATION_VERIFIED=1` in the trusted
terminal. This is an **operator attestation**, not automated verification or
enforcement. The private service refuses startup without it. No isolation test
with this deployment has been completed by this implementation.

## Build and launch

Use the already prepared Python environment and frontend dependencies. This
launcher does not install packages or read the legacy mail stores. Build:

```sh
npm run build --prefix frontend
.venv/bin/python -m snapshot.launch
```

The default launches the UI alone with private operations disabled. To enable
the private service after isolation verification, reuse the main app's Google
setup from your trusted terminal:

The service defaults to `~/CleanUpFolderService/google-client.json`. With
`--reuse-main-setup`, only the private service copies the main client configuration
from `GMAIL_CLIENT_CONFIG` or the main app's `.local/google-client.json`. It does
not overwrite an existing private configuration. Do not paste credentials into
Codex. If using another private directory, the default destination follows it.

```sh
GMAIL_SNAPSHOT_ISOLATION_VERIFIED=1 .venv/bin/python -m snapshot.launch --private-service --reuse-main-setup
```

The launcher creates the sibling folder and a per-launch API capability, then
starts both servers. It stops both on exit or if either process terminates.
Use the browser at `http://127.0.0.1:8766`. Click Connect Gmail, approve
`gmail.modify` if requested, then choose Updates and 10 and click Download.
The opt-in import reads the main Keychain entry locally and copies a compatible
saved authorization into the service's separate namespace. Existing service
authorization takes precedence; the main entry is never changed. Missing,
malformed, read-only or broader authorization requires consent with the same
Google client. Separate Keychain entries can still share a Google authorization;
revoking that authorization at Google can affect both apps.

Without `--reuse-main-setup`, supply the Desktop OAuth JSON in the private folder
yourself; the service does not read main configuration or authorization.
Alternatively use `--reuse-main-client-config` in place of `--reuse-main-setup`
to copy only the Google client configuration without reading main Keychain
authorization. Then use Connect Gmail for independent consent. Existing service
authorization, if previously saved, remains available.
“Refresh stored status” validates
the connected account and loads persisted progress after a restart. “Resume”
retries interrupted work; restarting never automatically fetches mail.

Do not run the private launcher inside the protected agent process: run it from
the trusted terminal so its storage access is deliberately separate. Ports 8766
and 9000 must be available. The existing `start.sh` and main server remain intact.

## Local redaction review

Install the optional local detector in the same Python environment:

```sh
.venv/bin/python -m pip install -r requirements-snapshot-redaction.txt
.venv/bin/python -m spacy download en_core_web_sm
```

Redaction remains an optional CLI/API experiment. The current topic explorer
uses original text and does not run redaction. Redaction does not fetch new mail
and runs entirely locally. Download, redaction and topic operations cannot overlap.

Each run creates a new `redaction-review-*` folder inside private storage with
numbered JSON text review copies (directory 700, files 600). It does not modify
originals or export Gmail IDs/account identity. Header display names, local
spaCy PERSON/ORG/location entities, email addresses, URLs, phone-like numbers
and labeled identifiers are replaced with placeholders consistent within each
message. Multipart mail prefers plain text, falling back to HTML text without
scripts, styles or link targets. Attachments and other raw headers are omitted.
Only counts, elapsed time and operation status return to the public UI; review
contents remain private. Inspect copies with your own local text viewer.

These are **experimental redacted copies, not guaranteed anonymous data**.
Unknown names, languages, addresses, identifiers and indirect clues can remain;
false positives can remove useful context. Keep the output private, do not
paste it into Codex, and review misses before considering external scoring.
No Jev calls or Gmail changes are implemented. Redaction status resets on service
restart; the private review folders persist. A failed run may leave partial
review copies; do not treat them as a completed run.

The same operation can be run directly from the trusted terminal:

```sh
.venv/bin/python -m snapshot.redact --limit 10
```

The detector uses the documented local [spaCy pipeline batching API](https://spacy.io/usage/processing-pipelines).

## Local BERTopic experiment

```sh
.venv/bin/python -m pip install -r requirements-snapshot-topics.txt
.venv/bin/python -m snapshot.prepare_topics
npm run build --prefix frontend
.venv/bin/python -m snapshot.launch --private-service --regular-session
```

Model preparation downloads only public model files. Analysis loads the local
all-MiniLM-L6-v2 weights with Hub access disabled and passes precomputed embeddings
to BERTopic. No mail is sent to a model provider. The pipeline uses UMAP,
HDBSCAN and c-TF-IDF keyword representations; it does not force a target number
of groups. Empty/unreadable messages are counted as skipped. At least five
readable messages are required. The initial model is primarily for English;
other languages and long messages require further evaluation.

Download the desired number of messages (8,700 by default), then choose
**Cluster up to 8,700 emails**. The separate **Maximum emails to cluster** field
can be reduced to 100 for a sample or increased up to 100,000. Gmail may return
fewer messages if the matching Updates listing is exhausted.
The run processes only stored IDs from that completed download and preserves
originals. Choose **View topic groups** to see keyword labels, counts, and
all stored email previews per group, and an explicit ungrouped section.
Select **Ignore emails with file attachments when clustering** before running
clustering to exclude whole messages with file attachments. Inline images such
as logos are not counted. The result reports the excluded count; downloaded
originals remain stored, and at least five readable emails must remain. Previews
are plain text, with attachments and remote HTML omitted. Each cluster (except the ungrouped section) has **Move … emails to Later**.
Clicking it changes Gmail: it creates or reuses the visible user label **Later**,
adds it to eligible cluster messages, and removes **CATEGORY_UPDATES**. It does
not archive, remove other labels, or delete messages. Downloaded originals stay
unchanged. Membership comes from the current private topic run, not browser
message IDs. The service rechecks the connected account and current message
labels, skips Spam/Trash/drafts and messages no longer in Updates, and reports
moved/already/skipped counts. Failed or interrupted requests can be retried;
Gmail is rechecked to handle a lost response after a successful label change.
No cloud-generated labels, merge/split controls or retention decisions are
included yet.

Each cluster also has **Delete … emails from Gmail**. This uses Gmail's message
label modification API to apply Trash and remove Inbox, with the existing
gmail.modify permission. Up to 500 eligible IDs are changed per request. Clicking starts a
background job for the selected private run's exact membership, with bounded
concurrency and moved/already/skipped/failed counts. It rechecks the account,
rejects stale views and browser-supplied message IDs, and allows retries after
partial or interrupted actions. It does not permanently delete mail, remove
local originals, regenerate groups, or change previews or cluster counts. The
ungrouped section has no bulk delete action. Nothing is trashed automatically.

### Jev cleanup staging

**Score and stage emails with Jev** evaluates the current completed download,
independently of topic grouping. The user-approved input is original subject
and body text, sent to TypeSafe's cloud API at
`https://api.typesafe.ai/v1/systemone` with `jev-latest`. Sender headers are used
locally for keep rules, and attachments, Gmail IDs and account identifiers are
not sent as fields. Identifying information inside the subject/body is sent.

The policy keeps apartment-complex mail (including Foothill Commons) and all
bank-related mail, including bank promotions. Everything else is a deletion
candidate. Local rules protect known names and banking/housing terms before
cloud calls. Jev then classifies the remaining emails and supplies a deletability
assessment under that policy. Protected categories override the score.
The candidate rule requires Jev's `other` classification, a deletability value
of at least 0.5, and apartment/bank probabilities individually below 0.2.
Uncertain/conflicting answers, short/unreadable text, and text over 32,000
characters are held for review. Rule/model detection is imperfect; the score
is not a guarantee of safe deletion. Review staged results before acceptance.

Each processed message retains a private `.eml` and JSON score file in
`CleanUpFolderService/cleanup-review/<run-id>/keep`, `delete-candidates`, or
`review`. Entries and resumable progress are also stored in SQLite. The originals
and current topic output are unchanged. Restarted or failed scoring resumes
without resending already-staged messages. Files remain after Trash acceptance.

Save a TypeSafe key privately as `CleanUpFolderService/jev-api-key.txt`, or
set `TYPESAFE_API_KEY` in the trusted launch environment. Do not paste the key
into chat, commit it, or store it in this project. For a hidden local prompt:

```bash
cd /Users/karan/workspace/LocalFirstGmailIntelligence
.venv/bin/python -c 'import getpass,os; from pathlib import Path; os.umask(0o077); p=Path.home()/"CleanUpFolderService"/"jev-api-key.txt"; key=getpass.getpass("TypeSafe API key: ").strip(); fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600); stream=os.fdopen(fd,"w"); stream.write(key); stream.close(); p.chmod(0o600)'
```

**Accept bulk Trash** is a separate user action. It only accepts the current
completed scoring run's delete-candidate membership, verifies retained copies,
rechecks account identity and current Gmail labels, and uses bulk label changes
in batches of up to 500. Keep/Review lists are excluded. Partial or uncertain
writes can be retried; already-trashed messages are detected. No scoring action
automatically approves or executes Trash. Gmail draft/missing messages are skipped.

Request/response shapes follow the [TypeSafe API reference](https://docs.typesafe.ai/api).

Embedding uses batches of 32 and one in-memory float32 matrix. For 8,700 emails
and this model's 384 dimensions, the matrix takes 13,363,200 bytes (about 12.7
MiB); model weights, original text and clustering need additional RAM. Vectors
are never persisted. The UI polls counts-only vectorization progress, followed
by a grouping phase; topic results and downloaded originals remain persistent.

Topic metadata and membership are stored privately in SQLite, scoped to account
and download job. Refresh stored status after restarting to recover results.
Interrupted runs are marked as interrupted and can be rerun. Status itself
never includes email text or keywords. Grouping does not determine whether a
message should be deleted; the sample's results need local inspection.

Preparation removes quoted reply lines and normalizes reply prefixes, but is
not a complete signature/footer remover. It decodes HTML entities, removes
common invisible preheader padding, and collapses excessive whitespace before
truncation. The 100-email sample uses a minimum group size of five, so smaller
recurring families can be considered; this is not a guarantee of assignment.
Input is capped at 12,000 characters,
and the embedding model's token limit may truncate it further. No deletion
decision should be inferred from a keyword label or a missing topic match.

Implementation follows the [BERTopic quick start](https://maartengr.github.io/BERTopic/getting_started/quickstart/quickstart.html)
and [embedding documentation](https://maartengr.github.io/BERTopic/getting_started/embeddings/embeddings.html).

## Synthetic verification

```sh
TMPDIR=/private/tmp .venv/bin/python -m unittest tests.test_snapshot tests.test_snapshot_setup tests.test_snapshot_redaction tests.test_snapshot_topics tests.test_snapshot_labels -v
npm run build --prefix frontend
```

Tests use synthetic provider responses and temporary synthetic databases only.
They cover page/checkpoint recovery, failure resume, deduplication, account
separation, sample coverage and UI request boundaries. Real OAuth and Gmail
downloads require user-run verification after isolation. No external inference
is enabled. Local clearing and schema migration controls are deferred.

The synthetic HTTP integration test also covers OAuth consent callback, a
ten-message Updates download, persistent counts after restart, and absence of
message content/account identity/credentials from status responses. It uses a
mock HTTP provider and mock Keychain; it makes no real Google calls.
Setup import tests additionally verify preservation of main and existing private
credentials, private file permissions, scope compatibility and consent fallback.

The cleanup review also offers **Apply label to candidates**, alongside bulk
Trash. Enter a custom Gmail label name (default `Later`). The service creates
or reuses that label and applies it only to the current run's verified saved
delete candidates, in batches of at most 500. Keep and review entries are
excluded. Enable **Also remove these emails from Updates** to remove their
Updates category; otherwise it only adds the chosen label. Inbox and other
labels are preserved. Trash, Spam, drafts, and missing messages are skipped.

Labeling preserves the completed cleanup review, scores, and retained originals,
so it does not require another Jev run and does not accept bulk Trash. Progress
is shown separately. If a request fails, retrying checks current Gmail labels
before continuing; it does not blindly repeat label creation. No Gmail change
occurs until the label button is clicked.

Bulk Trash paces Gmail API requests against an 80-quota-unit/second budget,
including current-label checks and retries. A rate-limit cooldown is shared
by concurrent requests on the connection. Progress reports checked counts and
sanitized error categories/HTTP codes, never provider bodies or message IDs.
Authorization, quota, network, and service-wide errors stop the batch before
mutation. A message-specific rejection is excluded from writes while other
validated candidates can proceed; remaining failures leave the review retryable.
Retries recheck current Gmail labels and skip messages already in Trash. With
thousands of candidates, current-label checks can take tens of minutes.
Quota reference: https://developers.google.com/workspace/gmail/api/reference/quota
