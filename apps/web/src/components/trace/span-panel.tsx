import { formatClock, formatCost, formatDuration, formatPayload, formatTokens, formatValue } from "@/lib/format";
import type { Document, Media, Message, Span } from "@/lib/types";
import { KindBadge, Mono, StatusBadge } from "@/components/ui";
import { AddToDataset } from "@/components/evals/add-to-dataset";
import { AttributesTable } from "./attributes-table";

const LONG_CHARS = 1200;
const LONG_LINES = 24;

function Section({ title, count, children }: { title: string; count?: number; children: React.ReactNode }) {
  return (
    <section className="border-t border-zinc-800 px-4 py-3">
      <h3 className="mb-2 flex items-center gap-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
        {title}
        {count !== undefined && <span className="font-mono text-zinc-600">{count}</span>}
      </h3>
      {children}
    </section>
  );
}

/** Preformatted block; long content is collapsed behind a native <details> toggle. */
export function Payload({ text, mime, label }: { text: string; mime?: string | null; label: string }) {
  const { text: shown, json } = formatPayload(text, mime);
  const lines = shown.split("\n").length;
  const pre = (
    <pre
      className={`max-h-[28rem] overflow-auto whitespace-pre-wrap break-words rounded border border-zinc-800 bg-zinc-950 p-2 font-mono text-xs leading-relaxed ${
        json ? "text-emerald-100/90" : "text-zinc-200"
      }`}
    >
      {shown}
    </pre>
  );
  if (shown.length <= LONG_CHARS && lines <= LONG_LINES) return pre;
  const preview = shown.split("\n").slice(0, 6).join("\n").slice(0, 400);
  return (
    <details className="group">
      <summary className="cursor-pointer list-none rounded focus-visible:outline-2">
        <pre className="overflow-hidden whitespace-pre-wrap break-words rounded-t border border-zinc-800 bg-zinc-950 p-2 font-mono text-xs text-zinc-300 group-open:hidden">
          {preview}…
        </pre>
        <span className="mt-1 inline-block text-xs text-emerald-300 group-open:hidden">
          Show all {label.toLowerCase()} ({lines} lines, {shown.length.toLocaleString("en-US")} chars)
        </span>
        <span className="mb-1 hidden text-xs text-emerald-300 group-open:inline-block">Collapse</span>
      </summary>
      {pre}
    </details>
  );
}

const ROLE_STYLE: Record<string, string> = {
  system: "border-zinc-600 bg-zinc-900/60",
  user: "border-sky-500/70 bg-sky-950/20",
  assistant: "border-emerald-500/70 bg-emerald-950/20",
  tool: "border-orange-400/70 bg-orange-950/20",
};

function Messages({ messages }: { messages: Message[] }) {
  return (
    <ol className="space-y-2">
      {messages.map((m, i) => (
        <li key={i} className={`rounded-r border-l-2 px-2.5 py-1.5 ${ROLE_STYLE[m.role] ?? ROLE_STYLE.system}`}>
          <div className="mb-0.5 text-[10px] font-medium uppercase tracking-wider text-zinc-400">{m.role}</div>
          {m.content && (
            <div className="whitespace-pre-wrap break-words text-xs leading-relaxed text-zinc-200">
              {m.content.length > LONG_CHARS ? <Payload text={m.content} label="message" /> : m.content}
            </div>
          )}
          {m.tool_calls?.map((tc, j) => (
            <pre
              key={j}
              className="mt-1.5 overflow-x-auto whitespace-pre-wrap break-words rounded border border-zinc-800 bg-zinc-950 p-2 font-mono text-[11px] text-zinc-200"
            >
              <span className="text-orange-300">{tc.name}</span>
              {"("}
              {formatPayload(tc.arguments).text}
              {")"}
            </pre>
          ))}
        </li>
      ))}
    </ol>
  );
}

