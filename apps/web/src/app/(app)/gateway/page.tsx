import { PageHeader } from "@/components/ui";
import { GatewayDashboard } from "@/components/gateway/gateway-dashboard";
import { apiGet } from "@/lib/api";
import type {
  GatewayLogsResponse,
  GatewayProvidersResponse,
  GatewayRulesResponse,
  GatewayStatsResponse,
} from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function GatewayPage() {
  const isSimulated = process.env.MATRIX_MOCK === "1";

  const [statsData, providersData, logsData, rulesData] = await Promise.all([
    apiGet<GatewayStatsResponse>("/api/v1/gateway/stats"),
    apiGet<GatewayProvidersResponse>("/api/v1/gateway/providers"),
    apiGet<GatewayLogsResponse>("/api/v1/gateway/logs"),
    apiGet<GatewayRulesResponse>("/api/v1/gateway/rules"),
  ]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="AI Gateway & Inference Proxy"
        subtitle="Multi-provider dynamic routing, automatic circuit breakers, semantic embedding cache, and resilient fallbacks."
      />

      <GatewayDashboard
        stats={statsData}
        providers={providersData?.providers ?? []}
        rules={rulesData?.rules ?? []}
        logs={logsData?.logs ?? []}
        isSimulated={isSimulated}
      />
    </div>
  );
}
