"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { useCan } from "@/components/auth/role-context";
import { EmptyState } from "@/components/ui";
import { formatTimestamp } from "@/lib/format";
import { PRIMARY_BTN_CLASS } from "@/lib/mlops-tokens";
import { explain, proxy } from "@/lib/proxy-client";
import type { ModelWebhook, ModelWebhookEvent, WebhookDelivery } from "@/lib/types";

export interface WebhooksTabProps {
  modelId: string;
}

const ALL_EVENTS: { id: ModelWebhookEvent; label: string }[] = [
  { id: "version.created", label: "version.created" },
  { id: "stage.changed", label: "stage.changed" },
  { id: "alias.moved", label: "alias.moved" },
];

export function WebhooksTab({ modelId }: WebhooksTabProps) {
  const isAdmin = useCan("admin");
  const [webhooks, setWebhooks] = useState<ModelWebhook[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Create form state
  const [url, setUrl] = useState("");
  const [selectedEvents, setSelectedEvents] = useState<ModelWebhookEvent[]>([
    "stage.changed",
    "alias.moved",
  ]);
  const [creating, setCreating] = useState(false);

  // One-time secret banner state
  const [oneTimeSecret, setOneTimeSecret] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // Deliveries drawer/table state
  const [selectedWebhookId, setSelectedWebhookId] = useState<string | null>(null);
  const [deliveries, setDeliveries] = useState<WebhookDelivery[]>([]);
  const [loadingDeliveries, setLoadingDeliveries] = useState(false);
  const webhooksReqRef = useRef(0);
  const deliveriesReqRef = useRef(0);

  const loadWebhooks = useCallback(async () => {
    if (!isAdmin) return;
    const reqId = ++webhooksReqRef.current;
    setLoading(true);
    setError(null);
    try {
      const resp = await proxy<{ items: ModelWebhook[] }>(
        "GET",
        `models/${encodeURIComponent(modelId)}/webhooks`,
      );
      if (webhooksReqRef.current !== reqId) return;
      setWebhooks(Array.isArray(resp?.items) ? resp.items : []);
    } catch (err) {
      if (webhooksReqRef.current === reqId) setError(explain(err));
    } finally {
      if (webhooksReqRef.current === reqId) setLoading(false);
    }
  }, [modelId, isAdmin]);

  useEffect(() => {
    setOneTimeSecret(null);
    setSelectedWebhookId(null);
    setDeliveries([]);
    void loadWebhooks();
  }, [loadWebhooks]);

  const loadDeliveries = async (webhookId: string) => {
    const reqId = ++deliveriesReqRef.current;
    setSelectedWebhookId(webhookId);
    setLoadingDeliveries(true);
    setError(null);
    try {
      const resp = await proxy<{ items: WebhookDelivery[] }>(
        "GET",
        `models/${encodeURIComponent(modelId)}/webhooks/${encodeURIComponent(webhookId)}/deliveries`,
      );
      if (deliveriesReqRef.current !== reqId) return;
      setDeliveries(Array.isArray(resp?.items) ? resp.items : []);
    } catch (err) {
      if (deliveriesReqRef.current === reqId) setError(explain(err));
    } finally {
      if (deliveriesReqRef.current === reqId) setLoadingDeliveries(false);
    }
  };

  const toggleEvent = (ev: ModelWebhookEvent) => {
    setSelectedEvents((prev) =>
      prev.includes(ev) ? prev.filter((x) => x !== ev) : [...prev, ev],
    );
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim() || selectedEvents.length === 0) {
      setError("Provide a valid HTTPS webhook URL and select at least one event.");
      return;
    }
    setCreating(true);
    setError(null);
    setOneTimeSecret(null);
    setCopied(false);
    try {
      const created = await proxy<ModelWebhook>(
        "POST",
        `models/${encodeURIComponent(modelId)}/webhooks`,
        {
          url: url.trim(),
          events: selectedEvents,
        },
      );
      if (created.secret) {
        setOneTimeSecret(created.secret);
      }
      setWebhooks((prev) => [created, ...prev]);
      setUrl("");
    } catch (err) {
      setError(explain(err));
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (webhookId: string) => {
    setError(null);
    try {
      await proxy(
        "DELETE",
        `models/${encodeURIComponent(modelId)}/webhooks/${encodeURIComponent(webhookId)}`,
      );
      setWebhooks((prev) => prev.filter((w) => w.id !== webhookId));
      if (selectedWebhookId === webhookId) {
        setSelectedWebhookId(null);
        setDeliveries([]);
      }
    } catch (err) {
      setError(explain(err));
    }
  };

  const handleCopySecret = async () => {
    if (!oneTimeSecret) return;
    try {
      await navigator.clipboard.writeText(oneTimeSecret);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  if (!isAdmin) {
    return (
      <div className="p-6">
        <EmptyState title="Admin role required">
          <p className="text-xs text-zinc-400">
            CI/CD webhook configuration and signing secrets are restricted to project administrators.
          </p>
        </EmptyState>
      </div>
    );
  }

  return (
    <div className="space-y-5 p-4 text-xs">
      <div>
        <h3 className="text-sm font-semibold text-zinc-100">CI/CD Webhooks</h3>
        <p className="text-xs text-zinc-400">
          Deliver signed HMAC-SHA256 notifications (<code className="font-mono text-zinc-200">X-Matrix-Signature</code>) via the project egress allowlist.
        </p>
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-lg border border-red-800/70 bg-red-950/40 p-3 text-xs text-red-200"
        >
          {error}
        </div>
      )}

      {/* One-time secret banner */}
      {oneTimeSecret && (
        <div
          role="alert"
          className="space-y-2 rounded-lg border border-amber-600/80 bg-amber-950/50 p-4 text-amber-100"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="font-semibold text-amber-200">
              Signing secret generated — copy it now (shown only once)
            </span>
            <button
              type="button"
              onClick={() => setOneTimeSecret(null)}
              className="rounded px-2 py-0.5 text-amber-200 hover:bg-amber-900/60"
            >
              Dismiss
            </button>
          </div>
          <p className="text-[11px] text-amber-200">
            This secret is encrypted at rest and will never be returned again by the API.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <code
              data-testid="webhook-one-time-secret"
              className="rounded border border-amber-700/80 bg-zinc-950 px-2.5 py-1.5 font-mono text-xs text-amber-200 select-all"
            >
              {oneTimeSecret}
            </code>
            <button
              type="button"
              onClick={handleCopySecret}
              className="rounded border border-amber-600 bg-amber-900/60 px-2.5 py-1.5 font-medium text-amber-100 hover:bg-amber-800/70"
            >
              {copied ? "Copied" : "Copy secret"}
            </button>
          </div>
        </div>
      )}

      {/* Create Webhook Form */}
      <form
        onSubmit={handleCreate}
        className="space-y-3 rounded-lg border border-zinc-800 bg-zinc-950/60 p-4"
      >
        <div className="font-semibold text-zinc-200">Add Webhook Endpoint</div>
        <div>
          <label htmlFor="webhook-url" className="block text-zinc-300">
            Endpoint URL (must match project egress allowlist)
          </label>
          <input
            id="webhook-url"
            type="url"
            required
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://ci.example.com/hooks/matrix"
            className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 font-mono text-zinc-100 placeholder:text-zinc-400 focus:border-emerald-500 focus:outline-none"
          />
        </div>
        <fieldset className="space-y-1.5">
          <legend className="text-zinc-300">Subscribed Events</legend>
          <div className="flex flex-wrap gap-3">
            {ALL_EVENTS.map((ev) => (
              <label key={ev.id} className="inline-flex items-center gap-1.5 font-mono text-zinc-200">
                <input
                  type="checkbox"
                  checked={selectedEvents.includes(ev.id)}
                  onChange={() => toggleEvent(ev.id)}
                  className="rounded border-zinc-700 bg-zinc-900 text-emerald-500"
                />
                {ev.label}
              </label>
            ))}
          </div>
        </fieldset>
        <button
          type="submit"
          disabled={creating}
          className={`rounded px-3.5 py-1.5 font-semibold disabled:opacity-50 ${PRIMARY_BTN_CLASS}`}
        >
          {creating ? "Creating..." : "Create Webhook"}
        </button>
      </form>

      {/* Webhooks List */}
      {loading ? (
        <div role="status" className="text-zinc-400">
          Loading webhooks…
        </div>
      ) : webhooks.length === 0 ? (
        <EmptyState title="No webhooks configured" />
      ) : (
        <div className="overflow-x-auto rounded-lg border border-zinc-800">
          <table className="w-full border-collapse text-left text-xs">
            <thead>
              <tr className="border-b border-zinc-800 bg-zinc-950/60 text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
                <th scope="col" className="px-3 py-2">
                  Endpoint URL
                </th>
                <th scope="col" className="px-3 py-2">
                  Events
                </th>
                <th scope="col" className="px-3 py-2">
                  Created
                </th>
                <th scope="col" className="px-3 py-2 text-right">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {webhooks.map((wh) => (
                <tr key={wh.id} className="hover:bg-zinc-900/60">
                  <td className="px-3 py-2 font-mono text-zinc-100">{wh.url}</td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {wh.events.map((ev) => (
                        <span
                          key={ev}
                          className="rounded border border-zinc-700 bg-zinc-900 px-1.5 py-0.5 font-mono text-[10px] text-zinc-200"
                        >
                          {ev}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-3 py-2 font-mono text-[11px] text-zinc-400">
                    {formatTimestamp(wh.created_at)}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <div className="inline-flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => void loadDeliveries(wh.id)}
                        className="rounded border border-zinc-700 bg-zinc-900 px-2 py-0.5 text-[11px] text-zinc-200 hover:bg-zinc-800"
                      >
                        Deliveries
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleDelete(wh.id)}
                        className="rounded border border-red-800/80 bg-red-950/50 px-2 py-0.5 text-[11px] text-red-200 hover:bg-red-900/60"
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Deliveries Table */}
      {selectedWebhookId && (
        <div className="space-y-2 rounded-lg border border-zinc-800 bg-zinc-950/60 p-4">
          <div className="flex items-center justify-between">
            <h4 className="font-semibold text-zinc-100">
              Delivery Attempts ({deliveries.length})
            </h4>
            <button
              type="button"
              onClick={() => void loadDeliveries(selectedWebhookId)}
              className="text-[11px] text-emerald-300 hover:underline"
            >
              Refresh
            </button>
          </div>
          {loadingDeliveries ? (
            <div role="status" className="text-zinc-400">
              Loading deliveries…
            </div>
          ) : deliveries.length === 0 ? (
            <p className="text-zinc-400">No deliveries recorded for this webhook yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left text-xs font-mono">
                <thead>
                  <tr className="border-b border-zinc-800 text-[11px] uppercase tracking-wider text-zinc-400">
                    <th scope="col" className="px-2.5 py-2">
                      Event
                    </th>
                    <th scope="col" className="px-2.5 py-2">
                      Status
                    </th>
                    <th scope="col" className="px-2.5 py-2 text-right">
                      Attempts
                    </th>
                    <th scope="col" className="px-2.5 py-2 text-right">
                      HTTP
                    </th>
                    <th scope="col" className="px-2.5 py-2">
                      Last Error
                    </th>
                    <th scope="col" className="px-2.5 py-2">
                      Created
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/70">
                  {deliveries.map((d) => (
                    <tr key={d.id}>
                      <td className="px-2.5 py-1.5 text-zinc-200">{d.event}</td>
                      <td className="px-2.5 py-1.5">
                        <span
                          className={`rounded border px-1.5 py-0.5 text-[10px] uppercase ${
                            d.status === "delivered"
                              ? "border-emerald-700/80 bg-emerald-950/60 text-emerald-200"
                              : d.status === "failed"
                                ? "border-red-700/80 bg-red-950/60 text-red-200"
                                : "border-amber-700/80 bg-amber-950/60 text-amber-200"
                          }`}
                        >
                          {d.status}
                        </span>
                      </td>
                      <td className="px-2.5 py-1.5 text-right text-zinc-200">{d.attempts}</td>
                      <td className="px-2.5 py-1.5 text-right text-zinc-200">
                        {d.last_status_code ?? "—"}
                      </td>
                      <td className="px-2.5 py-1.5 text-red-300">{d.last_error ?? "—"}</td>
                      <td className="px-2.5 py-1.5 text-zinc-400">
                        {formatTimestamp(d.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
