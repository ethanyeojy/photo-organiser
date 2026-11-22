"""Photo Organiser - Streamlit interface.  Run with:  streamlit run app.py"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from photo_organiser.datefallback import FALLBACK_LABELS
from photo_organiser.geocoder import ATTRIBUTION, NominatimGeocoder
from photo_organiser.models import LOCATION_STATUS_LABELS, OUTPUT_ROOT_NAME, SourceFile
from photo_organiser.naming import safe_filename
from photo_organiser.output import copy_photos, create_run_dir, make_zip, write_report
from photo_organiser.pipeline import (
    analyse_photos, build_tree_text, group_counts, plan_destinations, summarise,
)
from photo_organiser.scanner import scan_folder, validate_folders
from photo_organiser.ziputil import extract_jpegs

APP_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = Path.home() / "Pictures" / "Photo Organiser Output"  # the user's own folder, not the app's
CACHE_PATH = APP_DIR / ".cache" / "geocode_cache.json"

# Running on a hosted server (e.g. Streamlit Community Cloud)? Then there is no "this computer" folder to read
# or save to: use uploads and downloads only. Set PHOTO_ORGANISER_ONLINE=1 to force this mode.
IS_ONLINE = bool(os.environ.get("PHOTO_ORGANISER_ONLINE")) or APP_DIR.as_posix().startswith("/mount/src")

SRC_FOLDER, SRC_PHOTOS, SRC_ZIP = "Folder on this computer (fastest)", "Upload photos", "Upload a ZIP of photos"
SOURCE_CHOICES = [SRC_PHOTOS, SRC_ZIP] if IS_ONLINE else [SRC_FOLDER, SRC_PHOTOS, SRC_ZIP]

st.set_page_config(page_title="Photo Organiser", page_icon="📷", layout="wide")
st.session_state.setdefault("analysis", None)
st.session_state.setdefault("result", None)
st.session_state.setdefault("upload_dir", None)


def fmt_secs(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}m {seconds % 60}s" if seconds >= 60 else f"{seconds}s"


class Tracker:
    """A live progress panel: checklist of finished stages, progress bar, elapsed time."""

    def __init__(self, title: str):
        self.t0 = time.monotonic()
        self._last = 0.0
        self._msg = ""
        self._stage_t0 = self.t0
        self._lines: list = []
        self.status = st.status(title, expanded=True)
        with self.status:
            self.log = st.empty()
            self.bar = st.progress(0.0, text="Starting...")
            self.timing = st.empty()

    def done(self, text: str) -> None:
        self._lines.append(text)
        self.log.markdown("\n".join(f"- ✅ {line}" for line in self._lines))

    def stage(self, text: str) -> None:
        self.bar.progress(0.0, text=text)

    def update(self, done: int, total: int, message: str) -> None:
        now = time.monotonic()
        stage_key = message.split(" ")[0]
        if stage_key != self._msg:
            self._msg, self._stage_t0 = stage_key, now
        if done < total and now - self._last < 0.1:  # don't redraw faster than 10x/second
            return
        self._last = now
        frac = min(done / max(total, 1), 1.0)
        self.bar.progress(frac, text=f"{message}: {done} of {total} ({frac:.0%})")
        stage_elapsed = now - self._stage_t0
        eta = f" · about {fmt_secs(stage_elapsed * (1 - frac) / frac)} left" if frac > 0.05 and done < total else ""
        self.timing.caption(f"Elapsed {fmt_secs(now - self.t0)}{eta}")

    def finish(self, label: str) -> None:
        self.bar.progress(1.0, text="Finished")
        self.timing.caption(f"Took {fmt_secs(time.monotonic() - self.t0)}")
        self.status.update(label=label, state="complete", expanded=True)

    def fail(self, label: str) -> None:
        self.status.update(label=label, state="error", expanded=True)


def fresh_temp_dir() -> Path:
    old = st.session_state.get("upload_dir")
    if old:
        shutil.rmtree(old, ignore_errors=True)
    tmp = Path(tempfile.mkdtemp(prefix="photo_organiser_"))
    st.session_state.upload_dir = str(tmp)
    return tmp


# ================================================================ sidebar
with st.sidebar:
    st.header("⚙️ Options")
    if IS_ONLINE:
        st.info("☁️ Your photos are uploaded to this website's server to be sorted, then you download the "
                "result. Nothing is kept permanently - download your ZIP before closing the page.")
    else:
        st.success("🔒 Everything runs on this computer. Photos are never uploaded anywhere.")
    consent = st.checkbox(
        "Look up town/city names (sends GPS coordinates to OpenStreetMap)",
        value=False, key="consent",
        help="Off = date-only organising. Nothing leaves your computer.",
    )
    st.caption(
        "If ticked, each unique photo location (rounded to about 1 km) is sent to the public "
        "OpenStreetMap Nominatim service along with your IP address - never the photos. "
        "Limited to one request per second and cached in `.cache/`. If unticked, photos go "
        "into date folders only."
    )
    contact = st.text_input("Contact email for the lookup service (optional)", key="contact",
                            help="Added to the User-Agent, as Nominatim's usage policy encourages.")
    language = st.text_input("Place-name language (optional, e.g. en)", key="language",
                             help="Blank = local names (e.g. Whangārei).")
    fallback_label = st.selectbox(
        "If a photo has no embedded date", list(FALLBACK_LABELS.values()), key="date_fallback",
        help="Photos saved from chat apps often lose their date. These options give a labelled ESTIMATE only "
             "when no real date exists. The file modified date is available for folders and ZIPs, not for "
             "individually uploaded photos.")
    date_fallback = next(k for k, v in FALLBACK_LABELS.items() if v == fallback_label)
    st.divider()
    include_gps = st.checkbox("Include GPS coordinates in the CSV report", value=False, key="include_gps")
    if IS_ONLINE:
        output_text = str(Path(tempfile.gettempdir()) / "photo_organiser_output")
    else:
        output_text = st.text_input("Output folder", value=str(DEFAULT_OUTPUT), key="output_dir",
                                    help="Each run creates a new sub-folder here. Originals are never changed.")
    st.caption(ATTRIBUTION)

output_base = Path(output_text.strip().strip('"').strip("'")).expanduser()

# ================================================================ header
st.title("📷 Photo Organiser")
st.write("Sorts copies of your photos (JPG, HEIC, PNG, WebP, TIFF) into **Date → Location** folders using the date and GPS "
         "stored inside each photo. Your originals are never changed.")
st.markdown("**1** Choose photos &nbsp;→&nbsp; **2** Analyse and preview &nbsp;→&nbsp; **3** Organise and download")

# ================================================================ 1. choose
with st.container(border=True):
    st.subheader("1 · Choose your photos")
    source_kind = st.radio("Where are the photos?", SOURCE_CHOICES,
                           horizontal=True, key="source_kind")
    uploads, zip_upload, folder_text, recursive = [], None, "", True
    if source_kind == SRC_FOLDER:
        folder_text = st.text_input(
            "Folder path (then press Enter)", key="folder_path",
            placeholder="C:\\Users\\you\\Pictures\\Holiday   or   /Users/you/Pictures/Holiday")
        recursive = st.checkbox("Include sub-folders", value=True, key="recursive")
        st.caption("This path is on the computer **running this app** (your own PC, if you started it yourself). "
                   "Reading in place is the fastest option because nothing is copied until you organise.")
    elif source_kind == SRC_PHOTOS:
        uploads = st.file_uploader("Drop photos here (JPG, HEIC, PNG, WebP, TIFF)", type=["jpg", "jpeg", "heic", "heif", "png", "webp", "tif", "tiff"],
                                   accept_multiple_files=True, key="uploads")
        st.caption("Good for a handful of photos. For large sets, use a folder or a ZIP.")
    else:
        zip_upload = st.file_uploader("Drop a .zip file containing photos", type=["zip"], key="zip_upload")
        st.caption("The ZIP is unpacked into a temporary folder on this computer and only photo files (JPG, HEIC, PNG, WebP, TIFF) are used. "
                   "It must be sent through the browser first, so a folder path is still faster for big libraries.")

folder_path = Path(folder_text.strip().strip('"').strip("'")).expanduser() if folder_text.strip() else None
if source_kind == SRC_FOLDER and folder_path:
    try:
        st.caption("✅ Folder found" if folder_path.is_dir() else "❌ That folder was not found - check the path")
    except OSError:
        st.caption("❌ That path could not be read")

mode = "location" if consent else "date_only"
if source_kind == SRC_FOLDER:
    signature = ("folder", str(folder_path), recursive, mode, language.strip(), date_fallback)
elif source_kind == SRC_PHOTOS:
    signature = ("photos", tuple((f.name, f.size) for f in uploads), mode, language.strip(), date_fallback)
else:
    signature = ("zip", (zip_upload.name, zip_upload.size) if zip_upload else None, mode, language.strip(), date_fallback)


def source_problem() -> str:
    if source_kind == SRC_FOLDER:
        if not folder_path:
            return "Type or paste a folder path first, then press Enter."
        return " ".join(validate_folders(folder_path, output_base))
    if source_kind == SRC_PHOTOS and not uploads:
        return "Choose some photos to upload first."
    if source_kind == SRC_ZIP and not zip_upload:
        return "Choose a .zip file to upload first."
    return ""


def run_analysis() -> None:
    st.session_state.result = None
    st.session_state.analysis = None
    tracker = Tracker("Analysing photos...")
    try:
        files, skipped, warnings = [], [], []
        if source_kind == SRC_FOLDER:
            tracker.stage("Scanning folder for photos...")
            scan = scan_folder(folder_path, exclude_dirs=[output_base], recursive=recursive)
            files, skipped, warnings = scan.files, scan.skipped, scan.warnings
            tracker.done(f"Scanned folder: found {len(files)} photo(s)")
        elif source_kind == SRC_PHOTOS:
            tmp = fresh_temp_dir()
            for i, up in enumerate(uploads, 1):
                name = safe_filename(Path(up.name).name)
                folder = tmp / f"{i:05d}"  # one folder each so equal names never clash
                folder.mkdir()
                (folder / name).write_bytes(up.getbuffer())
                files.append(SourceFile(folder / name, up.name, up.name))
                tracker.update(i, len(uploads), f"Saving {name[:40]}")
            tracker.done(f"Received {len(files)} uploaded photo(s)")
        else:
            result = extract_jpegs(zip_upload, fresh_temp_dir(), tracker.update)
            files, skipped, warnings = result.files, result.skipped, result.warnings
            tracker.done(f"Unpacked ZIP: found {len(files)} photo(s)")

        if not files:
            tracker.fail("No supported photos were found")
            for w in warnings:
                st.warning(w)
            return

        geocoder = NominatimGeocoder(CACHE_PATH, contact=contact, language=language) if consent else None
        records = analyse_photos(files, geocoder, tracker.update, date_fallback)
        tracker.done(f"Read dates and GPS from {len(records)} photo(s)")
        if geocoder:
            tracker.done(f"Looked up places ({geocoder.requests_made} new request(s); the rest came from the cache)")
        elapsed = fmt_secs(time.monotonic() - tracker.t0)
        st.session_state.analysis = {
            "records": records, "signature": signature, "include_location": consent,
            "skipped": skipped, "scan_warnings": warnings,
            "geocoder_note": geocoder.disabled_reason if geocoder else "",
            "message": f"Analysed {len(records)} photo(s) in {elapsed}. Review the preview below, then organise.",
        }
        tracker.finish(f"Analysis complete: {len(records)} photo(s) in {elapsed}")
    except Exception as exc:  # never leave the user with a silent failure
        tracker.fail(f"Analysis stopped: {type(exc).__name__}: {exc}")


# ================================================================ 2. analyse
with st.container(border=True):
    st.subheader("2 · Analyse")
    st.caption("Reads each photo's capture date and GPS. Nothing is copied yet.")
    if st.button("🔍 Analyse photos", type="primary", key="analyse_btn"):
        problem = source_problem()
        if problem:
            st.error(problem)
        else:
            run_analysis()

    analysis = st.session_state.analysis
    if analysis:
        records = analysis["records"]
        stale = analysis["signature"] != signature
        st.success(analysis["message"])
        if stale:
            st.warning("Your source or options changed since the last analysis. Click **Analyse photos** "
                       "again before organising.")
        counts = summarise(records)
        row1, row2 = st.columns(4), st.columns(4)
        row1[0].metric("Photos found", counts["total"])
        row1[1].metric("Missing date", counts["missing_date"])
        row1[2].metric("Missing GPS", counts["missing_gps"])
        row1[3].metric("Processing errors", counts["errors"])
        row2[0].metric("Estimated dates", counts["estimated_date"])
        row2[1].metric("Lookups failed", counts["lookup_failed"] if analysis["include_location"] else "–")
        row2[2].metric("Unsupported (skipped)", len(analysis["skipped"]))
        st.caption("Unreadable files are counted only under *Processing errors*. They are still copied, "
                   "into 'Unknown Date'.")

        for w in analysis["scan_warnings"]:
            st.warning(w)
        if counts["errors"]:
            st.warning(f"{counts['errors']} file(s) could not be read; they will be copied to 'Unknown Date'.")
        if counts["estimated_date"]:
            st.info(f"{counts['estimated_date']} photo(s) have no embedded date, so their folder date is an "
                    "ESTIMATE from the filename or file date. They are marked '(estimated)' in the details and "
                    "in the CSV (date_is_estimate = yes).")
        if analysis["geocoder_note"]:
            st.warning(analysis["geocoder_note"])
        if counts["lookup_failed"]:
            st.warning(f"{counts['lookup_failed']} photo(s) have GPS but the place lookup failed. They go in "
                       "'Unknown Location'; the CSV marks them 'Lookup failed' (different from 'No usable GPS').")

        problems = [r for r in records if r.error or r.warnings or r.location_status == "lookup_failed"]
        tab_preview, tab_all, tab_problems = st.tabs(
            ["📁 Folder preview", "🖼️ All photos", f"⚠️ Needs attention ({len(problems)})"])

        def note(r) -> str:
            return "; ".join(([r.error] if r.error else []) + r.warnings
                             + ([r.location_note] if r.location_note else []))

        with tab_preview:
            if analysis["include_location"]:
                st.caption("Date and location folders. " + ATTRIBUTION)
            else:
                st.caption("Date-only mode: photos go straight into date folders.")
            left, right = st.columns([1, 2])
            left.dataframe(pd.DataFrame([(f.replace("/", "  /  "), n) for f, n in group_counts(records)],
                                        columns=["Folder", "Photos"]), hide_index=True)
            right.code(build_tree_text(records), language=None)
        with tab_all:
            st.dataframe(pd.DataFrame([{
                "File": r.original_filename,
                "Date": r.capture_date or "Unknown Date",
                "Date source": r.date_source or "none",
                "Location": r.location or "",
                "Location status": LOCATION_STATUS_LABELS.get(r.location_status, r.location_status),
                "Will be copied to": f"{r.dest_folder}/{r.dest_name}",
                "Notes": note(r),
            } for r in records]), hide_index=True)
        with tab_problems:
            if problems:
                st.dataframe(pd.DataFrame([{"File": r.relative_path, "Problem": note(r)} for r in problems]),
                             hide_index=True)
            else:
                st.write("Nothing needs attention.")
        if analysis["skipped"]:
            with st.expander(f"{len(analysis['skipped'])} unsupported file(s) skipped (not copied)"):
                st.write("Supported: JPG, HEIC, PNG, WebP, TIFF. Videos, RAW and GIF/BMP files are skipped.")
                st.code("\n".join(analysis["skipped"][:200]))

# ================================================================ 3. organise
analysis = st.session_state.analysis
if analysis:
    records = analysis["records"]
    stale = analysis["signature"] != signature
    with st.container(border=True):
        st.subheader("3 · Organise")
        if IS_ONLINE:
            st.caption("Your organised photos are packed into a ZIP for you to download.")
        else:
            st.caption(f"Copies are saved to a new folder inside `{output_base}` (change it in the sidebar).")
        if st.button("📂 Organise photos", type="primary", disabled=stale, key="organise_btn"):
            tracker = Tracker("Organising photos...")
            try:
                plan_destinations(records, analysis["include_location"])  # reset names, then copy
                run_dir = create_run_dir(output_base)
                copy_photos(records, run_dir, tracker.update)
                tracker.done("Copied photos into date/location folders")
                zip_path = make_zip(run_dir, tracker.update)
                tracker.done("Created ZIP")
                report_path = write_report(records, run_dir / "processing_report.csv",
                                           include_gps, analysis["skipped"])
                tracker.done("Wrote CSV report")
                st.session_state.result = {"run_dir": str(run_dir), "zip": str(zip_path),
                                           "report": str(report_path)}
                tracker.finish("Organising complete")
            except Exception as exc:
                tracker.fail(f"Could not finish: {type(exc).__name__}: {exc}")

        result = st.session_state.result
        if result:
            copied = sum(r.status.startswith("Copied") for r in records)
            failed = [r for r in records if r.status.startswith("Copy failed")]
            if IS_ONLINE:
                st.success(f"Done: {copied} of {len(records)} photos organised. Download your ZIP below "
                           "before closing this page.")
            else:
                st.success(f"Done: {copied} of {len(records)} photos copied and saved on this computer in "
                           f"`{result['run_dir']}`")
            cols = st.columns(4)
            cols[0].metric("Copied", copied)
            cols[1].metric("Copy failed", len(failed))
            cols[2].metric("In 'Unknown Date'", sum(r.dest_folder.startswith("Unknown Date") for r in records))
            cols[3].metric("In 'Unknown Location'",
                           sum(r.dest_folder.endswith("Unknown Location") for r in records)
                           if analysis["include_location"] else "–")
            for r in failed:
                st.error(f"{r.original_filename}: {r.status}")
            left, right = st.columns(2)
            with left, open(result["zip"], "rb") as fh:
                st.download_button(f"⬇️ Download {OUTPUT_ROOT_NAME}.zip", fh, file_name=f"{OUTPUT_ROOT_NAME}.zip",
                                   mime="application/zip", key="dl_zip")
            with right, open(result["report"], "rb") as fh:
                st.download_button("⬇️ Download processing_report.csv", fh, file_name="processing_report.csv",
                                   mime="text/csv", key="dl_csv")
