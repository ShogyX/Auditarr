/**
 * Stage 32 — Plugins page install + uninstall behavior tests.
 *
 * Pins:
 *
 *   - "Install plugin" button triggers a file picker.
 *   - Picking a file POSTs (multipart) to /plugins/install.
 *   - On success, the list is invalidated and a toast fires.
 *   - On 409, the operator sees the server's message verbatim.
 *   - Per-row "Uninstall" opens a confirmation modal — clicking
 *     it directly does NOT fire the DELETE (no accidents).
 *   - Confirming the modal DELETEs /plugins/{id}.
 *   - Cancelling the modal closes it without DELETEing.
 *   - Backend warnings (e.g. routes can't unmount) surface in
 *     the success toast.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiPostForm = vi.fn();
const apiDelete = vi.fn();
const toastSpy = vi.fn();

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: (path: string) => apiGet(path),
    post: (path: string, body?: unknown) => apiPost(path, body),
    postForm: (path: string, form: FormData) => apiPostForm(path, form),
    put: vi.fn(async () => null),
    delete: (path: string) => apiDelete(path),
    patch: vi.fn(async () => null),
  },
  ApiError: class extends Error {
    status = 500;
    code = "test";
  },
}));

vi.mock("@/stores/authStore", () => {
  const state = {
    tokens: {
      accessToken: "x",
      refreshToken: "x",
      expiresAt: Date.now() + 6e4,
    },
    user: { id: "u1", username: "tester", role: "admin" },
    isHydrated: true,
    setTokens: vi.fn(),
    setSession: vi.fn(),
    setUser: vi.fn(),
    clear: vi.fn(),
    hydrate: vi.fn(),
  };
  type S = typeof state;
  const useAuthStore = vi.fn((sel?: (s: S) => unknown) =>
    typeof sel === "function" ? sel(state) : state,
  ) as unknown as ((sel?: (s: S) => unknown) => unknown) & {
    getState: () => S;
    persist: { hasHydrated: () => boolean };
  };
  useAuthStore.getState = () => state;
  useAuthStore.persist = { hasHydrated: () => true };
  return { useAuthStore };
});

vi.mock("@/lib/toast", () => ({ toast: (...args: unknown[]) => toastSpy(...args) }));

import { PluginsPage } from "@/features/plugins/PluginsPage";

const PLUGIN_A = {
  id: "plugin-a",
  name: "Plugin A",
  version: "1.0.0",
  type: "generic",
  description: "Test plugin",
  author: "tests",
  status: "loaded",
  last_error: null,
  has_settings: false,
  routes: false,
  capabilities: [],
};

const GALLERY_EMPTY = { ok: true, feed_url: "", plugins: [], detail: null };

function wrap(child: ReactNode): ReactNode {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{child}</MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  apiPostForm.mockReset();
  apiDelete.mockReset();
  toastSpy.mockReset();

  apiGet.mockImplementation(async (path: string) => {
    if (path === "/plugins") return [PLUGIN_A];
    if (path === "/plugins/gallery") return GALLERY_EMPTY;
    return null;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

// ── Helpers ──────────────────────────────────────────────────

function makeZipFile(name = "test.zip"): File {
  // The test doesn't actually unzip — apiClient.postForm is
  // mocked. We just need a real File so onChange fires.
  return new File(["fake zip bytes"], name, { type: "application/zip" });
}

// ── Install tests ────────────────────────────────────────────

describe("Stage 32 — Plugins install + uninstall", () => {
  it("'Install plugin' button is present in the toolbar", async () => {
    render(wrap(<PluginsPage />));
    expect(
      await screen.findByRole("button", { name: /install plugin/i }),
    ).toBeInTheDocument();
  });

  it("selecting a file POSTs multipart to /plugins/install", async () => {
    apiPostForm.mockResolvedValue({ ...PLUGIN_A, id: "new-plugin" });

    render(wrap(<PluginsPage />));
    await screen.findByText("Plugin A");

    // The hidden file input has aria-label="Plugin zip file".
    // We fireEvent change on it directly — clicking the button
    // would open the OS file picker which jsdom can't drive.
    const fileInput = screen.getByLabelText(
      /plugin zip file/i,
    ) as HTMLInputElement;
    const file = makeZipFile("new-plugin.zip");
    fireEvent.change(fileInput, { target: { files: [file] } });

    await waitFor(() => {
      expect(apiPostForm).toHaveBeenCalledWith(
        "/plugins/install",
        expect.any(FormData),
      );
    });

    // And the FormData's "file" field carries our File.
    expect(apiPostForm.mock.calls.length).toBeGreaterThan(0);
    const sent = apiPostForm.mock.calls[0]![1] as FormData;
    expect(sent.get("file")).toBe(file);
  });

  it("install success fires an OK toast with the plugin name", async () => {
    apiPostForm.mockResolvedValue({ ...PLUGIN_A, name: "Fresh Plugin" });

    render(wrap(<PluginsPage />));
    await screen.findByText("Plugin A");

    const fileInput = screen.getByLabelText(
      /plugin zip file/i,
    ) as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: { files: [makeZipFile("fresh.zip")] },
    });

    await waitFor(() => {
      expect(toastSpy).toHaveBeenCalledWith(
        expect.stringContaining("Fresh Plugin"),
        "ok",
        expect.any(Number),
      );
    });
  });

  it("install failure surfaces the server message in a toast", async () => {
    apiPostForm.mockRejectedValue(
      new Error("A plugin with id 'foo' is already installed."),
    );

    render(wrap(<PluginsPage />));
    await screen.findByText("Plugin A");

    const fileInput = screen.getByLabelText(
      /plugin zip file/i,
    ) as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: { files: [makeZipFile("dup.zip")] },
    });

    await waitFor(() => {
      expect(toastSpy).toHaveBeenCalledWith(
        expect.stringContaining("already installed"),
        "error",
        expect.any(Number),
      );
    });
  });

  // ── Uninstall tests ────────────────────────────────────────

  it("clicking 'Uninstall' opens the confirmation modal, doesn't DELETE", async () => {
    render(wrap(<PluginsPage />));
    await screen.findByText("Plugin A");

    fireEvent.click(screen.getByRole("button", { name: /uninstall/i }));

    // Modal is now visible with the destructive copy.
    expect(
      await screen.findByRole("dialog", { name: /uninstall plugin a/i }),
    ).toBeInTheDocument();

    // No DELETE fired yet — confirmation is required.
    expect(apiDelete).not.toHaveBeenCalled();
  });

  it("confirming uninstall DELETEs /plugins/{id}", async () => {
    apiDelete.mockResolvedValue({
      id: "plugin-a",
      removed: true,
      warnings: [],
    });

    render(wrap(<PluginsPage />));
    await screen.findByText("Plugin A");

    fireEvent.click(screen.getByRole("button", { name: /uninstall/i }));

    const dialog = await screen.findByRole("dialog", {
      name: /uninstall plugin a/i,
    });
    // The dialog has a destructive "Uninstall" button at the
    // bottom; the per-row "Uninstall" button is outside the
    // dialog. Scope to within the dialog.
    fireEvent.click(
      within(dialog).getByRole("button", { name: /^uninstall$/i }),
    );

    await waitFor(() => {
      expect(apiDelete).toHaveBeenCalledWith("/plugins/plugin-a");
    });
  });

  it("cancelling the modal closes it without DELETEing", async () => {
    render(wrap(<PluginsPage />));
    await screen.findByText("Plugin A");

    fireEvent.click(screen.getByRole("button", { name: /uninstall/i }));

    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(apiDelete).not.toHaveBeenCalled();
  });

  it("uninstall warnings surface in the success toast", async () => {
    apiDelete.mockResolvedValue({
      id: "plugin-a",
      removed: true,
      warnings: [
        "Routes mounted by this plugin cannot be unregistered at runtime.",
      ],
    });

    render(wrap(<PluginsPage />));
    await screen.findByText("Plugin A");

    fireEvent.click(screen.getByRole("button", { name: /uninstall/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(
      within(dialog).getByRole("button", { name: /^uninstall$/i }),
    );

    await waitFor(() => {
      expect(toastSpy).toHaveBeenCalledWith(
        expect.stringContaining("Routes mounted"),
        "warn",
        expect.any(Number),
      );
    });
  });

  it("uninstall failure surfaces the error message", async () => {
    apiDelete.mockRejectedValue(new Error("Plugin not installed"));

    render(wrap(<PluginsPage />));
    await screen.findByText("Plugin A");

    fireEvent.click(screen.getByRole("button", { name: /uninstall/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(
      within(dialog).getByRole("button", { name: /^uninstall$/i }),
    );

    await waitFor(() => {
      expect(toastSpy).toHaveBeenCalledWith(
        expect.stringContaining("Plugin not installed"),
        "error",
        expect.any(Number),
      );
    });
  });
});
