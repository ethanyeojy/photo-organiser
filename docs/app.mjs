// Photo Organiser - browser interface. Everything happens on this device; photos are never uploaded.
import {
  ATTRIBUTION, FALLBACK_LABELS, FALLBACK_NONE, GPS_PRESENT, KNOWN_UNSUPPORTED, LOCATION_STATUS_LABELS,
  LOC_FAILED, LOC_NOT_REQUESTED, NominatimGeocoder, OUTPUT_ROOT_NAME, SUPPORTED_EXTENSIONS,
  applyDateFallback, applyExif, buildReport, buildTreeText, buildZip, entryName, extOf, groupCounts,
  newRecord, planDestinations, planParts, resolveLocations, summarise,
} from "./organiser.mjs";

const $ = (id) => document.getElementById(id);
const MB = 1024 * 1024;

const state = { files: [], skipped: [], records: null, signature: "", busy: false, downloads: [] };

// ------------------------------------------------------------------ small helpers
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v; else el.setAttribute(k, v);
  }
  for (const c of children.flat()) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}

function fmtSecs(s) {
  s = Math.round(s);
  return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
}

function fmtSize(bytes) {
  return bytes >= 1024 * MB ? `${(bytes / 1024 / MB).toFixed(1)} GB` : `${Math.round(bytes / MB)} MB`;
}

function safeStorage() {
  try { const s = window.localStorage; s.getItem("x"); return s; } catch { return null; }
}

function progressUI() {
  const t0 = performance.now();
  const box = $("progress"), bar = $("bar"), label = $("barLabel"), timing = $("barTiming");
  box.hidden = false;
  let stageStart = t0, stageKey = "";
  return {
    update(done, total, message) {
      const key = message.split(" ")[0];
      if (key !== stageKey) { stageKey = key; stageStart = performance.now(); }
      const frac = Math.min(done / Math.max(total, 1), 1);
      bar.value = frac;
      label.textContent = `${message}: ${done} of ${total} (${Math.round(frac * 100)}%)`;
      const now = performance.now();
      const eta = frac > 0.05 && done < total ? ` · about ${fmtSecs(((now - stageStart) / 1000) * (1 - frac) / frac)} left` : "";
      timing.textContent = `Elapsed ${fmtSecs((now - t0) / 1000)}${eta}`;
    },
    stage(text) { bar.value = 0; label.textContent = text; },
    finish(text) { bar.value = 1; label.textContent = text; timing.textContent = `Took ${fmtSecs((performance.now() - t0) / 1000)}`; },
    fail(text) { label.textContent = text; label.classList.add("error"); },
    reset() { label.classList.remove("error"); },
  };
}

/** Run `worker(item, index)` over items with at most `limit` running at once. Yields to the UI regularly. */
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

// ------------------------------------------------------------------ choosing photos
function currentOptions() {
  return {
    consent: $("consent").checked,
    language: $("language").value.trim(),
    fallback: $("fallback").value,
    includeGps: $("includeGps").checked,
    partBytes: Number($("partSize").value) * MB,
  };
}

function signature() {
  const o = currentOptions();
  return JSON.stringify([state.files.map((f) => [f.name, f.size, f.lastModified]), o.consent, o.language, o.fallback]);
}

function onFilesPicked(fileList) {
  const picked = Array.from(fileList);
  const files = [], skipped = [];
  let ignored = 0;
  for (const f of picked) {
    const ext = extOf(f.name);
    if (SUPPORTED_EXTENSIONS.has(ext) || (!ext && /^image\/(jpeg|png|webp|tiff|heic|heif)$/.test(f.type))) files.push(f);
    else if (KNOWN_UNSUPPORTED.has(ext)) skipped.push(f.name);
    else ignored += 1;
  }
  state.files = files;
  state.skipped = skipped;
  state.records = null;
  $("results").hidden = true;
  $("organiseCard").hidden = true;
  $("pickedInfo").textContent = files.length
    ? `${files.length} photo${files.length === 1 ? "" : "s"} selected (${fmtSize(files.reduce((n, f) => n + f.size, 0))}).`
    : picked.length ? "No supported photos in that selection." : "";
  const extra = [];
  if (skipped.length) extra.push(`${skipped.length} unsupported file(s) will be skipped`);
  if (ignored) extra.push(`${ignored} other file(s) ignored`);
  $("pickedWarn").textContent = extra.join("; ");
  $("analyseBtn").disabled = files.length === 0;
}

// ------------------------------------------------------------------ analyse
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

