import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DiagnosisPanel } from "@/components/diagnosis-panel";
import type { Incident } from "@/lib/types";

const incident: Incident = {
  id: "incident-1", workflow_id: "workflow-1", workflow_name: "Order import", execution_id: "execution-1",
  failed_node: "Fetch orders", error_type: "NodeApiError", error_message: "401 Unauthorized", execution_timestamp: null,
  severity: "high", status: "open", evidence: null, diagnosis: null, diagnosis_provider: null,
};

const diagnosed: Incident = {
  ...incident,
  status: "diagnosed",
  diagnosis_provider: "mock",
  diagnosis: {
    summary: "Upstream authorization failed at the Fetch orders node.",
    probable_root_cause: "The configured credential was rejected with HTTP 401.",
    affected_component: "Fetch orders",
    recommended_fix: "Check credential validity and permissions manually; test in a safe environment.",
    confidence: 0.55,
    confidence_basis: "Observed NodeApiError with HTTP 401; credential scope metadata was not supplied.",
    risk_level: "medium",
    hypotheses: [
      {
        rank: 1,
        explanation: "The configured credential is expired or lacks permission.",
        supporting_evidence: ["NodeApiError", "HTTP status 401"],
        contradictory_evidence: "No credential scope metadata was supplied.",
        verification_step: "Retry with a known-good credential and compare the HTTP status.",
      },
      {
        rank: 2,
        explanation: "The upstream service rejects the request for a different reason.",
        supporting_evidence: ["The upstream returned a generic authorization failure."],
        contradictory_evidence: "No contradictory evidence was identified for this hypothesis.",
        verification_step: "Inspect the sanitized error description for the rejection reason.",
      },
    ],
    recommendations: [
      {
        action: "Check credential validity and permissions in a safe environment.",
        verification: "A successful request or a non-401 status confirms the hypothesis.",
      },
    ],
  },
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

  it("renders ranked hypotheses, confidence basis, and verifiable recommendations", () => {
    render(<DiagnosisPanel incident={diagnosed} onDiagnosed={vi.fn()} />);
    expect(screen.getByText(/Observed NodeApiError with HTTP 401/)).toBeInTheDocument();
    expect(screen.getByText("#1 The configured credential is expired or lacks permission.")).toBeInTheDocument();
    expect(screen.getByText("#2 The upstream service rejects the request for a different reason.")).toBeInTheDocument();
    expect(screen.getByText(/Supporting evidence: NodeApiError; HTTP status 401/)).toBeInTheDocument();
    expect(screen.getByText(/Conflicting or missing evidence: No credential scope metadata/)).toBeInTheDocument();
    expect(screen.getByText(/How to verify: Retry with a known-good credential/)).toBeInTheDocument();
    expect(screen.getByText(/Confirms or rejects the hypothesis: A successful request/)).toBeInTheDocument();
  });
});
