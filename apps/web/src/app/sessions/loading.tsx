import { Skeleton, TableSkeleton } from "@/components/ui";

export default function Loading() {
  return (
    <div className="max-w-6xl space-y-3">
      <Skeleton className="h-6 w-32" />
      <TableSkeleton rows={8} />
    </div>
  );
}
