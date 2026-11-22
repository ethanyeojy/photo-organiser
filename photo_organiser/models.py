"""Shared data structures and constants."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

UNKNOWN_DATE = "Unknown Date"
UNKNOWN_LOCATION = "Unknown Location"
OUTPUT_ROOT_NAME = "Organised Photos"

# gps_status values
GPS_PRESENT = "present"
GPS_MISSING = "missing"  # no GPS tags in the file
GPS_INVALID = "invalid"  # tags exist but are unusable (bad ref, out of range, 0/0)

# location_status values (how the *location* ended up as it did)
LOC_NOT_REQUESTED = "not_requested"  # date-only mode: no lookups were made
LOC_NO_GPS = "no_gps"  # photo has no usable GPS, so nothing to look up
LOC_OK = "ok"  # place name found
LOC_NO_RESULT = "no_place_found"  # service answered, but no town/city for the point
LOC_FAILED = "lookup_failed"  # network/service problem: lookup did not complete

LOCATION_STATUS_LABELS = {
    LOC_NOT_REQUESTED: "Not requested (date-only)",
    LOC_NO_GPS: "No usable GPS in photo",
    LOC_OK: "Found",
    LOC_NO_RESULT: "No place found for coordinates",
    LOC_FAILED: "Lookup failed",
}


@dataclass
class SourceFile:
    """A file to analyse. `path` is where it is on disk right now."""

    path: Path
    relative_path: str  # shown in reports; never used to build output paths
    original_filename: str
    modified: Optional[float] = None  # file modified time (epoch), if known


@dataclass
class PhotoRecord:
    source_path: str
    original_filename: str
    relative_path: str
    capture_dt: Optional[datetime] = None  # naive local time exactly as recorded
    date_source: str = ""
    date_estimated: bool = False  # True when the date came from filename/modified time
    utc_offset: str = ""  # e.g. "+13:00" if the camera recorded it; informational
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    gps_status: str = GPS_MISSING
    location: Optional[str] = None
    location_status: str = LOC_NOT_REQUESTED
    location_note: str = ""
    error: str = ""  # set when metadata could not be read at all
    warnings: List[str] = field(default_factory=list)
    dest_folder: str = ""  # relative, "/"-separated, e.g. "2026-11-20/Paihia"
    dest_name: str = ""
    status: str = "Analysed"

    @property
    def capture_date(self) -> Optional[str]:
        return self.capture_dt.strftime("%Y-%m-%d") if self.capture_dt else None
