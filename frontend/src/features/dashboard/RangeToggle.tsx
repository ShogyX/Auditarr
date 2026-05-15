/**
 * Stage 26 — range toggle for sparkline series.
 *
 * Small segmented control letting operators flip between 7 / 30 / 90
 * day windows on the dashboard's sparkline trends. The backend's
 * ``/dashboard/series?days=N`` already accepts arbitrary values up
 * to 90; this just exposes the three useful choices to the UI.
 *
 * Lives in its own file because the dashboard header carries it as
 * a sibling to the page title, not as a per-card affordance.
 */

import { cn } from "@/lib/cn";

export type RangeDays = 7 | 30 | 90;

const OPTIONS: { days: RangeDays; label: string }[] = [
  { days: 7, label: "7d" },
  { days: 30, label: "30d" },
  { days: 90, label: "90d" },
];

export function RangeToggle({
  value,
  onChange,
}: {
  value: RangeDays;
  onChange: (days: RangeDays) => void;
}) {
  return (
    <div
      className="segmented segmented-sm"
      role="radiogroup"
      aria-label="Trend window"
    >
      {OPTIONS.map((opt) => (
        <button
          key={opt.days}
          type="button"
          role="radio"
          aria-checked={value === opt.days}
          className={cn(value === opt.days && "on")}
          onClick={() => onChange(opt.days)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
