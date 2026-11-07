import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MetricCard } from "@/components/metric-card";
import { StatePanel } from "@/components/state-panel";

describe("StatePanel", () => {
  it("renders an honest empty state", () => {
    render(<StatePanel title="No incidents yet" message="Connect n8n and sync failed executions." />);
    expect(screen.getByRole("status")).toHaveTextContent("No incidents yet");
    expect(screen.getByText("Connect n8n and sync failed executions.")).toBeInTheDocument();
  });

  it("announces errors as alerts", () => {
    render(<StatePanel tone="error" title="Incidents unavailable" message="Backend could not be reached." />);
    expect(screen.getByRole("alert")).toHaveTextContent("Incidents unavailable");
  });

  it("marks loading panels as busy", () => {
    render(<StatePanel tone="loading" title="Loading incidents" message="Requesting incidents." />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
  });
});

describe("MetricCard", () => {
  it("shows the value when loaded", () => {
    render(<MetricCard label="Healthy" value={4} detail="No recent failures" />);
    expect(screen.getByText("4")).toBeInTheDocument();
  });

  it("hides the value and announces loading while loading", () => {
    render(<MetricCard loading label="Healthy" value={4} detail="No recent failures" />);
    expect(screen.queryByText("4")).not.toBeInTheDocument();
    expect(screen.getByText("Loading")).toBeInTheDocument();
  });
});
