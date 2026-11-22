// Photo Organiser - core logic (runs in the browser, and in Node for the tests).
// A JavaScript port of the Python package in ../photo_organiser. No photo ever leaves the device.

export const UNKNOWN_DATE = "Unknown Date";
export const UNKNOWN_LOCATION = "Unknown Location";
export const OUTPUT_ROOT_NAME = "Organised Photos";

export const GPS_PRESENT = "present";
export const GPS_MISSING = "missing";
export const GPS_INVALID = "invalid";

export const LOC_NOT_REQUESTED = "not_requested";
export const LOC_NO_GPS = "no_gps";
export const LOC_OK = "ok";
export const LOC_NO_RESULT = "no_place_found";
export const LOC_FAILED = "lookup_failed";

export const LOCATION_STATUS_LABELS = {
  [LOC_NOT_REQUESTED]: "Not requested (date-only)",
  [LOC_NO_GPS]: "No usable GPS in photo",
  [LOC_OK]: "Found",
  [LOC_NO_RESULT]: "No place found for coordinates",
  [LOC_FAILED]: "Lookup failed",
};

export const SUPPORTED_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff"]);
export const KNOWN_UNSUPPORTED = new Set([
  ".gif", ".bmp", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".raf",
  ".mov", ".mp4", ".m4v", ".avi",
]);
const SUPPORTED_MIME = new Set(["image/jpeg", "image/png", "image/webp", "image/tiff", "image/heic", "image/heif"]);

const enc = new TextEncoder();