async function analyse() {
  if (state.busy || !state.files.length) return;
  if (!window.exifr) { alert("The photo reader did not load. Check your connection and reload the page."); return; }
  state.busy = true;
  setBusy(true);
  const ui = progressUI();
  ui.reset();
  $("results").hidden = true;
  $("organiseCard").hidden = true;
  try {
    const o = currentOptions();
    const records = state.files.map((f) => newRecord(f));
    ui.stage("Reading photos...");
    await pool(records, 6, async (rec) => {
      await readMetadata(rec);
      if (!rec.error) applyDateFallback(rec, rec.modified, o.fallback);
    }, (d, t) => ui.update(d, t, "Reading photo dates and GPS"));

    let geocoder = null;
    if (o.consent) {
      geocoder = new NominatimGeocoder({ storage: safeStorage(), language: o.language });
      await resolveLocations(records, geocoder, (d, t, m) => ui.update(d, t, m));
    } else {
      for (const r of records) r.locationStatus = LOC_NOT_REQUESTED;
    }
    planDestinations(records, o.consent);
    state.records = records;
    state.signature = signature();
    state.includeLocation = o.consent;
    state.geocoderNote = geocoder?.disabledReason || "";
    ui.finish(`Analysis complete: ${records.length} photo(s)`);
    renderResults();
  } catch (err) {
    ui.fail(`Analysis stopped: ${err?.name || "Error"}: ${err?.message || err}`);
  } finally {
    state.busy = false;
    setBusy(false);
  }
}

function setBusy(busy) {
  for (const id of ["analyseBtn", "organiseBtn", "photos"]) if ($(id)) $(id).disabled = busy || (id === "analyseBtn" && !state.files.length);
}

