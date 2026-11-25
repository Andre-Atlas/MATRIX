"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PRIMARY_BTN_CLASS } from "@/lib/mlops-tokens";
import { adaptSafetyFindings, adaptSafetyScan, summarizeScan } from "@/lib/safety";
import { proxy } from "@/lib/proxy-client";
import type {
  SafetyCategory,
  SafetyFinding,
  SafetyScan,
  SafetyScanReport,
} from "@/lib/types";
import { NotEvaluatedBadge, SimulatedBadge } from "@/components/ui";
import { FindingsTable } from "./findings-table";
import { NewScanModal } from "./new-scan-modal";
import { OwaspMatrix } from "./owasp-matrix";
import { SafetyGauge } from "./safety-gauge";

export interface SafetyDashboardProps {
  categories: SafetyCategory[];
  scans: SafetyScan[];
  initialReport: SafetyScanReport | null;
  allFindings: SafetyFinding[];
  initialError?: string | null;
}

export function SafetyDashboard({
  categories,
  scans: initialScans,
  initialReport,
  allFindings,
  initialError = null,
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
  const [fetchError, setFetchError] = useState<string | null>(initialError);
  const [loadingDetails, setLoadingDetails] = useState(false);
  const fetchedScanIdsRef = useRef<Set<string>>(
    new Set(
      initialReport?.scan?.id
        ? [initialReport.scan.id]
        : initialError && (initialScans[0]?.id ?? (initialScans[0] as any)?.scan_id)
          ? [String(initialScans[0]?.id ?? (initialScans[0] as any)?.scan_id)]
          : [],
    ),
  );

  const handleCloseModal = useCallback(() => setIsModalOpen(false), []);

  const loadScanDetails = useCallback(
    async (scanId: string, isCancelled?: () => boolean) => {
      if (!scanId) return;
      setFetchError(null);
      setLoadingDetails(true);
      try {
        const data = await proxy<any>(
          "GET",
          `safety/scans/${encodeURIComponent(scanId)}`,
        );
        if (isCancelled?.()) return;
        fetchedScanIdsRef.current.add(scanId);
        if (!data) return;
        const rawFindings = Array.isArray(data.findings) ? data.findings : [];
        if (rawFindings.length > 0) {
          const adaptedFindings = adaptSafetyFindings(
            rawFindings,
            scanId,
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
      } catch (err) {
        if (!isCancelled?.()) {
          setFetchError(
            err instanceof Error ? err.message : "Failed to load scan details.",
          );
        }
      } finally {
        if (!isCancelled?.()) {
          setLoadingDetails(false);
        }
      }
    },
    [categories],
  );

  // Fetch findings for selectedScanId if not yet loaded
  useEffect(() => {
    if (!selectedScanId) return;
    if (
      fetchedScanIdsRef.current.has(selectedScanId) ||
      findings.some((f) => f.scan_id === selectedScanId)
    ) {
      fetchedScanIdsRef.current.add(selectedScanId);
      return;
    }

    let cancelled = false;
    void loadScanDetails(selectedScanId, () => cancelled);

    return () => {
      cancelled = true;
    };
  }, [selectedScanId, findings, loadScanDetails]);

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
    fetchedScanIdsRef.current.add(newScan.id);
    setFetchError(null);
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
      <div className="space-y-4">
        {fetchError && (
          <div
            role="alert"
            className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-red-800/80 bg-red-950/40 px-3.5 py-2.5 text-xs text-red-200"
          >
            <span>{fetchError}</span>
          </div>
        )}
        <div className="space-y-4 rounded-lg border border-zinc-800 bg-zinc-900/50 p-8 text-center">
          <p className="text-sm text-zinc-300">
            No safety scans found yet for this project.
          </p>
          <button
            type="button"
            onClick={() => setIsModalOpen(true)}
            className={`inline-flex items-center justify-center rounded px-4 py-1.5 text-xs font-semibold transition-colors ${PRIMARY_BTN_CLASS}`}
          >
            Trigger New Scan
          </button>
          <NewScanModal
            isOpen={isModalOpen}
            onClose={handleCloseModal}
            categories={categories}
            onScanCreated={handleScanCreated}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {fetchError && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-red-800/80 bg-red-950/40 px-3.5 py-2.5 text-xs text-red-200"
        >
          <span>{fetchError}</span>
          <button
            type="button"
            disabled={loadingDetails}
            onClick={() => void loadScanDetails(activeScan.id)}
            className="inline-flex h-7 items-center rounded border border-red-700/70 bg-red-900/40 px-2.5 text-xs font-medium text-red-100 hover:bg-red-900/60 disabled:opacity-50"
          >
            {loadingDetails ? "Retrying…" : "Retry"}
          </button>
        </div>
      )}

      {/* Top Bar: Scan Selector & Launch Scan Button */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="scan-selector" className="text-xs font-medium text-zinc-400">
            Active Audit / Scan:
          </label>
          <select
            id="scan-selector"
            value={activeScan.id}
            onChange={(e) => {
              setSelectedScanId(e.target.value);
              setSelectedCategoryCode("all");
            }}
            className="rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-semibold text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
          >
            {scans.map((s) => {
              const scoreLabel =
                s.status === "not_evaluated" || (s.safety_score ?? s.global_score) == null
                  ? "Not evaluated"
                  : `${s.safety_score ?? s.global_score}%`;
              return (
                <option key={s.id} value={s.id}>
                  {s.name || s.id.slice(0, 8)} ({scoreLabel} — {s.target_model})
                </option>
              );
            })}
          </select>
          {activeScan.simulated === true && <SimulatedBadge reason="simulated_outputs" />}
          {activeScan.status === "not_evaluated" && <NotEvaluatedBadge />}
          <span className="text-xs text-zinc-400 font-mono">
            {new Date(activeScan.created_at).toLocaleDateString()}
          </span>
        </div>

        <button
          type="button"
          onClick={() => setIsModalOpen(true)}
          className={`inline-flex items-center justify-center gap-2 rounded px-4 py-1.5 text-xs font-semibold transition-colors ${PRIMARY_BTN_CLASS}`}
        >
          <span>Trigger New Scan</span>
        </button>
      </div>

      {/* Safety Score Speedometer & Risk Gauge */}
      <SafetyGauge
        score={activeReport.summary.score}
        riskLevel={activeReport.summary.risk_level}
        summary={activeReport.summary}
        scanName={activeScan.name || activeScan.id.slice(0, 8)}
        targetModel={activeScan.target_model}
        simulated={activeScan.simulated}
        notEvaluated={activeScan.status === "not_evaluated" || activeReport.summary.score === null}
        message={activeScan.message}
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
        onClose={handleCloseModal}
        categories={categories}
        onScanCreated={handleScanCreated}
      />
    </div>
  );
}
