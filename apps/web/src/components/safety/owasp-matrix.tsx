"use client";

import { formatComplianceStatus } from "@/lib/safety";
import type { SafetyCategory, SafetyCategoryScore } from "@/lib/types";

export interface OwaspMatrixProps {
  categories: SafetyCategory[];
  scores: SafetyCategoryScore[];
  selectedCategoryCode?: string;
  onSelectCategory?: (categoryCode: string) => void;
}

export function OwaspMatrix({
  categories,
  scores,
  selectedCategoryCode,
  onSelectCategory,
}: OwaspMatrixProps) {
  const scoresByCode = new Map(scores.map((s) => [s.category_code, s]));

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold tracking-wide text-zinc-100 uppercase">
            OWASP Top 10 for LLMs — Compliance Matrix
          </h2>
          <p className="text-xs text-zinc-400">
            Click any category card to filter security findings and probe logs.
          </p>
        </div>
        {selectedCategoryCode && selectedCategoryCode !== "all" && (
          <button
            type="button"
            onClick={() => onSelectCategory?.("all")}
            className="text-xs text-emerald-400 hover:text-emerald-300 hover:underline"
          >
            Clear category filter
          </button>
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {categories.map((cat) => {
          const score = scoresByCode.get(cat.code);
          const passRate = score ? Math.round(score.pass_rate * 100) : 100;
          const status = score ? score.status : "compliant";
          const statusMeta = formatComplianceStatus(status);
          const isSelected = selectedCategoryCode === cat.code;

          return (
            <button
              key={cat.id}
              type="button"
              onClick={() => {
                if (isSelected) {
                  onSelectCategory?.("all");
                } else {
                  onSelectCategory?.(cat.code);
                }
              }}
              className={`group flex flex-col justify-between rounded-lg border p-3 text-left transition-all duration-200 ${
                isSelected
                  ? "border-emerald-500/80 bg-zinc-900 shadow-md ring-1 ring-emerald-500/40"
                  : "border-zinc-800/80 bg-zinc-900/40 hover:border-zinc-700 hover:bg-zinc-900/80"
              }`}
            >
              {/* Card Header: Code & Compliance Badge */}
              <div>
                <div className="flex items-center justify-between gap-1.5">
                  <span className="font-mono text-xs font-bold text-zinc-300 group-hover:text-emerald-300">
                    {cat.code}
                  </span>
                  <span
                    className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium leading-none ${statusMeta.badge}`}
                  >
                    <span className={`h-1.5 w-1.5 rounded-full ${statusMeta.dot}`} aria-hidden />
                    {statusMeta.label}
                  </span>
                </div>

                {/* Category Title */}
                <h3 className="mt-2 line-clamp-1 text-xs font-semibold text-zinc-100" title={cat.name}>
                  {cat.name}
                </h3>

                {/* Short description */}
                <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-zinc-400">
                  {cat.short_description}
                </p>
              </div>

              {/* Card Footer: Pass Rate & Findings */}
              <div className="mt-3 border-t border-zinc-800/60 pt-2.5">
                <div className="flex items-center justify-between text-[11px]">
                  <span className="text-zinc-400">Pass Rate:</span>
                  <span className="font-mono font-medium text-zinc-200">
                    {passRate}%
                  </span>
                </div>

                {/* Pass rate mini progress bar */}
                <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-zinc-800">
                  <div
                    className={`h-full transition-all duration-500 ${
                      passRate >= 85
                        ? "bg-emerald-500"
                        : passRate >= 60
                          ? "bg-amber-500"
                          : "bg-red-500"
                    }`}
                    style={{ width: `${passRate}%` }}
                  />
                </div>

                <div className="mt-2 flex items-center justify-between text-[10px] text-zinc-400">
                  <span>
                    Probes: <strong className="text-zinc-300">{score ? `${score.probes_passed}/${score.probes_tested}` : `${cat.probes_count}/${cat.probes_count}`}</strong>
                  </span>
                  <span>
                    {score && score.findings_count > 0 ? (
                      <span className="font-semibold text-red-400">
                        {score.findings_count} {score.findings_count === 1 ? "issue" : "issues"}
                      </span>
                    ) : (
                      <span className="text-emerald-400">0 issues</span>
                    )}
                  </span>
                </div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
