/**
 * v1.8.1 — FilesScanErrorBanner unit tests.
 *
 * Pins the contract:
 *   - Banner only renders for ApiError(409).
 *   - Non-409 errors are filtered out (toast handles them).
 *   - "Unstick library" button calls onReset(libraryId).
 *   - Disabled while resetting=true.
 *   - Dismissable.
 *   - Doesn't render with no libraryId.
 */

import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/services/apiClient";

import { FilesScanErrorBanner } from "./FilesScanErrorBanner";

function make409(): ApiError {
  return new ApiError(409, {
    code: "conflict",
    message: "A scan is already queued for this library (run id abc).",
  });
}

describe("FilesScanErrorBanner", () => {
  it("renders for ApiError(409) with an Unstick button", () => {
    const onReset = vi.fn();
    render(
      <FilesScanErrorBanner
        error={make409()}
        libraryId="lib-1"
        resetting={false}
        onReset={onReset}
      />,
    );
    expect(
      screen.getByText(/already running for this library/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /unstick library/i }),
    ).toBeInTheDocument();
  });

  it("clicking Unstick calls onReset with the library id", () => {
    const onReset = vi.fn();
    render(
      <FilesScanErrorBanner
        error={make409()}
        libraryId="lib-42"
        resetting={false}
        onReset={onReset}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /unstick library/i }),
    );
    expect(onReset).toHaveBeenCalledWith("lib-42");
  });

  it("disables the Unstick button while resetting", () => {
    render(
      <FilesScanErrorBanner
        error={make409()}
        libraryId="lib-1"
        resetting={true}
        onReset={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", { name: /unsticking/i }),
    ).toBeDisabled();
  });

  it("does not render for a non-409 error", () => {
    const err = new ApiError(500, { code: "internal", message: "Boom" });
    const { container } = render(
      <FilesScanErrorBanner
        error={err}
        libraryId="lib-1"
        resetting={false}
        onReset={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("does not render with no library id", () => {
    const { container } = render(
      <FilesScanErrorBanner
        error={make409()}
        libraryId=""
        resetting={false}
        onReset={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("does not render for a non-ApiError error", () => {
    const { container } = render(
      <FilesScanErrorBanner
        error={new Error("plain error")}
        libraryId="lib-1"
        resetting={false}
        onReset={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("can be dismissed via the X button", () => {
    render(
      <FilesScanErrorBanner
        error={make409()}
        libraryId="lib-1"
        resetting={false}
        onReset={vi.fn()}
      />,
    );
    expect(
      screen.getByText(/already running for this library/i),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(
      screen.queryByText(/already running for this library/i),
    ).not.toBeInTheDocument();
  });
});
