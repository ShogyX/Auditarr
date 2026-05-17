/**
 * Stage 04 — built-in connectors are hidden from the Plugins
 * page by default.
 *
 * Plan §267: "render the table with both built-in and custom
 * plugins in the data; assert built-ins are hidden by default;
 * toggle 'Show built-in connectors'; assert they appear."
 *
 * Built-ins are first-party integrations whose lifecycle is
 * managed under the Integrations page; surfacing them on the
 * Plugins page misleads operators into thinking they can
 * uninstall them. The toggle exists for the rare debugging case.
 */
import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import {
  InstalledTable,
  BUILTIN_PLUGIN_IDS,
} from "./InstalledTable";
import type { PluginSummary } from "@/hooks/usePlugins";

function makePlugin(over: Partial<PluginSummary> & { id: string }): PluginSummary {
  return {
    name: `Plugin ${over.id}`,
    description: "",
    author: "",
    version: "1.0.0",
    enabled: true,
    status: "loaded",
    last_loaded_at: null,
    last_error: null,
    capabilities: [],
    settings_schema: null,
    is_builtin: false,
    type: "integration",
    homepage: null,
    ...over,
  } as PluginSummary;
}

function makeQueryMock(plugins: PluginSummary[]) {
  return {
    isLoading: false,
    isError: false,
    data: plugins,
    error: undefined,
  };
}

describe("InstalledTable — Stage 04 built-in hiding", () => {
  const customPlugin = makePlugin({ id: "my-custom-plugin", name: "Custom" });
  const plexPlugin = makePlugin({ id: "plex", name: "Plex" });
  const tdarrPlugin = makePlugin({ id: "tdarr", name: "Tdarr" });

  const all = [customPlugin, plexPlugin, tdarrPlugin];

  it("BUILTIN_PLUGIN_IDS contains the seven first-party connectors", () => {
    for (const id of [
      "plex",
      "jellyfin",
      "sonarr",
      "radarr",
      "bazarr",
      "tdarr",
    ]) {
      expect(BUILTIN_PLUGIN_IDS.has(id)).toBe(true);
    }
    // ``hello`` / example plugin is NOT in the list — it's
    // literally the canonical "how to author a plugin" example.
    expect(BUILTIN_PLUGIN_IDS.has("hello")).toBe(false);
    expect(BUILTIN_PLUGIN_IDS.has("example-hello")).toBe(false);
  });

  it("hides built-in connectors by default", () => {
    const noop = () => {};
    render(
      <InstalledTable
        plugins={makeQueryMock(all) as never}
        visiblePlugins={all}
        onConfigure={noop}
        onReload={noop}
        reloadingId={null}
        onUninstall={noop}
        uninstallingId={null}
      />,
    );
    expect(screen.queryByText("Custom")).not.toBeNull();
    expect(screen.queryByText("Plex")).toBeNull();
    expect(screen.queryByText("Tdarr")).toBeNull();
    // The toggle button announces the hidden count.
    expect(
      screen.getByRole("button", { name: /show 2 built-in connectors/i }),
    ).not.toBeNull();
  });

  it("reveals built-in connectors after clicking the toggle", () => {
    const noop = () => {};
    render(
      <InstalledTable
        plugins={makeQueryMock(all) as never}
        visiblePlugins={all}
        onConfigure={noop}
        onReload={noop}
        reloadingId={null}
        onUninstall={noop}
        uninstallingId={null}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /show 2 built-in connectors/i }),
    );
    expect(screen.getByText("Plex")).not.toBeNull();
    expect(screen.getByText("Tdarr")).not.toBeNull();
    // Toggle text inverts.
    expect(
      screen.getByRole("button", { name: /hide 2 built-in connectors/i }),
    ).not.toBeNull();
  });

  it("when search matches only built-ins, shows a hint and an inline reveal button", () => {
    const noop = () => {};
    // visiblePlugins is what the search filter has already
    // narrowed to. Simulate a search that landed on just Plex.
    render(
      <InstalledTable
        plugins={makeQueryMock(all) as never}
        visiblePlugins={[plexPlugin]}
        onConfigure={noop}
        onReload={noop}
        reloadingId={null}
        onUninstall={noop}
        uninstallingId={null}
      />,
    );
    // The "only built-in connectors match" empty state surfaces.
    expect(
      screen.getByText(/only built-in connectors match/i),
    ).not.toBeNull();
    // And the inline reveal button is present.
    expect(
      screen.getByRole("button", { name: /show built-in connectors/i }),
    ).not.toBeNull();
  });

  it("does not render the toggle when there are no built-ins in the visible set", () => {
    const noop = () => {};
    render(
      <InstalledTable
        plugins={makeQueryMock([customPlugin]) as never}
        visiblePlugins={[customPlugin]}
        onConfigure={noop}
        onReload={noop}
        reloadingId={null}
        onUninstall={noop}
        uninstallingId={null}
      />,
    );
    expect(
      screen.queryByRole("button", { name: /built-in connectors/i }),
    ).toBeNull();
  });
});
