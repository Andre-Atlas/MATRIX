import { SandboxesClient } from "@/components/sandboxes/sandboxes-client";
import { apiGet } from "@/lib/api";
import type { ListResponse, SandboxRun, SandboxTaskTranscript } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function SandboxesPage({
  searchParams,
}: {
  searchParams?: Promise<{ run?: string; task?: string }>;
}) {
  const sp = searchParams ? await searchParams : {};
  let initialRuns: SandboxRun[] = [];
  let initialError: string | null = null;

  try {
    const runsResp = await apiGet<ListResponse<SandboxRun> | SandboxRun[]>("/api/v1/sandboxes/runs");
    initialRuns = Array.isArray(runsResp) ? runsResp : runsResp?.items ?? [];
  } catch (err) {
    initialError =
      err instanceof Error ? err.message : "Could not load sandbox runs.";
  }

  const requestedRunId = sp?.run?.trim();
  const initialSelectedRunId =
    requestedRunId && initialRuns.some((r) => r.id === requestedRunId)
      ? requestedRunId
      : initialRuns[0]?.id ?? null;

  let initialTranscripts: SandboxTaskTranscript[] = [];
  if (initialSelectedRunId) {
    try {
      const trResp = await apiGet<
        ListResponse<SandboxTaskTranscript> | SandboxTaskTranscript[]
      >(`/api/v1/sandboxes/runs/${encodeURIComponent(initialSelectedRunId)}/transcripts`);
      initialTranscripts = Array.isArray(trResp) ? trResp : trResp?.items ?? [];
    } catch {
      // transcripts optional on initial load
    }
  }

  return (
    <SandboxesClient
      initialRuns={initialRuns}
      initialTranscripts={initialTranscripts}
      initialSelectedRunId={initialSelectedRunId}
      initialError={initialError}
      initialTaskKey={sp?.task}
    />
  );
}
