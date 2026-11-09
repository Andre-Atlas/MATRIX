import Link from "next/link";
import { EmptyState } from "@/components/ui";

export default function NotFound() {
  return (
    <EmptyState title="Not found">
      This trace or session does not exist in the current project.{" "}
      <Link href="/traces" className="text-emerald-300 underline-offset-2 hover:underline">
        Back to traces
      </Link>
    </EmptyState>
  );
}
