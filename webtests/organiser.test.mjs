// Run with:  node --test webtests/
import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync, readFileSync, readdirSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import * as o from "../docs/organiser.mjs";

const file = (name, text = "x", lastModified = Date.UTC(2026, 10, 20, 12)) => new File([text], name, { lastModified });

test("safeComponent / safeFilename", () => {
  assert.equal(o.safeComponent("a/b:c"), "a_b_c");
  assert.equal(o.safeComponent(".."), "_");
  assert.equal(o.safeComponent("CON"), "_CON");
  assert.equal(o.safeComponent("Whangārei "), "Whangārei");
  assert.equal(o.safeFilename("IMG 001 (2).jpg"), "IMG 001 (2).jpg");
  assert.equal(o.safeFilename("../../evil.jpg"), "evil.jpg");
  const long = o.safeFilename("a".repeat(400) + ".jpg");
  assert.ok(new TextEncoder().encode(long).length <= 200 && long.endsWith(".jpg"));
});

test("uniqueName is case-insensitive and never overwrites", () => {
  const taken = new Set();
  assert.equal(o.uniqueName("a.jpg", taken), "a.jpg");
  assert.equal(o.uniqueName("A.JPG", taken), "A_1.JPG");
  assert.equal(o.uniqueName("a.jpg", taken), "a_2.jpg");
});

test("parseExifDate", () => {
  assert.deepEqual(o.parseExifDate("2026:11:20 10:11:12"), { date: "2026-11-20", time: "10:11:12" });
  assert.deepEqual(o.parseExifDate("2026:11:20 10:11:12\0"), { date: "2026-11-20", time: "10:11:12" });
  assert.equal(o.parseExifDate("0000:00:00 00:00:00"), null);
  assert.equal(o.parseExifDate("2026:13:40 10:00:00"), null);
  assert.equal(o.parseExifDate("garbage"), null);
  assert.equal(o.parseExifDate(undefined), null);
});

test("checkGps", () => {
  assert.equal(o.checkGps(undefined)[0], o.GPS_MISSING);
  assert.equal(o.checkGps({ latitude: -35.28, longitude: 174.09 })[0], o.GPS_PRESENT);
  assert.equal(o.checkGps({ latitude: 0, longitude: 0 })[0], o.GPS_INVALID);
  assert.equal(o.checkGps({ latitude: 95, longitude: 0 })[0], o.GPS_INVALID);
  assert.equal(o.checkGps({ latitude: NaN, longitude: 1 })[0], o.GPS_INVALID);
});

test("dateFromFilename", () => {
  const now = new Date(2026, 10, 22);
  assert.equal(o.dateFromFilename("Image_20261115193017_1.jpg", now), "2026-11-15");
  assert.equal(o.dateFromFilename("IMG_20261115_193017.jpg", now), "2026-11-15");
  assert.equal(o.dateFromFilename("PXL_20261115_193017123.jpg", now), "2026-11-15");
  assert.equal(o.dateFromFilename("2026-11-15 photo.jpg", now), "2026-11-15");
  assert.equal(o.dateFromFilename("IMG_20261315.jpg", now), null);      // month 13
  assert.equal(o.dateFromFilename("IMG_20991115.jpg", now), null);      // far future
  assert.equal(o.dateFromFilename("IMG_0001.jpg", now), null);
});

test("applyDateFallback: real dates win, estimates are labelled", () => {
  const now = new Date(2026, 10, 22);
  const real = o.newRecord(file("IMG_20250101.jpg"));
  real.captureDate = "2026-11-20";
  o.applyDateFallback(real, Date.UTC(2020, 0, 1), o.FALLBACK_FILENAME_MTIME, now);
  assert.equal(real.captureDate, "2026-11-20");
  assert.equal(real.dateEstimated, false);

  const none = o.newRecord(file("IMG_20250101.jpg"));
  o.applyDateFallback(none, null, o.FALLBACK_NONE, now);
  assert.equal(none.captureDate, null);

  const named = o.newRecord(file("IMG_20250101.jpg"));
  o.applyDateFallback(named, null, o.FALLBACK_FILENAME, now);
  assert.equal(named.captureDate, "2025-01-01");
  assert.equal(named.dateEstimated, true);
  assert.match(named.dateSource, /estimated/);

  const mtime = o.newRecord(file("chat.jpg"));
  o.applyDateFallback(mtime, new Date(2025, 5, 7, 12).getTime(), o.FALLBACK_FILENAME_MTIME, now);
  assert.equal(mtime.captureDate, "2025-06-07");
  const mtimeOff = o.newRecord(file("chat.jpg"));
  o.applyDateFallback(mtimeOff, new Date(2025, 5, 7, 12).getTime(), o.FALLBACK_FILENAME, now);
  assert.equal(mtimeOff.captureDate, null);
});