function Documents({ docs }: { docs: Document[] }) {
  return (
    <ol className="space-y-2">
      {docs.map((d, i) => (
        <li key={d.id ?? i} className="rounded border border-zinc-800 bg-zinc-900/40 p-2">
          <div className="mb-1 flex items-center gap-2 text-xs">
            <Mono className="text-zinc-500">#{i + 1}</Mono>
            <span className="min-w-0 flex-1 truncate font-mono text-zinc-300" title={d.id ?? undefined}>
              {d.id ?? "(no id)"}
            </span>
            {d.score !== null && (
              <Mono className="rounded bg-zinc-800 px-1.5 text-emerald-300">{d.score.toFixed(3)}</Mono>
            )}
          </div>
          <p className="whitespace-pre-wrap break-words text-xs leading-relaxed text-zinc-300">{d.content}</p>
          {Object.keys(d.metadata).length > 0 && (
            <dl className="mt-1.5 flex flex-wrap gap-1">
              {Object.entries(d.metadata).map(([k, v]) => (
                <div key={k} className="flex rounded bg-zinc-800/80 px-1.5 text-[10px] leading-5">
                  <dt className="text-zinc-500">{k}</dt>
                  <dd className="ml-1 font-mono text-zinc-300">{formatValue(v)}</dd>
                </div>
              ))}
            </dl>
          )}
        </li>
      ))}
    </ol>
  );
}

