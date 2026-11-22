import csv
import hashlib
import zipfile
from pathlib import Path

from photo_organiser.geocoder import GeocodeResult, NominatimGeocoder
from photo_organiser.output import copy_photos, create_run_dir, csv_safe, make_zip, write_report
from photo_organiser.pipeline import analyse_photos, build_tree_text, plan_destinations, summarise
from photo_organiser.scanner import scan_folder
from tests.helpers import make_jpeg


def snapshot(folder: Path):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() + str(p.stat().st_mtime_ns)
            for p in sorted(folder.rglob("*")) if p.is_file()}


class StubGeocoder(NominatimGeocoder):
    """Pretends to be the web service; no network."""
    def __init__(self, answers):
        super().__init__()
        self.answers, self.requests_made = answers, 0

    def lookup(self, lat, lon):
        self.requests_made += 1
        return self.answers(lat, lon)


def build_library(root: Path):
    make_jpeg(root / "IMG_001.jpg", date="2026:11:20 10:00:00", gps=(-35.72, 174.32))   # Whangārei
    make_jpeg(root / "IMG_002.jpg", date="2026:11:20 11:00:00", gps=(-35.72, 174.32))
    make_jpeg(root / "IMG_003.jpg", date="2026:11:20 12:00:00", gps=(-35.28, 174.09))   # Paihia
    make_jpeg(root / "IMG_004.jpg", date="2026:11:21 09:00:00", gps=(-35.23, 173.95))   # lookup fails
    make_jpeg(root / "IMG_005.jpg")                                                     # nothing
    make_jpeg(root / "holiday" / "IMG_001.jpg", date="2026:11:20 10:00:00")             # duplicate name
    (root / "broken.jpg").write_bytes(b"not a jpeg")
    return root


def answers(lat, lon):
    if round(lat, 2) == -35.72:
        return GeocodeResult("ok", "Whangārei")
    if round(lat, 2) == -35.28:
        return GeocodeResult("ok", "Paihia")
    return GeocodeResult("failed", message="timeout")


def test_end_to_end_with_location(tmp_path):
    photos, out = build_library(tmp_path / "photos"), tmp_path / "out"
    before = snapshot(photos)
    files = scan_folder(photos, exclude_dirs=[out]).files
    geo = StubGeocoder(answers)
    records = analyse_photos(files, geo)
    assert geo.requests_made == 3  # 3 unique places, not 4 geotagged photos

    counts = summarise(records)
    assert counts == {"total": 7, "errors": 1, "missing_date": 1, "missing_gps": 2,
                      "lookup_failed": 1, "no_place_found": 0, "estimated_date": 0}

    run = create_run_dir(out)
    copy_photos(records, run)
    root = run / "Organised Photos"
    assert (root / "2026-11-20" / "Whangārei" / "IMG_001.jpg").is_file()
    assert (root / "2026-11-20" / "Whangārei" / "IMG_002.jpg").is_file()
    assert (root / "2026-11-20" / "Paihia" / "IMG_003.jpg").is_file()
    assert (root / "2026-11-21" / "Unknown Location" / "IMG_004.jpg").is_file()  # failed lookup
    assert (root / "Unknown Date" / "Unknown Location" / "IMG_005.jpg").is_file()
    assert (root / "Unknown Date" / "Unknown Location" / "broken.jpg").is_file()
    unknown_loc_names = sorted(p.name for p in (root / "2026-11-20" / "Unknown Location").iterdir())
    assert unknown_loc_names == ["IMG_001.jpg"]
    assert len(list(root.rglob("*.jpg"))) == 7

    assert snapshot(photos) == before  # originals untouched

    zip_path = make_zip(run)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert len(names) == 7 and all(n.startswith("Organised Photos/") and ".." not in n for n in names)
    assert "Organised Photos/2026-11-20/Whangārei/IMG_001.jpg" in names


