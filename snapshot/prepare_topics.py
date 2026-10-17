"""Download public embedding weights. Does not open mail or credentials."""
from .topics import MODEL_DIRECTORY


def main():
    from huggingface_hub import snapshot_download
    snapshot_download('sentence-transformers/all-MiniLM-L6-v2', local_dir=MODEL_DIRECTORY,
                      allow_patterns=['*.json', '*.txt', '*.safetensors'], token=False)
    print('Local topic embedding model prepared. Mail analysis can run offline.')


if __name__ == '__main__':
    main()
