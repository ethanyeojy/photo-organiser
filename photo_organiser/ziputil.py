"""Safely unpack the JPEGs inside an uploaded ZIP file.

Protections: paths that try to escape ("../x", "/abs", "C:\\x") are ignored, every file is
written under its own numbered folder with a sanitised name, encrypted entries are skipped,
and size/file-count limits stop "zip bombs". Only JPG/JPEG entries are extracted.
"""
from __future__ import annotations

import zipfile
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, List, Optional

from .models import SourceFile
from .naming import safe_filename
from .scanner import KNOWN_UNSUPPORTED, SUPPORTED_EXTENSIONS

MAX_FILES = 50_000
MAX_TOTAL_BYTES = 20 * 1024 ** 3
MAX_FILE_BYTES = 1024 ** 3

Progress = Optional[Callable[[int, int, str], None]]


@dataclass
class ZipResult:
    files: List[SourceFile] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def extract_jpegs(
    zip_source,
    dest: Path,
    progress: Progress = None,
    max_files: int = MAX_FILES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    max_file_bytes: int = MAX_FILE_BYTES,
) -> ZipResult:
    """Extract JPEGs from `zip_source` (path or file-like) into `dest`."""
    result = ZipResult()
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        zf = zipfile.ZipFile(zip_source)
    except (zipfile.BadZipFile, OSError):
        result.warnings.append("This is not a valid ZIP file.")
        return result

    with zf:
        entries = [i for i in zf.infolist() if not i.is_dir()]
        total_bytes = 0
        for n, info in enumerate(entries, 1):
            name = info.filename.replace("\\", "/")
            path = PurePosixPath(name)
            parts = path.parts
            if progress:
                progress(n, len(entries), f"Unpacking {parts[-1][:40] if parts else '?'}")
            if not parts or path.is_absolute() or ".." in parts or ":" in parts[0]:
                result.warnings.append(f"Ignored unsafe path in ZIP: {info.filename!r}")
                continue
            if any(p.startswith(".") or p == "__MACOSX" for p in parts):
                continue
            ext = path.suffix.lower()
            if ext in KNOWN_UNSUPPORTED:
                result.skipped.append(name)
                continue
            if ext not in SUPPORTED_EXTENSIONS:
                continue
            if info.flag_bits & 0x1:
                result.warnings.append(f"Skipped password-protected file: {name}")
                continue
            if info.file_size > max_file_bytes:
                result.warnings.append(f"Skipped (too large): {name}")
                continue
            if len(result.files) >= max_files or total_bytes + info.file_size > max_total_bytes:
                result.warnings.append("ZIP size/file limit reached; the remaining files were not unpacked.")
                break

            folder = dest / f"{len(result.files):05d}"  # one folder each: equal names never clash
            folder.mkdir()
            target = folder / safe_filename(parts[-1])
            try:
                copied = 0
                with zf.open(info) as src, open(target, "wb") as out:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        copied += len(chunk)
                        if copied > max_file_bytes:
                            raise OSError("file is larger than allowed")
                        out.write(chunk)
            except Exception as exc:  # one bad entry must not stop the rest
                target.unlink(missing_ok=True)
                result.warnings.append(f"Could not unpack {name}: {type(exc).__name__}: {exc}")
                continue
            total_bytes += copied
            try:
                modified = datetime(*info.date_time).timestamp()
            except (ValueError, OverflowError, OSError):
                modified = None
            result.files.append(SourceFile(target, name, parts[-1], modified))

    result.files.sort(key=lambda f: f.relative_path.lower())
    return result
