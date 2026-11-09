import { Skeleton, TableSkeleton } from "@/components/ui";

export default function Loading() {
  return (
    <div className="space-y-3">
      <Skeleton className="h-6 w-32" />
      <Skeleton className="h-12 w-[640px] max-w-full" />
      <TableSkeleton rows={12} />
    </div>
  );
}
