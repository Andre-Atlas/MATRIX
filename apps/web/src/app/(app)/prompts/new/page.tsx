import { apiGet, isNotFound } from "@/lib/api";
import { promptPath } from "@/lib/prompt-template";
import type { PromptVersion } from "@/lib/types";
import { PageHeader } from "@/components/ui";
import { PromptForm } from "@/components/prompts/prompt-form";

export const dynamic = "force-dynamic";

export default async function NewPromptPage({ searchParams }: { searchParams: Promise<{ from?: string; version?: string }> }) {
  const sp = await searchParams;
  let base: PromptVersion | null = null;
  if (sp.from) {
    try {
      base = await apiGet<PromptVersion>(`/api/v1/prompts/${promptPath(sp.from)}`, { version: sp.version });
    } catch (e) {
      if (!isNotFound(e)) throw e;
    }
  }
  return (
    <div className="max-w-4xl">
      <PageHeader
        title={base ? `New version of ${base.name}` : "New prompt"}
        subtitle={base ? `Starts from v${base.version}. Saving creates a new version; earlier versions never change` : "Creates version 1"}
      />
      <PromptForm base={base} />
    </div>
  );
}
