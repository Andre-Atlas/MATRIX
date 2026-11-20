import { PageHeader } from "@/components/ui";
import { GatewayDashboard } from "@/components/gateway/gateway-dashboard";
import { apiGet } from "@/lib/api";
import {
  MOCK_GATEWAY_LOGS,
  MOCK_GATEWAY_PROVIDERS,
  MOCK_GATEWAY_ROUTING_RULES,
  MOCK_GATEWAY_STATS,
} from "@/lib/mock-data";
import type {
  GatewayLogsResponse,
  GatewayProvidersResponse,
  GatewayRulesResponse,
  GatewayStatsResponse,
} from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function GatewayPage() {
  let stats = MOCK_GATEWAY_STATS;
  let providers = MOCK_GATEWAY_PROVIDERS;
  let logs = MOCK_GATEWAY_LOGS;
  let rules = MOCK_GATEWAY_ROUTING_RULES;
  let isSimulated = process.env.MATRIX_MOCK === "1";

  try {
    const [statsData, providersData, logsData, rulesData] = await Promise.all([
      apiGet<GatewayStatsResponse>("/api/v1/gateway/stats"),
      apiGet<GatewayProvidersResponse>("/api/v1/gateway/providers"),
      apiGet<GatewayLogsResponse>("/api/v1/gateway/logs"),
      apiGet<GatewayRulesResponse>("/api/v1/gateway/rules").catch(() => {
        isSimulated = true;
        return {
          rules: MOCK_GATEWAY_ROUTING_RULES,
          total: MOCK_GATEWAY_ROUTING_RULES.length,
        };
      }),
    ]);

    if (statsData) stats = statsData;
    if (providersData?.providers) providers = providersData.providers;
    if (logsData?.logs) logs = logsData.logs;
    if (rulesData?.rules) rules = rulesData.rules;
  } catch (error) {
    // Graceful mock fallback when backend gateway routes are unavailable.
    console.warn("Using fallback mock data for Gateway Page:", error);
    isSimulated = true;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="AI Gateway & Inference Proxy"
        subtitle="Multi-provider dynamic routing, automatic circuit breakers, semantic embedding cache, and resilient fallbacks."
      />

      <GatewayDashboard
        stats={stats}
        providers={providers}
        rules={rules}
        logs={logs}
        isSimulated={isSimulated}
      />
    </div>
  );
}
