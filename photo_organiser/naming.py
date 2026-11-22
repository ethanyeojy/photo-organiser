"""Safe file and folder names."""
from __future__ import annotations

import os
import re
import unicodedata
from typing import Set

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {
    f"LPT{i}" for i in range(1, 10)
}
MAX_NAME_BYTES = 200


def _truncate_bytes(text: str, max_bytes: int) -> str:
    while len(text.encode("utf-8")) > max_bytes and text:
        text = text[:-1]
    return text


def safe_component(name: str, fallback: str = "_") -> str:
    """Make one path component (no slashes, no '..', no reserved Windows names)."""
    name = unicodedata.normalize("NFC", str(name or ""))
    name = _INVALID_CHARS.sub("_", name).strip().rstrip(". ")
    if not name or set(name) <= {"."}:
        return fallback
    if name.split(".")[0].strip().upper() in _RESERVED:
        name = "_" + name
    return name


def safe_filename(name: str, fallback: str = "photo.jpg") -> str:
    """Like safe_component, but keeps the extension when shortening long names.

    Ordinary names such as 'IMG 001 (2).jpg' come back unchanged.
    """
    name = os.path.basename(str(name or "").replace("\\", "/"))
    name = safe_component(name, fallback)
    stem, ext = os.path.splitext(name)
    if len((stem + ext).encode("utf-8")) > MAX_NAME_BYTES:
        stem = _truncate_bytes(stem, MAX_NAME_BYTES - len(ext.encode("utf-8")))
        name = (stem or "photo") + ext
    return name


def unique_name(name: str, taken: Set[str]) -> str:
    """Return `name`, or name_1, name_2 ... so it is not in `taken`.

    Comparison is case-insensitive (Windows/macOS file systems are). The chosen
    name is added to `taken`.
    """
    stem, ext = os.path.splitext(name)
    candidate, counter = name, 0
    while candidate.lower() in taken:
        counter += 1
        candidate = f"{stem}_{counter}{ext}"
    taken.add(candidate.lower())
    return candidate
