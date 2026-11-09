import { Skeleton } from "@/components/ui";

export default function Loading() {
  return (
    <div role="status" aria-label="Loading" className="space-y-3">
      <Skeleton className="h-6 w-64" />
      <div className="grid gap-4 xl:grid-cols-2">
        <Skeleton className="h-[480px] w-full" />
        <Skeleton className="h-[480px] w-full" />
      </div>
      <span className="sr-only">Loading…</span>
    </div>
  );
}
