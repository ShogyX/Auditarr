import { cn } from "@/lib/cn";

interface DocBodyProps {
  html: string;
  className?: string;
}

/**
 * Render server-rendered Markdown HTML.
 *
 * The backend's Markdown renderer is configured with ``html: false``, so
 * any ``<script>`` or other raw HTML in the source is escaped before it
 * reaches the browser. We intentionally render via ``dangerouslySetInnerHTML``
 * because the alternative — re-parsing the HTML in the browser — adds bytes
 * and a second renderer for no security benefit.
 */
export function DocBody({ html, className }: DocBodyProps) {
  return (
    <div
      className={cn("doc-body text-[13.5px] leading-relaxed text-text-2", className)}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
