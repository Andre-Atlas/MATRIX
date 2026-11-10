"use client";

import { useMemo, useState } from "react";
import { summarizeScan } from "@/lib/safety";
import type {
  SafetyCategory,
  SafetyFinding,
  SafetyScan,
  SafetyScanReport,
} from "@/lib/types";
import { FindingsTable } from "./findings-table";
import { NewScanModal } from "./new-scan-modal";
import { OwaspMatrix } from "./owasp-matrix";
import { SafetyGauge } from "./safety-gauge";

export interface SafetyDashboardProps {
  categories: SafetyCategory[];
  scans: SafetyScan[];
  initialReport: SafetyScanReport | null;
  allFindings: SafetyFinding[];
}

export function SafetyDashboard({
  categories,
  scans: initialScans,
  initialReport,
  allFindings,
}: SafetyDashboardProps) {
  const [scans, setScans] = useState<SafetyScan[]>(initialScans);
  const [selectedScanId, setSelectedScanId] = useState<string>(
    initialReport?.scan.id ?? initialScans[0]?.id ?? "",
  );
  const [findings, setFindings] = useState<SafetyFinding[]>(allFindings);
  const [selectedCategoryCode, setSelectedCategoryCode] = useState<string>("all");
  const [isModalOpen, setIsModalOpen] = useState(false);

  // Find currently active scan
  const activeScan = useMemo(() => {
    return scans.find((s) => s.id === selectedScanId) ?? scans[0];
  }, [scans, selectedScanId]);

  // Generate or use current scan report
  const activeReport = useMemo(() => {
    if (!activeScan) return null;
    return summarizeScan(activeScan, findings, categories);
  }, [activeScan, findings, categories]);

  const handleScanCreated = (newScan: SafetyScan) => {
    setScans((prev) => [newScan, ...prev]);
    setSelectedScanId(newScan.id);
  };

  if (!activeScan || !activeReport) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-8 text-center text-zinc-400">
        No safety scans found. Click &quot;Disparar Novo Scan&quot; to begin your first audit.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Top Bar: Scan Selector & Launch Scan Button */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="scan-selector" className="text-xs font-medium text-zinc-400">
            Auditoria / Scan Ativo:
          </label>
          <select
            id="scan-selector"
            value={activeScan.id}
            onChange={(e) => {
              setSelectedScanId(e.target.value);
              setSelectedCategoryCode("all");
            }}
            className="rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-semibold text-zinc-100 focus:border-emerald-500 focus:outline-none"
          >
            {scans.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} ({s.safety_score}% — {s.target_model})
              </option>
            ))}
          </select>
          <span className="text-xs text-zinc-500 font-mono">
            {new Date(activeScan.created_at).toLocaleDateString()}
          </span>
        </div>

        <button
          type="button"
          onClick={() => setIsModalOpen(true)}
          className="inline-flex items-center justify-center gap-2 rounded bg-emerald-600 px-4 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-emerald-500 focus:outline-none focus:ring-2 focus:ring-emerald-500 transition-colors"
        >
          <span aria-hidden>🛡️</span>
          <span>Disparar Novo Scan</span>
        </button>
      </div>

      {/* Safety Score Speedometer & Risk Gauge */}
      <SafetyGauge
        score={activeReport.summary.score}
        riskLevel={activeReport.summary.risk_level}
        summary={activeReport.summary}
        scanName={activeScan.name}
        targetModel={activeScan.target_model}
      />

      {/* OWASP LLM Top 10 Matrix */}
      <OwaspMatrix
        categories={categories}
        scores={activeReport.category_scores}
        selectedCategoryCode={selectedCategoryCode}
        onSelectCategory={setSelectedCategoryCode}
      />

      {/* Findings Table with Inspection Drawer */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold tracking-wide text-zinc-100 uppercase">
            Defensive Probe Findings & Guardrail Recommendations
          </h2>
          {selectedCategoryCode !== "all" && (
            <span className="text-xs text-zinc-400">
              Filtering by: <strong className="text-emerald-400">{selectedCategoryCode}</strong>
            </span>
          )}
        </div>

        <FindingsTable
          findings={activeReport.findings}
          activeCategoryCode={selectedCategoryCode}
          onClearCategoryFilter={() => setSelectedCategoryCode("all")}
        />
      </div>

      {/* New Scan Modal */}
      <NewScanModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        categories={categories}
        onScanCreated={handleScanCreated}
      />
    </div>
  );
}