def test_rerun_is_safe_and_output_is_not_rescanned(tmp_path):
    photos = tmp_path / "photos"
    make_jpeg(photos / "a.jpg", date="2026:11:20 10:00:00")
    out = photos / "organised_output"  # output INSIDE the source folder
    for _ in range(2):
        files = scan_folder(photos, exclude_dirs=[out]).files
        assert len(files) == 1  # earlier output never shows up
        records = analyse_photos(files)
        run = create_run_dir(out)
        copy_photos(records, run)
    runs = [p for p in out.iterdir()]
    assert len(runs) == 2 and len({p.name for p in runs}) == 2


def test_date_only_mode_never_calls_geocoder_and_has_no_location_folder(tmp_path):
    photos = build_library(tmp_path / "photos")
    records = analyse_photos(scan_folder(photos).files, geocoder=None)
    assert all(r.location_status == "not_requested" for r in records)
    assert {r.dest_folder for r in records} == {"2026-11-20", "2026-11-21", "Unknown Date"}
    assert "Whangārei" not in build_tree_text(records)


def test_duplicate_names_get_suffix_and_never_overwrite(tmp_path):
    src = tmp_path / "photos"
    make_jpeg(src / "a" / "IMG_1.jpg", date="2026:11:20 10:00:00", color="red")
    make_jpeg(src / "b" / "IMG_1.jpg", date="2026:11:20 10:00:00", color="blue")
    records = analyse_photos(scan_folder(src).files)
    assert sorted(r.dest_name for r in records) == ["IMG_1.jpg", "IMG_1_1.jpg"]
    run = create_run_dir(tmp_path / "out")
    copy_photos(records, run)
    # Even if a file already exists at the destination, copying picks a new name
    folder = run / "Organised Photos" / "2026-11-20"
    plan_destinations(records, False)
    copy_photos(records, run)
    assert len(list(folder.glob("*.jpg"))) == 4


def test_copy_failure_is_recorded_and_job_continues(tmp_path):
    src = tmp_path / "photos"
    make_jpeg(src / "a.jpg", date="2026:11:20 10:00:00")
    make_jpeg(src / "b.jpg", date="2026:11:20 10:00:00")
    records = analyse_photos(scan_folder(src).files)
    Path(records[0].source_path).unlink()  # vanishes between analysis and copy
    copy_photos(records, create_run_dir(tmp_path / "out"))
    assert records[0].status.startswith("Copy failed") and records[1].status == "Copied"


def test_report_columns_gps_optional_and_csv_injection(tmp_path):
    src = tmp_path / "photos"
    make_jpeg(src / "=evil.jpg", date="2026:11:20 10:00:00", gps=(-35.72, 174.32))
    (src / "movie.mov").write_bytes(b"x")
    scan = scan_folder(src)
    records = analyse_photos(scan.files, StubGeocoder(answers))
    run = create_run_dir(tmp_path / "out")
    copy_photos(records, run)

    without = write_report(records, run / "r1.csv", include_gps=False, skipped=scan.skipped)
    with_gps = write_report(records, run / "r2.csv", include_gps=True, skipped=scan.skipped)
    rows1 = list(csv.DictReader(open(without, encoding="utf-8-sig")))
    rows2 = list(csv.DictReader(open(with_gps, encoding="utf-8-sig")))
    assert "latitude" not in rows1[0] and "latitude" in rows2[0]
    assert rows2[0]["latitude"] == "-35.720000"
    first = rows1[0]
    assert first["original_filename"] == "'=evil.jpg"
    assert first["capture_date"] == "2026-11-20" and first["metadata_source"] == "EXIF DateTimeOriginal"
    assert first["location"] == "Whangārei" and first["status"] == "Copied"
    assert first["destination"].startswith("Organised Photos/2026-11-20/Whangārei/")
    assert rows1[1]["status"].startswith("Skipped")
    assert csv_safe("normal") == "normal"
