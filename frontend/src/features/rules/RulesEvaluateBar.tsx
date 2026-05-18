/**
 * Stage 4 — Rules page header "evaluate library" controls.
 *
 * Extracted from the inline JSX in the ``PageHeader`` ``actions``
 * prop. Renders the library picker + Evaluate button. The selected
 * library state lives in ``useRulesPageState``; this component is
 * purely presentational.
 */

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";

export interface RulesEvaluateBarProps {
  libraries: { id: string; name: string }[];
  selectedLibrary: string;
  onSelectLibrary: (id: string) => void;
  onEvaluate: () => void;
  isEvaluating: boolean;
  onEvaluateAll: () => void;
  isEvaluatingAll: boolean;
}

export function RulesEvaluateBar({
  libraries,
  selectedLibrary,
  onSelectLibrary,
  onEvaluate,
  isEvaluating,
  onEvaluateAll,
  isEvaluatingAll,
}: RulesEvaluateBarProps) {
  const busy = isEvaluating || isEvaluatingAll;
  return (
    <>
      <select
        value={selectedLibrary}
        onChange={(e) => onSelectLibrary(e.target.value)}
        className="settings-input"
        aria-label="Library to evaluate"
        disabled={busy}
      >
        <option value="">Pick a library to evaluate…</option>
        {libraries.map((lib) => (
          <option key={lib.id} value={lib.id}>
            {lib.name}
          </option>
        ))}
      </select>
      <Button
        size="sm"
        variant="ghost"
        disabled={!selectedLibrary || busy}
        onClick={onEvaluate}
        title="Re-evaluate every file in the chosen library against all enabled rules"
      >
        <Icon name="refresh" size={12} />
        <span className="ml-1">{isEvaluating ? "Evaluating…" : "Evaluate"}</span>
      </Button>
      <Button
        size="sm"
        variant="ghost"
        disabled={busy || libraries.length === 0}
        onClick={onEvaluateAll}
        title="Re-evaluate every file in every library against all enabled rules"
      >
        <Icon name="refresh" size={12} />
        <span className="ml-1">
          {isEvaluatingAll ? "Evaluating all…" : "Evaluate all libraries"}
        </span>
      </Button>
    </>
  );
}
