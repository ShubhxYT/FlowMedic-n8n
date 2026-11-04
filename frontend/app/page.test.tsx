import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: {
    incidents: vi.fn().mockResolvedValue({ data: [] }),
    overviewMetrics: vi.fn().mockResolvedValue({
      monitored_workflow_count: 0,
      healthy_workflow_count: 0,
      degraded_workflow_count: 0,
      unhealthy_workflow_count: 0,
      unknown_workflow_count: 0,
      open_incident_count: 0,
      recent_failure_count: 0,
      recent_success_count: 0,
      last_successful_monitoring_sync_at: null,
      monitoring: {
        enabled: false,
        state: "disabled",
        poll_interval_seconds: 60,
        last_checked_at: null,
        last_successful_sync_at: null,
        last_error_at: null,
        last_error_summary: null,
        consecutive_failure_count: 0,
      },
    }),
  },
}));

import OverviewPage from "@/app/page";

describe("OverviewPage", () => {
  it("renders an honest empty incident state", async () => {
    render(<OverviewPage />);
    expect(screen.getByRole("heading", { name: "Automation health" })).toBeInTheDocument();
    expect(await screen.findByText("No incidents yet")).toBeInTheDocument();
    expect(screen.getByText("disabled")).toBeInTheDocument();
  });
});
