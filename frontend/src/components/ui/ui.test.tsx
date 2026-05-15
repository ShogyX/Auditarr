import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Pill, Tag } from "@/components/ui/Pill";
import { Sparkline } from "@/components/ui/Sparkline";

describe("Pill", () => {
  it("renders children with severity dot", () => {
    render(<Pill sev="warn">High bitrate</Pill>);
    expect(screen.getByText("High bitrate")).toBeInTheDocument();
  });

  it("renders solid variant without a dot", () => {
    const { container } = render(<Pill solid>Active</Pill>);
    expect(container.querySelector(".dot")).toBeNull();
    expect(container.querySelector(".pill.solid")).not.toBeNull();
  });
});

describe("Tag", () => {
  it("renders accent styling", () => {
    const { container } = render(<Tag accent>media.tags</Tag>);
    expect(container.querySelector(".tag.accent")).not.toBeNull();
  });
});

describe("Sparkline", () => {
  it("renders an empty fragment for no values", () => {
    const { container } = render(<Sparkline values={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders an svg path for values", () => {
    const { container } = render(<Sparkline values={[1, 2, 3, 4, 5]} />);
    expect(container.querySelector("svg.spark")).not.toBeNull();
    expect(container.querySelectorAll("svg.spark path").length).toBe(2);
  });
});
