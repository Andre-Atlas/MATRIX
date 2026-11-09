import { Skeleton } from "@/components/ui";

export default function Loading() {
  return (
    <div role="status" aria-label="Loading" className="max-w-6xl space-y-4">
      <Skeleton className="h-6 w-40" />
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-[68px]" />
        ))}
      </div>
      <Skeleton className="h-48 w-full" />
      <span className="sr-only">Loading…</span>
    </div>
  );
}
