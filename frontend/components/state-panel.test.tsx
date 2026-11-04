import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatePanel } from "@/components/state-panel";

describe("StatePanel", () => {
  it("renders an honest empty state", () => {
    render(<StatePanel title="No incidents yet" message="Connect n8n and sync failed executions." />);
    expect(screen.getByRole("status")).toHaveTextContent("No incidents yet");
    expect(screen.getByText("Connect n8n and sync failed executions.")).toBeInTheDocument();
  });
});
