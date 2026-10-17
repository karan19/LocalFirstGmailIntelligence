# Privacy and local data

This source release runs one local user and one Gmail connection per server.
It binds to `127.0.0.1`; do not expose it through a public reverse proxy or run
it as a shared multi-user service. Local cookies, Origin checks and account
binding are application controls, not protection from other software running
under your macOS account.

## Network use

- Google receives OAuth and Gmail API requests. Authorization uses a Desktop
  client, PKCE and a loopback callback. Tokens are stored in macOS Keychain.
- Installation downloads packages and public model weights from their upstream
  services. Explorer downloads its public embedding model on first use.
- Model inference uses local Laya/MLX, local Ollama/Qwen, optional local Winnow,
  and local FastEmbed/ONNX. The main app has no Jev/cloud-model integration.
- An explicitly approved one-click unsubscribe sends an HTTPS request to the
  subscription provider. Accepted HTTP status is not proof of non-delivery.

## Local storage

| Location | Contents | Lifetime |
| --- | --- | --- |
| `.local/google-client.json` or `GMAIL_CLIENT_CONFIG` | Your Google Desktop client configuration | Until you remove it |
| macOS Keychain | Google authorization for this app | Until disconnect/removal; revoke the grant at Google separately |
| Process/browser memory | Active fetched message content and classification results | Server restart / close the browser tab |
| `.local/history/` | Account-separated review IDs, action/recovery journal, subscription evidence, metrics and optional learned corrections | Persists across restart |
| `.local/explorer/index.sqlite3` | Private metadata, subjects, recipient/sender information, derived terms, attachment metadata and embeddings | Persists across restart |
| `.local/explorer/models`, `.models/laya`, Ollama model store | Public model weights/cache | Until removed |
| `.local/winnow-runtime` (optional) | Third-party runtime and downloaded public weights | Until removed |

Explorer does not persist complete bodies or HTML in its index. Opening an email
refetches its body. The derived index and embeddings still reveal information;
they are not anonymized. Optional correction learning can retain the parsed
sender, subject, List-ID, and up to 2,000 characters of normalized email text.
Turning learning off stops new examples; use the explicit clear control to erase
saved examples. Review history, learning and action recovery are separate stores
and controls; clearing one does not imply all others were erased.

Private SQLite files use mode 0600 and directories 0700. `.local/`, model weights,
virtual environments, client configuration and common database/secret filenames
are excluded from Git. Git ignore rules do not encrypt or erase files, and cannot
remove secrets already committed in earlier history. Never upload your local
folders or unredacted browser screenshots in an issue.

## Disconnect and erase

Stop the server before removing local data. Disconnect removes the app's saved
Keychain authorization; Google Account → third-party connections revokes the
Google-side grant. Neither disconnect nor revocation deletes local history.

For a complete local application-data reset, back up anything you need, stop the
server and remove this checkout's `.local/` directory in Finder. This also removes
its client JSON, review history, correction examples, subscription/action journals,
Explorer index and cached embedding model (and optional Winnow install). It does
not change Gmail or clear the separate Ollama model store, `.models/laya`, an
externally configured client JSON, or operating-system backups. Filesystem deletion
is not a secure-erasure guarantee. No personal data is required to contribute.

## Mailbox actions

Recommendations are uncalibrated and may be mistaken. Only explicit selection
and confirmation authorize Trash, Restore or supported unsubscribe actions.
Current account and labels are checked again before writes. Starred messages are
protected; an Important label alone is not a blanket protection in the current
policy. No permanent Gmail deletion is implemented. Gmail controls Trash retention.
