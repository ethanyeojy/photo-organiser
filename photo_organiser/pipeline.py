"""Analyse photos and plan where each one will be copied (no files are touched)."""
from __future__ import annotations

from collections import Counter
from typing import Callable, Dict, List, Optional

from .datefallback import FALLBACK_NONE, apply_date_fallback
from .geocoder import STATUS_FAILED, STATUS_OK, NominatimGeocoder
from .metadata import read_photo
from .models import (
    GPS_PRESENT, LOC_FAILED, LOC_NO_GPS, LOC_NO_RESULT, LOC_NOT_REQUESTED, LOC_OK,
    UNKNOWN_DATE, UNKNOWN_LOCATION, OUTPUT_ROOT_NAME, PhotoRecord, SourceFile,
)
from .naming import safe_component, safe_filename, unique_name

Progress = Optional[Callable[[int, int, str], None]]


def analyse_photos(
    files: List[SourceFile],
    geocoder: Optional[NominatimGeocoder] = None,
    progress: Progress = None,
    date_fallback: str = FALLBACK_NONE,
) -> List[PhotoRecord]:
    """Read metadata for every file. Location lookups happen only if a geocoder is given
    (i.e. the user consented). Individual failures never stop the run."""
    records = []
    for i, f in enumerate(files, 1):
        rec = read_photo(f.path, f.relative_path, f.original_filename)
        apply_date_fallback(rec, f.modified, date_fallback)
        records.append(rec)
        if progress:
            progress(i, len(files), f"Reading {f.original_filename[:40]}")

    if geocoder is None:
        for rec in records:
            rec.location_status = LOC_NOT_REQUESTED
    else:
        _resolve_locations(records, geocoder, progress)
    plan_destinations(records, include_location=geocoder is not None)
    return records


def _resolve_locations(records: List[PhotoRecord], geocoder: NominatimGeocoder, progress: Progress) -> None:
    groups: Dict[str, List[PhotoRecord]] = {}
    for rec in records:
        if rec.error or rec.gps_status != GPS_PRESENT:
            rec.location_status = LOC_NO_GPS
        else:
            groups.setdefault(geocoder.cache_key(rec.latitude, rec.longitude), []).append(rec)

    for i, members in enumerate(groups.values(), 1):
        result = geocoder.lookup(members[0].latitude, members[0].longitude)
        for rec in members:
            if result.status == STATUS_OK:
                rec.location, rec.location_status = result.name, LOC_OK
            elif result.status == STATUS_FAILED:
                rec.location_status, rec.location_note = LOC_FAILED, result.message
            else:
                rec.location_status = LOC_NO_RESULT
        if progress:
            progress(i, len(groups), "Looking up place names (max 1 per second)")


def plan_destinations(records: List[PhotoRecord], include_location: bool) -> None:
    """Fill dest_folder/dest_name. Deterministic; safe to call again to reset names."""
    taken: Dict[str, set] = {}
    for rec in records:
        parts = [rec.capture_date or UNKNOWN_DATE]
        if include_location:
            parts.append(safe_component(rec.location, UNKNOWN_LOCATION) if rec.location else UNKNOWN_LOCATION)
        rec.dest_folder = "/".join(parts)
        rec.dest_name = unique_name(safe_filename(rec.original_filename), taken.setdefault(rec.dest_folder.lower(), set()))


def summarise(records: List[PhotoRecord]) -> Dict[str, int]:
    """Counts. Unreadable files are counted as errors only, not as missing date/GPS."""
    ok = [r for r in records if not r.error]
    return {
        "total": len(records),
        "errors": len(records) - len(ok),
        "missing_date": sum(1 for r in ok if r.capture_dt is None),
        "estimated_date": sum(1 for r in records if r.date_estimated),
        "missing_gps": sum(1 for r in ok if r.gps_status != GPS_PRESENT),
        "lookup_failed": sum(1 for r in records if r.location_status == LOC_FAILED),
        "no_place_found": sum(1 for r in records if r.location_status == LOC_NO_RESULT),
    }


def group_counts(records: List[PhotoRecord]) -> List[tuple]:
    counts = Counter(r.dest_folder for r in records)
    return sorted(counts.items(), key=lambda kv: (kv[0].startswith(UNKNOWN_DATE), kv[0]))


def build_tree_text(records: List[PhotoRecord], max_lines: int = 300) -> str:
    tree: dict = {}
    for rec in records:
        node = tree
        for part in rec.dest_folder.split("/"):
            node = node.setdefault(part + "/", {})
        node[rec.dest_name] = None
    order = lambda k: (k.startswith(UNKNOWN_DATE), k)  # noqa: E731  (Unknown last)
    lines = [OUTPUT_ROOT_NAME + "/"]

    def walk(node: dict, prefix: str) -> None:
        keys = sorted(node, key=order)
        for i, key in enumerate(keys):
            last = i == len(keys) - 1
            lines.append(f"{prefix}{'└── ' if last else '├── '}{key}")
            if node[key] is not None:
                walk(node[key], prefix + ("    " if last else "│   "))

    walk(tree, "")
    if len(lines) > max_lines:
        extra = len(lines) - max_lines
        lines = lines[:max_lines] + [f"... and {extra} more lines"]
    return "\n".join(lines)
