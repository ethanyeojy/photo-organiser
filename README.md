# Photo Organiser

Copies photos (JPG, HEIC, PNG, WebP, TIFF) into `Organised Photos / YYYY-MM-DD / Location / filename` using the date and GPS
stored inside each photo. Originals are never modified, moved or deleted.

## Website version (no install, works on phones)

The [`docs/`](docs/) folder is a static website (published with GitHub Pages) that does the same job **entirely in the
browser**: photos are read on your device, never uploaded, and the result is downloaded as ZIP files. Large selections
are split into several ZIPs (200 MB / 400 MB / 1 GB) so phones do not run out of memory. It is plain JavaScript
(`docs/organiser.mjs` = logic, `docs/app.mjs` = page); tests: `node --test webtests/`.

## Python version: setup (Python 3.9 or newer)

```bash
cd photo-organiser
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Your browser opens at http://localhost:8501. To run the tests: `pip install -r requirements-dev.txt` then `pytest`.

## Using it

1. Pick a folder path (fastest; a path on the computer running the app), upload photos, or upload a ZIP of photos.
2. Click **Analyse photos** and review the counts and folder preview.
3. Click **Organise photos**, then download the ZIP and the CSV report.

Location lookup is **off** until you tick the consent box in the sidebar. Without it, photos go into date folders only.
Each run writes to a new `run_<timestamp>/` folder inside your own `Pictures/Photo Organiser Output` folder (you can change this in the sidebar), so repeated runs never overwrite anything.

Photos are never uploaded anywhere. `.streamlit/config.toml` also switches off Streamlit's anonymous usage stats and allows ZIP uploads up to 4 GB.

## Rules the app follows

- Date: EXIF `DateTimeOriginal`, then `DateTimeDigitized`. Nothing else (no filenames, no file modified time, no image content).
  The recorded local date is used as-is; a recorded UTC offset is reported in the CSV but never used to shift the date.
- Optional estimates (off by default, sidebar): if a photo has no embedded date you can use the date in its filename (e.g. `Image_20261115193017_1.jpg` -> 2026-11-15), then optionally the file's modified date. Estimates are marked `(estimated)` and `date_is_estimate = yes` in the CSV, and real EXIF dates always win. The modified date is available for folders and ZIPs, not individually uploaded photos. Chat apps such as WeChat strip metadata, so their files can only be estimated.
- Location: only embedded GPS. Missing GPS -> "Unknown Location" (status *No usable GPS*). Lookup problem -> "Unknown Location"
  (status *Lookup failed*). Missing date -> "Unknown Date".
- Geocoding: OpenStreetMap Nominatim, only after consent, coordinates rounded to ~1 km, max 1 request/second,
  identifying User-Agent, results cached in `.cache/geocode_cache.json`, stops after repeated failures or HTTP 403/429.
- Duplicate names in one destination become `name_1.jpg`, `name_2.jpg`... Nothing is overwritten.
- Unreadable/corrupt files are copied to `Unknown Date` and flagged as errors.

## Limitations (version 1)

- **Photo formats:** JPG, HEIC, PNG, WebP and TIFF are read (HEIC via `pillow-heif`). RAW, GIF, BMP and video files are skipped because they have no reliable date/GPS we can read. Files with no embedded date can still use the optional filename/file-date estimates.
- Photos edited by software that removed EXIF will be "Unknown Date" even if the filename has a date. This is deliberate.
- Photos in the same ~1 km grid cell share one lookup, so a photo right on a town boundary may get the neighbouring town.
- Nominatim's public server is for light use. Thousands of *different* places would be slow and may be refused; use a smaller batch or your own server.
- The ZIP is held in memory for the download button; for very large libraries use the saved `run_*` folder in your output folder instead.
- The live Nominatim call has been tested only with a simulated service in the automated tests.

## Future improvements (not implemented)

HEIC support, optional use of file modified time (clearly labelled), a local offline geocoder, skip-duplicates-by-content,
a "re-run lookups for failed photos only" button.
