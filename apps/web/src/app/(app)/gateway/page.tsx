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

  try {
    const [statsData, providersData, logsData, rulesData] = await Promise.all([
      apiGet<GatewayStatsResponse>("/api/v1/gateway/stats"),
      apiGet<GatewayProvidersResponse>("/api/v1/gateway/providers"),
      apiGet<GatewayLogsResponse>("/api/v1/gateway/logs"),
      apiGet<GatewayRulesResponse>("/api/v1/gateway/rules").catch(() => ({
        rules: MOCK_GATEWAY_ROUTING_RULES,
        total: MOCK_GATEWAY_ROUTING_RULES.length,
      })),
    ]);

    if (statsData) stats = statsData;
    if (providersData?.providers) providers = providersData.providers;
    if (logsData?.logs) logs = logsData.logs;
    if (rulesData?.rules) rules = rulesData.rules;
  } catch (error) {
    // If running in an environment without the gateway backend running,
    // gracefully fall back to initialized mock fixtures.
    console.warn("Using fallback mock data for Gateway Page:", error);
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="AI Gateway & Proxy de Inferência"
        subtitle="Roteamento dinâmico multi-provedor, Circuit Breakers automáticos, cache semântico de embeddings e fallback resiliente."
      />

      <GatewayDashboard
        stats={stats}
        providers={providers}
        rules={rules}
        logs={logs}
      />
    </div>
  );
}
