import { cn } from "@/lib/cn";

interface SparklineProps {
  values: number[];
  accent?: boolean;
  height?: number;
  className?: string;
  /**
   * Optional label rendered inside the SVG when the dataset has been
   * loaded but every value is zero — distinguishes "no data yet" from
   * "data, but everything is zero" (which legitimately is a flat
   * baseline). Stage 6 (audit follow-up). Pass an empty string or omit
   * to render nothing extra in that case.
   */
  emptyLabel?: string;
}

/** SVG sparkline. Same math as the original Auditarr UI, with two
 *  Stage 6 (audit follow-up) edge cases handled:
 *    - ``values.length === 1`` used to land every point at x=0 because
 *      ``(values.length - 1)`` is zero. We now render a flat line
 *      across the full width — the operationally correct visual for
 *      a single observation.
 *    - ``values.every(v => v === 0)`` used to render a flat line at
 *      the baseline, indistinguishable from "small but non-zero".
 *      We surface ``emptyLabel`` so the caller can show a "no data
 *      yet" pill (the dashboard does this).
 */
export function Sparkline({
  values,
  accent,
  height = 36,
  className,
  emptyLabel,
}: SparklineProps) {
  if (!values?.length) return null;
  const w = 100;
  const h = height;

  // Stage 6 (audit follow-up): single-value case. Render a flat line
  // at mid-height across the full width, rather than the degenerate
  // "all points at x=0" produced by the original formula.
  if (values.length === 1) {
    const y = h / 2;
    const d = `M0 ${y.toFixed(2)} L${w} ${y.toFixed(2)}`;
    const da = `${d} L${w} ${h} L0 ${h} Z`;
    return (
      <svg
        className={cn("spark", className)}
        viewBox={`0 0 ${w} ${h}`}
        preserveAspectRatio="none"
        data-spark-single="true"
      >
        <path className="area" d={da} />
        <path className={cn("line", accent && "accent")} d={d} />
      </svg>
    );
  }

  const allZero = values.every((v) => v === 0);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const pts = values.map<[number, number]>((v, i) => [
    (i / (values.length - 1 || 1)) * w,
    h - 2 - ((v - min) / range) * (h - 6),
  ]);
  const d = pts
    .map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(2)} ${p[1].toFixed(2)}`)
    .join(" ");
  const da = `${d} L${w} ${h} L0 ${h} Z`;
  return (
    <svg
      className={cn("spark", className)}
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      data-spark-empty={allZero ? "true" : undefined}
    >
      <path className="area" d={da} />
      <path className={cn("line", accent && "accent")} d={d} />
      {/* Stage 6: visible "no data" badge when the dataset is loaded
          but flat at zero. The badge is rendered as SVG text so the
          caller doesn't have to wrap us in another element to add it. */}
      {allZero && emptyLabel ? (
        <text
          x="50%"
          y="50%"
          dominantBaseline="middle"
          textAnchor="middle"
          fontSize="8"
          fill="currentColor"
          opacity="0.5"
          className="spark-empty-label"
        >
          {emptyLabel}
        </text>
      ) : null}
    </svg>
  );
}
