import { Skeleton } from "@/components/ui";

export default function Loading() {
  return (
    <div className="max-w-4xl space-y-3" role="status" aria-label="Loading">
      <Skeleton className="h-6 w-40" />
      <Skeleton className="h-8 w-80" />
      <Skeleton className="h-64 w-full" />
      <span className="sr-only">Loading…</span>
    </div>
  );
}
