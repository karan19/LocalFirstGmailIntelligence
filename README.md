# Local-first Gmail Intelligence

A local Gmail review app for Apple Silicon Macs. Explore similar emails, compare
local Keep/Cleanup recommendations, and explicitly confirm mailbox actions.

**Public alpha:** intended for technical users running from source. This is not
a hosted service or an automatic deletion system. Recommendations and topic
labels can be wrong. Start with a small read-only sample before using cleanup.
No classification accuracy or throughput guarantee is established.

## What you can do

- **Test:** classify up to 10 Inbox messages with local Laya, without changing Gmail.
- **Inbox cleanup:** fetch once, then independently classify with Laya or optional
  Winnow. Request Qwen review, correct decisions, select candidates, and confirm
  moving an exact selection to Trash. Starred messages are protected.
- **Explorer:** reuse a local derived index to group by Source, Repetition,
  Purpose, Human Attention, or Retention. These are descriptive estimates, not
  permission to delete. Default scope includes all folders; adjust it before scanning.
- **Subscriptions:** discover subscription evidence, review supported one-click
  unsubscribe requests, and inspect matching historical mail.
- **History:** inspect action outcomes and request Restore while Gmail retains
  messages in Trash. Restore is not a promise of an Inbox destination.

This main-branch app runs at **http://127.0.0.1:8765**. The separate experimental
Updates/Jev service is not part of this release or its installation instructions.

## Requirements

- Apple Silicon Mac, macOS 14+, native **Python 3.13** recommended (3.11+ supported
  by the runtime checks). Intel Macs, Windows, and Linux are not supported.
- **Node.js 22+**, npm, and Xcode command-line tools (`xcode-select --install`).
  Native dependencies such as HNSW may need the compiler during installation.
