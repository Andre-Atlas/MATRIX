import { unstable_rethrow } from "next/navigation";
import { PageHeader } from "@/components/ui";
import { SafetyDashboard } from "@/components/safety/safety-dashboard";
import { apiGet } from "@/lib/api";
import { adaptSafetyData } from "@/lib/safety";
import type {
  SafetyCategoriesResponse,
  SafetyScanReport,
  SafetyScansResponse,
} from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function SafetyPage() {
  const [categoriesData, scansData] = await Promise.all([
    apiGet<SafetyCategoriesResponse>("/api/v1/safety/categories"),
    apiGet<SafetyScansResponse>("/api/v1/safety/scans"),
  ]);

  const firstScan = scansData.scans?.[0];
  const firstScanId = firstScan?.id ?? (firstScan as any)?.scan_id;
  let rawReport: SafetyScanReport | null = null;
  let initialError: string | null = null;

  if (firstScanId) {
    try {
      rawReport = await apiGet<SafetyScanReport>(
        `/api/v1/safety/scans/${encodeURIComponent(firstScanId)}`,
      );
    } catch (err) {
      unstable_rethrow(err);
      rawReport = null;
      initialError =
        err instanceof Error
          ? `Could not load safety scan details: ${err.message}`
          : "Could not load safety scan details.";
    }
  }

  const adapted = adaptSafetyData(
    categoriesData.categories,
    scansData.scans,
    rawReport,
  );

  const allFindings = adapted.findings;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Safety & OWASP Top 10 Compliance"
        subtitle="Auditorias defensivas contínuas contra injeção de prompts, vazamento de contexto, consumo excessivo e agência desregulada em LLMs."
      />
      <SafetyDashboard
        categories={adapted.categories}
        scans={adapted.scans}
        initialReport={adapted.initialReport}
        allFindings={allFindings}
        initialError={initialError}
      />
    </div>
  );
}
