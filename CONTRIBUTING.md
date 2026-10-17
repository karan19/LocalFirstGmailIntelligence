# Contributing

Use an isolated branch/check-out and synthetic fixtures. Do not commit mailbox
content, OAuth configuration, tokens, learned corrections, databases, model files,
or screenshots containing personal information. The main application is separate
from the experimental Updates/Jev workflow; keep unrelated experiments out of
main changes.

On Apple Silicon macOS with Python 3.13 and Node.js 22+:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./start.sh --check
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_public_tree.py
```

These checks need no Google credentials, model weights, Gmail access or running
model servers. Build the frontend before running HTTP route tests. Model smoke
checks are separate (`./doctor.sh`) and require your installed local models.

Describe the user-facing change and verification in pull requests. Changes to
mailbox writes must preserve explicit selection, account binding, expiring
confirmation and fresh label/protection checks. Test failures and unknown write
outcomes must remain visible and recoverable; never add automatic deletion.

When reporting a suspected vulnerability, use GitHub's private reporting feature
if it is enabled for this repository. Otherwise contact the repository owner
privately; do not publish credentials or exploit details in a public issue.

Contributions are provided under this project's MIT license. Third-party code,
models and other assets keep their own terms; retain their attribution.
