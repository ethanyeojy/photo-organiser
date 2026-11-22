"""Find JPEG files in a folder, skipping the output directory."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from .models import SourceFile

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg"}
# Common media types we deliberately do NOT process in v1. They are listed as "skipped".
KNOWN_UNSUPPORTED = {
    ".png", ".heic", ".heif", ".webp", ".tif", ".tiff", ".gif", ".bmp",
    ".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".raf",
    ".mov", ".mp4", ".m4v", ".avi",
}


@dataclass
class ScanResult:
    files: List[SourceFile] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)  # relative paths of unsupported media
    warnings: List[str] = field(default_factory=list)


def is_within(child: Path, parent: Path) -> bool:
    child, parent = child.resolve(), parent.resolve()
    return child == parent or parent in child.parents


def validate_folders(source: Optional[Path], output_base: Path) -> List[str]:
    """Return a list of problems (empty list = OK)."""
    problems = []
    if source is not None:
        if not source.exists():
            problems.append(f"Source folder does not exist: {source}")
        elif not source.is_dir():
            problems.append(f"Source path is not a folder: {source}")
        elif is_within(source, output_base):
            problems.append(
                "The source folder is inside the output folder. Choose an output folder "
                "that does not contain your photos."
            )
    return problems


def scan_folder(source: Path, exclude_dirs: Iterable[Path] = (), recursive: bool = True) -> ScanResult:
    source = Path(source)
    excluded = [Path(p).resolve() for p in exclude_dirs]
    result = ScanResult()

    def on_error(err: OSError) -> None:
        result.warnings.append(f"Could not read folder {err.filename}: {err.strerror}")

    for dirpath, dirnames, filenames in os.walk(source, topdown=True, onerror=on_error):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(".") and d != "__MACOSX"
            and not any(is_within(here / d, ex) for ex in excluded)
        )
        if not recursive:
            dirnames[:] = []
        for name in sorted(filenames):
            if name.startswith("."):  # hidden files, macOS "._" resource forks
                continue
            full = here / name
            rel = Path(os.path.relpath(full, source)).as_posix()
            ext = full.suffix.lower()
            if ext in SUPPORTED_EXTENSIONS:
                try:
                    modified = full.stat().st_mtime
                except OSError:
                    modified = None
                result.files.append(SourceFile(full, rel, name, modified))
            elif ext in KNOWN_UNSUPPORTED:
                result.skipped.append(rel)
    return result
