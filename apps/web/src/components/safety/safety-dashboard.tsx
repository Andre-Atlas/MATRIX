"use client";

import { useEffect, useMemo, useState } from "react";
import { adaptSafetyFindings, adaptSafetyScan, summarizeScan } from "@/lib/safety";
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
  const [scans, setScans] = useState<SafetyScan[]>(() =>
    initialScans.map((s) => adaptSafetyScan(s)),
  );
  const [selectedScanId, setSelectedScanId] = useState<string>(
    initialReport?.scan?.id ??
      (initialReport as any)?.scan_id ??
      (initialReport as any)?.id ??
      initialScans[0]?.id ??
      (initialScans[0] as any)?.scan_id ??
      "",
  );
  const [findings, setFindings] = useState<SafetyFinding[]>(allFindings);
  const [selectedCategoryCode, setSelectedCategoryCode] = useState<string>("all");
  const [isModalOpen, setIsModalOpen] = useState(false);

  // Fetch findings for selectedScanId if not yet loaded
  useEffect(() => {
    if (!selectedScanId) return;
    const hasFindingsForScan = findings.some((f) => f.scan_id === selectedScanId);
    if (hasFindingsForScan) return;

    let cancelled = false;
    fetch(`/api/proxy/safety/scans/${encodeURIComponent(selectedScanId)}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (cancelled || !data) return;
        const rawFindings = Array.isArray(data.findings) ? data.findings : [];
        if (rawFindings.length > 0) {
          const adaptedFindings = adaptSafetyFindings(
            rawFindings,
            selectedScanId,
            categories,
            data.created_at ?? data.scan?.created_at,
          );
          setFindings((prev) => {
            const existingIds = new Set(prev.map((f) => f.id));
            const merged = [...prev];
            for (const f of adaptedFindings) {
              if (!existingIds.has(f.id)) merged.push(f);
            }
            return merged;
          });
        }
      })
      .catch(() => {
        // Ignore fetch errors in offline/test environments
      });

    return () => {
      cancelled = true;
    };
  }, [selectedScanId, findings, categories]);

  // Find currently active scan
  const activeScan = useMemo(() => {
    return scans.find((s) => s.id === selectedScanId) ?? scans[0];
  }, [scans, selectedScanId]);

  // Generate or use current scan report
  const activeReport = useMemo(() => {
    if (!activeScan) return null;
    return summarizeScan(activeScan, findings, categories);
  }, [activeScan, findings, categories]);

  const handleScanCreated = (rawNewScan: SafetyScan) => {
    const newScan = adaptSafetyScan(rawNewScan);
    const rawFindings = Array.isArray((rawNewScan as any)?.findings)
      ? (rawNewScan as any).findings
      : [];
    if (rawFindings.length > 0) {
      const adaptedFindings = adaptSafetyFindings(
        rawFindings,
        newScan.id,
        categories,
        newScan.created_at,
      );
      setFindings((prev) => [...adaptedFindings, ...prev]);
    }
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
                {s.name || s.id.slice(0, 8)} ({s.safety_score ?? (s as any).global_score ?? 100}% — {s.target_model})
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
        scanName={activeScan.name || activeScan.id.slice(0, 8)}
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
