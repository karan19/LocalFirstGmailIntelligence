# Local-first Gmail Intelligence — V0

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
`127.0.0.1:11434`, bypassing HTTP proxies. Synthetic input is used exclusively.
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
the readiness results. There is no Gmail integration or dashboard in this version.

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
