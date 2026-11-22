"""Copy photos into the output folder, build the ZIP and the CSV report."""
from __future__ import annotations

import csv
import shutil
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, List, Optional

from .models import LOCATION_STATUS_LABELS, OUTPUT_ROOT_NAME, PhotoRecord
from .naming import unique_name

Progress = Optional[Callable[[int, int, str], None]]


def create_run_dir(output_base: Path) -> Path:
    """Every run gets its own new folder, so repeated runs never mix or overwrite."""
    output_base = Path(output_base)
    output_base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for n in range(0, 1000):
        run_dir = output_base / (f"run_{stamp}" + (f"_{n}" if n else ""))
        try:
            run_dir.mkdir()
            return run_dir
        except FileExistsError:
            continue
    raise OSError("Could not create a unique run folder")


def _copy_without_overwrite(src: Path, dest_dir: Path, name: str) -> str:
    """Copy src into dest_dir using exclusive creation. Returns the final file name."""
    taken = {p.name.lower() for p in dest_dir.iterdir()}
    final = unique_name(name, taken)
    while True:
        dest = dest_dir / final
        try:
            with open(src, "rb") as fin, open(dest, "xb") as fout:  # 'x' = fail if exists
                shutil.copyfileobj(fin, fout, 1024 * 1024)
            break
        except FileExistsError:
            final = unique_name(final, taken)
        except BaseException:
            dest.unlink(missing_ok=True)  # remove only our own partial copy
            raise
    if dest.stat().st_size != Path(src).stat().st_size:
        dest.unlink(missing_ok=True)
        raise OSError("copied file size does not match the original")
    try:
        shutil.copystat(src, dest)  # keep the original file times; best effort
    except OSError:
        pass
    return final


def copy_photos(records: List[PhotoRecord], run_dir: Path, progress: Progress = None) -> None:
    """Copy each photo to run_dir/Organised Photos/<dest_folder>/. Originals are only read."""
    root = (Path(run_dir) / OUTPUT_ROOT_NAME).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for i, rec in enumerate(records, 1):
        try:
            dest_dir = (root / Path(*PurePosixPath(rec.dest_folder).parts)).resolve()
            if root not in dest_dir.parents and dest_dir != root:
                raise ValueError("destination escapes the output folder")
            dest_dir.mkdir(parents=True, exist_ok=True)
            rec.dest_name = _copy_without_overwrite(Path(rec.source_path), dest_dir, rec.dest_name)
            rec.status = "Copied (metadata unreadable)" if rec.error else "Copied"
        except Exception as exc:  # one bad file must not stop the job
            rec.status = f"Copy failed: {type(exc).__name__}: {exc}"
        if progress:
            progress(i, len(records), f"Copying {rec.dest_name[:40]}")


def safe_arcname(path: Path, base: Path) -> str:
    arc = PurePosixPath(*Path(path).relative_to(base).parts)
    if arc.is_absolute() or ".." in arc.parts:
        raise ValueError(f"unsafe ZIP path: {arc}")
    return str(arc)


def make_zip(run_dir: Path, progress: Progress = None) -> Path:
    run_dir = Path(run_dir)
    root = run_dir / OUTPUT_ROOT_NAME
    zip_path = run_dir / f"{OUTPUT_ROOT_NAME}.zip"
    # JPEGs are already compressed, so store them as-is (fast); strict_timestamps=False
    # avoids errors for photos with file dates before 1980.
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED, allowZip64=True, strict_timestamps=False) as zf:
        paths = [p for p in sorted(root.rglob("*")) if p.is_file()]
        for i, path in enumerate(paths, 1):
            zf.write(path, safe_arcname(path, run_dir))
            if progress:
                progress(i, len(paths), f"Adding to ZIP: {path.name[:40]}")
    return zip_path


# ---- CSV report ------------------------------------------------------------
BASE_COLUMNS = [
    "original_filename", "source_relative_path", "capture_date", "capture_time_local",
    "utc_offset_recorded", "metadata_source", "date_is_estimate", "gps_status", "location", "location_status",
    "destination", "status", "notes",
]


def csv_safe(value: object) -> str:
    """Stop spreadsheet programs treating text such as '=cmd' as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def write_report(
    records: Iterable[PhotoRecord],
    path: Path,
    include_gps: bool = False,
    skipped: Iterable[str] = (),
) -> Path:
    columns = BASE_COLUMNS + (["latitude", "longitude"] if include_gps else [])
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:  # BOM so Excel shows macrons
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for r in records:
            copied = r.status.startswith("Copied")
            notes = "; ".join([r.error] * bool(r.error) + r.warnings + [r.location_note] * bool(r.location_note))
            row = {
                "original_filename": r.original_filename,
                "source_relative_path": r.relative_path,
                "capture_date": r.capture_date or "Unknown Date",
                "capture_time_local": r.capture_dt.strftime("%H:%M:%S") if r.capture_dt and not r.date_estimated else "",
                "utc_offset_recorded": r.utc_offset,
                "metadata_source": r.date_source or "none",
                "date_is_estimate": "yes" if r.date_estimated else "no",
                "gps_status": r.gps_status,
                "location": r.location or "",
                "location_status": LOCATION_STATUS_LABELS.get(r.location_status, r.location_status),
                "destination": f"{OUTPUT_ROOT_NAME}/{r.dest_folder}/{r.dest_name}" if copied else "",
                "status": r.status,
                "notes": notes,
            }
            row = {k: csv_safe(v) for k, v in row.items()}
            if include_gps:  # numbers are written as-is (not passed through csv_safe)
                row["latitude"] = "" if r.latitude is None else f"{r.latitude:.6f}"
                row["longitude"] = "" if r.longitude is None else f"{r.longitude:.6f}"
            writer.writerow(row)
        for rel in skipped:
            row = {c: "" for c in columns}
            row.update(original_filename=csv_safe(Path(rel).name), source_relative_path=csv_safe(rel),
                       status="Skipped: unsupported format (videos, RAW, GIF and BMP are not handled)")
            writer.writerow(row)
    return Path(path)
