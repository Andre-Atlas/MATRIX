import { PageHeader } from "@/components/ui";
import { SafetyDashboard } from "@/components/safety/safety-dashboard";
import { apiGet } from "@/lib/api";
import { MOCK_FINDINGS } from "@/lib/mock-data";
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

  const firstScanId = scansData.scans[0]?.id;
  let initialReport: SafetyScanReport | null = null;

  if (firstScanId) {
    try {
      initialReport = await apiGet<SafetyScanReport>(
        `/api/v1/safety/scans/${encodeURIComponent(firstScanId)}`,
      );
    } catch {
      // Fallback if detail fetch fails
      initialReport = null;
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Safety & OWASP Top 10 Compliance"
        subtitle="Auditorias defensivas contínuas contra injeção de prompts, vazamento de contexto, consumo excessivo e agência desregulada em LLMs."
      />
      <SafetyDashboard
        categories={categoriesData.categories}
        scans={scansData.scans}
        initialReport={initialReport}
        allFindings={MOCK_FINDINGS}
      />
    </div>
  );
}
