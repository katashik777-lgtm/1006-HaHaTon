"""Enumerate directory imports without counting identical Finder/Explorer copies twice."""
import re
from pathlib import Path


def unique_files(folder, extensions):
    for path in sorted(Path(folder).iterdir()):
        if not path.is_file() or path.suffix.lower() not in extensions:
            continue
        stem = re.sub(r' (?:[2-9]|[1-9][0-9]+)$', '', path.stem)
        original = path.with_name(stem + path.suffix)
        if original != path and original.is_file() and path.read_bytes() == original.read_bytes():
            continue
        yield path


def documents(folder):
    return unique_files(folder, ('.json', '.xml', '.csv'))
