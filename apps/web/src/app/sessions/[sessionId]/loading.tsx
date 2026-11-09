import { Skeleton } from "@/components/ui";

export default function Loading() {
  return (
    <div role="status" aria-label="Loading session" className="max-w-4xl space-y-4">
      <Skeleton className="h-8 w-2/3" />
      {Array.from({ length: 4 }, (_, i) => (
        <div key={i} className="space-y-1.5">
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-12" />
          <Skeleton className="h-16" />
        </div>
      ))}
      <span className="sr-only">Loading…</span>
    </div>
  );
}
