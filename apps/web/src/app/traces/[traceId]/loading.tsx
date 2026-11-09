import { Skeleton } from "@/components/ui";

export default function Loading() {
  return (
    <div role="status" aria-label="Loading trace" className="space-y-4">
      <Skeleton className="h-4 w-48" />
      <Skeleton className="h-8 w-2/3" />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(400px,38%)]">
        <div className="space-y-1.5">
          {Array.from({ length: 14 }, (_, i) => (
            <Skeleton key={i} className="h-5" />
          ))}
        </div>
        <Skeleton className="h-96" />
      </div>
      <span className="sr-only">Loading…</span>
    </div>
  );
}
