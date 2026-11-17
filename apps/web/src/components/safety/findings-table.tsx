"use client";

import { useMemo, useState } from "react";
import { filterFindings, formatSeverityBadge } from "@/lib/safety";
import type { SafetyFinding } from "@/lib/types";

export interface FindingsTableProps {
  findings: SafetyFinding[];
  activeCategoryCode?: string;
  onClearCategoryFilter?: () => void;
}

export function FindingsTable({
  findings,
  activeCategoryCode,
  onClearCategoryFilter,
}: FindingsTableProps) {
  const [selectedSeverity, setSelectedSeverity] = useState<string>("all");
  const [selectedStatus, setSelectedStatus] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const filtered = useMemo(() => {
    return filterFindings(findings, {
      categoryCode: activeCategoryCode,
      severity: selectedSeverity,
      status: selectedStatus,
      search: searchQuery,
    });
  }, [findings, activeCategoryCode, selectedSeverity, selectedStatus, searchQuery]);

  return (
    <div className="space-y-3">
      {/* Filters Header */}
      <div className="flex flex-col gap-3 rounded-lg border border-zinc-800 bg-zinc-900/40 p-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          {/* Search bar */}
          <div className="relative min-w-[200px] flex-1 sm:w-64 sm:flex-initial">
            <input
              type="text"
              placeholder="Search probes, payloads, guardrails..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full rounded border border-zinc-700/80 bg-zinc-950 px-2.5 py-1 text-xs text-zinc-100 placeholder-zinc-500 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery("")}
                className="absolute right-2 top-1 text-xs text-zinc-400 hover:text-zinc-200"
              >
                ×
              </button>
            )}
          </div>

          {/* Severity selector */}
          <select
            value={selectedSeverity}
            aria-label="Filter findings by severity"
            onChange={(e) => setSelectedSeverity(e.target.value)}
            className="rounded border border-zinc-700/80 bg-zinc-950 px-2 py-1 text-xs text-zinc-200 focus:border-emerald-500 focus:outline-none"
          >
            <option value="all">All Severities</option>
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>

          {/* Status selector */}
          <select
            value={selectedStatus}
            aria-label="Filter findings by status"
            onChange={(e) => setSelectedStatus(e.target.value)}
            className="rounded border border-zinc-700/80 bg-zinc-950 px-2 py-1 text-xs text-zinc-200 focus:border-emerald-500 focus:outline-none"
          >
            <option value="all">All Statuses</option>
            <option value="failed">Failed (Vulnerabilities)</option>
            <option value="passed">Passed (Resilient)</option>
          </select>
        </div>

        {/* Filter status / Category active pill */}
        <div className="flex items-center gap-2 text-xs text-zinc-400">
          {activeCategoryCode && activeCategoryCode !== "all" && (
            <span className="inline-flex items-center gap-1 rounded bg-zinc-800 px-2 py-0.5 text-zinc-300">
              <span>Category: <strong>{activeCategoryCode}</strong></span>
              <button
                type="button"
                onClick={onClearCategoryFilter}
                className="text-zinc-400 hover:text-zinc-100"
                title="Clear category filter"
              >
                ×
              </button>
            </span>
          )}
          <span>
            Showing <strong className="text-zinc-200">{filtered.length}</strong> of {findings.length} findings
          </span>
        </div>
      </div>

      {/* Findings Table */}
      {filtered.length === 0 ? (
        <div className="rounded-lg border border-zinc-800/80 bg-zinc-900/20 p-8 text-center text-xs text-zinc-400">
          No security findings match the current filters.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-zinc-800 bg-zinc-900/40">
          <table className="w-full min-w-[760px] text-left text-xs">
            <thead className="border-b border-zinc-800 bg-zinc-900/80 text-zinc-400">
              <tr>
                <th className="px-3.5 py-2 font-medium">Severity</th>
                <th className="px-3.5 py-2 font-medium">OWASP Category</th>
                <th className="px-3.5 py-2 font-medium">Probe / Attack Vector</th>
                <th className="px-3.5 py-2 font-medium">Status</th>
                <th className="px-3.5 py-2 font-medium">Recommended Guardrail</th>
                <th className="px-3.5 py-2 text-right font-medium">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/60">
              {filtered.map((item) => {
                const isExpanded = expandedId === item.id;
                const badgeClass = formatSeverityBadge(item.severity);

                return (
                  <tr
                    key={item.id}
                    className={`transition-colors hover:bg-zinc-900/60 ${
                      isExpanded ? "bg-zinc-900/70" : ""
                    }`}
                  >
                    <td colSpan={6} className="p-0">
                      <div className="flex items-center px-3.5 py-2.5">
                        {/* Severity */}
                        <div className="w-24 shrink-0">
                          <span
                            className={`inline-flex items-center rounded border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${badgeClass}`}
                          >
                            {item.severity}
                          </span>
                        </div>

                        {/* Category */}
                        <div className="w-40 shrink-0">
                          <span className="font-mono text-[11px] font-bold text-zinc-300">
                            {item.category_code}
                          </span>
                          <span className="block truncate text-[11px] text-zinc-400" title={item.category_name}>
                            {item.category_name}
                          </span>
                        </div>

                        {/* Probe Name */}
                        <div className="min-w-0 flex-1 pr-3">
                          <div className="flex items-center gap-1.5 font-medium text-zinc-200">
                            <span className="truncate">{item.probe_name}</span>
                            {item.cwe_id && (
                              <span className="rounded bg-zinc-800 px-1 font-mono text-[10px] text-zinc-400">
                                {item.cwe_id}
                              </span>
                            )}
                          </div>
                          <div className="line-clamp-1 font-mono text-[11px] text-zinc-400">
                            Payload: {item.attack_prompt}
                          </div>
                        </div>

                        {/* Status */}
                        <div className="w-24 shrink-0">
                          {item.status === "failed" ? (
                            <span className="inline-flex items-center gap-1 rounded border border-red-800/60 bg-red-950/40 px-2 py-0.5 text-[11px] font-medium text-red-300">
                              <span className="h-1.5 w-1.5 rounded-full bg-red-400" aria-hidden />
                              Vulnerable
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 rounded border border-emerald-800/60 bg-emerald-950/40 px-2 py-0.5 text-[11px] font-medium text-emerald-300">
                              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" aria-hidden />
                              Defended
                            </span>
                          )}
                        </div>

                        {/* Recommended Guardrail snippet */}
                        <div className="hidden w-48 shrink-0 truncate text-[11px] text-zinc-300 md:block" title={item.recommended_guardrail}>
                          {item.recommended_guardrail}
                        </div>

                        {/* Expand button */}
                        <div className="w-20 shrink-0 text-right">
                          <button
                            type="button"
                            onClick={() => setExpandedId(isExpanded ? null : item.id)}
                            className="rounded border border-zinc-700 bg-zinc-800 px-2 py-1 text-[11px] text-zinc-200 hover:border-zinc-600 hover:bg-zinc-700"
                          >
                            {isExpanded ? "Hide" : "Inspect"}
                          </button>
                        </div>
                      </div>

                      {/* Expandable Inspection Drawer / Accordion */}
                      {isExpanded && (
                        <div className="border-t border-zinc-800 bg-zinc-950/80 px-5 py-4">
                          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                            {/* Left: Adversarial Prompt & Observed Output */}
                            <div className="space-y-3">
                              <div>
                                <div className="mb-1 flex items-center justify-between text-[11px] font-semibold uppercase tracking-wider text-red-400">
                                  <span>Adversarial Probe Payload</span>
                                  <span className="font-mono text-zinc-500">{item.probe_id}</span>
                                </div>
                                <div className="rounded border border-zinc-800 bg-zinc-900/90 p-2.5 font-mono text-xs text-zinc-300">
                                  {item.attack_prompt}
                                </div>
                              </div>

                              <div>
                                <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-amber-400">
                                  Observed Model Output
                                </div>
                                <div className="max-h-36 overflow-y-auto rounded border border-zinc-800 bg-zinc-900/90 p-2.5 font-mono text-xs text-zinc-300">
                                  {item.observed_output}
                                </div>
                              </div>
                            </div>

                            {/* Right: Technical Defense Analysis & Guardrail Recommendation */}
                            <div className="space-y-3">
                              <div>
                                <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
                                  Vulnerability & Defense Analysis
                                </div>
                                <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-2.5 text-xs leading-relaxed text-zinc-300">
                                  {item.defense_analysis}
                                </div>
                              </div>

                              <div>
                                <div className="mb-1 flex items-center justify-between text-[11px] font-semibold uppercase tracking-wider text-emerald-400">
                                  <span>Recommended Guardrail Mitigation</span>
                                  {item.mitigation_applied ? (
                                    <span className="text-[10px] text-emerald-300">✓ Mitigation Deployed</span>
                                  ) : (
                                    <span className="text-[10px] text-amber-400">Action Required</span>
                                  )}
                                </div>
                                <div className="rounded border border-emerald-900/50 bg-emerald-950/20 p-2.5 text-xs font-medium text-emerald-200">
                                  {item.recommended_guardrail}
                                </div>
                              </div>
                            </div>
                          </div>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
