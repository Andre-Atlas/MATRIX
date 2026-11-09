"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { proxy } from "@/lib/proxy-client";
import type { Dataset } from "@/lib/types";
import { field, label, primaryBtn } from "./ui";

export function CreateDatasetForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const d = await proxy<Dataset>("POST", "datasets", {
        name: name.trim(),
        ...(description.trim() ? { description: description.trim() } : {}),
      });
      router.push(`/datasets/${encodeURIComponent(d.id)}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} aria-label="Create dataset" className="mb-4 flex flex-wrap items-end gap-2">
      <label className={label}>
        Name
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          pattern=".*\S.*"
          placeholder="rag-golden"
          className={`${field} w-48 font-mono`}
        />
      </label>
      <label className={label}>
        Description
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="optional"
          className={`${field} w-80`}
        />
      </label>
      <button type="submit" disabled={busy || !name.trim()} className={primaryBtn}>
        {busy ? "Creating…" : "Create dataset"}
      </button>
      {error && (
        <p role="alert" className="h-7 rounded border border-red-900/70 bg-red-950/40 px-2 leading-7 text-red-200">
          {error}
        </p>
      )}
    </form>
  );
}
