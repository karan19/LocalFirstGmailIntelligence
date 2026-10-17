# Local-first Gmail Intelligence

**Experimental snapshot:** this branch preserves the Updates/Jev experiment.
Use the main branch for the public alpha. The experiment has a separate Updates
download UI and private service. See [snapshot setup and isolation prerequisite](docs/snapshot.md).
Its launcher does not change the main application's saved authorization or runs.

V3 adds subscription discovery, explicitly confirmed unsubscribe, historical
cleanup, and durable action history with Restore. V2's local review of up to
5,000 Inbox emails and confirmed Trash actions remains available.
The V1 read-only 10-email test remains available (see the sections below).
V0 proves this computer can run the local model stack. It provides guided CLI setup
and diagnostics with synthetic smoke-test inputs. The product specification is
NexusNote’s **Local-first Gmail Intelligence Agent — Product Design**, pages
**Project Goal and Product Description**, **V0 — Environment Ready**, and **Questions on V0**.

## Quick start

Use Apple Silicon macOS 14+ and a native arm64 Python 3.11+ installation.
The tested Python on this machine is 3.13. A provisional 16 GiB total-memory
threshold is configured in `config.json`; it is not a benchmark-certified minimum.
Allow about 7 GB for model downloads plus space for the Python environment.

```sh
./setup.sh
```

Setup creates `.venv`, installs `laya-mlx==0.2.0` and its dependencies, explains each
missing model download, asks for approval, downloads it, and runs doctor.
System software is installed by the user: if Ollama is missing, setup provides
`brew install --cask ollama`, followed by `open -a Ollama`. Without Homebrew,
install the macOS app from https://ollama.com/download/mac and launch it.
If Python is missing, use `brew install python@3.13`.

To select an installed Python for the initial environment:

```sh
V0_PYTHON=python3.13 ./setup.sh
```

Later verification:

```sh
./doctor.sh
./doctor.sh --json
```

A success prints **Environment Ready: YES** and **Your machine is ready.**
Failure exits with status 1 and supplies corrective instructions. Doctor does not
install packages, start services, or download models. It runs genuine inference
on every invocation, sequentially, with bounded subprocess/API timeouts.
Qwen is unloaded after its smoke test to reduce overlap with Laya.

For automation, `./setup.sh --download-models` explicitly consents to missing
model downloads listed below. Without that flag, noninteractive input defaults
to declining downloads. Reruns skip completed downloads and satisfied packages;
interrupted downloads can be resumed by rerunning setup. A damaged Laya checkpoint
is detected by checksum validation and repaired during setup.

## Models and local data

| Model | Purpose | Download | Destination |
|---|---|---|---|
| `qwen3:8b` (Ollama Q4_K_M) | Small text-generation smoke test | About 5.2 GB | Ollama service model store, normally `~/.ollama/models` |
| `aac6fef/laya-mlx` (FP16) | Structured probabilistic decision | About 0.85 GB | `.models/laya` inside this project |

Laya revision: `20aed815fc6acde75733882e7ec0e3f28aeb9717`. The upstream
manifest is stored in `readiness/laya-manifest.json`; runtime files are verified
by size and SHA-256. The Qwen tag is explicit but its publisher can update it;
the validation record includes the digest actually used.

Python packages come from PyPI and Laya weights from Hugging Face; Qwen downloads
through Ollama. These setup operations require internet. Inference uses local
Laya paths with Hub offline mode and loopback-only Ollama HTTP at
`127.0.0.1:11434`, bypassing HTTP proxies. V0 smoke tests use synthetic input exclusively.
Model files and the virtual environment are excluded from Git.

## Configuration and scope

`config.json` is the single source of truth for model identifiers, download-size
estimates, the memory threshold, minimum macOS version, and smoke-test timeout.
`V0_MIN_MEMORY_GIB=24 ./doctor.sh` overrides the provisional memory threshold.
This reports total physical memory; real inference detects load/execution failures
when available memory is insufficient. Mac mini M4 Pro / 64 GiB is the initial
test target. Intel Macs, Linux, and alternative model backends are unsupported
in V0 and fail before model setup.

