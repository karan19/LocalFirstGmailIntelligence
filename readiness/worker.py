"""Isolated MLX work. Doctor always runs this with Hub offline mode enabled."""
import json
import sys
from pathlib import Path


def main():
    operation = sys.argv[1]
    if operation == 'mlx':
        import mlx.core as mx
        mx.eval(mx.array([1, 2]) + 1)
        print(json.dumps({'device': str(mx.default_device())}))
    elif operation == 'laya-import':
        import laya_mlx
        from importlib.metadata import version
        print(json.dumps({'version': version('laya-mlx')}))
    elif operation == 'download':
        from huggingface_hub import snapshot_download
        from .cli import CONFIG, LAYA_PATH, weights_ready
        repair = (LAYA_PATH / 'model.safetensors').exists() and not weights_ready()
        snapshot_download(CONFIG['laya_model'], revision=CONFIG['laya_revision'],
                          local_dir=LAYA_PATH, force_download=repair,
                          allow_patterns=['model.safetensors', 'rl_agent_config.json',
                                          'encoder/config.json', 'tokenizer/*', 'mlx_config.json',
                                          'LICENSE', 'NOTICE', 'manifest.json'])
        from .cli import weights_ready
        if not weights_ready():
            raise ValueError('Downloaded Laya checkpoint failed checksum verification; rerun setup.')
    elif operation == 'laya-smoke':
        import laya_mlx as laya
        agent = laya.load(Path(sys.argv[2]), dtype='float16')
        result = agent.predict('The light is green. It is safe to proceed.', {
            'signal': {'type': 'choice', 'instructions': 'What color is the light?',
                       'criteria': ['red', 'green', 'blue']}})
        import mlx.core as mx
        result['readiness_metrics'] = {'mlx_peak_memory_bytes': mx.get_peak_memory()}
        print(json.dumps(result))
    else:
        raise ValueError('Unknown worker operation')


if __name__ == '__main__':
    main()
