interface BrandMarkProps {
  size?: number;
}

/** Auditarr brand mark — stacked audit bars in a soft squircle with an accent dot. */
export function BrandMark({ size = 28 }: BrandMarkProps) {
  return (
    <svg
      className="brand-mark-svg"
      width={size}
      height={size}
      viewBox="0 0 32 32"
      aria-label="Auditarr"
    >
      <rect x="1.5" y="1.5" width="29" height="29" rx="8" ry="8" fill="currentColor" />
      <g fill="var(--surface)" stroke="none">
        <rect x="7" y="18" width="3" height="7" rx="1" />
        <rect x="12" y="13" width="3" height="12" rx="1" />
        <rect x="17" y="9" width="3" height="16" rx="1" />
        <rect x="22" y="15" width="3" height="10" rx="1" />
      </g>
      <circle
        cx="23.5"
        cy="9"
        r="2.6"
        fill="var(--accent)"
        stroke="var(--surface)"
        strokeWidth="1.2"
      />
    </svg>
  );
}
