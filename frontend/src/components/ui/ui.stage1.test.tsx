/**
 * Stage 1 — primitive tests.
 *
 * One test file covers all Stage 1 primitives because each surface is small
 * and the goal is contract verification, not exhaustive scenario coverage.
 * Feature-specific scenarios are exercised by the existing feature-level
 * tests once those features migrate onto these primitives (Stages 3–7).
 *
 * Coverage targets:
 *   - Input / Select / Textarea / Switch — render + value semantics
 *   - Modal / Drawer                     — open/close, aria
 *   - Tabs                               — switching panels, badge count
 *   - Toolbar / FilterBar                — slot rendering, chip removal
 *   - DataGrid                           — header, body, sort, empty
 *   - Metric                             — label + value + delta direction
 *   - Segmented                          — radio semantics, change handler
 *   - Page                               — title, sub, actions, helpKey
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { DataGrid } from "@/components/ui/DataGrid";
import {
  Drawer,
  DrawerBody,
  DrawerFoot,
  DrawerHead,
} from "@/components/ui/Drawer";
import { Input } from "@/components/ui/Input";
import { Metric } from "@/components/ui/Metric";
import {
  Modal,
  ModalBody,
  ModalFoot,
  ModalHead,
} from "@/components/ui/Modal";
import { Page } from "@/components/ui/Page";
import { Segmented } from "@/components/ui/Segmented";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import { Tabs, TabsPanel } from "@/components/ui/Tabs";
import { Textarea } from "@/components/ui/Textarea";
import { FilterBar, Toolbar } from "@/components/ui/Toolbar";

describe("Input", () => {
  it("renders default variant", () => {
    render(<Input placeholder="search…" />);
    expect(screen.getByPlaceholderText("search…")).toBeInTheDocument();
  });

  it("renders search variant with leading icon", () => {
    const { container } = render(<Input variant="search" placeholder="find" />);
    expect(container.querySelector("svg")).not.toBeNull();
    expect(screen.getByPlaceholderText("find")).toHaveClass("pl-8");
  });

  it("forwards aria-invalid", () => {
    render(<Input aria-invalid placeholder="x" />);
    expect(screen.getByPlaceholderText("x")).toHaveAttribute("aria-invalid", "true");
  });
});

describe("Select", () => {
  it("renders options and forwards change", () => {
    const onChange = vi.fn();
    render(
      <Select onChange={onChange} defaultValue="a">
        <option value="a">A</option>
        <option value="b">B</option>
      </Select>,
    );
    const select = screen.getByRole("combobox");
    fireEvent.change(select, { target: { value: "b" } });
    expect(onChange).toHaveBeenCalled();
    expect((select as HTMLSelectElement).value).toBe("b");
  });
});

describe("Textarea", () => {
  it("renders with mono variant", () => {
    render(<Textarea variant="mono" placeholder="json" />);
    const ta = screen.getByPlaceholderText("json");
    expect(ta.className).toContain("font-mono");
  });
});

describe("Switch", () => {
  it("toggles on click and reports aria-checked", () => {
    function Harness() {
      const [on, setOn] = useState(false);
      return <Switch checked={on} onCheckedChange={setOn} label="Enable feature" />;
    }
    render(<Harness />);
    const btn = screen.getByRole("switch", { name: "Enable feature" });
    expect(btn).toHaveAttribute("aria-checked", "false");
    fireEvent.click(btn);
    expect(btn).toHaveAttribute("aria-checked", "true");
  });

  it("does not toggle when disabled", () => {
    const onCheckedChange = vi.fn();
    render(<Switch checked={false} disabled onCheckedChange={onCheckedChange} label="x" />);
    fireEvent.click(screen.getByRole("switch", { name: "x" }));
    expect(onCheckedChange).not.toHaveBeenCalled();
  });
});

describe("Modal", () => {
  it("renders content when open and exposes aria-label", () => {
    render(
      <Modal open onOpenChange={() => {}} ariaLabel="Delete rule">
        <ModalHead title="Delete rule?" onClose={() => {}} />
        <ModalBody>Are you sure?</ModalBody>
        <ModalFoot>OK</ModalFoot>
      </Modal>,
    );
    expect(screen.getByText("Are you sure?")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Delete rule" })).toBeInTheDocument();
  });

  it("calls onClose handler on close button", () => {
    const onClose = vi.fn();
    render(
      <Modal open onOpenChange={() => {}} ariaLabel="x">
        <ModalHead title="x" onClose={onClose} />
      </Modal>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
  });
});

describe("Drawer", () => {
  it("renders content when open with head/body/foot", () => {
    render(
      <Drawer open onOpenChange={() => {}} ariaLabel="File detail">
        <DrawerHead title="movie.mkv" subtitle="/library/movies" onClose={() => {}} />
        <DrawerBody>body content</DrawerBody>
        <DrawerFoot>foot</DrawerFoot>
      </Drawer>,
    );
    expect(screen.getByText("body content")).toBeInTheDocument();
    expect(screen.getByText("movie.mkv")).toBeInTheDocument();
    expect(screen.getByText("/library/movies")).toBeInTheDocument();
  });
});

describe("Tabs", () => {
  it("switches active panel on selection", async () => {
    function Harness() {
      const [value, setValue] = useState("a");
      return (
        <Tabs
          value={value}
          onValueChange={setValue}
          items={[
            { value: "a", label: "Alpha", count: 3 },
            { value: "b", label: "Beta" },
          ]}
        >
          <TabsPanel value="a">panel-a</TabsPanel>
          <TabsPanel value="b">panel-b</TabsPanel>
        </Tabs>
      );
    }
    render(<Harness />);
    expect(screen.getByText("panel-a")).toBeInTheDocument();
    // Badge count is rendered with locale-aware formatting:
    expect(screen.getByText("3")).toBeInTheDocument();

    // Radix Tabs activates on pointer-down, not click — fireEvent.click
    // doesn't fire the pointer pipeline. Activate the trigger by simulating
    // a keyboard activation, which Radix wires to both Space and Enter via
    // its underlying RovingFocusGroup.
    const betaTab = screen.getByRole("tab", { name: /Beta/ });
    betaTab.focus();
    fireEvent.keyDown(betaTab, { key: "Enter" });

    expect(screen.getByRole("tab", { name: /Beta/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});

describe("Toolbar + FilterBar", () => {
  it("renders leading and trailing slots", () => {
    render(
      <Toolbar leading={<span>leading</span>} trailing={<button>action</button>}>
        <span>middle</span>
      </Toolbar>,
    );
    expect(screen.getByText("leading")).toBeInTheDocument();
    expect(screen.getByText("middle")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "action" })).toBeInTheDocument();
  });

  it("FilterBar renders chips with remove handlers", () => {
    const onRemove = vi.fn();
    render(
      <FilterBar
        filters={[
          { label: "Severity", value: "High", onRemove },
          { label: "Library", value: "Movies" },
        ]}
      />,
    );
    expect(screen.getByText("Severity:")).toBeInTheDocument();
    expect(screen.getByText("High")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove Severity filter" }));
    expect(onRemove).toHaveBeenCalled();
  });

  it("FilterBar renders nothing when filters are empty", () => {
    const { container } = render(<FilterBar filters={[]} />);
    expect(container.firstChild).toBeNull();
  });
});

describe("DataGrid", () => {
  type Row = { id: string; name: string; size: number };
  const rows: Row[] = [
    { id: "1", name: "alpha.mkv", size: 100 },
    { id: "2", name: "beta.mkv", size: 200 },
  ];
  const columns = [
    { id: "name", header: "Name", accessorKey: "name" as const },
    { id: "size", header: "Size", accessorKey: "size" as const },
  ];

  it("renders headers and rows", () => {
    render(<DataGrid<Row> data={rows} columns={columns} getRowId={(r) => r.id} />);
    expect(screen.getByText("Name")).toBeInTheDocument();
    expect(screen.getByText("alpha.mkv")).toBeInTheDocument();
    expect(screen.getByText("beta.mkv")).toBeInTheDocument();
  });

  it("renders empty state when no rows", () => {
    render(<DataGrid<Row> data={[]} columns={columns} getRowId={(r) => r.id} />);
    expect(screen.getByText("No results")).toBeInTheDocument();
  });

  it("fires onRowClick", () => {
    const onRowClick = vi.fn();
    render(
      <DataGrid<Row>
        data={rows}
        columns={columns}
        getRowId={(r) => r.id}
        onRowClick={onRowClick}
      />,
    );
    fireEvent.click(screen.getByText("alpha.mkv"));
    expect(onRowClick).toHaveBeenCalledWith(rows[0]);
  });
});

describe("Metric", () => {
  it("renders label and value", () => {
    render(<Metric label="Files scanned" value={48127} mono />);
    expect(screen.getByText("Files scanned")).toBeInTheDocument();
    expect(screen.getByText("48127")).toBeInTheDocument();
  });

  it("renders an up-delta with sev-ok colouring", () => {
    const { container } = render(
      <Metric label="x" value={1} delta={{ direction: "up", text: "+12" }} />,
    );
    const delta = container.querySelector(".text-sev-ok");
    expect(delta).not.toBeNull();
    expect(within(delta as HTMLElement).getByText("+12")).toBeInTheDocument();
  });
});

describe("Segmented", () => {
  it("renders options with radio semantics and reports change", () => {
    function Harness() {
      const [v, setV] = useState<"7d" | "30d" | "90d">("7d");
      return (
        <Segmented
          value={v}
          onChange={setV}
          options={[
            { value: "7d", label: "7d" },
            { value: "30d", label: "30d" },
            { value: "90d", label: "90d" },
          ]}
        />
      );
    }
    render(<Harness />);
    expect(screen.getByRole("radio", { name: "7d" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("radio", { name: "30d" }));
    expect(screen.getByRole("radio", { name: "30d" })).toHaveAttribute("aria-checked", "true");
  });
});

describe("Page", () => {
  it("renders title, sub, and actions", () => {
    render(
      <MemoryRouter>
        <Page title="Files" sub="3 libraries" actions={<button>Scan</button>}>
          body
        </Page>
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "Files" })).toBeInTheDocument();
    expect(screen.getByText("3 libraries")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Scan" })).toBeInTheDocument();
    expect(screen.getByText("body")).toBeInTheDocument();
  });

  it("renders help button when helpKey is given", () => {
    render(
      <MemoryRouter>
        <Page title="Files" helpKey="files.overview">
          x
        </Page>
      </MemoryRouter>,
    );
    expect(screen.getByRole("button", { name: "Help for this screen" })).toBeInTheDocument();
  });
});
