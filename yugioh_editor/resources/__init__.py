from pathlib import Path

RESOURCE_DIRECTORY = Path(__file__).resolve().parent


def get_resource_path(file_name: str) -> Path:
    path = RESOURCE_DIRECTORY / file_name
    if not path.is_file():
        raise FileNotFoundError(f"Resource file does not exist: {path}")
    return path