function renderResults() {
  const records = state.records;
  const counts = summarise(records);
  const metric = (label, value) => h("div", { class: "metric" }, h("div", { class: "metric-value" }, value), h("div", { class: "metric-label" }, label));
  $("metrics").replaceChildren(
    metric("Photos found", counts.total), metric("Missing date", counts.missingDate),
    metric("Missing GPS", counts.missingGps), metric("Processing errors", counts.errors),
    metric("Estimated dates", counts.estimatedDate),
    metric("Lookups failed", state.includeLocation ? counts.lookupFailed : "–"),
    metric("Unsupported (skipped)", state.skipped.length),
  );

  const notes = [];
  if (counts.errors) notes.push(`${counts.errors} file(s) could not be read; they will go to 'Unknown Date'.`);
  if (counts.estimatedDate) notes.push(`${counts.estimatedDate} photo(s) have no embedded date, so their folder date is an ESTIMATE from the filename or file date. They are marked in the CSV (date_is_estimate = yes).`);
  if (state.geocoderNote) notes.push(state.geocoderNote);
  if (counts.lookupFailed) notes.push(`${counts.lookupFailed} photo(s) have GPS but the place lookup failed; they go in 'Unknown Location'.`);
  if (!state.includeLocation && counts.missingGps === counts.total && counts.total > 0) {
    notes.push("None of these photos has GPS data. On an iPhone, choose photos with Options → Location turned on, or location will always be missing.");
  }
  $("notes").replaceChildren(...notes.map((n) => h("p", { class: "note" }, n)));

  $("folderTable").replaceChildren(
    h("tr", {}, h("th", {}, "Folder"), h("th", {}, "Photos")),
    ...groupCounts(records).map(([folder, n]) => h("tr", {}, h("td", {}, folder.replace(/\//g, "  /  ")), h("td", {}, n))),
  );
  $("tree").textContent = buildTreeText(records);
  $("attribution").textContent = state.includeLocation ? ATTRIBUTION : "Date-only mode: photos go straight into date folders.";

  const problems = records.filter((r) => r.error || r.warnings.length || r.locationStatus === LOC_FAILED);
  $("problemsTitle").textContent = `Needs attention (${problems.length})`;
  $("problems").replaceChildren(...(problems.length
    ? problems.slice(0, 200).map((r) => h("li", {}, `${r.originalFilename}: ${[r.error, ...r.warnings, r.locationNote].filter(Boolean).join("; ")}`))
    : [h("li", {}, "Nothing needs attention.")]));
  if (state.skipped.length) {
    $("skipped").hidden = false;
    $("skippedList").textContent = state.skipped.slice(0, 200).join("\n");
    $("skippedSummary").textContent = `${state.skipped.length} unsupported file(s) skipped (not included)`;
  } else {
    $("skipped").hidden = true;
  }
  $("results").hidden = false;
  $("organiseCard").hidden = false;
  $("downloads").replaceChildren();
  $("done").textContent = "";
  $("results").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ------------------------------------------------------------------ organise + download
function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 120000);
}

async function organise() {
  if (state.busy || !state.records) return;
  if (state.signature !== signature()) {
    alert("Your photos or options changed since the last analysis. Click Analyse photos again first.");
    return;
  }
  state.busy = true;
  setBusy(true);
  const ui = progressUI();
  ui.reset();
  try {
    const o = currentOptions();
    const records = state.records;
    planDestinations(records, state.includeLocation);
    const parts = planParts(records, o.partBytes);
    const blobs = [];
    for (let p = 0; p < parts.length; p++) {
      const group = parts[p];
      ui.stage(`Packing ZIP ${p + 1} of ${parts.length}...`);
      const blob = await buildZip(
        group.map((rec) => ({ name: entryName(rec), file: rec.file })),
        (d, t) => ui.update(d, t, `Packing ZIP ${p + 1} of ${parts.length}`),
      );
      for (const rec of group) rec.status = `Added to ZIP ${p + 1}`;
      blobs.push({ blob, count: group.length });
    }
    const report = new Blob([buildReport(records, { includeGps: o.includeGps, skipped: state.skipped })], { type: "text/csv" });
    const stem = OUTPUT_ROOT_NAME.replace(/ /g, "-");
    state.downloads = blobs.map((b, i) => ({
      blob: b.blob, count: b.count,
      name: parts.length === 1 ? `${stem}.zip` : `${stem}-part-${i + 1}-of-${parts.length}.zip`,
    }));
    state.report = report;
    ui.finish("Organising complete");
    renderDownloads();
  } catch (err) {
    ui.fail(`Could not finish: ${err?.name || "Error"}: ${err?.message || err}`);
  } finally {
    state.busy = false;
    setBusy(false);
  }
}

function renderDownloads() {
  const n = state.downloads.length;
  $("done").textContent = `Done: ${state.records.length} photo(s) organised into ${n} ZIP file${n === 1 ? "" : "s"}. ` +
    "Download every ZIP below" + (n > 1 ? " and unzip them all into the same folder" : "") + ". Nothing was uploaded anywhere.";
  const items = state.downloads.map((d) => h("li", {}, h("button", { type: "button", class: "btn", "data-name": d.name },
    `⬇️ Download ${d.name} (${d.count} photos, ${fmtSize(d.blob.size)})`)));
  const csv = h("li", {}, h("button", { type: "button", class: "btn secondary", "data-csv": "1" }, "⬇️ Download processing_report.csv"));
  $("downloads").replaceChildren(...items, csv);
  if (n > 1) {
    $("downloads").append(h("li", {}, h("button", { type: "button", class: "btn secondary", "data-all": "1" }, "⬇️ Download all ZIPs one after another")));
  }
}

$("downloads").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button");
  if (!btn) return;
  if (btn.dataset.csv) return triggerDownload(state.report, "processing_report.csv");
  if (btn.dataset.all) {
    for (const d of state.downloads) { triggerDownload(d.blob, d.name); await new Promise((r) => setTimeout(r, 1500)); }
    return;
  }
  const d = state.downloads.find((x) => x.name === btn.dataset.name);
  if (d) triggerDownload(d.blob, d.name);
});

// ------------------------------------------------------------------ wire up
$("fallback").replaceChildren(...Object.entries(FALLBACK_LABELS).map(([k, v]) => h("option", { value: k }, v)));
$("fallback").value = FALLBACK_NONE;
$("photos").addEventListener("change", (e) => onFilesPicked(e.target.files));
$("analyseBtn").addEventListener("click", analyse);
$("organiseBtn").addEventListener("click", organise);
for (const id of ["consent", "language", "fallback"]) {
  $(id).addEventListener("change", () => {
    if (state.records && state.signature !== signature()) $("stale").hidden = false;
  });
}
$("analyseBtn").addEventListener("click", () => { $("stale").hidden = true; });
$("consent").addEventListener("change", () => { $("consentDetails").hidden = !$("consent").checked; });
$("analyseBtn").disabled = true;
// Browsers can keep a previous selection after a reload.
if ($("photos").files?.length) onFilesPicked($("photos").files);

// Used by the automated browser test.
window.__photoOrganiser = { state, LOCATION_STATUS_LABELS, GPS_PRESENT, LOC_FAILED };
