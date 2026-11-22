import os
from datetime import date

import pytest

from photo_organiser.datefallback import date_from_filename
from photo_organiser.output import copy_photos, create_run_dir, write_report
from photo_organiser.pipeline import analyse_photos
from photo_organiser.scanner import scan_folder
from tests.helpers import make_jpeg
import csv


@pytest.mark.parametrize("name,expected", [
    ("Image_20261115193017_921_1.jpg", date(2026, 11, 15)),   # the WeChat-style name
    ("IMG_20261115_193017.jpg", date(2026, 11, 15)),
    ("PXL_20261115_193017123.jpg", date(2026, 11, 15)),
    ("2026-11-15 10.30.00.jpg", date(2026, 11, 15)),
    ("holiday_2026_11_15.jpg", date(2026, 11, 15)),
    ("IMG_20261345_000000.jpg", None),       # month 13
    ("IMG_18990101.jpg", None),              # too old
    ("IMG_20991231.jpg", None),              # in the future
    ("IMG_9202611151.jpg", None),            # digits run into a longer number
    ("DSC00123.jpg", None),
])
def test_date_from_filename(name, expected):
    assert date_from_filename(name) == expected


def test_fallback_is_off_by_default_and_exif_always_wins(tmp_path):
    make_jpeg(tmp_path / "Image_20261115193017_1.jpg")                                   # no EXIF
    make_jpeg(tmp_path / "IMG_20260101_000000.jpg", date="2026:11:20 10:00:00")          # EXIF beats name
    files = scan_folder(tmp_path).files
    off = {r.original_filename: r for r in analyse_photos(files)}
    assert off["Image_20261115193017_1.jpg"].dest_folder == "Unknown Date"
    on = {r.original_filename: r for r in analyse_photos(files, date_fallback="filename")}
    est, real = on["Image_20261115193017_1.jpg"], on["IMG_20260101_000000.jpg"]
    assert est.dest_folder == "2026-11-15" and est.date_estimated and "estimated" in est.date_source
    assert real.dest_folder == "2026-11-20" and not real.date_estimated


def test_modified_date_fallback_and_csv_flags_estimates(tmp_path):
    p = make_jpeg(tmp_path / "plain.jpg")
    ts = date(2026, 3, 9)
    stamp = __import__("time").mktime((ts.year, ts.month, ts.day, 12, 0, 0, 0, 0, -1))
    os.utime(p, (stamp, stamp))
    files = scan_folder(tmp_path).files
    assert analyse_photos(files, date_fallback="filename")[0].dest_folder == "Unknown Date"
    rec = analyse_photos(files, date_fallback="filename_mtime")[0]
    assert rec.dest_folder == "2026-03-09" and rec.date_source == "File modified date (estimated)"
    run = create_run_dir(tmp_path / "out")
    copy_photos([rec], run)
    rows = list(csv.DictReader(open(write_report([rec], run / "r.csv"), encoding="utf-8-sig")))
    assert rows[0]["date_is_estimate"] == "yes" and rows[0]["capture_time_local"] == ""
