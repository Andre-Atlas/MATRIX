import { apiGet } from "@/lib/api";
import type { VectorDriftResponse, VectorProjectionResponse } from "@/lib/types";
import { PageHeader } from "@/components/ui";
import { VectorsDashboard } from "@/components/vectors/vectors-dashboard";

export const dynamic = "force-dynamic";

export default async function VectorsPage() {
  const [projection, drift] = await Promise.all([
    apiGet<VectorProjectionResponse>("/api/v1/vectors/projection"),
    apiGet<VectorDriftResponse>("/api/v1/vectors/drift"),
  ]);

  return (
    <div className="space-y-4">
      <PageHeader
        title="Vector Space & Drift Observatory"
        subtitle="Projeção interativa 2D/3D de embeddings de consultas vs documentos, assertividade de clusters, gaps de conhecimento e métricas de drift estatístico."
      />
      <VectorsDashboard projection={projection} drift={drift} />
    </div>
  );
}
