import pytest

from photo_organiser.metadata import parse_exif_datetime, read_photo
from tests.helpers import make_jpeg


def test_reads_datetime_original_and_gps(tmp_path):
    p = make_jpeg(tmp_path / "a.jpg", date="2026:11:20 09:30:00", gps=(-35.7251, 174.3237))
    rec = read_photo(p)
    assert rec.capture_date == "2026-11-20"
    assert rec.date_source == "EXIF DateTimeOriginal"
    assert rec.gps_status == "present"
    assert rec.latitude == pytest.approx(-35.7251, abs=1e-4)  # southern hemisphere is negative
    assert rec.longitude == pytest.approx(174.3237, abs=1e-4)


def test_falls_back_to_digitized_date(tmp_path):
    rec = read_photo(make_jpeg(tmp_path / "a.jpg", digitized="2026:01:02 03:04:05"))
    assert rec.capture_date == "2026-01-02" and rec.date_source == "EXIF DateTimeDigitized"


def test_no_metadata_means_unknown_not_guessed(tmp_path):
    # filename looks like a date, but we must NOT use it
    rec = read_photo(make_jpeg(tmp_path / "2026-11-20_IMG.jpg"))
    assert rec.capture_dt is None and rec.date_source == ""
    assert rec.gps_status == "missing" and not rec.error


def test_invalid_date_is_ignored_with_warning(tmp_path):
    rec = read_photo(make_jpeg(tmp_path / "a.jpg", date="0000:00:00 00:00:00"))
    assert rec.capture_dt is None
    assert any("not a valid date" in w for w in rec.warnings)


def test_local_date_is_preserved_not_shifted_by_timezone(tmp_path):
    # 23:30 local at +13:00 is 10:30 UTC the same day; the folder must use the LOCAL date.
    rec = read_photo(make_jpeg(tmp_path / "a.jpg", date="2026:11:20 23:30:00", offset="+13:00"))
    assert rec.capture_date == "2026-11-20"
    assert rec.utc_offset == "+13:00"


def test_gps_zero_zero_and_bad_reference_are_invalid(tmp_path):
    zero = read_photo(make_jpeg(tmp_path / "z.jpg", gps_raw={1: "N", 2: (0.0, 0.0, 0.0), 3: "E", 4: (0.0, 0.0, 0.0)}))
    assert zero.gps_status == "invalid"
    no_ref = read_photo(make_jpeg(tmp_path / "r.jpg", gps_raw={2: (36.0, 0.0, 0.0), 4: (174.0, 0.0, 0.0)}))
    assert no_ref.gps_status == "invalid"  # would risk the wrong hemisphere


def test_corrupt_and_non_jpeg_files_do_not_raise(tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"this is not an image")
    assert "Could not read" in read_photo(bad).error
    empty = tmp_path / "empty.jpg"
    empty.write_bytes(b"")
    assert read_photo(empty).error
    assert read_photo(tmp_path / "missing.jpg").error


@pytest.mark.parametrize("text,ok", [
    ("2026:11:20 09:30:00", True), ("2026-11-20 09:30:00", True), ("2026:11:20 09:30:00\x00", True),
    ("", False), ("garbage", False), ("2026:13:40 00:00:00", False),
])
def test_parse_exif_datetime(text, ok):
    assert (parse_exif_datetime(text) is not None) is ok
