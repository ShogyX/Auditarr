import type { ReactNode } from "react";

interface SeverityDatum {
  key: string;
  label: string;
  count: number;
  color: string;
  scope?: string;
}

interface SeverityHeatmapProps {
  data: SeverityDatum[];
  onPick?: (datum: SeverityDatum) => void;
}

export function SeverityHeatmap({ data, onPick }: SeverityHeatmapProps): ReactNode {
  const total = data.reduce((s, x) => s + x.count, 0);
  // Stage 6 (audit follow-up): when total === 0 every cell's
  // ``flexGrow`` collapsed to zero (Math.max(0, 0) === 0), leaving an
  // empty bar that looked like a rendering bug. We now distribute the
  // bar evenly across all cells when there's no data, so the chart
  // continues to occupy the slot and signals "this is a heatmap with
  // nothing to show yet" via a low-opacity placeholder label.
  const hasData = total > 0;
  return (
    <div>
      <div
        className="heatmap"
        role="img"
        aria-label={
          hasData
            ? "Severity distribution"
            : "Severity distribution — no files indexed yet"
        }
        data-empty={hasData ? undefined : "true"}
        style={hasData ? undefined : { position: "relative" }}
      >
        {data.map((s) => (
          <div
            key={s.key}
            className={`bg-${s.color}`}
            style={
              hasData
                ? { flexGrow: Math.max(s.count, total * 0.0015) }
                : {
                    // Equal slices across all cells when total is zero.
                    flexGrow: 1,
                    flexBasis: 0,
                    opacity: 0.25,
                  }
            }
            title={`${s.label}: ${s.count.toLocaleString()}`}
            onClick={() => onPick?.(s)}
          />
        ))}
        {!hasData ? (
          <span
            className="heatmap-empty-label"
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 11,
              color: "var(--text-2)",
              pointerEvents: "none",
            }}
          >
            no files indexed yet
          </span>
        ) : null}
      </div>
      <div className="heatmap-legend">
        {data.map((s) => (
          <div key={s.key}>
            <span className="dot" style={{ background: `var(--${s.color})` }} />
            {s.label}
            <span className="mono" style={{ color: "var(--text)" }}>
              {s.count.toLocaleString()}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
