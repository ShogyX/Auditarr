/**
 * v1.9 Stage 2.6 — FactoryResetDialog contract.
 *
 * Pins:
 *   1. The confirm button is disabled until the operator types the
 *      exact phrase ``reset auditarr`` (case-insensitive).
 *   2. The dialog passes the trimmed, exact phrase to onConfirm —
 *      not the raw input value with surrounding whitespace.
 *   3. Cancel closes without firing onConfirm.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FactoryResetDialog } from "@/features/settings/FactoryResetDialog";

const onOpenChange = vi.fn();
const onConfirm = vi.fn();

beforeEach(() => {
  onOpenChange.mockReset();
  onConfirm.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("v1.9 Stage 2.6 — FactoryResetDialog", () => {
  it("confirm button is disabled until the exact phrase is typed", () => {
    render(
      <FactoryResetDialog
        open={true}
        onOpenChange={onOpenChange}
        onConfirm={onConfirm}
      />,
    );

    // Initially disabled.
    const button = screen.getByRole("button", { name: /factory reset/i });
    expect(button).toBeDisabled();

    const input = screen.getByLabelText(/type reset auditarr to confirm/i);

    // Wrong phrase keeps it disabled.
    fireEvent.change(input, { target: { value: "wrong" } });
    expect(
      screen.getByRole("button", { name: /factory reset/i }),
    ).toBeDisabled();

    // Right phrase enables it.
    fireEvent.change(input, { target: { value: "reset auditarr" } });
    expect(
      screen.getByRole("button", { name: /factory reset/i }),
    ).not.toBeDisabled();
  });

  it("accepts case-insensitive phrase and trims surrounding whitespace", () => {
    render(
      <FactoryResetDialog
        open={true}
        onOpenChange={onOpenChange}
        onConfirm={onConfirm}
      />,
    );
    const input = screen.getByLabelText(/type reset auditarr to confirm/i);
    fireEvent.change(input, { target: { value: "  RESET AUDITARR  " } });
    fireEvent.click(screen.getByRole("button", { name: /factory reset/i }));
    // onConfirm receives the trimmed input (original case preserved
    // after trim — the backend lowercases it server-side).
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith("RESET AUDITARR");
  });

  it("Cancel closes without firing onConfirm", () => {
    render(
      <FactoryResetDialog
        open={true}
        onOpenChange={onOpenChange}
        onConfirm={onConfirm}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
