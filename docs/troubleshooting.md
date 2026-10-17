# Setup and troubleshooting

Use native arm64 Python on Apple Silicon macOS 14+. Python 3.13 and Node.js 22+
are the recommended path. `uname -m` and `python3.13 -c 'import platform; print(platform.machine())'`
should both print `arm64`. If they disagree, exit Rosetta and use a native terminal.

## Installation

- **No `.venv`:** run `V0_PYTHON=python3.13 ./setup.sh`. The setup command validates
  the platform before installing the model runtime. If Homebrew is available,
  `brew install python@3.13` installs the recommended interpreter.
- **Native build fails (HNSW/compiler):** install Xcode command-line tools with
  `xcode-select --install`, finish Apple's installer, then rerun `./start.sh --check`.
- **Missing Explorer imports:** `start.sh` installs `requirements-app.txt`, which
  includes `requirements-v5.txt` and the base web dependencies. Rerun it; do not
  manually copy packages from someone else's environment.
- **Dependency conflict or broken old environment:** stop this app, rename its
  `.venv` folder as a backup, then recreate it with setup and rerun start. Keep
  `.local/` if you want to retain your own client configuration/history. Never
  copy someone else's `.local/` into the project.
- **Node/npm missing or too old:** install Node.js 22+ and verify `node --version`.
- **First-run download interrupted:** rerun setup. Existing verified Laya weights
  and installed Qwen models are reused. Explorer can need internet on first use.
- **Model memory failure:** close other model processes, choose Laya before optional
  Winnow, reduce the scan size, and rerun doctor. A passing total-RAM gate does
  not prove sufficient currently available memory.

## Local services

Start Ollama before setup/doctor or Qwen review. `./doctor.sh` diagnoses the local
model stack with synthetic input and never downloads weights. Winnow is optional
and runs separately via `./scripts/run_winnow.sh` on port 8091.

If port 8765 is occupied, stop the older app terminal or use
`LOCAL_APP_PORT=8875 ./start.sh` and open `http://127.0.0.1:8875`.
Use the numeric loopback address printed by the app, not `localhost`; Host and
Origin checks deliberately reject other addresses. Reload after a backend
restart to obtain a fresh browser session cookie. Avoid restarting during a
mailbox action; scan results are temporary and interrupted writes need their
history/recovery flow checked against Gmail.

## Google sign-in

- **Missing client configuration:** create a Desktop OAuth client and save the
  downloaded JSON as `.local/google-client.json` with directory/file modes 700/600,
  or set an absolute `GMAIL_CLIENT_CONFIG` when launching.
- **App blocked / access denied:** ensure the Gmail API is enabled, the consent
  audience includes your own account as a test user, and the correct requested
  scope is configured. This project does not provide a shared verified client.
- **Read-only test works, cleanup/Explorer does not:** reconnect through the
  cleanup connection flow and consent to `gmail.modify`. A saved readonly grant
  is intentionally insufficient for cleanup features.
- **Grant expired:** reconnect. Google testing-mode authorizations can expire.
- **Keychain denied/locked:** unlock the login Keychain and allow the app's Python
  process to use its own entry. There is no plaintext-token fallback.
- **Rate-limit/network error:** wait and retry the affected operation. Review any
  partial results and history before confirming another write. Main's network
  handling is separate from the experimental Updates/Jev service.

For bug reports include macOS version, architecture, Python/Node versions, which
screen/action failed, and a redacted error. Never include tokens, OAuth client
JSON, mailbox dumps, database files, identifying subjects, or raw doctor JSON
without inspecting paths first. Use synthetic mail when reproducing a problem.