- **Ollama** installed and running: install from [ollama.com](https://ollama.com/download/mac).
- A Google account and your own **Desktop OAuth client** with the Gmail API enabled.
- Internet for dependencies, public model downloads, Google authorization, and
  Gmail requests. Model inference runs locally; no cloud AI API key is required.
- A provisional **16 GiB RAM** gate; validation to date used a 64 GiB development
  Mac. That gate is not a certified minimum for every feature. Qwen and Laya
  downloads total about 7 GB, plus the environment, Explorer model/cache and index.
  Optional Winnow adds approximately 12.67 GB of model download.

## Install and launch

Clone this repository and enter its directory, then:

```sh
V0_PYTHON=python3.13 ./setup.sh
./start.sh
```

Setup creates `.venv`, installs the pinned Laya runtime, asks before downloading
missing Laya/Qwen models, and runs synthetic model diagnostics. Start Ollama if
setup requests it, then rerun setup. For unattended setup, `./setup.sh --download-models`
explicitly approves those downloads. System tools are installed by you, not by setup.

`start.sh` installs the complete application dependencies, checks dependency
compatibility against the tested Python dependency constraints, installs locked frontend packages, builds the UI, checks native
imports, and launches only on loopback. Open **http://127.0.0.1:8765** and keep the
terminal open. Ctrl-C stops the server. Run the same command for subsequent launches.

To verify application installation without starting the server or touching Gmail:

```sh
./start.sh --check
```

This still installs dependencies and builds the UI; it does not download model
weights or run inference. To check actual model execution after setup:

```sh
./doctor.sh
./doctor.sh --json
```

Doctor uses synthetic input, offline Laya weights, and local Ollama. It does not
install software or access Gmail. See [troubleshooting](docs/troubleshooting.md).

## Connect your Gmail

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project
   and enable **Gmail API**.
2. In **Google Auth Platform**, configure Branding and Audience. For personal
   testing, choose External / Testing and add your Google account as a test user.
3. Under Data Access, add `https://www.googleapis.com/auth/gmail.readonly` for the
   read-only test. Add `https://www.googleapis.com/auth/gmail.modify` if you plan
   to use Explorer, cleanup, unsubscribe, or Restore.
4. Create an OAuth client of type **Desktop app** and download its client JSON.
5. Store it privately in this checkout (never commit it):

```sh
mkdir -p .local
chmod 700 .local
# Replace this example path with your downloaded Desktop client JSON.
cp "$HOME/Downloads/client_secret_YOUR_CLIENT.json" .local/google-client.json
chmod 600 .local/google-client.json
```

Alternatively set `GMAIL_CLIENT_CONFIG` to its absolute path when launching.
For the smallest first test, choose **Test 10 emails → Connect Gmail**. The browser
opens Google consent and returns to a loopback callback. Cleanup/Explorer require
reconnecting with `gmail.modify`; an existing read-only grant is insufficient.

Each user supplies a separate Google client. This source release does not ship a
shared verified OAuth application. Testing-mode consent can expire and may show
an unverified-app warning. Broad distribution of a shared OAuth client is a
separate publishing/verification task; see Google's
[Desktop OAuth guidance](https://developers.google.com/identity/protocols/oauth2/native-app).

Authorization is stored in macOS Keychain, not a plaintext token file.
Disconnect removes the local authorization; revoke Google's grant separately in
Google Account → third-party connections. Disconnect does not erase local history.

## What stays on disk and what leaves your Mac

Inference uses local Laya, local Ollama/Qwen, optional local Winnow, and local
FastEmbed/ONNX embeddings. Dependencies and public weights come from their
upstream services. Gmail data is exchanged with Google. Explicitly confirmed
unsubscribe requests contact the subscription provider.

The read-only test and active cleanup results are held in memory. History,
action journals, opted-in correction examples, and Explorer's derived index
persist under Git-ignored `.local/`. The Explorer index contains private subjects,
sender/recipient metadata, derived terms and embeddings, but not full bodies or
raw HTML. It is not anonymized. Correction learning is optional and can retain
bounded text excerpts. See [privacy and data handling](docs/privacy.md).

Subject-based cleanup overrides are empty by default. Advanced users may set
`cleanup_subject_preferences` in `config.json` to exact subjects they explicitly
want recommended for cleanup. Matching ignores case, repeated whitespace and
trailing periods/exclamation marks; it does not match a sender or partial subject.
These rules affect recommendations only; Starred protection and confirmation
still govern Gmail writes. Restart after changing configuration.

No model recommendation alone changes Gmail. Cleanup requires selection and an
expiring, account-bound confirmation with fresh label checks. Trash is reversible
only while Gmail retains the message; this project has no permanent-delete route.
Do not rely on Trash or action history as a complete mailbox backup.

## Optional Winnow classifier

Laya is the default working path. Winnow needs a separate native runtime, Xcode
command-line tools, CMake and OpenSSL. Review the upstream model terms first:

```sh
mkdir -p .local
# These are public runtime sources; no Gmail data is sent to this repository.
git clone https://github.com/EldanRing/winnow-inference.git .local/winnow-runtime
git -C .local/winnow-runtime checkout 77d14580c6732ca2f3745750c1dc1fd446d8bcee
python3.13 .local/winnow-runtime/scripts/setup.py --text-only --jobs 8
./scripts/run_winnow.sh
```

Keep it running at `127.0.0.1:8091`. It uses an 8,192-token context and the native
Apple Silicon text-only profile. The app reports Winnow as unavailable until its
runtime is healthy. Laya and Winnow keep separate recommendations over the same
fetched snapshot. Model scores are uncalibrated, not measured deletion safety.

## Development and validation

```sh
./start.sh --check
.venv/bin/python -m unittest discover -s tests -v
```

GitHub Actions performs a fresh dependency install, frontend build, native import
check and synthetic tests on Apple Silicon macOS. It needs no Gmail credentials,
model weights or API keys. Model inference checks remain separate from CI.

See [release validation](docs/release-validation.md) for what was actually tested
and what remains unverified. Public alpha does not imply broad real-mailbox
accuracy testing or a completed independent security audit. Contributions should
use synthetic fixtures: [CONTRIBUTING.md](CONTRIBUTING.md).

## License and attribution

Project code is [MIT licensed](LICENSE). Models and dependencies retain their own
licenses and notices; they are not relicensed by this project. No model weights,
credentials, or private mailbox data are distributed in the source release.
See [third-party components](THIRD_PARTY_NOTICES.md).