function MediaList({ media, spanName }: { media: Media[]; spanName: string }) {
  return (
    <ul className="flex flex-wrap gap-2">
      {media.map((m, i) => (
        <li key={i} className="max-w-full">
          {m.kind === "image" ? (
            <figure>
              {/* Plain <img>: media URLs are arbitrary (signed URLs, data URIs). */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={m.url}
                alt={`Image ${i + 1} attached to ${spanName}${m.mime ? ` (${m.mime})` : ""}`}
                loading="lazy"
                decoding="async"
                className="max-h-64 max-w-full rounded border border-zinc-800 bg-zinc-900"
              />
              <figcaption className="mt-0.5 font-mono text-[10px] text-zinc-500">{m.mime ?? "image"}</figcaption>
            </figure>
          ) : m.kind === "audio" ? (
            <audio controls preload="none" src={m.url} className="max-w-full">
              <a href={m.url}>Download audio</a>
            </audio>
          ) : (
            <a
              href={m.url}
              target="_blank"
              rel="noreferrer noopener"
              className="font-mono text-xs text-emerald-300 hover:underline"
            >
              {m.mime ?? "file"} ↗
            </a>
          )}
        </li>
      ))}
    </ul>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</dt>
      <dd className="truncate font-mono text-xs tabular-nums text-zinc-200">{children}</dd>
    </div>
  );
}

export function SpanPanel({ span, traceStart, readOnly = false }: { span: Span; traceStart: number; readOnly?: boolean }) {
  const startOffset = Date.parse(span.start_time) - traceStart;
  const inMime = typeof span.attributes["input.mime_type"] === "string" ? (span.attributes["input.mime_type"] as string) : null;
  const outMime =
    typeof span.attributes["output.mime_type"] === "string" ? (span.attributes["output.mime_type"] as string) : null;
  const hasTokens = span.input_tokens + span.output_tokens + span.reasoning_tokens > 0;
  const params = Object.keys(span.invocation_parameters);

  return (
    <article aria-label={`Span ${span.name}`} className="text-sm">
      <header className="flex items-start gap-2 px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <KindBadge kind={span.kind} />
            <StatusBadge status={span.status} />
          </div>
          <h2 className="mt-1.5 break-words font-semibold text-zinc-50">{span.name}</h2>
          <Mono className="text-[11px] text-zinc-500">{span.span_id}</Mono>
        </div>
        <div className="shrink-0">
          {!readOnly && <AddToDataset traceId={span.trace_id} spanId={span.span_id} />}
        </div>
      </header>

      {span.status === "error" && span.status_message && (
        <div role="note" className="mx-4 mb-3 rounded border border-red-900/70 bg-red-950/40 px-2.5 py-1.5 font-mono text-xs text-red-200">
          {span.status_message}
        </div>
      )}

      <Section title="Summary">
        <dl className="grid grid-cols-3 gap-x-3 gap-y-2">
          <Stat label="Duration">{formatDuration(span.duration_ms)}</Stat>
          <Stat label="Starts at">+{formatDuration(Math.max(0, startOffset))}</Stat>
          <Stat label="TTFT">{span.ttft_ms !== null ? formatDuration(span.ttft_ms) : "—"}</Stat>
          {span.model && <Stat label="Model">{span.model}</Stat>}
          {span.provider && <Stat label="Provider">{span.provider}</Stat>}
          <Stat label="Start (UTC)">{formatClock(span.start_time)}</Stat>
          {hasTokens && (
            <>
              <Stat label="Tokens in">{formatTokens(span.input_tokens)}</Stat>
              <Stat label="Tokens out">{formatTokens(span.output_tokens)}</Stat>
              <Stat label="Reasoning">{formatTokens(span.reasoning_tokens)}</Stat>
              {span.cache_read_tokens + span.cache_write_tokens > 0 && (
                <>
                  <Stat label="Cache read">{formatTokens(span.cache_read_tokens)}</Stat>
                  <Stat label="Cache write">{formatTokens(span.cache_write_tokens)}</Stat>
                </>
              )}
            </>
          )}
          {span.cost_usd > 0 && <Stat label="Cost">{formatCost(span.cost_usd)}</Stat>}
        </dl>
      </Section>

      {span.input_messages.length > 0 && (
        <Section title="Input messages" count={span.input_messages.length}>
          <Messages messages={span.input_messages} />
        </Section>
      )}
      {span.output_messages.length > 0 && (
        <Section title="Output messages" count={span.output_messages.length}>
          <Messages messages={span.output_messages} />
        </Section>
      )}
      {span.input !== null && span.input !== "" && (
        <Section title="Input">
          <Payload text={span.input} mime={inMime} label="input" />
        </Section>
      )}
      {span.output !== null && span.output !== "" && (
        <Section title="Output">
          <Payload text={span.output} mime={outMime} label="output" />
        </Section>
      )}
      {span.media.length > 0 && (
        <Section title="Media" count={span.media.length}>
          <MediaList media={span.media} spanName={span.name} />
        </Section>
      )}
      {span.documents.length > 0 && (
        <Section title="Retrieved documents" count={span.documents.length}>
          <Documents docs={span.documents} />
        </Section>
      )}
      {span.tool && (
        <Section title="Tool">
          <p className="font-mono text-xs text-orange-300">{span.tool.name}</p>
          {span.tool.description && <p className="mt-0.5 text-xs text-zinc-400">{span.tool.description}</p>}
          {span.tool.parameters && (
            <div className="mt-2">
              <Payload text={span.tool.parameters} mime="application/json" label="parameters" />
            </div>
          )}
        </Section>
      )}
      {params.length > 0 && (
        <Section title="Invocation parameters">
          <Payload text={JSON.stringify(span.invocation_parameters)} mime="application/json" label="parameters" />
        </Section>
      )}
      <Section title="Attributes" count={Object.keys(span.attributes).length}>
        <AttributesTable attributes={span.attributes} />
      </Section>
      {span.events.length > 0 && (
        <Section title="Events" count={span.events.length}>
          <ol className="space-y-2">
            {span.events.map((e, i) => (
              <li key={i} className="text-xs">
                <div className="flex items-center gap-2">
                  <Mono className="text-zinc-500">{formatClock(e.time)}</Mono>
                  <span className={e.name === "exception" ? "font-medium text-red-300" : "text-zinc-200"}>{e.name}</span>
                </div>
                {Object.keys(e.attributes).length > 0 && (
                  <dl className="mt-1 space-y-1 rounded border border-zinc-800 bg-zinc-950 p-2">
                    {Object.entries(e.attributes).map(([k, v]) => (
                      <div key={k}>
                        <dt className="font-mono text-[10px] text-zinc-500">{k}</dt>
                        <dd className="whitespace-pre-wrap break-words font-mono text-[11px] text-zinc-200">
                          {formatValue(v)}
                        </dd>
                      </div>
                    ))}
                  </dl>
                )}
              </li>
            ))}
          </ol>
        </Section>
      )}
    </article>
  );
}
