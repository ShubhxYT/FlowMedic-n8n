import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DiagnosisPanel } from "@/components/diagnosis-panel";
import type { Incident } from "@/lib/types";

const incident: Incident = {
  id: "incident-1", workflow_id: "workflow-1", workflow_name: "Order import", execution_id: "execution-1",
  failed_node: "Fetch orders", error_type: "NodeApiError", error_message: "401 Unauthorized", execution_timestamp: null,
  severity: "high", status: "open", diagnosis: null, diagnosis_provider: null,
};

describe("DiagnosisPanel", () => {
  it("runs the diagnosis action and preserves the advisory warning", async () => {
    const onDiagnosed = vi.fn().mockResolvedValue(incident);
    render(<DiagnosisPanel incident={incident} onDiagnosed={onDiagnosed} />);
    fireEvent.click(screen.getByRole("button", { name: "Run AI Diagnosis" }));
    expect(onDiagnosed).toHaveBeenCalledWith(incident);
    expect(await screen.findByText("No diagnosis yet")).toBeInTheDocument();
  });

  it("shows a safe error when diagnosis fails", async () => {
    const onDiagnosed = vi.fn().mockRejectedValue(new Error("network failure"));
    render(<DiagnosisPanel incident={incident} onDiagnosed={onDiagnosed} />);
    fireEvent.click(screen.getByRole("button", { name: "Run AI Diagnosis" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Diagnosis could not be completed.");
  });
});