test("planDestinations: date-only, with location, duplicates, Unknown last", () => {
  const a = o.newRecord(file("a.jpg")); a.captureDate = "2026-11-20";
  const b = o.newRecord(file("A.JPG")); b.captureDate = "2026-11-20";
  const c = o.newRecord(file("c.jpg"));
  const d = o.newRecord(file("d.jpg")); d.captureDate = "2026-11-21"; d.location = "Paihia";
  o.planDestinations([a, b, c, d], false);
  assert.deepEqual([a.destFolder, b.destFolder, c.destFolder], ["2026-11-20", "2026-11-20", "Unknown Date"]);
  assert.deepEqual([a.destName, b.destName], ["a.jpg", "A_1.JPG"]);
  o.planDestinations([a, b, c, d], true);
  assert.equal(d.destFolder, "2026-11-21/Paihia");
  assert.equal(a.destFolder, "2026-11-20/Unknown Location");
  assert.equal(c.destFolder, "Unknown Date/Unknown Location");
  assert.equal(o.groupCounts([a, b, c, d]).at(-1)[0], "Unknown Date/Unknown Location");
  assert.match(o.buildTreeText([a, b, c, d]), /Organised Photos\/\n.*2026-11-20\//s);
  const s = o.summarise([a, b, c, d]);
  assert.equal(s.total, 4);
  assert.equal(s.missingDate, 1);
});

test("CSV report: BOM, quoting, formula protection, skipped rows, optional GPS", () => {
  const a = o.newRecord(file('=evil,"x".jpg'));
  a.captureDate = "2026-11-20"; a.captureTime = "10:00:00"; a.latitude = -35.5; a.longitude = 174.25;
  a.gpsStatus = o.GPS_PRESENT; a.status = "Added to ZIP 1";
  o.planDestinations([a], false);
  const csv = o.buildReport([a], { includeGps: true, skipped: ["clip.mov"] });
  assert.ok(csv.startsWith("﻿original_filename,"));
  assert.ok(csv.includes('"\'=evil,""x"".jpg"'));
  assert.ok(csv.includes("-35.500000,174.250000"));
  assert.ok(csv.includes("clip.mov"));
  assert.ok(csv.includes("Organised Photos/2026-11-20/"));
  const noGps = o.buildReport([a], { includeGps: false });
  assert.ok(!noGps.includes("latitude") && !noGps.includes("-35.5"));
});

test("geocoder: rounds coords, caches, rate limits, stops on 429 and repeated failures", async () => {
  const urls = [], waits = [];
  let t = 1000;
  const mk = (responder, extra = {}) => new o.NominatimGeocoder({
    fetchFn: async (url) => { urls.push(url); return responder(url); },
    sleep: async (ms) => { waits.push(ms); t += ms; },
    now: () => t, ...extra,
  });
  const okRes = (address) => ({ ok: true, status: 200, json: async () => ({ address }) });

  const g = mk(() => okRes({ town: "Paihia" }), { language: "en" });
  assert.deepEqual(await g.lookup(-35.28123, 174.09123), { status: "ok", name: "Paihia" });
  assert.match(urls[0], /lat=-35.28&lon=174.09/);
  assert.match(urls[0], /accept-language=en/);
  assert.equal((await g.lookup(-35.2799, 174.0899)).fromCache, true);  // same ~1 km cell
  await g.lookup(10, 10);
  assert.ok(waits.some((w) => w > 0 && w <= 1100), "waited between requests");
  assert.equal(g.requestsMade, 2);

  const limited = mk(() => ({ ok: false, status: 429 }));
  assert.equal((await limited.lookup(1, 1)).status, "failed");
  assert.match(limited.disabledReason, /429/);
  const before = urls.length;
  await limited.lookup(2, 2);
  assert.equal(urls.length, before, "no more requests after 429");

  const flaky = mk(() => { throw new Error("offline"); });
  for (let i = 1; i <= 3; i++) await flaky.lookup(i, i);
  assert.match(flaky.disabledReason, /paused/);

  const none = mk(() => okRes({}));
  assert.equal((await none.lookup(5, 5)).status, "no_result");
});

test("resolveLocations groups by cell and sets statuses", async () => {
  const mkRec = (name, lat, lon) => {
    const r = o.newRecord(file(name));
    if (lat !== undefined) { r.gpsStatus = o.GPS_PRESENT; r.latitude = lat; r.longitude = lon; }
    return r;
  };
  const recs = [mkRec("a.jpg", -35.281, 174.091), mkRec("b.jpg", -35.282, 174.092), mkRec("c.jpg")];
  let calls = 0;
  const g = new o.NominatimGeocoder({
    fetchFn: async () => { calls++; return { ok: true, status: 200, json: async () => ({ address: { town: "Paihia" } }) }; },
    sleep: async () => {},
  });
  await o.resolveLocations(recs, g);
  assert.equal(calls, 1);
  assert.deepEqual(recs.map((r) => r.location), ["Paihia", "Paihia", null]);
  assert.equal(recs[2].locationStatus, o.LOC_NO_GPS);
});

test("planParts splits by size and count", () => {
  const recs = [10, 10, 10, 50, 5].map((size) => ({ size }));
  assert.deepEqual(o.planParts(recs, 25).map((p) => p.length), [2, 1, 1, 1]);
  assert.deepEqual(o.planParts(recs, 1000, 2).map((p) => p.length), [2, 2, 1]);
  assert.deepEqual(o.planParts([], 10), []);
});

test("sniffImageType recognises photos by content, withExtension only fills a missing one", () => {
  const b = (...xs) => new Uint8Array(xs);
  const s = (text) => [...new TextEncoder().encode(text)];
  assert.equal(o.sniffImageType(b(0xff, 0xd8, 0xff, 0xe1)), ".jpg");
  assert.equal(o.sniffImageType(b(0x89, ...s("PNG"), 0x0d, 0x0a, 0x1a, 0x0a)), ".png");
  assert.equal(o.sniffImageType(b(...s("RIFF"), 0, 0, 0, 0, ...s("WEBP"))), ".webp");
  assert.equal(o.sniffImageType(b(0x49, 0x49, 42, 0)), ".tif");
  assert.equal(o.sniffImageType(b(0x4d, 0x4d, 0, 42)), ".tif");
  assert.equal(o.sniffImageType(b(0, 0, 0, 24, ...s("ftypheic"))), ".heic");
  assert.equal(o.sniffImageType(b(0, 0, 0, 24, ...s("ftypmif1"))), ".heic");
  assert.equal(o.sniffImageType(b(0, 0, 0, 24, ...s("ftypqt  "))), null);   // a video
  assert.equal(o.sniffImageType(b(...s("GIF89a"))), null);
  assert.equal(o.sniffImageType(b()), null);
  assert.equal(o.withExtension("1000012345", ".jpg"), "1000012345.jpg");
  assert.equal(o.withExtension("photo.jfif", ".jpg"), "photo.jfif");
});

test("crc32 matches the standard check value", () => {
  assert.equal(o.crc32Update(0, new TextEncoder().encode("123456789")), 0xcbf43926);
});

test("buildZip makes a valid archive the system unzipper can read (incl. unicode, duplicates, empty file)", async () => {
  const dir = mkdtempSync(join(tmpdir(), "po-zip-"));
  const big = new Uint8Array(5 * 1024 * 1024 + 17).map((_, i) => (i * 31) & 255);  // spans several CRC chunks
  const recs = [
    Object.assign(o.newRecord(new File([big], "big.jpg", { lastModified: Date.UTC(2026, 10, 20, 12) })), { destFolder: "2026-11-20/Whangārei", destName: "big.jpg" }),
    Object.assign(o.newRecord(file("a.jpg", "hello")), { destFolder: "Unknown Date", destName: "a.jpg" }),
    Object.assign(o.newRecord(file("empty.jpg", "")), { destFolder: "Unknown Date", destName: "empty.jpg" }),
  ];
  const blob = await o.buildZip(recs.map((r) => ({ name: o.entryName(r), file: r.file })));
  const zipPath = join(dir, "t.zip");
  writeFileSync(zipPath, Buffer.from(await blob.arrayBuffer()));
  const out = join(dir, "out");
  mkdirSync(out);
  if (process.platform === "win32") {  // Windows' bsdtar reads ZIP files (Git's GNU tar does not)
    execFileSync(join(process.env.SystemRoot, "System32", "tar.exe"), ["-xf", zipPath, "-C", out]);
  } else {
    execFileSync("unzip", ["-q", zipPath, "-d", out]);
  }
  const root = join(out, "Organised Photos");
  assert.deepEqual(readdirSync(root).sort(), ["2026-11-20", "Unknown Date"]);
  const extracted = readFileSync(join(root, "2026-11-20", "Whangārei", "big.jpg"));
  assert.equal(extracted.length, big.length);
  assert.ok(Buffer.from(big).equals(extracted), "big file bytes identical");
  assert.equal(readFileSync(join(root, "Unknown Date", "a.jpg"), "utf8"), "hello");
  assert.equal(statSync(join(root, "Unknown Date", "empty.jpg")).size, 0);
});
