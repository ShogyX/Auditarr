/**
 * v1.9 Stage 3.4 — containerLabel JS unit test.
 *
 * Mirrors the Python counterpart's coverage so both implementations
 * have an enforceable contract — the table is the source of truth.
 */

import { describe, expect, it } from "vitest";

import { containerLabel } from "@/lib/containerLabel";

describe("containerLabel", () => {
  it.each([
    ["matroska", "MKV"],
    ["matroska,webm", "MKV"],
    ["MKV", "MKV"],
    ["webm", "WEBM"],
    ["mov", "MP4"],
    ["mp4", "MP4"],
    ["mov,mp4,m4a,3gp,3g2,mj2", "MP4"],
    ["m4a", "MP4"],
    ["mpegts", "TS"],
    ["ts", "TS"],
    ["avi", "AVI"],
    ["flv", "FLV"],
    ["ogg", "OGG"],
  ])("known value %s → %s", (raw, expected) => {
    expect(containerLabel(raw)).toBe(expected);
  });

  it("handles null / undefined / empty", () => {
    expect(containerLabel(null)).toBeNull();
    expect(containerLabel(undefined)).toBeNull();
    expect(containerLabel("")).toBeNull();
    expect(containerLabel("   ")).toBeNull();
  });

  it("is case-insensitive on input", () => {
    expect(containerLabel("MaTrOsKa")).toBe("MKV");
    expect(containerLabel("MOV")).toBe("MP4");
  });

  it("unknown input is upper-cased", () => {
    expect(containerLabel("brand_new_demuxer")).toBe("BRAND_NEW_DEMUXER");
  });

  it("falls back on the first comma-separated token", () => {
    expect(containerLabel("matroska,future_tag")).toBe("MKV");
  });
});