The application modules are independent of a UI, so future versions can reuse
the readiness results. V1 extends these modules with read-only Gmail integration and a local browser UI.

## Development checks

```sh
.venv/bin/python -m unittest discover -s tests -v
./doctor.sh
```

Tests cover unsupported-platform fail-fast behavior, offline workers, partial
weights, invalid decisions, and download consent. Doctor validates actual model
execution in addition to these unit checks.

## Provenance

- Runtime: https://github.com/mizorewww/laya-mlx (independent MLX port).
- Converted weights: https://huggingface.co/aac6fef/laya-mlx.
- Upstream code: https://github.com/NandhaKishorM/laya.
- Upstream English weights: https://huggingface.co/convaiinnovations/laya.
- Qwen model: https://ollama.com/library/qwen3:8b.

Retain model/runtime licenses and attribution when redistributing them. Setup
keeps the converted checkpoint’s LICENSE and NOTICE with its downloaded files.

The provisional 16 GiB gate budgets roughly 5.2 GB of quantized Qwen weights,
about 1 GiB for Laya inference, and headroom for model execution and macOS. These
are planning estimates, not proof of performance on a 16 GiB Mac. The smoke tests
use a 2,048-token Qwen context and run one model at a time. See `docs/validation.json`
for observed results on the development machine once validation completes.

## V1 — Gmail connection and local classification

V1 extends the V0 runtime with a local browser UI. It reads **up to 10 newest
Inbox messages**, classifies each with Laya as Action Required, Informational,
or Promotional, and displays the model’s probability distribution. It does not
invoke Qwen/Ollama or perform Gmail writes. The V0 setup and doctor commands remain
available unchanged.

### Launch

After completing V0 setup, install Node.js 22+ from https://nodejs.org and run:

```sh
./start.sh
```

This installs the project’s V1 Python dependencies, installs the locked frontend
packages, builds the UI, and starts the local app. Open **http://127.0.0.1:8765**.
Keep the terminal open; Ctrl-C stops the app and clears its in-memory email/results.
Subsequent launches use the same command. The app binds only to loopback.

### Gmail setup

Each user supplies their own Google Desktop OAuth client:

1. Open https://console.cloud.google.com/ and create or select a project.
2. Enable the **Gmail API** under APIs & Services → Library.
3. In **Google Auth Platform**, configure Branding and Audience. For personal
   testing select External / Testing and add your Gmail address as a test user.
4. Under Data Access add only `https://www.googleapis.com/auth/gmail.readonly`.
5. Under Clients create an OAuth client with application type **Desktop app**.
6. Download its JSON configuration. Save it outside Git:

```sh
mkdir -p .local
# Replace the source path with your downloaded Desktop client JSON.
cp ~/Downloads/client_secret_YOUR_CLIENT.json .local/google-client.json
chmod 700 .local
chmod 600 .local/google-client.json
```

Alternatively set `GMAIL_CLIENT_CONFIG` to an absolute path to the downloaded JSON
when launching. Never commit or share client configuration or tokens. The local
app detects missing/invalid configuration and displays a precise setup message.

Click **Test → Connect Gmail**. Google opens in the browser and returns to the
loopback callback. Approve read-only Gmail access; analysis starts automatically.
The installed-app flow uses OAuth state and PKCE. Google may show an unverified
app notice during personal testing. Testing-mode authorizations for this scope
can expire; reconnect when prompted. Public distribution may require Google’s
OAuth verification; this milestone is a personal local test.

Authorization is stored through the **macOS Keychain backend**, never in a
plaintext token file. macOS may request Keychain access. A locked/unavailable
Keychain produces an actionable error without falling back to file storage.
**Disconnect this Mac** removes the locally saved authorization; to revoke the
Google grant too, remove the app in your Google Account’s third-party connections.

### Privacy and behavior

- Gmail requests are limited to message list/get with the Inbox label and a
  10-message sample, excluding Spam and Trash. Results sort by received timestamp.
- Fewer than 10 messages, including an empty Inbox, are valid outcomes.
- Plain text and HTML bodies are normalized as text; attachments are excluded.
  No email HTML is rendered or executed. Classification input is capped at 12,000
  characters; Laya’s tokenizer also has its own context limit.
