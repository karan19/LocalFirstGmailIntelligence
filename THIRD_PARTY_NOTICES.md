# Third-party components

The MIT license in this repository applies to project code. It does not relicense
upstream packages, model weights or runtimes. This source release does not bundle
model weights or third-party runtime source trees; installation retrieves them
from their respective upstream projects. Retain upstream LICENSE/NOTICE files
when redistributing those artifacts, and review the terms for the exact revision.

| Component | Use and upstream |
| --- | --- |
| Laya-MLX `0.2.0` | Independent MLX runtime, Apache-2.0. [Source/license/notice](https://github.com/mizorewww/laya-mlx). Credits Convai Innovations and upstream Laya contributors. |
| Laya MLX checkpoint | Public converted weights by aac6fef, pinned at `20aed815fc6acde75733882e7ec0e3f28aeb9717`. [Checkpoint and artifact terms](https://huggingface.co/aac6fef/laya-mlx/tree/20aed815fc6acde75733882e7ec0e3f28aeb9717). Setup retains LICENSE and NOTICE and verifies its recorded manifest. |
| Upstream Laya | [NandhaKishorM/laya](https://github.com/NandhaKishorM/laya) and [Convai Innovations weights](https://huggingface.co/convaiinnovations/laya). Follow the checkpoint notices for upstream attribution. |
| Qwen3 `8b` | Local generation via Ollama. [Model and license](https://ollama.com/library/qwen3:8b). The tag can change; doctor records the installed digest. |
| Ollama | Local inference service installed separately. [Source and terms](https://github.com/ollama/ollama). |
| BGE-small-en-v1.5 | Explorer embeddings via FastEmbed/ONNX. [BAAI model card/license](https://huggingface.co/BAAI/bge-small-en-v1.5). |
| Winnow runtime (optional) | MIT-licensed native runtime, pinned at `77d14580c6732ca2f3745750c1dc1fd446d8bcee`. [Source/license](https://github.com/EldanRing/winnow-inference). Its dependencies retain their own terms. |
| Winnow-12B (optional) | Third-party fine-tuned weights; [model card, license and base-model notices](https://huggingface.co/EldanRing/Winnow-12B). Consult the files fetched by the pinned runtime, not this project's MIT license. |

Python dependency pins are in `requirements.txt`, `requirements-app.txt`,
`requirements-v1.txt` and `requirements-v5.txt`, with a tested resolved set in
`constraints-macos-py313.txt`. The frontend lockfile is
`frontend/package-lock.json`. Their distributions include their respective
license metadata. `.local/`, `.models/`, `.venv/`, frontend dependencies and build
output are intentionally not part of the tracked source distribution.

Gmail and Google trademarks belong to their owners. This project is independent
and is not a Google product or an endorsement by any listed upstream project.
