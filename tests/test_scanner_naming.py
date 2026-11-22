from photo_organiser.naming import safe_component, safe_filename, unique_name
from photo_organiser.scanner import scan_folder, validate_folders
from tests.helpers import make_jpeg


def test_scan_finds_jpegs_case_insensitively_and_lists_unsupported(tmp_path):
    make_jpeg(tmp_path / "a.JPG")
    make_jpeg(tmp_path / "sub" / "b.jpeg")
    (tmp_path / "c.heic").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("hi")
    res = scan_folder(tmp_path)
    assert sorted(f.relative_path for f in res.files) == ["a.JPG", "sub/b.jpeg"]
    assert res.skipped == ["c.heic"]  # .txt is ignored silently


def test_scan_excludes_output_dir_hidden_and_non_recursive(tmp_path):
    make_jpeg(tmp_path / "keep.jpg")
    make_jpeg(tmp_path / "out" / "run_1" / "Organised Photos" / "2026-01-01" / "old.jpg")
    make_jpeg(tmp_path / ".hidden" / "h.jpg")
    make_jpeg(tmp_path / "._keep.jpg")
    make_jpeg(tmp_path / "sub" / "deep.jpg")
    names = sorted(f.relative_path for f in scan_folder(tmp_path, exclude_dirs=[tmp_path / "out"]).files)
    assert names == ["keep.jpg", "sub/deep.jpg"]
    assert [f.relative_path for f in scan_folder(tmp_path, [tmp_path / "out"], recursive=False).files] == ["keep.jpg"]


def test_validate_folders(tmp_path):
    assert validate_folders(tmp_path / "nope", tmp_path / "out")
    (tmp_path / "out" / "inner").mkdir(parents=True)
    assert any("inside the output" in p for p in validate_folders(tmp_path / "out" / "inner", tmp_path / "out"))
    assert validate_folders(tmp_path, tmp_path / "out") == []


def test_safe_names():
    assert safe_filename("IMG 001 (2).jpg") == "IMG 001 (2).jpg"  # normal names untouched
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("a:b*c?.jpg") == "a_b_c_.jpg"
    assert safe_filename("CON.jpg") == "_CON.jpg"
    assert safe_component("..") == "_" and safe_component("") == "_"
    assert safe_component("a/b\\c") == "a_b_c"
    long = safe_filename("x" * 400 + ".jpg")
    assert long.endswith(".jpg") and len(long.encode()) <= 200


def test_unique_name_is_case_insensitive():
    taken = set()
    assert unique_name("IMG.jpg", taken) == "IMG.jpg"
    assert unique_name("img.JPG", taken) == "img_1.JPG"
    assert unique_name("IMG.jpg", taken) == "IMG_2.jpg"
