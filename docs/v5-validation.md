# V5 implementation and validation

Implemented in an isolated managed worktree to preserve the main application's
active runs, original data, frontend build and model processes. The original
service on 8765 was not restarted. V5's read-only exploration preview runs on
8766 with its own environment, index and embedding-model cache. Cleanup handoff
is implemented but disabled in that preview; its integration is tested with
mock Gmail/model adapters. No live emails were trashed or unsubscribed for V5.

## Implemented

- Shared Gmail adapter/OAuth/account binding; paginated all-folder ingestion.
- Account-separated private SQLite derived index with reusable embeddings.
- Local 384-dimensional BGE-small ONNX embeddings on one CPU thread.
- Sender/domain/List-ID/reply metadata, normalized subjects, body-template hashes,
  derived terms, attachment metadata, authentication-domain observations.
- HNSW approximate neighbors, bounded metadata candidates and sparse weighted
  edges; no dense pairwise mailbox matrix.
- Configurable/versioned Source and Repetition views, pluggable components and
  Louvain engines, explicit outliers, readable summaries and explanations.
- Cluster detail, representative-message refetch, filtering, mode switching,
  stop/partial results and restart recovery for the connected account.
- Explicit Laya/Winnow exact-ID handoff into existing classification, correction
  memory, preview, confirmation, Trash, action journal and Restore.
- Current Starred/Sent/Draft label checks; normal Inbox rules preserved.
- Shared in-process busy guards serialize Explorer with existing cleanup jobs.

## Verification

The complete Python suite and frontend production build pass. Coverage includes
privacy allowlists/permissions, account separation, cache reuse, no Gmail fetch
on mode switch, all-folder request parameters, protected handoff, running cleanup
exclusion, current labels at Trash time, exact confirmation, partial errors,
restart recovery, browser route and read-only preview restrictions. Existing
V1–V4, Winnow and cleanup taxonomy tests remain green.

A real local-model smoke test produced two 384-dimensional vectors. Browser
verification used a separate synthetic six-message fixture: Analyze produced
one Source group, switching to Repetition produced two template groups,
cluster inspection showed explanations and protection, and opening an email
refetched plaintext. No evaluation sample or training dataset was created.

A 20,000-message synthetic derived corpus with 384-dimensional random vectors
and K=20 measured Source at 21.51 seconds (200 groups, 200,961 sparse edges),
Repetition at 21.18 seconds (20,000 outliers, deliberately unrelated random
vectors), and process peak RSS at 362.6 MiB on this Mac. This measures graph
runtime, not classification quality, Gmail fetch time, embedding generation
time, or end-to-end real-mailbox performance. Initial ingestion can take much
longer. View switching reuses embeddings but may take tens of seconds to build
the new sparse graph. A later small body-template weight was added; the numbers
above describe the initial sparse-graph benchmark.

## Limits and follow-up

- All five views are implemented; the optional map remains deferred. Purpose, Human Attention and Retention are exploratory estimates with limits described below.
- Source organization names use domain heuristics, not verified corporate
  identities; authentication headers are observed features, not trust proof.
- Embeddings use a bounded cleaned-text excerpt. Subject patterns/body hashes
  and semantic similarity are approximate and do not prove retention value.
- Automated-population hints are descriptive; cleanup suitability is judged
  only in the existing classifier and explicit review flow.
- Real 20,000-message ingestion and live cleanup/restore acceptance remain for
  user testing. V5 source and the later Starred-only protection changes have been consolidated
  into the main checkout. All 105 tests and the frontend build pass. Changes
  remain uncommitted and unpushed. The running main service and its frontend
  build were left untouched; the isolated preview still blocks mailbox changes.


## Repetition v2 — campaign wording

Subject similarity now compares normalized meaningful words rather than raw
space-separated tokens with punctuation. Urgency wording and numeric placeholders
do not define a campaign. A configurable semantic-family route additionally
requires cosine similarity >=0.94, meaningful subject overlap >=0.25, common
sender or List-ID, and automation signals on both messages. Shared sender alone
never establishes repetition. The mode is versioned `repetition_v2`, preventing
reuse of the old view cache. No embedding re-generation or Gmail ingestion is
needed to rebuild a view over the indexed corpus.

Validation against the existing 100 Inbox Promotions records joined the missed
NYT Anniversary Sale email to its three counterparts (cosine similarity 0.989).
The resulting view has two groups (NYT:4, Bath & Body Works:3) and 93 outliers.
This is a targeted grouping improvement, not evidence that every unclustered
promotion is unique or should be kept. Regression fixtures cover wording changes
and unrelated content from the same sender. All 106 tests pass. Additional
Purpose, Human Attention and Retention modes remain a separate next step.


## Additional V5 modes

Purpose, Human Attention (`attention`) and Retention are implemented as
configurable/versioned sparse graph views. They reuse account-separated derived
records and embeddings and share the same detail and cleanup handoff APIs.
Purpose discovers semantic communities across senders and labels them from
recurring corpus terms, without a fixed purpose taxonomy. Human Attention uses
observable list/automation/recipient/reply signals. Retention combines provisional
subject/list cues with available prior cleanup buckets and explicit same-message
user decisions; no body text is loaded from correction storage.

These are exploratory estimates, not full model-derived semantic classifications.
Labels explicitly distinguish observed signals from verified personal importance,
resolved actions or safe deletion. Unknown attention/retention evidence may remain
unclustered. The existing cleanup pipeline remains unchanged. Cache identities
include retention-result features so a later decision is not hidden by an old
view cache. No new classifier inference, body retention or Gmail mutations are
introduced by these modes. Internal cached feature sets are excluded from output.

Tests cover semantic cross-sender discovery, machine/conversation separation,
uncertain outliers, action-cue limits, retention result cache invalidation,
account isolation, and reuse of the index without Gmail/embedding/classifier
calls. Existing protected cleanup/handoff/restore tests continue to pass.

The new views are ready in main and validated. Activation is pending a safe
service restart because a new Laya cleanup run started during deployment checks.
The frontend consults backend capabilities and hides views the running backend
has not loaded, preserving current testing.
