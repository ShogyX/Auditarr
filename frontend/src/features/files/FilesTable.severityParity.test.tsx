/**
 * Stage 02 — severity colour parity.
 *
 * Plan §181: "confirm severity colours map 1:1 with the scope-bar
 * swatches". Before this stage the column pill for ``crit`` fell
 * through ``sevToClass`` to the bare ``crit`` class which doesn't
 * exist, so the cell rendered without colour. The fix added
 * ``crit: "sev-crit"`` to the map.
 *
 * For each severity in the canonical list, this test asserts:
 *
 *   1. ``SEVERITY_META[key].color`` (the scope-bar swatch token)
 *      maps to a ``sev-<key>`` CSS variable name.
 *   2. ``sevToClass[key]`` (the pill class lookup) yields the
 *      same ``sev-<key>`` class.
 *
 * If the two ever drift, the operator sees mismatched colours
 * between the scope bar and the column pill — exactly the bug
 * the user reported. The test guards against the regression.
 */
import { describe, expect, it } from "vitest";

import { SEVERITY_KEYS, SEVERITY_META } from "./filesShared";
import { sevToClass } from "@/lib/format";

describe("Files severity colour parity (Stage 02)", () => {
  it.each(SEVERITY_KEYS)(
    "%s: scope-bar swatch and column pill share the same sev-* token",
    (key) => {
      // The scope bar applies ``background: var(--<color>)`` where
      // ``color`` is the SEVERITY_META.color string. The colour
      // tokens follow the pattern ``sev-<key>``.
      const swatchToken = SEVERITY_META[key].color;
      expect(swatchToken).toMatch(/^sev-(ok|info|warn|high|error|crit)$/);

      // The pill uses ``sevToClass[key]`` to pick its className.
      const pillClass = sevToClass[key];
      expect(pillClass, `sevToClass missing entry for "${key}"`).toBeTruthy();
      expect(pillClass).toBe(swatchToken);
    },
  );

  it("legacy aliases still map to the same canonical sev-* class", () => {
    // ``warning`` and ``critical`` are pre-Stage-3 alternate
    // spellings that the rule engine emitted at one point. They
    // must continue to map to the same colour so legacy rows
    // still paint correctly. The Pill component falls back to
    // ``sev ?? sev`` for unknown keys; we don't want unknowns.
    expect(sevToClass.warning).toBe("sev-warn");
    expect(sevToClass.critical).toBe("sev-crit");
    expect(sevToClass.crit).toBe("sev-crit");
  });
});
