"use client";

import { formatRiskLevel } from "@/lib/safety";
import type { RiskLevel, SafetyScanSummary } from "@/lib/types";

export interface SafetyGaugeProps {
  score: number;
  riskLevel: RiskLevel;
  summary: SafetyScanSummary;
  scanName?: string;
  targetModel?: string;
}

export function SafetyGauge({
  score,
  riskLevel,
  summary,
  scanName,
  targetModel,
}: SafetyGaugeProps) {
  const riskInfo = formatRiskLevel(riskLevel);

  // SVG Gauge calculations (semi-circular / 240 degree arc)
  const radius = 68;
  const circumference = 2 * Math.PI * radius;
  // Use a 240-degree gauge (from -210 deg to 30 deg)
  const arcLength = circumference * (240 / 360);
  const strokeOffset = arcLength - (arcLength * Math.max(0, Math.min(100, score))) / 100;

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5 shadow-sm">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
        {/* Left: Speedometer Gauge */}
        <div className="flex flex-col items-center sm:flex-row sm:items-center sm:gap-6">
          <div className="relative flex h-36 w-36 items-center justify-center">
            <svg
              className="h-36 w-36 -rotate-90 transform"
              viewBox="0 0 160 160"
              role="meter"
              aria-label="Global Safety Score"
              aria-valuenow={score}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              {/* Background circle track */}
              <circle
                cx="80"
                cy="80"
                r={radius}
                className="stroke-zinc-800"
                strokeWidth="12"
                fill="transparent"
                strokeDasharray={`${arcLength} ${circumference}`}
                strokeLinecap="round"
                transform="rotate(150 80 80)"
              />
              {/* Active colored score arc */}
              <circle
                cx="80"
                cy="80"
                r={radius}
                stroke={riskInfo.stroke}
                strokeWidth="12"
                fill="transparent"
                strokeDasharray={`${arcLength} ${circumference}`}
                strokeDashoffset={strokeOffset}
                strokeLinecap="round"
                transform="rotate(150 80 80)"
                className="transition-all duration-700 ease-out"
              />
            </svg>

            {/* Inner Content */}
            <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
              <span className="font-mono text-3xl font-extrabold tracking-tight text-zinc-100">
                {score}
                <span className="text-lg font-medium text-zinc-400">%</span>
              </span>
              <span className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400">
                Safety Score
              </span>
            </div>
          </div>

          <div className="mt-3 text-center sm:mt-0 sm:text-left">
            <div className="flex items-center justify-center gap-2 sm:justify-start">
              <span
                className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${riskInfo.badge}`}
              >
                <span
                  className="mr-1.5 h-1.5 w-1.5 rounded-full"
                  style={{ backgroundColor: riskInfo.stroke }}
                  aria-hidden
                />
                {riskInfo.label}
              </span>
              <span className="text-xs text-zinc-400">
                OWASP LLM 2025
              </span>
            </div>

            {scanName && (
              <h2 className="mt-2 text-base font-medium text-zinc-100">
                {scanName}
              </h2>
            )}

            {targetModel && (
              <div className="mt-0.5 font-mono text-xs text-zinc-400">
                Target: <span className="text-zinc-300">{targetModel}</span>
              </div>
            )}

            <div className="mt-2 flex items-center justify-center gap-4 text-xs sm:justify-start">
              <div>
                <span className="text-zinc-400">Compliance: </span>
                <span className="font-mono font-semibold text-zinc-200">
                  {summary.compliance_rate}%
                </span>
              </div>
              <span className="text-zinc-700">•</span>
              <div>
                <span className="text-zinc-400">Evaluated: </span>
                <span className="font-mono font-semibold text-zinc-200">
                  {summary.total_probes} probes
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Right: Metrics & Findings Breakdown */}
        <div className="flex flex-col justify-center border-t border-zinc-800/80 pt-4 sm:flex-row sm:gap-6 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
          {/* Probes Pass/Fail */}
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-medium uppercase tracking-wider text-zinc-400">
              Probes Results
            </span>
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-emerald-400" aria-hidden />
                <span className="text-xs text-zinc-400">Passed:</span>
                <span className="font-mono text-sm font-semibold text-emerald-400">
                  {summary.passed}
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-red-400" aria-hidden />
                <span className="text-xs text-zinc-400">Failed:</span>
                <span className="font-mono text-sm font-semibold text-red-400">
                  {summary.failed}
                </span>
              </div>
            </div>

            {/* Compliance progress bar */}
            <div className="mt-1 h-1.5 w-full min-w-[160px] overflow-hidden rounded-full bg-zinc-800">
              <div
                className="h-full bg-emerald-500 transition-all duration-500"
                style={{ width: `${summary.compliance_rate}%` }}
              />
            </div>
          </div>

          {/* Severity Counters */}
          <div className="mt-3 flex flex-col gap-1.5 sm:mt-0">
            <span className="text-xs font-medium uppercase tracking-wider text-zinc-400">
              Detected Vulnerabilities
            </span>
            <div className="flex flex-wrap items-center gap-2">
              <span
                title="Critical Findings"
                className="inline-flex items-center gap-1 rounded border border-red-800/60 bg-red-950/40 px-2 py-0.5 text-xs font-mono text-red-300"
              >
                <span>Critical:</span>
                <span className="font-bold">{summary.critical_count}</span>
              </span>
              <span
                title="High Findings"
                className="inline-flex items-center gap-1 rounded border border-orange-800/60 bg-orange-950/40 px-2 py-0.5 text-xs font-mono text-orange-300"
              >
                <span>High:</span>
                <span className="font-bold">{summary.high_count}</span>
              </span>
              <span
                title="Medium Findings"
                className="inline-flex items-center gap-1 rounded border border-amber-800/60 bg-amber-950/40 px-2 py-0.5 text-xs font-mono text-amber-300"
              >
                <span>Med:</span>
                <span className="font-bold">{summary.medium_count}</span>
              </span>
              <span
                title="Low Findings"
                className="inline-flex items-center gap-1 rounded border border-blue-800/60 bg-blue-950/40 px-2 py-0.5 text-xs font-mono text-blue-300"
              >
                <span>Low:</span>
                <span className="font-bold">{summary.low_count}</span>
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
