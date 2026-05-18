/**
 * v1.9 Stage 2.4 — DeleteFilesDialog contract.
 *
 * Pins the four behaviors the plan calls out:
 *
 *   1. Index-only delete (``remove_from_disk=false``, the default)
 *      can be confirmed in one click — no typed-phrase gate.
 *   2. Toggling the "Also remove from disk" checkbox exposes a
 *      typed-confirmation field and the confirm button stays
 *      disabled until the operator types ``DELETE`` exactly.
 *   3. The ``onConfirm`` callback fires with the right
 *      ``{remove_from_disk, reason}`` payload so the parent's
 *      mutation receives the operator's choices.
 *   4. The dialog calls ``onOpenChange(false)`` when the operator
 *      cancels — the parent owns selection-clearing on success;
 *      this test verifies the cancel path closes cleanly.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import { DeleteFilesDialog } from "@/features/files/DeleteFilesDialog";

const onOpenChange = vi.fn();
const onConfirm = vi.fn();

beforeEach(() => {
  onOpenChange.mockReset();
  onConfirm.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("v1.9 Stage 2.4 — DeleteFilesDialog", () => {
  it("index-only mode confirms in one click with the expected payload", () => {
    render(
      <DeleteFilesDialog
        open={true}
        onOpenChange={onOpenChange}
        fileNames={["movie.mkv"]}
        onConfirm={onConfirm}
      />,
    );
    // Default state: "Remove" button is enabled, no typed-confirm field.
    const confirm = screen.getByRole("button", { name: /^remove$/i });
    expect(confirm).not.toBeDisabled();
    expect(
      screen.queryByLabelText(/type delete to confirm/i),
    ).toBeNull();

    fireEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith({
      remove_from_disk: false,
      reason: null,
    });
  });

  it("checking 'Also remove from disk' gates confirm behind typing DELETE", () => {
    render(
      <DeleteFilesDialog
        open={true}
        onOpenChange={onOpenChange}
        fileNames={["movie.mkv"]}
        onConfirm={onConfirm}
      />,
    );

    // Toggle the on-disk checkbox.
    const checkbox = screen.getByRole("checkbox", {
      name: /also remove from disk/i,
    });
    fireEvent.click(checkbox);

    // The confirm button's label flips to "Move to trash" and the
    // typed-confirmation field appears.
    const confirm = screen.getByRole("button", { name: /move to trash/i });
    expect(confirm).toBeDisabled();
    const typedConfirm = screen.getByLabelText(/type delete to confirm/i);
    expect(typedConfirm).toBeInTheDocument();

    // Typing the wrong phrase keeps the button disabled.
    fireEvent.change(typedConfirm, { target: { value: "delete" } }); // lowercase
    expect(
      screen.getByRole("button", { name: /move to trash/i }),
    ).toBeDisabled();

    // Typing the right phrase enables it.
    fireEvent.change(typedConfirm, { target: { value: "DELETE" } });
    const enabled = screen.getByRole("button", { name: /move to trash/i });
    expect(enabled).not.toBeDisabled();

    fireEvent.click(enabled);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith({
      remove_from_disk: true,
      reason: null,
    });
  });

  it("a typed reason flows through to onConfirm", () => {
    render(
      <DeleteFilesDialog
        open={true}
        onOpenChange={onOpenChange}
        fileNames={["movie.mkv"]}
        onConfirm={onConfirm}
      />,
    );
    const reasonInput = screen.getByPlaceholderText(
      /recorded in the audit log/i,
    );
    fireEvent.change(reasonInput, { target: { value: "  duplicate  " } });

    fireEvent.click(screen.getByRole("button", { name: /^remove$/i }));
    expect(onConfirm).toHaveBeenCalledWith({
      remove_from_disk: false,
      reason: "duplicate", // trimmed
    });
  });

  it("Cancel button closes the dialog without firing onConfirm", () => {
    render(
      <DeleteFilesDialog
        open={true}
        onOpenChange={onOpenChange}
        fileNames={["movie.mkv"]}
        onConfirm={onConfirm}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("bulk mode renders a count and expandable list of names", () => {
    const names = Array.from({ length: 5 }, (_, i) => `file_${i}.mkv`);
    render(
      <DeleteFilesDialog
        open={true}
        onOpenChange={onOpenChange}
        fileNames={names}
        onConfirm={onConfirm}
      />,
    );
    // Bulk title surfaces the count. The same text appears both
    // as the visible heading and as the modal's aria-label, so
    // getAllByText returns ≥1 match.
    expect(
      screen.getAllByText(/remove 5 files from index/i).length,
    ).toBeGreaterThan(0);
    // Expandable list is collapsed by default — open it.
    const summary = screen.getByText(/show 5 file names/i);
    fireEvent.click(summary);
    for (const n of names) {
      expect(screen.getByText(n)).toBeInTheDocument();
    }
  });
});
