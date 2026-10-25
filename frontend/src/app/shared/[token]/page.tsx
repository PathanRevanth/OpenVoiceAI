"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Streamdown } from "streamdown";
import "streamdown/styles.css";
import { backend, type SharedConversationView } from "@/lib/backend";
import { Icon } from "@/components/icons";
import { ArtifactPreview } from "@/components/artifact-preview";
import { ARTIFACT_KIND_LABEL, parseSegments, type Artifact, type Segment } from "@/lib/artifacts";

const PREVIEWABLE = new Set<Artifact["kind"]>(["html", "svg", "markdown", "react", "pdf", "csv", "image"]);

export default function SharedPage() {
  const params = useParams<{ token: string }>();
  const token = params?.token ?? "";
  const [view, setView] = useState<SharedConversationView | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    backend
      .sharedConversation(token)
      .then(setView)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Could not load this conversation."));
  }, [token]);

  const messages = useMemo(
    () =>
      (view?.messages ?? []).map((m, i) => ({
        ...m,
        segments:
          m.role === "assistant" ? parseSegments(m.text, `m${i}`, true) : ([] as Segment[]),
      })),
    [view],
  );

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <span className="flex items-center justify-between gap-3">
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--text-3)]">
          Shared conversation
        </p>
        <Link
          href="/"
          className="press shrink-0 rounded-full bg-[var(--solid)] px-3.5 py-1.5 text-[11.5px] font-semibold text-[var(--on-solid)] hover:opacity-90"
        >
          Open in Voice-First AI
        </Link>
      </span>
      <h1 className="mt-1 text-[22px] font-semibold tracking-tight text-[var(--text-1)]">
        {view?.title ?? (error ? "Unavailable" : "Loading")}
      </h1>
      {error && <p className="mt-4 text-[13px] text-[var(--danger)]">{error}</p>}
      <div className="mt-6 space-y-5">
        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="flex justify-end">
              <p className="max-w-[82%] rounded-2xl rounded-br-md bg-[var(--panel-hover)] px-4 py-2.5 text-[13.5px] leading-relaxed text-[var(--text-1)]">
                {m.text}
              </p>
            </div>
          ) : (
            <div key={i} className="text-[13.5px] leading-relaxed text-[var(--text-1)]">
              <div className="space-y-3">
                {m.segments.map((seg, j) =>
                  seg.type === "text" ? (
                    seg.text.trim() ? (
                      <div
                        key={j}
                        className="[&_code]:rounded [&_code]:bg-[var(--panel-hover)] [&_code]:px-1 [&_pre]:overflow-x-auto [&_pre]:rounded-xl [&_pre]:border [&_pre]:border-[var(--hairline)] [&_pre]:bg-[var(--bg-raised)] [&_pre]:p-3 [&_strong]:font-semibold"
                      >
                        <Streamdown>{seg.text}</Streamdown>
                      </div>
                    ) : null
                  ) : (
                    <SharedArtifact key={j} artifact={seg.artifact} />
                  ),
                )}
              </div>
              {m.model && (
                <p className="mt-1 font-mono text-[10px] tabular text-[var(--text-3)]">
                  {m.model.replace(/^(openai|anthropic|google):/, "")}
                </p>
              )}
              {m.sources && m.sources.length > 0 && (
                <div className="mt-2.5 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] p-2.5">
                  <p className="px-1 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--text-3)]">
                    Sources
                  </p>
                  <div className="flex flex-col gap-1">
                    {m.sources.map((s) => (
                      <a
                        key={s.id}
                        href={s.url}
                        target="_blank"
                        rel="noreferrer"
                        className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-[var(--panel-hover)]"
                      >
                        <span className="grid h-5 w-5 shrink-0 place-items-center rounded-md bg-[var(--accent-dim)] font-mono text-[10px] font-bold text-[var(--accent)]">
                          {s.id}
                        </span>
                        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-[var(--text-1)]">
                          {s.title}
                        </span>
                        <span className="shrink-0 font-mono text-[10px] text-[var(--text-3)]">
                          {s.url.replace(/^https?:\/\//, "").split("/")[0]}
                        </span>
                      </a>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ),
        )}
      </div>
      <p className="mt-10 flex items-center justify-center gap-1.5 text-[11px] text-[var(--text-3)]">
        <Icon name="waveform" size={12} /> Shared from Voice-First AI
      </p>
    </div>
  );
}

function SharedArtifact({ artifact }: { artifact: Artifact }) {
  const [showSource, setShowSource] = useState(false);
  const previewable = PREVIEWABLE.has(artifact.kind);

  return (
    <figure className="my-4 overflow-hidden rounded-2xl border border-[var(--hairline)] bg-[var(--bg-raised)]">
      <figcaption className="flex items-center gap-2.5 border-b border-[var(--hairline)] px-4 py-2.5">
        <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-[var(--accent-dim)] text-[var(--accent)]">
          <Icon name="artifact" size={14} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-semibold tracking-tight text-[var(--text-1)]">
            {artifact.title}
          </span>
          <span className="block font-mono text-[10.5px] text-[var(--text-3)]">
            {ARTIFACT_KIND_LABEL[artifact.kind]}
            {artifact.language && (artifact.kind === "code" || artifact.kind === "react")
              ? ` · ${artifact.language}`
              : ""}
          </span>
        </span>
        {previewable && (
          <button
            onClick={() => setShowSource((open) => !open)}
            className="press fluid rounded-lg border border-[var(--hairline)] px-2.5 py-1 text-[11px] font-medium text-[var(--text-3)] hover:border-[var(--hairline-strong)] hover:text-[var(--text-1)]"
          >
            {showSource ? "Preview" : "Source"}
          </button>
        )}
      </figcaption>
      {showSource || !previewable ? (
        <pre className="max-h-[min(70vh,560px)] overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-[11.5px] leading-relaxed text-[var(--text-2)]">
          {artifact.content}
        </pre>
      ) : (
        <div className="h-[min(70vh,560px)] overflow-auto bg-[var(--bg)]">
          <ArtifactPreview artifact={artifact} />
        </div>
      )}
    </figure>
  );
}
