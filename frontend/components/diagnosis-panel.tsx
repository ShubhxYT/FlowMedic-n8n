"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api";
import { badgeTone } from "@/lib/format";
import type { Incident } from "@/lib/types";

export function DiagnosisPanel({
  incident,
  onDiagnosed,
}: Readonly<{ incident: Incident; onDiagnosed: (incident: Incident) => Promise<Incident> }>) {
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function runDiagnosis() {
    setRunning(true);
    setError(null);
    try {
      await onDiagnosed(incident);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Diagnosis could not be completed.");
    } finally {
      setRunning(false);
    }
  }

  const diagnosis = incident.diagnosis;
  return (
    <section className="panel p-5 sm:p-6">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
        <div><h2>AI diagnosis</h2><p className="mt-1 text-sm text-slate-400">Advisory output from the configured diagnosis provider.</p></div>
        <button className="button" onClick={runDiagnosis} disabled={running}>{running ? "Running diagnosis…" : diagnosis ? "Run again" : "Run AI Diagnosis"}</button>
      </div>
      {error && <p className="mt-5 rounded-lg border border-rose-400/30 bg-rose-400/10 p-3 text-sm text-rose-100" role="alert">{error}</p>}
      {diagnosis ? <>
        <dl className="diagnosis-grid mt-4">
          <div><dt>Summary</dt><dd>{diagnosis.summary}</dd></div>
          <div><dt>Probable root cause</dt><dd>{diagnosis.probable_root_cause}</dd></div>
          <div><dt>Affected component</dt><dd>{diagnosis.affected_component}</dd></div>
          <div><dt>Recommended fix</dt><dd>{diagnosis.recommended_fix}</dd></div>
          <div className="grid grid-cols-2 gap-4"><div><dt>Confidence</dt><dd>{Math.round(diagnosis.confidence * 100)}%</dd></div><div><dt>Risk level</dt><dd><span className={badgeTone(diagnosis.risk_level)}>{diagnosis.risk_level}</span></dd></div></div>
        </dl>
        <p className="notice">AI-generated diagnosis from <strong>{incident.diagnosis_provider ?? "an unknown provider"}</strong>. Review before changing production workflows. FlowMedic did not apply a fix.</p>
      </> : <div className="state-panel mt-5 min-h-[170px]"><h3>No diagnosis yet</h3><p>Run the configured provider to generate an advisory diagnosis from sanitized incident data.</p></div>}
    </section>
  );
}
