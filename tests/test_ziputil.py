import io
import zipfile

from photo_organiser.ziputil import extract_jpegs
from tests.helpers import make_jpeg


def jpeg_bytes(tmp_path, name="x.jpg"):
    return make_jpeg(tmp_path / name, date="2026:11:20 10:00:00").read_bytes()


def build_zip(tmp_path, entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    buf.seek(0)
    return buf


def test_extracts_only_jpegs_and_ignores_unsafe_and_hidden(tmp_path):
    data = jpeg_bytes(tmp_path)
    z = build_zip(tmp_path, {
        "trip/a.JPG": data, "trip/sub/a.jpg": data, "__MACOSX/._a.jpg": data,
        "../evil.jpg": data, "/abs.jpg": data, "C:/win.jpg": data,
        "pic.png": b"x", "notes.txt": b"hi", ".hidden/h.jpg": data,
    })
    dest = tmp_path / "unpacked"
    res = extract_jpegs(z, dest)
    assert [f.relative_path for f in res.files] == ["trip/a.JPG", "trip/sub/a.jpg"]
    assert res.skipped == ["pic.png"]
    assert len([w for w in res.warnings if "unsafe" in w]) == 3
    assert not (tmp_path / "evil.jpg").exists()
    # everything written lives under dest
    assert all(dest in f.path.parents for f in res.files)
    # same filename in two folders does not clash
    assert res.files[0].path != res.files[1].path


def test_bad_zip_and_limits(tmp_path):
    res = extract_jpegs(io.BytesIO(b"not a zip"), tmp_path / "d")
    assert res.warnings and not res.files
    data = jpeg_bytes(tmp_path)
    z = build_zip(tmp_path, {f"{i}.jpg": data for i in range(5)})
    limited = extract_jpegs(z, tmp_path / "d2", max_files=2)
    assert len(limited.files) == 2 and any("limit" in w for w in limited.warnings)
