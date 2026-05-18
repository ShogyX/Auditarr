/**
 * v1.9 Stage 9.1 — DevicesCard tests.
 *
 * Pins:
 *   1. Card hides itself when no devices are returned.
 *   2. Renders one row per device.
 *   3. Transcode-ratio bar width matches the ratio.
 *   4. Unnamed devices render a placeholder rather than empty.
 *   5. Loading state surfaces a loading message.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

const apiGet = vi.fn();

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: (path: string) => apiGet(path),
    post: vi.fn(async () => null),
    patch: vi.fn(async () => null),
    put: vi.fn(async () => null),
    delete: vi.fn(async () => null),
  },
  ApiError: class extends Error {
    status = 500;
    code = "test";
  },
}));

import { DevicesCard } from "@/features/dashboard/DevicesCard";

function withProviders(node: ReactNode) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return <QueryClientProvider client={client}>{node}</QueryClientProvider>;
}

beforeEach(() => {
  apiGet.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("DevicesCard", () => {
  it("hides the card when no devices are returned", async () => {
    apiGet.mockResolvedValueOnce({ devices: [], total: 0 });
    const { container } = render(withProviders(<DevicesCard />));
    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    await waitFor(() => {
      expect(
        container.querySelector('[data-testid="devices-card"]'),
      ).toBeNull();
    });
  });

  it("renders one row per device", async () => {
    apiGet.mockResolvedValueOnce({
      devices: [
        {
          id: "1",
          integration_id: "i",
          client_key: "k1",
          name: "Living Room TV",
          platform: "Roku",
          product: null,
          device_model: null,
          first_seen_at: null,
          last_seen_at: null,
          playback_count: 20,
          transcode_count: 5,
          direct_play_count: 15,
          direct_stream_count: 0,
        },
        {
          id: "2",
          integration_id: "i",
          client_key: "k2",
          name: "Bedroom AppleTV",
          platform: "AppleTV",
          product: null,
          device_model: null,
          first_seen_at: null,
          last_seen_at: null,
          playback_count: 8,
          transcode_count: 0,
          direct_play_count: 8,
          direct_stream_count: 0,
        },
      ],
      total: 2,
    });
    render(withProviders(<DevicesCard />));
    await waitFor(() =>
      expect(screen.getByText("Living Room TV")).toBeInTheDocument(),
    );
    expect(screen.getByText("Bedroom AppleTV")).toBeInTheDocument();
    const rows = screen.getAllByTestId("devices-card-row");
    expect(rows).toHaveLength(2);
  });

  it("transcode bar width matches the ratio", async () => {
    apiGet.mockResolvedValueOnce({
      devices: [
        {
          id: "1",
          integration_id: "i",
          client_key: "k1",
          name: "Heavy",
          platform: "Roku",
          product: null,
          device_model: null,
          first_seen_at: null,
          last_seen_at: null,
          playback_count: 10,
          transcode_count: 7,
          direct_play_count: 3,
          direct_stream_count: 0,
        },
      ],
      total: 1,
    });
    render(withProviders(<DevicesCard />));
    await waitFor(() => expect(screen.getByText("Heavy")).toBeInTheDocument());
    const bar = screen.getByTestId("devices-card-transcode-bar");
    expect((bar as HTMLElement).style.width).toBe("70%");
    expect(screen.getByText("70%")).toBeInTheDocument();
  });

  it("renders placeholder for unnamed devices", async () => {
    apiGet.mockResolvedValueOnce({
      devices: [
        {
          id: "1",
          integration_id: "i",
          client_key: "k1",
          name: null,
          platform: "Web",
          product: null,
          device_model: null,
          first_seen_at: null,
          last_seen_at: null,
          playback_count: 1,
          transcode_count: 0,
          direct_play_count: 1,
          direct_stream_count: 0,
        },
      ],
      total: 1,
    });
    render(withProviders(<DevicesCard />));
    await waitFor(() =>
      expect(screen.getByText(/unnamed device/i)).toBeInTheDocument(),
    );
  });

  it("renders a loading message before the query resolves", () => {
    // Don't resolve; just rely on the initial loading state.
    apiGet.mockReturnValueOnce(new Promise(() => {}));
    render(withProviders(<DevicesCard />));
    expect(screen.getByText(/loading devices/i)).toBeInTheDocument();
  });
});
