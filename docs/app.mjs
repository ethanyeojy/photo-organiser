// Photo Organiser - browser interface. Pick photos and everything else is automatic.
// Everything happens on this device; photos are never uploaded.
import {
  FALLBACK_FILENAME_MTIME, GPS_PRESENT, KNOWN_UNSUPPORTED, LOC_FAILED, LOC_NOT_REQUESTED, NominatimGeocoder,
  OUTPUT_ROOT_NAME, SUPPORTED_EXTENSIONS, UNKNOWN_DATE, applyDateFallback, applyExif, buildReport, buildTreeText,
  buildZip, entryName, extOf, newRecord, planDestinations, planParts, resolveLocations, sniffImageType, summarise,
  withExtension,
} from "./organiser.mjs";

const $ = (id) => document.getElementById(id);
const MB = 1024 * 1024;
const PART_BYTES = 250 * MB;   // each ZIP stays small enough for a phone browser

const state = { busy: false, downloads: [], report: null };

const ICON_DOWNLOAD = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4v11m0 0l-4-4m4 4l4-4"/><path d="M5 19h14"/></svg>';
const ICON_SHEET = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h4"/></svg>';

// ------------------------------------------------------------------ helpers
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  for (const c of children.flat()) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}

const fmtSecs = (s) => { s = Math.round(s); return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`; };
const fmtSize = (b) => (b >= 1024 * MB ? `${(b / 1024 / MB).toFixed(1)} GB` : `${Math.max(1, Math.round(b / MB))} MB`);
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function safeStorage() {
  try { const s = window.localStorage; s.getItem("x"); return s; } catch { return null; }
}

function show(view) {
  for (const id of ["idleView", "workView", "doneView"]) $(id).hidden = id !== view;
}

function progressUI() {
  const t0 = performance.now();
  let stageStart = t0, stageKey = "";
  const set = (frac, text) => {
    $("bar").style.width = `${Math.round(frac * 100)}%`;
    $("barWrap").setAttribute("aria-valuenow", String(Math.round(frac * 100)));
    $("barLabel").textContent = text;
  };
  $("barLabel").classList.remove("error");
  set(0, "Starting...");
  $("barTiming").textContent = "";
  return {
    stage(title, text) { $("workTitle").textContent = title; set(0, text); stageStart = performance.now(); stageKey = ""; },
    update(done, total, message) {
      const key = message.split(" ")[0];
      if (key !== stageKey) { stageKey = key; stageStart = performance.now(); }
      const frac = Math.min(done / Math.max(total, 1), 1);
      set(frac, `${message}: ${done} of ${total}`);
      const now = performance.now();
      const eta = frac > 0.05 && done < total ? ` · about ${fmtSecs(((now - stageStart) / 1000) * (1 - frac) / frac)} left` : "";
      $("barTiming").textContent = `Elapsed ${fmtSecs((now - t0) / 1000)}${eta}`;
    },
    fail(text) { $("workTitle").textContent = "Something went wrong"; $("barLabel").textContent = text; $("barLabel").classList.add("error"); },
  };
}

/** Run worker over items with at most `limit` in flight, yielding to the page now and then. */
async function pool(items, limit, worker, onDone) {
  let next = 0, done = 0;
  async function run() {
    while (next < items.length) {
      const i = next++;
      await worker(items[i], i);
      done += 1;
      onDone?.(done, items.length);
      if (done % 20 === 0) await new Promise((r) => setTimeout(r, 0));
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, run));
}

// ------------------------------------------------------------------ reading photos
async function readMetadata(rec) {
  try {
    const tags = await window.exifr.parse(rec.file, {
      pick: ["DateTimeOriginal", "CreateDate", "OffsetTimeOriginal", "OffsetTimeDigitized"],
      reviveValues: false, translateValues: false,
    });
    let gps;
    try { gps = await window.exifr.gps(rec.file); } catch { gps = undefined; }
    applyExif(rec, tags, gps);
  } catch (err) {
    rec.error = `Could not read metadata (${err?.name || "Error"}: ${err?.message || err})`;
  }
}

/**
 * Decide which picked files are photos. Names and MIME types from phone galleries are unreliable
 * (Android often gives "1000012345" with no extension, sometimes no type), so anything not clearly
 * supported is checked by its first bytes. Returns photos as {file, name} with a usable file name.
 */
async function splitSelection(picked) {
  const photos = [], skipped = [], ignored = [];
  for (const f of picked) {
    const ext = extOf(f.name);
    if (SUPPORTED_EXTENSIONS.has(ext)) { photos.push({ file: f, name: f.name }); continue; }
    if (KNOWN_UNSUPPORTED.has(ext)) { skipped.push(f.name); continue; }
    let detected = null;
    try { detected = sniffImageType(new Uint8Array(await f.slice(0, 16).arrayBuffer())); } catch { /* unreadable */ }
    if (detected) photos.push({ file: f, name: withExtension(f.name || "photo", detected) });
    else ignored.push(`${f.name || "(no name)"} (${f.type || "unknown type"}, ${f.size} bytes)`);
  }
  return { photos, skipped, ignored };
}

function showPickMessage(text, details = []) {
  const box = $("pickMsg");
  box.replaceChildren(h("strong", {}, text), ...details.slice(0, 5).map((d) => h("span", { class: "detail" }, d)));
  box.hidden = false;
}

// ------------------------------------------------------------------ the whole job
async function processPhotos(picked) {
  if (state.busy) return;
  state.busy = true;
  $("pickMsg").hidden = true;
  // React straight away so it is obvious the selection was received.
  show("workView");
  const ui = progressUI();
  ui.stage("Checking your photos", `Received ${plural(picked.length, "file")}...`);
  let photos, skipped, ignored;
  try {
    ({ photos, skipped, ignored } = await splitSelection(picked));
  } catch (err) {
    state.busy = false;
    show("idleView");
    showPickMessage(`Could not open the selected files (${err?.name || "Error"}: ${err?.message || err}). Please try again.`);
    return;
  }
  if (!photos.length) {
    state.busy = false;
    show("idleView");
    showPickMessage(
      (picked.length === 1 ? "The selected file could not be read as a photo" : `None of the ${picked.length} selected files could be read as a photo`) +
        " (JPG, HEIC, PNG, WebP or TIFF).",
      [...skipped.map((s) => `${s} (not supported)`), ...ignored],
    );
    return;
  }
  if (!window.exifr) {
    state.busy = false;
    show("idleView");
    showPickMessage("The photo reader did not load. Check your connection and reload the page.");
    return;
  }

  try {
    // 1. read dates and GPS (only the start of each file is read)
    ui.stage("Reading your photos", "Reading photos...");
    const records = photos.map(({ file, name }) => {
      const rec = newRecord(file);
      rec.originalFilename = rec.relativePath = name;
      return rec;
    });
    await pool(records, 6, async (rec) => {
      await readMetadata(rec);
      // No embedded date? Use the date in the filename, then the date the file was saved.
      applyDateFallback(rec, rec.modified, FALLBACK_FILENAME_MTIME);
    }, (d, t) => ui.update(d, t, "Reading photos"));

    // 2. name the places (only if some photos actually have GPS)
    let geocoder = null;
    if (records.some((r) => r.gpsStatus === GPS_PRESENT)) {
      ui.stage("Finding places", "Looking up place names...");
      geocoder = new NominatimGeocoder({ storage: safeStorage() });
      await resolveLocations(records, geocoder, (d, t, m) => ui.update(d, t, m));
    } else {
      for (const r of records) r.locationStatus = LOC_NOT_REQUESTED;
    }
    // Only add a place level if at least one place was found; otherwise date folders only.
    const includeLocation = records.some((r) => r.location);
    planDestinations(records, includeLocation);

    // 3. pack ZIP files
    const parts = planParts(records, PART_BYTES);
    const downloads = [];
    for (let p = 0; p < parts.length; p++) {
      ui.stage("Packing your folders", `Packing ZIP ${p + 1} of ${parts.length}...`);
      const group = parts[p];
      const blob = await buildZip(
        group.map((rec) => ({ name: entryName(rec), file: rec.file })),
        (d, t) => ui.update(d, t, `Packing ZIP ${p + 1} of ${parts.length}`),
      );
      for (const rec of group) rec.status = `Added to ZIP ${p + 1}`;
      const stem = OUTPUT_ROOT_NAME.replace(/ /g, "-");
      downloads.push({ blob, count: group.length, name: parts.length === 1 ? `${stem}.zip` : `${stem}-part-${p + 1}-of-${parts.length}.zip` });
    }
    state.downloads = downloads;
    state.report = new Blob([buildReport(records, { includeGps: false, skipped: skipped.concat(ignored) })], { type: "text/csv" });
    skipped = skipped.concat(ignored);
    renderDone(records, skipped, includeLocation, geocoder);
  } catch (err) {
    ui.fail(`${err?.name || "Error"}: ${err?.message || err}`);
    $("workView").append(h("div", { class: "row" }, h("button", { class: "btn ghost", type: "button", id: "retryBtn" }, "Start over")));
    $("retryBtn").addEventListener("click", reset);
  } finally {
    state.busy = false;
  }
}

// ------------------------------------------------------------------ results
function renderDone(records, skipped, includeLocation, geocoder) {
  const counts = summarise(records);
  const folders = new Set(records.map((r) => r.destFolder.split("/")[0]));
  const places = new Set(records.filter((r) => r.location).map((r) => r.location));
  const n = state.downloads.length;

  $("doneTitle").textContent = "Your photos are ready";
  $("doneText").textContent =
    `${plural(records.length, "photo")} sorted into ${plural(folders.size, "date folder")}. ` +
    (n > 1 ? `Download all ${n} ZIP files below and unzip them into the same folder.` : "Download your ZIP file below.");

  const chips = [plural(records.length, "photo"), plural(folders.size, "date folder")];
  if (includeLocation) chips.push(plural(places.size, "place"));
  if (counts.estimatedDate) chips.push(`${counts.estimatedDate} dated from filename or file date`);
  const unknown = records.filter((r) => r.destFolder.startsWith(UNKNOWN_DATE)).length;
  if (unknown) chips.push(`${unknown} with no date`);
  $("chips").replaceChildren(...chips.map((c) => h("span", { class: "chip" }, c)));

  const dlButton = (label, meta, attrs, primary, icon) => {
    const b = h("button", { type: "button", class: "dl" + (primary ? " primary" : ""), ...attrs },
      h("span", { class: "t" }, h("b", {}, label), h("span", {}, meta)));
    b.insertAdjacentHTML("beforeend", icon);
    return h("li", {}, b);
  };
  const items = state.downloads.map((d) => dlButton(d.name, `${plural(d.count, "photo")} · ${fmtSize(d.blob.size)}`, { "data-name": d.name }, true, ICON_DOWNLOAD));
  if (n > 1) items.push(dlButton("Download all ZIP files", "One after another", { "data-all": "1" }, false, ICON_DOWNLOAD));
  items.push(dlButton("Sorting report", "A spreadsheet listing where every photo went", { "data-csv": "1" }, false, ICON_SHEET));
  $("downloads").replaceChildren(...items);

  const notes = [];
  if (geocoder?.disabledReason) notes.push(geocoder.disabledReason);
  if (counts.lookupFailed && !geocoder?.disabledReason) notes.push(`${plural(counts.lookupFailed, "photo")} could not be matched to a place and went into 'Unknown Location'.`);
  if (counts.total && counts.missingGps === counts.total) {
    notes.push("None of these photos has location data, so they were sorted by date only. On iPhone, choose photos with Options → Location switched on to keep it.");
  }
  $("notes").replaceChildren(...notes.map((t) => h("p", { class: "tip" }, t)));

  $("tree").textContent = buildTreeText(records);
  const problems = records.filter((r) => r.error || r.warnings.length || r.locationStatus === LOC_FAILED);
  $("problemsBox").hidden = problems.length === 0;
  $("problemsTitle").textContent = `${plural(problems.length, "photo")} to check`;
  $("problems").replaceChildren(...problems.slice(0, 200).map((r) =>
    h("li", {}, `${r.originalFilename}: ${[r.error, ...r.warnings, r.locationNote].filter(Boolean).join("; ")}`)));
  $("skippedBox").hidden = skipped.length === 0;
  $("skippedSummary").textContent = `${plural(skipped.length, "file")} skipped (videos, RAW, GIF and BMP are not supported)`;
  $("skippedList").textContent = skipped.slice(0, 200).join("\n");
  show("doneView");
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 120000);
}

$("downloads").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button");
  if (!btn) return;
  if (btn.dataset.csv) return triggerDownload(state.report, "sorting-report.csv");
  if (btn.dataset.all) {
    for (const d of state.downloads) { triggerDownload(d.blob, d.name); await new Promise((r) => setTimeout(r, 1500)); }
    return;
  }
  const d = state.downloads.find((x) => x.name === btn.dataset.name);
  if (d) triggerDownload(d.blob, d.name);
});

function reset() {
  state.downloads = []; state.report = null;
  $("photos").value = "";
  show("idleView");
}

// ------------------------------------------------------------------ wire up
// Some Android browsers fire "input", others "change" (most fire both). Handle whichever comes first;
// clearing the value afterwards means the second event finds no files and is ignored.
function onPicked(e) {
  const picked = Array.from(e.target.files || []);
  if (!picked.length) return;
  e.target.value = "";
  processPhotos(picked);
}
$("photos").addEventListener("input", onPicked);
$("photos").addEventListener("change", onPicked);
$("againBtn").addEventListener("click", reset);

const dz = $("dropzone");
for (const type of ["dragenter", "dragover"]) dz.addEventListener(type, (e) => { e.preventDefault(); dz.classList.add("over"); });
for (const type of ["dragleave", "drop"]) dz.addEventListener(type, () => dz.classList.remove("over"));
dz.addEventListener("drop", (e) => {
  e.preventDefault();
  const picked = Array.from(e.dataTransfer?.files || []);
  if (picked.length) processPhotos(picked);
});
// A file dropped outside the drop zone should not make the browser navigate away from the page.
for (const type of ["dragover", "drop"]) window.addEventListener(type, (e) => { if (!dz.contains(e.target)) e.preventDefault(); });
