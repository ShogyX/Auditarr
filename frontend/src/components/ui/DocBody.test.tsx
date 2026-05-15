import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DocBody } from "@/components/ui/DocBody";

describe("DocBody", () => {
  it("renders provided HTML as-is", () => {
    const { container } = render(<DocBody html="<h1>Title</h1><p>Body</p>" />);
    expect(container.querySelector("h1")?.textContent).toBe("Title");
    expect(container.querySelector("p")?.textContent).toBe("Body");
  });

  it("applies the doc-body class for typography", () => {
    const { container } = render(<DocBody html="<p>x</p>" />);
    expect(container.firstChild).toHaveClass("doc-body");
  });

  it("forwards additional classes", () => {
    const { container } = render(<DocBody html="<p>x</p>" className="custom-class" />);
    expect(container.firstChild).toHaveClass("custom-class");
  });
});