// ------------------------------------------------------------------ names
const INVALID_CHARS = /[<>:"/\\|?*\x00-\x1f]/g;
const RESERVED = new Set(["CON", "PRN", "AUX", "NUL"]);
for (let i = 1; i <= 9; i++) { RESERVED.add("COM" + i); RESERVED.add("LPT" + i); }
const MAX_NAME_BYTES = 200;

export function safeComponent(name, fallback = "_") {
  let s = String(name ?? "").normalize("NFC").replace(INVALID_CHARS, "_").trim().replace(/[. ]+$/, "");
  if (!s || /^\.+$/.test(s)) return fallback;
  if (RESERVED.has(s.split(".")[0].trim().toUpperCase())) s = "_" + s;
  return s;
}

export function splitExt(name) {
  const i = name.lastIndexOf(".");
  if (i <= 0) return [name, ""];
  return [name.slice(0, i), name.slice(i)];
}

export function extOf(name) {
  const [, ext] = splitExt(String(name).replace(/\\/g, "/").split("/").pop());
  return ext.toLowerCase();
}

function truncateBytes(text, maxBytes) {
  const chars = Array.from(text);
  while (chars.length && enc.encode(chars.join("")).length > maxBytes) chars.pop();
  return chars.join("");
}

export function safeFilename(name, fallback = "photo.jpg") {
  const base = String(name ?? "").replace(/\\/g, "/").split("/").pop();
  const cleaned = safeComponent(base, fallback);
  let [stem, ext] = splitExt(cleaned);
  if (enc.encode(stem + ext).length > MAX_NAME_BYTES) {
    stem = truncateBytes(stem, MAX_NAME_BYTES - enc.encode(ext).length);
    return (stem || "photo") + ext;
  }
  return cleaned;
}

/** Return name, or name_1, name_2 ... so it is not in `taken` (case-insensitive). Adds the result to `taken`. */
export function uniqueName(name, taken) {
  const [stem, ext] = splitExt(name);
  let candidate = name, counter = 0;
  while (taken.has(candidate.toLowerCase())) {
    counter += 1;
    candidate = `${stem}_${counter}${ext}`;
  }
  taken.add(candidate.toLowerCase());
  return candidate;
}

// ------------------------------------------------------------------ dates
const pad = (n, w = 2) => String(n).padStart(w, "0");

function validDay(y, m, d) {
  if (m < 1 || m > 12 || d < 1) return false;
  return d <= new Date(Date.UTC(y, m, 0)).getUTCDate();
}

/** Parse an EXIF date string ("2026:11:20 10:11:12"). Returns {date, time} exactly as recorded, or null. */
export function parseExifDate(value) {
  if (value instanceof Date && !isNaN(value)) {
    return { date: `${value.getUTCFullYear()}-${pad(value.getUTCMonth() + 1)}-${pad(value.getUTCDate())}`,
             time: `${pad(value.getUTCHours())}:${pad(value.getUTCMinutes())}:${pad(value.getUTCSeconds())}` };
  }
  const text = (typeof value === "string" ? value : "").replace(/\0/g, "").trim();
  const m = /^(\d{4})[:/-](\d{2})[:/-](\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/.exec(text);
  if (!m) return null;
  const [y, mo, d] = [+m[1], +m[2], +m[3]];
  if (y < 1900 || !validDay(y, mo, d) || +m[4] > 23 || +m[5] > 59) return null;
  return { date: `${m[1]}-${m[2]}-${m[3]}`, time: `${m[4]}:${m[5]}:${m[6] ?? "00"}` };
}

export const FALLBACK_NONE = "none";
export const FALLBACK_FILENAME = "filename";
export const FALLBACK_FILENAME_MTIME = "filename_mtime";
export const FALLBACK_LABELS = {
  [FALLBACK_NONE]: "Leave as 'Unknown Date' (recommended)",
  [FALLBACK_FILENAME]: "Estimate from the date in the filename",
  [FALLBACK_FILENAME_MTIME]: "Filename date, then the file's modified date",
};
const LABEL_FILENAME = "Filename date (estimated)";
const LABEL_MTIME = "File modified date (estimated)";
const MIN_YEAR = 1990;
// YYYYMMDD with optional - _ . separators, optionally followed by a time (no lookbehind: older iPhones)
const FILENAME_DATE = /(?:^|\D)((?:19|20)\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?:\d{4,9})?(?!\d)/g;

function plausible(y, m, d, now = new Date()) {
  if (y < MIN_YEAR) return false;
  const limit = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) + 86400000;
  return Date.UTC(y, m - 1, d) <= limit;
}

export function dateFromFilename(name, now = new Date()) {
  FILENAME_DATE.lastIndex = 0;
  let m;
  while ((m = FILENAME_DATE.exec(name)) !== null) {
    const [y, mo, d] = [+m[1], +m[2], +m[3]];
    if (validDay(y, mo, d) && plausible(y, mo, d, now)) return `${m[1]}-${m[2]}-${m[3]}`;
  }
  return null;
}

function markEstimate(rec, date, label) {
  rec.captureDate = date;
  rec.captureTime = "";
  rec.dateSource = label;
  rec.dateEstimated = true;
  rec.warnings.push(`Date is an estimate (${label.split(" (")[0].toLowerCase()}); no embedded capture date`);
}

/** modifiedMs: the file's last-modified time in milliseconds, if known. */
export function applyDateFallback(rec, modifiedMs, mode, now = new Date()) {
  if (rec.captureDate || mode === FALLBACK_NONE) return;
  const fromName = dateFromFilename(rec.originalFilename, now);
  if (fromName) return markEstimate(rec, fromName, LABEL_FILENAME);
  if (mode === FALLBACK_FILENAME_MTIME && modifiedMs) {
    const w = new Date(modifiedMs);
    if (!isNaN(w) && plausible(w.getFullYear(), w.getMonth() + 1, w.getDate(), now)) {
      markEstimate(rec, `${w.getFullYear()}-${pad(w.getMonth() + 1)}-${pad(w.getDate())}`, LABEL_MTIME);
    }
  }
}

// ------------------------------------------------------------------ records
export function newRecord(file, relativePath = "") {
  return {
    file,
    originalFilename: file.name,
    relativePath: relativePath || file.name,
    size: file.size,
    modified: file.lastModified || null,
    captureDate: null, captureTime: "", dateSource: "", dateEstimated: false, utcOffset: "",
    latitude: null, longitude: null, gpsStatus: GPS_MISSING,
    location: null, locationStatus: LOC_NOT_REQUESTED, locationNote: "",
    error: "", warnings: [],
    destFolder: "", destName: "", status: "Analysed",
  };
}

const OFFSET_RE = /^[+-]\d{2}:\d{2}$/;

/** Fill a record from the tag values exifr returned. `tags` may be undefined (no EXIF at all). */
export function applyExif(rec, tags, gps) {
  const t = tags || {};
  const candidates = [
    ["DateTimeOriginal", "EXIF DateTimeOriginal", "OffsetTimeOriginal"],
    ["CreateDate", "EXIF DateTimeDigitized", "OffsetTimeDigitized"],
  ];
  for (const [key, label, offsetKey] of candidates) {
    const raw = t[key];
    if (raw === undefined || raw === null || raw === "") continue;
    const parsed = parseExifDate(raw);
    if (!parsed) {
      rec.warnings.push(`${label} is present but not a valid date (${JSON.stringify(String(raw))})`);
      continue;
    }
    rec.captureDate = parsed.date;
    rec.captureTime = parsed.time;
    rec.dateSource = label;
    const off = String(t[offsetKey] ?? "").replace(/\0/g, "").trim();
    if (OFFSET_RE.test(off)) rec.utcOffset = off;
    break;
  }
  const [status, lat, lon, note] = checkGps(gps);
  rec.gpsStatus = status; rec.latitude = lat; rec.longitude = lon;
  if (note) rec.warnings.push(note);
}

export function checkGps(gps) {
  if (!gps || gps.latitude === undefined || gps.longitude === undefined) return [GPS_MISSING, null, null, ""];
  const { latitude: lat, longitude: lon } = gps;
  if (typeof lat !== "number" || typeof lon !== "number" || !isFinite(lat) || !isFinite(lon)) {
    return [GPS_INVALID, null, null, "GPS tags present but unreadable"];
  }
  if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return [GPS_INVALID, null, null, "GPS coordinates out of range"];
  if (lat === 0 && lon === 0) return [GPS_INVALID, null, null, "GPS is 0,0 (usually means 'no fix'), ignored"];
  return [GPS_PRESENT, lat, lon, ""];
}

// ------------------------------------------------------------------ planning
export function planDestinations(records, includeLocation) {
  const taken = new Map();
  for (const rec of records) {
    const parts = [rec.captureDate || UNKNOWN_DATE];
    if (includeLocation) parts.push(rec.location ? safeComponent(rec.location, UNKNOWN_LOCATION) : UNKNOWN_LOCATION);
    rec.destFolder = parts.join("/");
    const key = rec.destFolder.toLowerCase();
    if (!taken.has(key)) taken.set(key, new Set());
    rec.destName = uniqueName(safeFilename(rec.originalFilename), taken.get(key));
  }
}

export function summarise(records) {
  const ok = records.filter((r) => !r.error);
  return {
    total: records.length,
    errors: records.length - ok.length,
    missingDate: ok.filter((r) => !r.captureDate).length,
    estimatedDate: records.filter((r) => r.dateEstimated).length,
    missingGps: ok.filter((r) => r.gpsStatus !== GPS_PRESENT).length,
    lookupFailed: records.filter((r) => r.locationStatus === LOC_FAILED).length,
  };
}

const folderOrder = (a, b) => {
  const ua = a.startsWith(UNKNOWN_DATE), ub = b.startsWith(UNKNOWN_DATE);
  return ua !== ub ? (ua ? 1 : -1) : a < b ? -1 : a > b ? 1 : 0;
};

export function groupCounts(records) {
  const counts = new Map();
  for (const r of records) counts.set(r.destFolder, (counts.get(r.destFolder) || 0) + 1);
  return [...counts.entries()].sort((a, b) => folderOrder(a[0], b[0]));
}

export function buildTreeText(records, maxLines = 300) {
  const tree = {};
  for (const rec of records) {
    let node = tree;
    for (const part of rec.destFolder.split("/")) node = node[part + "/"] ??= {};
    node[rec.destName] = null;
  }
  const lines = [OUTPUT_ROOT_NAME + "/"];
  (function walk(node, prefix) {
    const keys = Object.keys(node).sort(folderOrder);
    keys.forEach((key, i) => {
      const last = i === keys.length - 1;
      lines.push(`${prefix}${last ? "└── " : "├── "}${key}`);
      if (node[key] !== null) walk(node[key], prefix + (last ? "    " : "│   "));
    });
  })(tree, "");
  if (lines.length > maxLines) {
    const extra = lines.length - maxLines;
    return [...lines.slice(0, maxLines), `... and ${extra} more lines`].join("\n");
  }
  return lines.join("\n");
}

// ------------------------------------------------------------------ CSV report
export const BASE_COLUMNS = [
  "original_filename", "source_relative_path", "capture_date", "capture_time_local",
  "utc_offset_recorded", "metadata_source", "date_is_estimate", "gps_status", "location", "location_status",
  "destination", "status", "notes",
];

/** Stop spreadsheet programs treating text such as '=cmd' as a formula. */
export function csvSafe(value) {
  const text = value === null || value === undefined ? "" : String(value);
  return ["=", "+", "-", "@", "\t", "\r"].includes(text.charAt(0)) ? "'" + text : text;
}

function csvField(text) {
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function buildReport(records, { includeGps = false, skipped = [] } = {}) {
  const columns = BASE_COLUMNS.concat(includeGps ? ["latitude", "longitude"] : []);
  const lines = [columns.join(",")];
  const emit = (row) => lines.push(columns.map((c) => csvField(row[c] ?? "")).join(","));
  for (const r of records) {
    const copied = r.status.startsWith("Added");
    const notes = [].concat(r.error ? [r.error] : [], r.warnings, r.locationNote ? [r.locationNote] : []).join("; ");
    const row = {
      original_filename: r.originalFilename,
      source_relative_path: r.relativePath,
      capture_date: r.captureDate || UNKNOWN_DATE,
      capture_time_local: r.captureTime && !r.dateEstimated ? r.captureTime : "",
      utc_offset_recorded: r.utcOffset,
      metadata_source: r.dateSource || "none",
      date_is_estimate: r.dateEstimated ? "yes" : "no",
      gps_status: r.gpsStatus,
      location: r.location || "",
      location_status: LOCATION_STATUS_LABELS[r.locationStatus] || r.locationStatus,
      destination: copied ? `${OUTPUT_ROOT_NAME}/${r.destFolder}/${r.destName}` : "",
      status: r.status,
      notes,
    };
    for (const k of Object.keys(row)) row[k] = csvSafe(row[k]);
    if (includeGps) {
      row.latitude = r.latitude === null ? "" : r.latitude.toFixed(6);
      row.longitude = r.longitude === null ? "" : r.longitude.toFixed(6);
    }
    emit(row);
  }
  for (const rel of skipped) {
    emit({
      original_filename: csvSafe(rel.split("/").pop()), source_relative_path: csvSafe(rel),
      status: "Skipped: unsupported format (videos, RAW, GIF and BMP are not handled)",
    });
  }
  return "﻿" + lines.join("\r\n") + "\r\n";
}

// ------------------------------------------------------------------ place names (OpenStreetMap Nominatim)
export const ATTRIBUTION = "Place names © OpenStreetMap contributors (ODbL), via Nominatim.";
const NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse";
const PLACE_KEYS = ["city", "town", "village", "municipality", "hamlet", "suburb",
  "city_district", "county", "state_district", "state"];

/**
 * Follows Nominatim's usage policy as far as a browser allows: only coordinates rounded to ~1 km are
 * sent, at most one request per second, results cached, and it stops after repeated failures or 403/429.
 */
export class NominatimGeocoder {
  constructor({ fetchFn, sleep, now, storage, language = "", precision = 2, minInterval = 1100,
                maxConsecutiveFailures = 3, storageKey = "photoOrganiserGeocache" } = {}) {
    this.fetchFn = fetchFn || ((...a) => fetch(...a));
    this.sleep = sleep || ((ms) => new Promise((r) => setTimeout(r, ms)));
    this.now = now || (() => Date.now());
    this.storage = storage || null;
    this.language = language.trim();
    this.precision = precision;
    this.minInterval = minInterval;
    this.maxFailures = maxConsecutiveFailures;
    this.storageKey = storageKey;
    this.requestsMade = 0;
    this.disabledReason = "";
    this.failures = 0;
    this.lastRequest = null;
    this.cache = this.loadCache();
  }

  cacheKey(lat, lon) {
    const p = this.precision;
    return `${this.language}|${(+lat.toFixed(p)).toFixed(p)},${(+lon.toFixed(p)).toFixed(p)}`;
  }

  loadCache() {
    try {
      const data = JSON.parse(this.storage?.getItem(this.storageKey) || "{}");
      return data && typeof data === "object" ? data : {};
    } catch { return {}; }
  }

  saveCache() {
    try { this.storage?.setItem(this.storageKey, JSON.stringify(this.cache)); } catch { /* optional */ }
  }

  async lookup(lat, lon) {
    const key = this.cacheKey(lat, lon);
    if (this.cache[key]) return { status: this.cache[key].status, name: this.cache[key].name, fromCache: true };
    if (this.disabledReason) return { status: "failed", message: this.disabledReason };

    if (this.lastRequest !== null) {
      const wait = this.minInterval - (this.now() - this.lastRequest);
      if (wait > 0) await this.sleep(wait);
    }
    const p = this.precision;
    const params = new URLSearchParams({
      format: "jsonv2", lat: lat.toFixed(p), lon: lon.toFixed(p), zoom: "10", addressdetails: "1",
    });
    if (this.language) params.set("accept-language", this.language);
    let data;
    try {
      this.requestsMade += 1;
      const res = await this.fetchFn(`${NOMINATIM_URL}?${params}`, { headers: { Accept: "application/json" } });
      this.lastRequest = this.now();
      if (!res.ok) {
        if (res.status === 403 || res.status === 429) {
          this.disabledReason = `Place lookup service refused requests (HTTP ${res.status}); stopped to respect its usage policy.`;
          return { status: "failed", message: this.disabledReason };
        }
        return this.fail(`HTTP ${res.status}`);
      }
      data = await res.json();
    } catch (err) {
      this.lastRequest = this.now();
      return this.fail(`${err?.name || "Error"}: ${err?.message || err}`);
    }
    this.failures = 0;
    let name = null;
    if (data && typeof data === "object" && !data.error) {
      const address = data.address || {};
      const k = PLACE_KEYS.find((x) => address[x]);
      name = (k && address[k]) || data.name || null;
    }
    const status = name ? "ok" : "no_result";
    this.cache[key] = { status, name };
    this.saveCache();
    return { status, name };
  }

  fail(message) {
    this.failures += 1;
    if (this.failures >= this.maxFailures) {
      this.disabledReason = `Place lookup paused after ${this.failures} failures in a row (last: ${message}). ` +
        "Remaining photos keep 'Unknown Location'; try again later.";
    }
    return { status: "failed", message };
  }
}

/** Look up one place per ~1 km cell. Mutates records. */
export async function resolveLocations(records, geocoder, progress) {
  const groups = new Map();
  for (const rec of records) {
    if (rec.error || rec.gpsStatus !== GPS_PRESENT) { rec.locationStatus = LOC_NO_GPS; continue; }
    const key = geocoder.cacheKey(rec.latitude, rec.longitude);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(rec);
  }
  let i = 0;
  for (const members of groups.values()) {
    const result = await geocoder.lookup(members[0].latitude, members[0].longitude);
    for (const rec of members) {
      if (result.status === "ok") { rec.location = result.name; rec.locationStatus = LOC_OK; }
      else if (result.status === "failed") { rec.locationStatus = LOC_FAILED; rec.locationNote = result.message || ""; }
      else rec.locationStatus = LOC_NO_RESULT;
    }
    i += 1;
    if (progress) progress(i, groups.size, "Looking up place names (max 1 per second)");
  }
}

// ------------------------------------------------------------------ ZIP (stored, no compression)
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

export function crc32Update(crc, bytes) {
  let c = crc ^ 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

const CRC_CHUNK = 2 * 1024 * 1024;

/** CRC-32 of a Blob/File, read in small chunks so memory stays low. */
export async function crc32OfBlob(blob) {
  let crc = 0;
  for (let off = 0; off < blob.size; off += CRC_CHUNK) {
    crc = crc32Update(crc, new Uint8Array(await blob.slice(off, off + CRC_CHUNK).arrayBuffer()));
  }
  return crc;
}

export function dosDateTime(ms) {
  let d = new Date(ms);
  if (isNaN(d)) d = new Date();
  const year = Math.min(Math.max(d.getFullYear(), 1980), 2107);
  return {
    date: ((year - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate(),
    time: (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1),
  };
}

const MAX_ZIP_ENTRY = 0xfffffffe;

/** Build one ZIP as a Blob. entries: [{name, file}]. The photo data stays as file references (not copied into RAM). */
export async function buildZip(entries, progress) {
  const parts = [];
  const central = [];
  let offset = 0;
  for (let i = 0; i < entries.length; i++) {
    const { name, file } = entries[i];
    if (file.size > MAX_ZIP_ENTRY) throw new Error(`${name} is too large for a ZIP (over 4 GB)`);
    const nameBytes = enc.encode(name);
    const crc = await crc32OfBlob(file);
    const { date, time } = dosDateTime(file.lastModified);

    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true);
    local.setUint16(4, 20, true);
    local.setUint16(6, 0x0800, true);   // UTF-8 names
    local.setUint16(8, 0, true);        // stored
    local.setUint16(10, time, true);
    local.setUint16(12, date, true);
    local.setUint32(14, crc, true);
    local.setUint32(18, file.size, true);
    local.setUint32(22, file.size, true);
    local.setUint16(26, nameBytes.length, true);
    local.setUint16(28, 0, true);
    parts.push(local.buffer, nameBytes, file);

    const cd = new DataView(new ArrayBuffer(46));
    cd.setUint32(0, 0x02014b50, true);
    cd.setUint16(4, 20, true);
    cd.setUint16(6, 20, true);
    cd.setUint16(8, 0x0800, true);
    cd.setUint16(10, 0, true);
    cd.setUint16(12, time, true);
    cd.setUint16(14, date, true);
    cd.setUint32(16, crc, true);
    cd.setUint32(20, file.size, true);
    cd.setUint32(24, file.size, true);
    cd.setUint16(28, nameBytes.length, true);
    cd.setUint32(42, offset, true);
    central.push(cd.buffer, nameBytes);

    offset += 30 + nameBytes.length + file.size;
    if (progress) progress(i + 1, entries.length);
  }
  const cdSize = central.reduce((n, p) => n + (p.byteLength ?? p.length), 0);
  const eocd = new DataView(new ArrayBuffer(22));
  eocd.setUint32(0, 0x06054b50, true);
  eocd.setUint16(8, entries.length, true);
  eocd.setUint16(10, entries.length, true);
  eocd.setUint32(12, cdSize, true);
  eocd.setUint32(16, offset, true);
  return new Blob([...parts, ...central, eocd.buffer], { type: "application/zip" });
}

/** Split records into groups whose total size is at most maxBytes (a bigger single photo gets its own group)
 *  and at most maxFiles photos (a ZIP can hold 65,535 entries). */
export function planParts(records, maxBytes, maxFiles = 60000) {
  const parts = [];
  let current = [], size = 0;
  for (const rec of records) {
    if (current.length && (size + rec.size > maxBytes || current.length >= maxFiles)) {
      parts.push(current); current = []; size = 0;
    }
    current.push(rec); size += rec.size;
  }
  if (current.length) parts.push(current);
  return parts;
}

/**
 * Work out the image type from a file's first bytes. Android gallery apps often hand over photos with no
 * extension ("1000012345") and sometimes no MIME type, so the name alone cannot be trusted.
 * Returns ".jpg", ".png", ".webp", ".tif", ".heic" or null.
 */
export function sniffImageType(bytes) {
  const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const ascii = (from, to) => String.fromCharCode(...b.subarray(from, to));
  if (b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return ".jpg";
  if (b.length >= 8 && b[0] === 0x89 && ascii(1, 4) === "PNG") return ".png";
  if (b.length >= 12 && ascii(0, 4) === "RIFF" && ascii(8, 12) === "WEBP") return ".webp";
  if (b.length >= 4 && ((b[0] === 0x49 && b[1] === 0x49 && b[2] === 42 && b[3] === 0) ||
                        (b[0] === 0x4d && b[1] === 0x4d && b[2] === 0 && b[3] === 42))) return ".tif";
  if (b.length >= 12 && ascii(4, 8) === "ftyp" &&
      ["heic", "heix", "hevc", "hevx", "heim", "heis", "mif1", "msf1"].includes(ascii(8, 12))) return ".heic";
  return null;
}

/** Give a name with no extension the detected one, so the copied file opens normally. */
export function withExtension(name, ext) {
  return splitExt(String(name))[1] ? String(name) : `${name}${ext}`;
}

export function entryName(rec) {
  return `${OUTPUT_ROOT_NAME}/${rec.destFolder}/${rec.destName}`;
}
