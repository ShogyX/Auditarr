/**
 * Stage 11 (v1.7) — Webhook HMAC-disabled warning rendering.
 *
 * Plan §549 contract: the channel editor for HTTP / Webhook
 * exposes the new fields with an inline warning when HMAC is
 * disabled.
 *
 * The warning is extracted into ``WebhookHmacDisabledWarning``
 * so we can exercise its rendering contract without mounting
 * the full dialog (which would need to mock the kinds
 * endpoint, auth store, query client, mutation, etc.).
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WebhookHmacDisabledWarning } from "@/features/notifications/NotificationChannelDialog";

describe("Stage 11 — WebhookHmacDisabledWarning", () => {
  it("renders the warning when kind=webhook and hmac_required=false", () => {
    render(
      <WebhookHmacDisabledWarning
        kind="webhook"
        config={{ hmac_required: false, url: "https://hook.example.com/" }}
      />,
    );
    const warning = screen.getByTestId("webhook-hmac-disabled-warning");
    expect(warning).toBeInTheDocument();
    // It uses ``role="alert"`` so screen readers announce the
    // security downgrade.
    expect(warning).toHaveAttribute("role", "alert");
    // Copy mentions "unsigned" so the operator understands
    // the consequence of the toggle.
    expect(warning).toHaveTextContent(/unsigned/i);
    // Copy mentions "spoof" so the operator understands the
    // attack surface they're opening up.
    expect(warning).toHaveTextContent(/spoof/i);
  });

  it("does NOT render when hmac_required is true", () => {
    render(
      <WebhookHmacDisabledWarning
        kind="webhook"
        config={{ hmac_required: true }}
      />,
    );
    expect(
      screen.queryByTestId("webhook-hmac-disabled-warning"),
    ).not.toBeInTheDocument();
  });

  it("does NOT render when hmac_required is undefined (default True)", () => {
    /* When the form has just opened, hmac_required hasn't been
       touched — but the schema's default is True, so we treat
       undefined as "the default applies" and do NOT show the
       warning. This avoids a flash of red on form open. */
    render(<WebhookHmacDisabledWarning kind="webhook" config={{}} />);
    expect(
      screen.queryByTestId("webhook-hmac-disabled-warning"),
    ).not.toBeInTheDocument();
  });

  it("does NOT render for non-webhook channel kinds", () => {
    /* Only the webhook provider has the hmac_required field;
       a stray ``hmac_required: false`` on a Discord channel
       should not render the warning. */
    render(
      <WebhookHmacDisabledWarning
        kind="discord"
        config={{ hmac_required: false }}
      />,
    );
    expect(
      screen.queryByTestId("webhook-hmac-disabled-warning"),
    ).not.toBeInTheDocument();
  });

  it("does NOT render when kind is some unrelated provider", () => {
    render(
      <WebhookHmacDisabledWarning
        kind="slack"
        config={{ hmac_required: false }}
      />,
    );
    expect(
      screen.queryByTestId("webhook-hmac-disabled-warning"),
    ).not.toBeInTheDocument();
  });
});
