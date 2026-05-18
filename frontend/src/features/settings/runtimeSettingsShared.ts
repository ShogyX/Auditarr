/**
 * Stage 2 — Runtime settings panel shared helpers.
 *
 * Promoted from inline definitions in RuntimeSettingsPanel. The
 * ``sameValue`` helper handles the number/string drift that comes
 * from <input type="number"> emitting strings: ``"30" === 30`` for
 * dirty-check purposes. Booleans don't get confused this way.
 */

// v1.10 — ``string[]`` added so the renderer for
// ``string_list``-typed fields (like ``preferred_audio_languages``)
// can pass a tokenized list through the edit state instead of a
// raw string. The backend's pre-coerce accepts either, but
// keeping the typed list inside the UI state means the chips
// preview stays accurate without re-parsing on every render.
export type EditValue = string | number | boolean | string[];
export type Edits = Record<string, EditValue>;

/** Value equality that tolerates the string-vs-number drift from
 *  uncontrolled number inputs. ``"30" === 30`` for dirty-check
 *  purposes. */
export function sameValue(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a === "number" && typeof b === "string") return a === Number(b);
  if (typeof b === "number" && typeof a === "string") return b === Number(a);
  return false;
}
