/**
 * Stage 12 (plan §585) — ForgotPasswordPage copy adapts to
 * email-configured state.
 *
 * Pins:
 *   - Email configured → "reset link / inbox" copy on submit.
 *   - Email NOT configured → "server logs / one-time
 *     password" copy on submit.
 *   - Submit button label adapts BEFORE submission too.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

// Mock the API client BEFORE importing the page.
let __emailConfigured = true;

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: vi.fn(async (path: string) => {
      if (path === "/auth/email-configured") {
        return { configured: __emailConfigured };
      }
      return {};
    }),
    post: vi.fn(async () => ({})),
  },
  ApiError: class ApiError extends Error {},
}));

vi.mock("@/hooks/useAuth", () => ({
  useRequestPasswordReset: () => ({
    mutateAsync: vi.fn(async () => undefined),
    isPending: false,
  }),
}));

import { ForgotPasswordPage } from "@/features/auth/ForgotPasswordPage";

function wrap(node: ReactNode): ReactNode {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>
  );
}

describe("Stage 12 — ForgotPasswordPage copy-swap", () => {
  it("shows the email-style copy when email is configured", async () => {
    __emailConfigured = true;
    render(wrap(<ForgotPasswordPage />));

    // Wait for the probe to resolve and the button label
    // to update.
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /send reset link/i }),
      ).toBeInTheDocument();
    });

    // Submit the form.
    const email = screen.getByPlaceholderText(/you@example.com/i);
    fireEvent.change(email, { target: { value: "alice@example.com" } });
    const button = screen.getByRole("button", { name: /send reset link/i });
    fireEvent.click(button);

    await waitFor(() => {
      expect(
        screen.getByTestId("forgot-password-email-copy"),
      ).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("forgot-password-terminal-copy"),
    ).not.toBeInTheDocument();
  });

  it("shows the terminal-OTP copy when email is NOT configured", async () => {
    __emailConfigured = false;
    render(wrap(<ForgotPasswordPage />));

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /request one-time password/i }),
      ).toBeInTheDocument();
    });

    const email = screen.getByPlaceholderText(/you@example.com/i);
    fireEvent.change(email, { target: { value: "alice@example.com" } });
    fireEvent.click(
      screen.getByRole("button", { name: /request one-time password/i }),
    );

    await waitFor(() => {
      expect(
        screen.getByTestId("forgot-password-terminal-copy"),
      ).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("forgot-password-email-copy"),
    ).not.toBeInTheDocument();
  });

  it("adapts the help text before submission too", async () => {
    __emailConfigured = false;
    render(wrap(<ForgotPasswordPage />));

    await waitFor(() => {
      expect(
        screen.getByText(/printed to the server logs/i),
      ).toBeInTheDocument();
    });
  });
});