- Only sender, subject, and normalized content reach Laya. Tokens stay in the
  authentication layer. Displayed confidence is the highest category probability,
  not Laya’s separate confidence metric; bars preserve returned probabilities.
- Email content and results remain in process/browser memory. No email database,
  browser storage, or full email/access logs are created. Stopping the app clears
  server state; close its browser tab to clear displayed results.
- Local API calls require a session cookie; mutations also require the local
  Origin. Host validation prevents DNS rebinding. This app is for one local user
  and one Gmail connection at a time.
- OAuth, fetch, and classification failures can be retried from the UI. A repeated
  test replaces the previous results; concurrent starts share the active run.

### V1 verification

```sh
.venv/bin/python -m unittest discover -s tests -v
npm run build --prefix frontend
```

Automated V1 checks cover local request boundaries, read-only OAuth/PKCE,
normalization, credential isolation, probability validation, small/empty inboxes,
and the pipeline’s error state. Real Gmail end-to-end verification requires the
user’s configured OAuth client and browser consent.

References: [Google Desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app),
[Gmail message listing](https://developers.google.com/workspace/gmail/api/guides/list-messages).

## V2 — 1,000-email cleanup and Laya/Qwen review

Open **http://127.0.0.1:8765/cleanup** after `./start.sh`. The landing page also
retains the V1 read-only **Test 10 emails** path.

### Upgrade Gmail permission

V2 requests only `https://www.googleapis.com/auth/gmail.modify`, replacing the
V1 read-only grant. Add that scope in your Google Auth Platform project's
**Data Access**, then use **Connect Gmail** on the cleanup page and personally
approve Google's consent. Existing read-only credentials cannot run cleanup.
Credentials remain in macOS Keychain; no broader `mail.google.com` scope is used.
Testing users still see Google's unverified-app warning until the publishing and
verification work tracked in the notebook is complete.

### Review and confirm

1. Click **Scan up to 5,000 emails**. The app paginates Inbox message IDs with a
   maximum of 500 per page and fetches details with at most four concurrent
   requests. Spam, Trash, attachments, and messages that left the Inbox are excluded.
2. Every fetched message is classified by Laya. Promotional probability ≥0.85
   goes to **Ready to Trash**, and ≤0.15 to **Keep**. Values between those thresholds
   receive local `qwen3:8b` review. Only high-confidence promotional/keep judgments
   route automatically; uncertainty, invalid JSON, and model failures need review.
   Qwen confidence is qualitative, not a calibrated probability.
3. **Needs Review** allows Promotional or Keep decisions. Ordinary Ready/Keep
   messages can also be corrected. Promotional places an email in Ready; it does
   not change Gmail. Starred or Important messages are always Protected/Keep.
4. Search sender/domain/subject, filter the decision source, or expand sender
   groups. Select individual Ready messages or all filtered messages explicitly.
   Selection count includes selected messages hidden by later filters.
5. Review the exact count in the confirmation dialog before moving to Trash.
   The server binds confirmation to those IDs and the current in-memory run for
   five minutes. It checks the connected account and re-fetches each message's
   labels before writing. Newly Starred/Important messages are refused.
6. See succeeded/failed counts. **Retry eligible failures only** requests a fresh
   confirmation and excludes successful or newly protected messages. Gmail Trash
   remains the source of truth; there is no permanent-delete endpoint.

Gmail network failures, HTTP 429, and 5xx use bounded exponential retry. 401/403
requires reconnecting. Partial fetch failures are counted as skipped, so the UI
reports the actual number analyzed. A scan can take several minutes; no throughput
or accuracy guarantee is implied. Closing/restarting the backend loses scans,
selections, confirmations, and results; operations never replay after restart.

### Optional feedback and local privacy

Feedback saving is **off by default on every backend launch**. Enabling it saves
only manual review/correction records: normalized sender, subject, email text,
received time, model outputs, and your decision in `.local/feedback/reviews.jsonl`.
That directory is private (0700) and the file is 0600, excluded from Git. No Gmail
tokens are included. There is no training or upload in V2. Disabling stops new
records but leaves existing ones; **Delete saved feedback** requires explicit
confirmation and permanently erases this dataset.

Other email bodies and results stay in memory. Gmail data is sent to Google's
Gmail API and the local models, never to a cloud model provider. Email content is
untrusted model input and is never executed or rendered as HTML. Thresholds,
scan size, request concurrency, and Qwen timeout are centralized in `config.json`.
Qwen uses an 8,192-token context in V2; V0's 16 GiB provisional memory gate has
not been benchmarked for this larger workload.

References: [Gmail Trash operation](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users/messages/trash),
[Ollama API](https://github.com/ollama/ollama/blob/main/docs/api.md).

### Scan controls, stopping, and review history

Before scanning, select any combination of Primary, Social, Promotions, Updates,
and Forums; no selection means All Inbox. The categories form an OR filter,
intersected with Inbox. Gmail's category is a fetch filter, independent of the
models' promotional decision. Choose 50/100/250/500/1,000/2,500/5,000 or a custom integer
from 1 to 5,000. The limit is shared across the selected categories, newest first.

**Only emails I haven't reviewed** is enabled by default. Previously reviewed
IDs are excluded before fetching bodies and do not consume the requested scan
limit; listing continues through later pages until enough eligible IDs are found
or matching mail runs out. Scanning or displaying results alone never records a
review. An explicit Keep/Promotional correction, Mark reviewed, or successful
confirmed Trash action does. Automatic Keep results must be marked reviewed if
you want them excluded next time.

Review history is separate from optional feedback, saved in
`.local/history/reviews.sqlite3` (directory 0700, database 0600). It contains an
account hash, Gmail message ID, decision, and timestamp, with no bodies or
subjects. It persists across backend restarts on this Mac, segregates accounts,
and is not synchronized across installations. **Clear review history on this Mac**
requires confirmation, clears every account's review records, and does not alter
Gmail or delete training feedback. Successful Gmail writes remain successful if
saving their local history fails.

**Stop scan and review completed results** stops scheduling new work and lets
in-flight fetches/current inference finish. The dashboard retains partial results:
completed decisions keep their buckets; Laya ambiguity awaiting Qwen shows
**Qwen review not completed** in Needs Review; ordinary fetched messages not yet
classified show **Not analyzed** and cannot be bulk-selected for Trash. They can
be manually corrected. Protected messages remain Keep. Stopping does not resume
later, stop a confirmed Trash operation, or undo Gmail changes. Results still
vanish when the backend stops; only review history and opted-in feedback persist.

Each stage has a progress bar and counts. Scan details distinguish messages that
left Inbox, moved to Spam/Trash, and fetch failures, and show up to ten safe failure
explanations. Previously reviewed exclusions are reported separately. Promotional
probability is visible on each analyzed result, with all three Laya probabilities
expandable. Thresholds remain 0.15 and 0.85.

### Read / unread filter

The scan setup includes **Gmail read status**: All emails (default), Read
(opened), or Unread (unopened). This combines with category selection, scan limit,
and Only unreviewed. Read/unread means Gmail's current mark; users can manually
change that mark, so it is not proof of whether an email was opened. It is
independent of this app's review history. Listing uses Gmail's `is:read` or
`is:unread` search operator, and fetched labels are checked again in case status
changed. Mismatches appear in skip reasons. Scanning never changes read status.

Reference: [Gmail search operators](https://support.google.com/mail/answer/7190?hl=en).

### Account-aware category picker

The picker is labeled **Gmail categories**, not enabled tabs. All Inbox is always
available. On connection and after changes to read status or completed scans,
the app requests Gmail's estimated matching Inbox counts for All Inbox and each
standard category, using ID-only list calls with two concurrent requests. No
bodies are fetched for counts. Counts use the current read/unread filter but are
before local review-history exclusion; they are explicitly approximate. An empty
category shows No matching emails. Missing/failed counts show Count unavailable
while keeping the scope selectable. Gmail's API does not expose enabled-tab
configuration. Custom label selection is not included in this picker.

## V3 — Subscriptions, action history, and Restore

V3 adds **Subscriptions** (`/subscriptions`) and **Action history / Restore**
(`/history`) to the existing local app. Launch with `./start.sh`; existing
`gmail.modify` authorization is sufficient. No sending or permanent-delete
features are added, and V2 model thresholds are unchanged.

### Discover and unsubscribe

Choose a scan limit from 1 to 10,000. Discovery reads Inbox and archived messages,
excluding Spam, Trash, sent mail and drafts, and uses message headers without
Laya/Qwen. It groups by exact `List-ID`, with exact sender address as the fallback
when subscription headers provide evidence. Different lists from the same sender
stay separate. Inventory counts are distinct messages observed across scans, not
live mailbox totals. The scan reports listed/scanned/skipped counts, completeness,
and an approximate remaining candidate count when Gmail supplies an estimate.
Stopping retains the inventory already discovered; rescan to refresh it.

**Keep subscription** persists across restarts and suppresses unsubscribe
suggestions only. It does not exempt mail from V2's ordinary review flow.

Direct one-click is enabled only after locally verifying an aligned DKIM signature
covering both `List-Unsubscribe` and `List-Unsubscribe-Post` on the actual raw
message. Verification temporarily fetches raw message bytes (which may contain
MIME attachments) and looks up the sender's DKIM public DNS key; raw bytes are
never stored. Invalid, unavailable or expired signing keys fall back to manual
unsubscribe. This implementation intentionally supports header-based discovery;
body-only unsubscribe link extraction is deferred.

After confirmation, the backend revalidates the source message, destination and
signature, then sends one HTTPS POST with `List-Unsubscribe=One-Click`. It uses no
browser cookies, Gmail credentials, HTTP authorization, environment proxies or
redirect following. Destinations must resolve exclusively to public IP addresses;
the connection is pinned to a validated IP while TLS verifies the original host.
There is no automatic POST retry after an uncertain network outcome.

HTTP 2xx is displayed as **Request accepted; unsubscribe not verified**. Web and
mailto paths require manual completion; the app does not crawl forms or send
unsubscribe email. Explicitly report completion with **I completed unsubscribe**
or **I completed manual unsubscribe**. Such completion is recorded as user-reported,
not independently verified.

References: [RFC 8058](https://www.rfc-editor.org/rfc/rfc8058.html).

### Review and clean a subscription backlog

After an accepted request or reported manual completion, use **Review emails**.
The app discovers candidate IDs and checks each actual `List-ID`, or exact sender
plus subscription evidence. Archived messages are included. It displays subject,
sender, date and protected state without fetching bodies for review. The selected
subscription supplies the review context; Laya/Qwen are not run again.

For sender fallback, **Review broader exact-sender matches** offers an explicit
separate review, including messages without subscription evidence. This mode
requires individual message selection, because the sender may also send receipts,
security messages or other lists. Ordinary subscription matches support selecting
up to 1,000 eligible messages at a time. Scan completeness and matching counts
always apply to the requested bounded scan, rather than the entire mailbox.

Review the exact count and confirm before Trash. Confirmation expires after five
minutes and binds to the account, current scan and exact ordered message IDs.
Immediately before each write the app verifies current labels, list/sender
identity and Starred/Important protections again. Partial results include per-message
outcomes; retry requests a fresh confirmation for eligible failures only.

### History and recovery

Every new V2/V3 Trash write and V3 Restore/one-click request has a durable pending
journal entry before the external action. Successful results and failures are
recorded separately. Pending records survive crashes and uncertain Gmail network
results so recovery can reconcile actual Gmail state. Failure to save the pending
record prevents that external action; a failure to update the result does not
turn a successful Gmail write into a failed write.

The recovery view imports legacy V2 records marked `trashed` once per account,
checks Gmail, and offers selection only for messages currently in Trash. Restore
requires a separate exact-count confirmation and uses message-level `untrash`.
It leaves already-restored messages unchanged, handles missing messages separately,
and supports partial failure/retry. It removes the Trash label and does not promise
an Inbox destination or recovery after Gmail has removed a message permanently.
Restored messages remain in local review history and are put in Keep if still
present in the current V2 session.

Subscriptions, observed IDs, preferences and per-message actions are stored in
`.local/history/reviews.sqlite3` alongside the existing review table. Accounts use
separate SHA-256 keys. The directory is 0700 and database 0600, both Git-ignored.
No bodies, subjects, unsubscribe tokens/URLs or OAuth credentials are persisted
for V3. Recovery display metadata is fetched into memory on demand.

Separate confirmed controls clear the connected account's inventory/preferences
or action/restore history. Clearing recovery history loses the app's recovery list;
it does not change Gmail. Cleared legacy records are not imported again. V2's
review-history and optional feedback controls retain their separate behavior.

Reference: [Gmail untrash](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/untrash).

### V3 verification

```sh
.venv/bin/python -m unittest discover -s tests -v
npm run build --prefix frontend
```

The V3 tests include real cryptographic DKIM verification using a synthetic signed
message and public-key fixture, malicious URL/DNS checks, confirmation/account
binding, exact grouping and matching, stopped/partial scans, protected mail,
partial writes, legacy migration, durable restart recovery, and independent clear
controls. See `docs/v3-validation.md` for the current validation record. A live
unsubscribe requires the user to choose a disposable subscription and explicitly
confirm that specific action; no real subscription is selected automatically.

## V4 — Dashboard and learning from corrections

Open `http://127.0.0.1:8765/dashboard` for account-separated all-time totals.
Completed classification sessions, stopped sessions, subscription discovery, and
backlog scans are counted separately. Reviewed totals deduplicate Gmail message
IDs; Keep reflects their latest recorded state. Successful Trash/Restore actions
come from the retained journal, including deliberate repeat cycles. Accepted
unsubscribe requests are distinct from verified completion. Earlier ephemeral
scans cannot be reconstructed; the dashboard explains its historical coverage.
V1 smoke tests and the isolated developer model check are excluded from product
run totals. Clearing inventory/action history resets the corresponding totals.

**Learning from corrections** is one persistent, per-account switch, initially
off. Enabling captures explicit Keep/Cleanup classifications and allows their
use as examples. Disabling stops both capture and retrieval, retaining saved
examples. A confirmed **Clear learned corrections** deletes only that account's
examples; it leaves metrics, review/action history, and the consent choice intact.
Legacy V2 feedback is retained separately and never imported into learning.

Examples share the private, Git-ignored `.local/history/reviews.sqlite3` database
with existing history. Learning stores a parsed sender address, bounded subject,
List-ID, up to 2,000 characters of normalized text, and the explicit label. URLs
are redacted; OAuth state and attachments are never passed to the store. Dashboard
metrics store only message IDs, current buckets, and run summaries, not bodies.

Deterministic local token similarity and sender/List-ID/domain evidence retrieve
up to three examples from the latest 1,000 account-specific corrections. Sender
identity alone is insufficient. Relevant memory triggers a local Qwen preference
review, even outside Laya's ambiguous band; original Laya Keep/Cleanup probabilities remain
visible. Weak matches, conflicting examples, or model failure yield Needs Review.
A result shows its prior-example provenance. This changes recommendations only:
Starred/Important protection, selection, confirmation expiry, account binding,
and Gmail revalidation continue to govern every write. No training, cloud sync,
scheduler, or sender rules are added.

Verification:

```sh
.venv/bin/python -m unittest discover -s tests -v
npm run build --prefix frontend
# Optional real-model check: synthetic mail, temporary store, no Gmail access.
PYTHONPATH=. .venv/bin/python scripts/validate_v4_learning.py
```

See `docs/v4-validation.md` for the validation record and local acceptance steps.

## Keep vs Cleanup decisioning

The updated notebook question is: **Is this email something I am realistically
likely to want or need to come back to later as a durable personal record?**

Keep has a high bar: durable records and meaningful conversations. Routine
receipts, ride confirmations, account/security/transaction alerts, reminders,
verification codes, newsletters and easily recoverable content normally belong
in Cleanup. A financial, legitimate, transactional or informational sender/type
alone does not establish archive value.

Laya uses compact instructions and described KEEP/CLEANUP/NEEDS_REVIEW choices.
The complete choice prompt fits its installed 192-token question-head budget;
long prose beyond that budget would be truncated by the library. A separate local
value-basis choice detects contradictory judgments. Explicit ambiguity, close
competing decisions (less than 0.10 score separation), or contradictory value
rationale yield Needs Review. The old blanket 15–85% routing band is retired.
Confidence alone does not force review.

Laya returns a decision, a concise criteria-based reason, and its native entropy
confidence scaled to 0–100. This is an uncalibrated model score, not a measured
probability of personal usefulness. Laya is a discriminative model and does not
produce an open-ended generated explanation; the application summarizes the
selected decision and ambiguity evidence in a sentence. Qwen uses the fuller
updated guidance and returns decision, reason and numeric 0–100 confidence.

The UI separates final recommendation from original Laya scores and confidence.
Relevant correction memory still triggers a local Qwen pass. Conflicting memory,
weak retrieval and call failure retain review. The installed models, history,
consent choice, and selection/confirmation/Gmail revalidation remain unchanged.
No automatic mailbox action results from a classification.

Previous correction examples were cleared at the user's earlier request; this
prompt revision does not clear newly learned examples again. Untagged legacy
examples remain excluded. The user excluded a 50–100-email evaluation dataset and
labeling workflow. Contract/regression tests and small local integration checks
are not claims of real-mailbox classification accuracy.

### Current retention prompt

The user-supplied retention policy now checks current unresolved importance before durable record value, then transient/replaceable content and genuine ambiguity. Laya and Qwen share the full policy in `app/services.py`. Laya uses a 1,024-token question allowance and 1,536-token total sequence (within its 8,192-position encoder capacity), replacing the previous compact 192/512 configuration to avoid truncating the policy. This changes inference context, not model weights. Classification remains a recommendation requiring confirmation for Trash.

### Fetch once, classify independently with Laya or Winnow

The Cleanup UI fetches an Inbox snapshot first without running any classifier or Qwen. After fetching, separate **Classify with Laya** and **Classify with Winnow** controls evaluate that same snapshot on request. Each branch retains its own recommendations and Qwen results. **View Laya results** and **View Winnow results** switch between branch-specific Keep, Cleanup and Needs Review lists. Classification jobs are serialized to avoid competing for local compute; neither branch needs another Gmail fetch.

Keep and Needs Review items have branch-specific **Send to Qwen** controls, individually or for a filtered view up to 1,000 messages. Qwen changes only that branch's final recommendation, preserving the other branch and both original model judgments. Relevant corrections can inform Qwen; failed reviews preserve the existing recommendation. These actions never mark reviewed, capture training examples or perform Gmail actions. Comparison reports independent original model distributions, times and disagreements; it does not merge the recommendations or measure accuracy.

Actual Trash/Restore state is shared across branches. Starred/Important protection, user preferences and confirmation checks remain enforced. A new fetch or service restart clears both temporary branches. The compatibility API still accepts the earlier immediate classification options, but the UI uses fetch-first, independent manual branches.

Winnow uses its native typed-decision server at `127.0.0.1:8091`, separately from the application at port 8765. The model is text-only Q8; no vision projector is needed. The runtime is in ignored `.local/winnow-runtime`, source revision `77d14580c6732ca2f3745750c1dc1fd446d8bcee`. Prerequisites include Xcode command-line tools, CMake and OpenSSL. Its setup downloads approximately 12.67 GB and verifies the release checksum:

```sh
git clone https://github.com/EldanRing/winnow-inference.git .local/winnow-runtime
git -C .local/winnow-runtime checkout 77d14580c6732ca2f3745750c1dc1fd446d8bcee
python3 .local/winnow-runtime/scripts/setup.py --text-only --jobs 8
./scripts/run_winnow.sh
```

The launcher uses an 8,192-token context and the native Apple Silicon profile. Until the runtime is healthy, choose Laya; Winnow/comparison scan starts are blocked with a clear message. No hosted API key is needed. Confidence is an entropy-derived distribution concentration score, not a correctness guarantee. The larger model's scan throughput must be measured on the actual Mac.

### Winnow probability routing (latest policy)

Winnow asks a single binary KEEP/CLEANUP question; it no longer asks a separate rationale question or lets a rationale override the score. The original retention instructions are adapted to two model choices. Cleanup probability strictly above 60% routes to Cleanup candidates; strictly below 20% routes to Keep; both boundaries and the intervening band automatically go to Qwen. Local correction retrieval only influences this intermediate review, not direct high/low-score routing. Laya retains its existing independent policy. The UI shows the two primary Winnow buckets and reveals unresolved review/failure or unfinished items only when present. Starred/Important protection and confirmation before Trash remain enforced; threshold routing does not automatically mutate Gmail. Binary probabilities can differ from earlier three-choice scores and are uncalibrated.

## V5 — Mailbox Explorer

Explorer adds Source, Repetition, Purpose, Human Attention and Retention views over a reusable local mailbox index.
Install `requirements-v5.txt` in the application environment and build the existing
frontend. The local embedding runtime uses `BAAI/bge-small-en-v1.5` through
FastEmbed/ONNX on one CPU thread; its first use downloads public model weights.
It does not use Laya, Winnow, or Qwen for graph construction.

The default scope includes all Gmail folders, including Sent, Drafts, Spam and
Trash. Inbox and Inbox + Archive scopes, categories, sender, dates and limits are
available. The default limit is 20,000 and the configured upper bound is 50,000.
Source and Repetition reuse the same indexed corpus and embeddings. View weights,
thresholds, bounded neighbor count and clustering engines live in
`app/explorer_config.json`. Cluster labels and explanations are deterministic.
Purpose discovers semantic/content groups across senders. Human Attention uses observed automation, list and reply signals. Retention combines provisional subject/list cues with available account-scoped prior cleanup results. These views reuse the same corpus without new model classification. The optional 2D map remains deferred.

Only metadata, subjects, derived terms/template hashes and embeddings persist in
`.local/explorer/index.sqlite3`, separated by account hash. Raw HTML and full
bodies are never saved there. Opening a message refetches its body. Index files
are private (0600), their directory is 0700, and `.local/` is Git-ignored. These
features can still reveal private information; they are not anonymous data.
The latest account-specific report survives an application restart.

Review for Cleanup selects Laya or Winnow and refetches up to 5,000 exact cluster
IDs into the existing cleanup workflow. It excludes Sent, Drafts, Trash and
unavailable messages, preserves Starred protection, and requires the
existing preview/confirmation before Trash. Explorer-originated cleanup can
include Archive or Spam; write-time labels are rechecked. Normal Inbox cleanup
keeps its original scope rules. History and Restore reuse the existing journal.
No cluster starts an automatic cleanup action.

`LOCAL_APP_PORT` can select another loopback port. For an isolated inspection
instance, set `LOCAL_READONLY_PREVIEW=1`: Gmail exploration is available, while
cleanup handoff, Gmail writes, OAuth changes and shared Keychain token updates
are disabled. Use separate `WINNOW_LOCAL_URL` and `QWEN_LOCAL_URL` loopback
endpoints to keep test inference separate from an active service. The live V5
preview is in a separate worktree; do not rebuild or restart the original
application while a user run is active.


### Additional Explorer views

Purpose uses local embedding similarity and shared terms to discover corpus
communities. Labels summarize recurring language; the algorithm does not force
messages into a predefined receipt/newsletter taxonomy. Human Attention groups
mailing-list, automated, reply-linked and direct-mail signals. It does not infer
that a person read/replied to a message or that a conversation is meaningful.

Retention displays provisional transient, durable-record, action-related or
uncertain cues. Available prior cleanup buckets and explicit decisions enter as
additional graph features, account-bound and limited to selected IDs. They are
not treated as ground truth or permission to delete. No new Laya/Winnow/Qwen
calls occur on a view switch. Retention cache keys change when those prior
results change. Unknown evidence can remain unclustered. Cluster detail explains
both the evidence and its limits. Subject-derived action cues do not establish
whether an issue is currently unresolved. Existing cleanup review remains the
only route to retention decisions and explicit mailbox actions.
