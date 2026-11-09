import Link from "next/link";
import { apiGet } from "@/lib/api";
import type { Dataset, ExperimentListItem, ListResponse } from "@/lib/types";
import { EmptyState, PageHeader } from "@/components/ui";
import { NewExperimentForm } from "@/components/evals/new-experiment-form";

export const dynamic = "force-dynamic";

export default async function NewExperimentPage({ searchParams }: { searchParams: Promise<{ dataset?: string }> }) {
  const sp = await searchParams;
  const [datasets, experiments] = await Promise.all([
    apiGet<ListResponse<Dataset>>("/api/v1/datasets"),
    apiGet<ListResponse<ExperimentListItem>>("/api/v1/experiments"),
  ]);
  const initial = datasets.items.find((d) => d.id === sp.dataset)?.id ?? null;

  return (
    <div>
      <nav aria-label="Breadcrumb" className="mb-1 text-xs text-zinc-500">
        <Link href="/experiments" className="hover:text-zinc-200">
          Experiments
        </Link>{" "}
        / new
      </nav>
      <PageHeader title="New experiment" subtitle="Run every variant on every dataset item, then score each output" />
      {datasets.items.length === 0 ? (
        <EmptyState title="Create a dataset first">
          Experiments run over a dataset.{" "}
          <Link href="/datasets" className="text-emerald-300 hover:underline">
            Go to datasets
          </Link>
          .
        </EmptyState>
      ) : (
        <NewExperimentForm datasets={datasets.items} experiments={experiments.items} initialDataset={initial} />
      )}
    </div>
  );
}
