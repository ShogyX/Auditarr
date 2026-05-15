import { describe, expect, it } from "vitest";

import { fmtBytes, fmtDur, fmtNum, fmtTB, sevToClass } from "@/lib/format";

describe("format helpers", () => {
  it("fmtNum handles null/undefined", () => {
    expect(fmtNum(null)).toBe("0");
    expect(fmtNum(undefined)).toBe("0");
    expect(fmtNum(1234)).toBe("1,234");
  });

  it("fmtBytes scales up the units", () => {
    expect(fmtBytes(0)).toBe("0 B");
    expect(fmtBytes(1024)).toBe("1.0 KB");
    expect(fmtBytes(1024 * 1024 * 1024)).toBe("1.0 GB");
    expect(fmtBytes(null)).toBe("—");
  });

  it("fmtTB formats with one decimal", () => {
    expect(fmtTB(47.2)).toBe("47.2 TB");
  });

  it("fmtDur picks the right unit", () => {
    expect(fmtDur(45)).toBe("45s");
    expect(fmtDur(125)).toBe("2m 5s");
    expect(fmtDur(3700)).toBe("1h 1m");
    expect(fmtDur(null)).toBe("—");
  });

  it("sevToClass maps known severities", () => {
    expect(sevToClass.ok).toBe("sev-ok");
    expect(sevToClass.unplayable).toBe("sev-error");
    expect(sevToClass.possible_malicious).toBe("sev-crit");
  });
});
