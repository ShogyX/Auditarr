import { beforeEach, describe, expect, it } from "vitest";

import { useHelpStore } from "@/stores/helpStore";

describe("helpStore", () => {
  beforeEach(() => {
    useHelpStore.setState({ activeKey: null, isOpen: false });
  });

  it("starts closed with no active key", () => {
    expect(useHelpStore.getState().isOpen).toBe(false);
    expect(useHelpStore.getState().activeKey).toBeNull();
  });

  it("setKey updates the active key", () => {
    useHelpStore.getState().setKey("rules.conditions");
    expect(useHelpStore.getState().activeKey).toBe("rules.conditions");
  });

  it("open without argument keeps the existing key", () => {
    useHelpStore.getState().setKey("rules.actions");
    useHelpStore.getState().open();
    expect(useHelpStore.getState().isOpen).toBe(true);
    expect(useHelpStore.getState().activeKey).toBe("rules.actions");
  });

  it("open with argument overrides the active key", () => {
    useHelpStore.getState().setKey("a");
    useHelpStore.getState().open("b");
    expect(useHelpStore.getState().isOpen).toBe(true);
    expect(useHelpStore.getState().activeKey).toBe("b");
  });

  it("toggle flips isOpen", () => {
    useHelpStore.getState().toggle();
    expect(useHelpStore.getState().isOpen).toBe(true);
    useHelpStore.getState().toggle();
    expect(useHelpStore.getState().isOpen).toBe(false);
  });

  it("close shuts the drawer without touching the key", () => {
    useHelpStore.getState().open("x");
    useHelpStore.getState().close();
    expect(useHelpStore.getState().isOpen).toBe(false);
    expect(useHelpStore.getState().activeKey).toBe("x");
  });
});
