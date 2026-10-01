# Local-first Gmail Intelligence

V1 adds a local browser UI for read-only Gmail classification (see the V1 section below).
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
