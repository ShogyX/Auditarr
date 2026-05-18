/**
 * v1.9 Stage 3.2 — column-resize visibility / cursor.
 *
 * The visual changes (1px header-hover rule, global col-resize
 * cursor) are CSS, not directly testable in jsdom. What IS
 * testable is the DOM-state hook the CSS reads: the handle's
 * pointerdown writes ``body.dataset.resizingCol = "1"`` and the
 * release clears it. That body-level attribute is what powers
 * the global cursor-override rule in components.css.
 */
import { fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResizableHeaderCell } from "@/components/ui/ResizableHeaderCell";

afterEach(() => {
  // Ensure no test leaks the body attribute into the next.
  delete document.body.dataset.resizingCol;
});

function renderHandle() {
  // The handle finds its <col> via the closest table; render a
  // minimal table around it so colEl lookup doesn't return null.
  const onCommit = vi.fn();
  const { container } = render(
    <table>
      <colgroup>
        <col data-col-key="severity" style={{ width: "120px" }} />
      </colgroup>
      <thead>
        <tr>
          <th>
            severity
            <ResizableHeaderCell
              columnKey="severity"
              currentWidth={120}
              onCommit={onCommit}
            />
          </th>
        </tr>
      </thead>
    </table>,
  );
  const handle = container.querySelector(".ui-th-resizer") as HTMLElement;
  // jsdom doesn't implement the Pointer Events capture API on
  // generic elements. Stub the three methods the component calls
  // so the gesture path executes end-to-end. Track capture state
  // in a local closure so hasPointerCapture returns the right
  // answer for the finish() branch.
  const captured = new Set<number>();
  (handle as unknown as HTMLElement & {
    setPointerCapture: (id: number) => void;
    releasePointerCapture: (id: number) => void;
    hasPointerCapture: (id: number) => boolean;
  }).setPointerCapture = (id: number) => {
    captured.add(id);
  };
  (handle as unknown as HTMLElement & {
    releasePointerCapture: (id: number) => void;
  }).releasePointerCapture = (id: number) => {
    captured.delete(id);
  };
  (handle as unknown as HTMLElement & {
    hasPointerCapture: (id: number) => boolean;
  }).hasPointerCapture = (id: number) => captured.has(id);
  return { handle, onCommit };
}

describe("v1.9 Stage 3.2 — ResizableHeaderCell body[data-resizing-col]", () => {
  it("sets body[data-resizing-col=1] on pointerdown", () => {
    const { handle } = renderHandle();
    expect(document.body.dataset.resizingCol).toBeUndefined();

    // jsdom's pointer-capture is a no-op but it doesn't throw — so
    // setPointerCapture's `expect-the-call-doesn't-explode`
    // satisfies the handler's contract enough for this test.
    fireEvent.pointerDown(handle, { clientX: 100, pointerId: 1 });
    expect(document.body.dataset.resizingCol).toBe("1");
  });

  it("clears body[data-resizing-col] on pointerup", () => {
    const { handle } = renderHandle();
    fireEvent.pointerDown(handle, { clientX: 100, pointerId: 1 });
    expect(document.body.dataset.resizingCol).toBe("1");

    fireEvent.pointerUp(handle, { clientX: 100, pointerId: 1 });
    expect(document.body.dataset.resizingCol).toBeUndefined();
  });

  it("also clears on pointercancel (gesture aborted)", () => {
    const { handle } = renderHandle();
    fireEvent.pointerDown(handle, { clientX: 100, pointerId: 1 });
    fireEvent.pointerCancel(handle, { pointerId: 1 });
    expect(document.body.dataset.resizingCol).toBeUndefined();
  });
});
