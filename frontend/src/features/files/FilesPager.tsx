/**
 * Stage 3 — Files pager.
 *
 * Extracted from the inline ``Pager`` in ``FilesPage.tsx``. Renders
 * the Prev / Next control beneath the table. Page state lives in the
 * page hook (``useFilesPageState``); this component is purely
 * presentational.
 */

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";

export interface FilesPagerProps {
  page: number;
  totalPages: number;
  onPage: (p: number) => void;
}

export function FilesPager({ page, totalPages, onPage }: FilesPagerProps) {
  return (
    <div className="files-pager">
      <span className="text-[12px] text-muted">
        Page {page + 1} of {totalPages}
      </span>
      <div className="flex items-center gap-1">
        <Button
          size="sm"
          variant="ghost"
          onClick={() => onPage(Math.max(0, page - 1))}
          disabled={page === 0}
        >
          <Icon name="arrow_left" size={12} /> Prev
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => onPage(Math.min(totalPages - 1, page + 1))}
          disabled={page >= totalPages - 1}
        >
          Next <Icon name="arrow_right" size={12} />
        </Button>
      </div>
    </div>
  );
}
