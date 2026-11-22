"""Read capture date and GPS from a JPEG's embedded EXIF. Nothing is guessed."""
from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Tuple

from PIL import Image

from .models import GPS_INVALID, GPS_MISSING, GPS_PRESENT, PhotoRecord

try:  # lets Pillow open iPhone HEIC/HEIF photos
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:  # not installed: HEIC files will simply be reported as unreadable
    pass

# We only read headers and never decode pixels, so very large images are fine.
Image.MAX_IMAGE_PIXELS = None

# Image formats whose embedded EXIF we read.
SUPPORTED_FORMATS = {"JPEG", "MPO", "PNG", "WEBP", "TIFF", "HEIF"}

EXIF_IFD = 0x8769
GPS_IFD = 0x8825

# (date tag, label shown to the user, matching timezone-offset tag)
DATE_TAGS = [
    (0x9003, "EXIF DateTimeOriginal", 0x9011),  # OffsetTimeOriginal
    (0x9004, "EXIF DateTimeDigitized", 0x9012),  # OffsetTimeDigitized
]

_OFFSET_RE = re.compile(r"^[+-]\d{2}:\d{2}$")
_DATE_FORMATS = ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M", "%Y/%m/%d %H:%M:%S")


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    return value.replace("\x00", "").strip() if isinstance(value, str) else ""


def parse_exif_datetime(value: Any) -> Optional[datetime]:
    """Parse an EXIF date string. Returns the recorded local time (no conversion)."""
    text = _text(value)
    for candidate in (text, text[:19]):
        for fmt in _DATE_FORMATS:
            try:
                dt = datetime.strptime(candidate, fmt)
            except ValueError:
                continue
            return dt if dt.year >= 1900 else None
    return None


def _dms_to_degrees(values: Any) -> Optional[float]:
    try:
        parts = [float(v) for v in values]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if not 1 <= len(parts) <= 3 or not all(math.isfinite(p) for p in parts):
        return None
    degrees = 0.0
    for i, part in enumerate(parts):
        degrees += part / (60 ** i)
    return degrees


def extract_gps(gps: Any) -> Tuple[str, Optional[float], Optional[float], str]:
    """Return (gps_status, lat, lon, note) from a GPS IFD mapping."""
    if not gps or 2 not in gps or 4 not in gps:
        return GPS_MISSING, None, None, ""
    lat = _dms_to_degrees(gps.get(2))
    lon = _dms_to_degrees(gps.get(4))
    lat_ref = _text(gps.get(1)).upper()
    lon_ref = _text(gps.get(3)).upper()
    if lat is None or lon is None or lat_ref not in ("N", "S") or lon_ref not in ("E", "W"):
        return GPS_INVALID, None, None, "GPS tags present but unreadable or missing N/S/E/W reference"
    lat = -lat if lat_ref == "S" else lat
    lon = -lon if lon_ref == "W" else lon
    if abs(lat) > 90 or abs(lon) > 180:
        return GPS_INVALID, None, None, "GPS coordinates out of range"
    if lat == 0 and lon == 0:
        return GPS_INVALID, None, None, "GPS is 0,0 (usually means 'no fix'), ignored"
    return GPS_PRESENT, lat, lon, ""


def read_photo(path: Path, relative_path: str = "", original_filename: str = "") -> PhotoRecord:
    """Read metadata from one file. Never raises: problems go into record.error/warnings."""
    path = Path(path)
    rec = PhotoRecord(
        source_path=str(path),
        original_filename=original_filename or path.name,
        relative_path=relative_path or path.name,
    )
    try:
        with Image.open(path) as img:
            if img.format not in SUPPORTED_FORMATS:
                rec.error = f"Unsupported image type (detected {img.format})"
                return rec
            exif = img.getexif()
            exif_ifd = dict(exif.get_ifd(EXIF_IFD)) if exif else {}
            gps_ifd = dict(exif.get_ifd(GPS_IFD)) if exif else {}
    except Exception as exc:  # corrupt files raise many different errors
        rec.error = f"Could not read metadata ({type(exc).__name__}: {exc})"
        return rec

    for tag, label, offset_tag in DATE_TAGS:
        raw = exif_ifd.get(tag)
        if raw in (None, ""):
            continue
        dt = parse_exif_datetime(raw)
        if dt is None:
            rec.warnings.append(f"{label} is present but not a valid date ({_text(raw)!r})")
            continue
        rec.capture_dt, rec.date_source = dt, label
        offset = _text(exif_ifd.get(offset_tag))
        if _OFFSET_RE.match(offset):
            rec.utc_offset = offset
        break

    rec.gps_status, rec.latitude, rec.longitude, note = extract_gps(gps_ifd)
    if note:
        rec.warnings.append(note)
    return rec
