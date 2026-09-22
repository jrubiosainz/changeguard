import hashlib
import json
import re
from pathlib import Path

ENTRY_FILES = ("app.py", "gunicorn.conf.py", "requirements.txt")
SOURCE_FOLDERS = ("changeguard", "static", "templates", "workflow")


def runtime_paths(root: Path) -> list[Path]:
    paths = [root / name for name in ENTRY_FILES]
    for folder in SOURCE_FOLDERS:
        paths.extend(
            path
            for path in (root / folder).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        )
    if any(not path.resolve().is_relative_to(root.resolve()) for path in paths):
        raise ValueError("Runtime source must not reference files outside the application")
    return sorted(paths)


def source_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in runtime_paths(root):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def verified_build(root: Path) -> dict:
    path = root / "build-info.json"
    if not path.exists():
        raise ValueError("Live mode requires a deployment-bundled build identity")
    info = json.loads(path.read_text())
    commit = info.get("sourceCommit", "")
    if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", commit) or set(commit) == {"0"}:
        raise ValueError("Live build identity must contain a real source commit")
    if info.get("sourceDigest") != source_digest(root):
        raise ValueError("Deployed runtime files do not match the bundled build identity")
    return info
