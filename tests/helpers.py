"""Helpers that create small JPEGs with chosen EXIF data."""
from pathlib import Path

from PIL import Image


def _dms(value: float):
    value = abs(value)
    deg = int(value)
    minutes = int((value - deg) * 60)
    seconds = round((value - deg - minutes / 60) * 3600, 4)
    return (float(deg), float(minutes), seconds)


def make_jpeg(path: Path, date=None, digitized=None, offset=None, gps=None, gps_raw=None, color="red") -> Path:
    """date/digitized: 'YYYY:MM:DD HH:MM:SS'. gps: (lat, lon) decimal degrees."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    exif_ifd = exif.get_ifd(0x8769)
    if date:
        exif_ifd[0x9003] = date
    if digitized:
        exif_ifd[0x9004] = digitized
    if offset:
        exif_ifd[0x9011] = offset
    gps_ifd = exif.get_ifd(0x8825)
    if gps:
        lat, lon = gps
        gps_ifd[1], gps_ifd[2] = ("S" if lat < 0 else "N"), _dms(lat)
        gps_ifd[3], gps_ifd[4] = ("W" if lon < 0 else "E"), _dms(lon)
    if gps_raw:
        gps_ifd.update(gps_raw)
    Image.new("RGB", (8, 8), color).save(path, "JPEG", exif=exif)
    return path
