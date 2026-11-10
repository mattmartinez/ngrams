"""Downloads restricted to two intended public files."""

from pathlib import Path

PUBLIC_ROOT = Path("/srv/public")
PUBLIC_FILES = frozenset({"guide.txt", "prices.txt"})


def download(name):
    basename = Path(name).name
    if basename != name or basename not in PUBLIC_FILES:
        raise ValueError("not a public download")
    path = PUBLIC_ROOT / basename
    return path.read_text(encoding="utf-8")
