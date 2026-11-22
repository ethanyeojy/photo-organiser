"""Optional, clearly labelled date ESTIMATES for photos with no embedded capture date.

Off by default. Real EXIF dates always win; these are only used when none exists, and
every estimated photo is flagged (record.date_estimated, "(estimated)" source, CSV column).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional

from .models import PhotoRecord

FALLBACK_NONE = "none"
FALLBACK_FILENAME = "filename"
FALLBACK_FILENAME_MTIME = "filename_mtime"
FALLBACK_LABELS = {
    FALLBACK_NONE: "Leave as 'Unknown Date' (recommended)",
    FALLBACK_FILENAME: "Estimate from the date in the filename",
    FALLBACK_FILENAME_MTIME: "Filename date, then the file's modified date",
}

LABEL_FILENAME = "Filename date (estimated)"
LABEL_MTIME = "File modified date (estimated)"
MIN_YEAR = 1990

# YYYYMMDD with optional - _ . separators, optionally followed by a time such as
# 193017 or 193017123 (Image_20261115193017_1.jpg, IMG_20261115_193017.jpg, PXL_20261115_193017123.jpg)
_PATTERN = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?:\d{4,9})?(?!\d)")


def _plausible(d: date) -> bool:
    return d.year >= MIN_YEAR and d <= date.today() + timedelta(days=1)


def date_from_filename(name: str) -> Optional[date]:
    """First real, plausible calendar date found in the filename, else None."""
    for y, m, d in _PATTERN.findall(name):
        try:
            found = date(int(y), int(m), int(d))
        except ValueError:  # e.g. month 13
            continue
        if _plausible(found):
            return found
    return None


def _mark(rec: PhotoRecord, day: date, label: str) -> None:
    rec.capture_dt = datetime(day.year, day.month, day.day)
    rec.date_source = label
    rec.date_estimated = True
    rec.warnings.append(f"Date is an estimate ({label.split(' (')[0].lower()}); no embedded capture date")


def apply_date_fallback(rec: PhotoRecord, modified: Optional[float], mode: str) -> None:
    if rec.capture_dt is not None or mode == FALLBACK_NONE:
        return
    day = date_from_filename(rec.original_filename)
    if day:
        _mark(rec, day, LABEL_FILENAME)
        return
    if mode == FALLBACK_FILENAME_MTIME and modified:
        try:
            when = datetime.fromtimestamp(modified).date()
        except (OverflowError, OSError, ValueError):
            return
        if _plausible(when):
            _mark(rec, when, LABEL_MTIME)
