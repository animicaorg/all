import React from "react";
import { describe, expect, it } from "vitest";
import { act } from "react-dom/test-utils";
import { createRoot } from "react-dom/client";

import AccountSelect, { type AccountItem } from "../../src/ui/popup/components/AccountSelect";

function render(ui: React.ReactElement) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(ui);
  });
  return { container, root };
}

describe("AccountSelect", () => {
  it("updates when accounts are injected", async () => {
    const firstRender = render(<AccountSelect accounts={[]} />);
    expect(firstRender.container.textContent).toContain("No accounts");

    const accounts: AccountItem[] = [
      { address: "anim1abcdefghijklmnopqrstuvwx0123456789abcdef" },
    ];

    await act(async () => {
      firstRender.root.render(<AccountSelect accounts={accounts} />);
    });

    expect(firstRender.container.textContent).not.toContain("No accounts");
    expect(firstRender.container.textContent).toContain("anim1abc");
  });
});

