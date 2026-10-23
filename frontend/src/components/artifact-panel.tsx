"use client";

/**
 * ArtifactPanel — the right-hand workspace for agent deliverables.
 *
 * Kinds and their views:
 * - markdown     Document (rendered) / source
 * - html, svg    Live preview in a sandboxed iframe (scripts only, no same-origin)
 * - react        Compiled in-browser (Babel) and mounted in the same sandbox
 * - csv          Searchable table / source
 * - json         Validated, pretty-printed source
 * - pdf          Embedded PDF viewer
 * - image        Image preview
 * - code, mermaid  Source
 *
 * Every artifact can be copied (text kinds), downloaded, and printed or saved
 * as PDF via a sandboxed print frame. Generated code never runs on the app's
 * origin: previews and print frames are opaque-origin iframes.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Streamdown } from "streamdown";
import "streamdown/styles.css";
import { Icon } from "@/components/icons";
import {
  artifactBytes,
  artifactFilename,
  artifactMime,
  buildPrintDocument,
  buildReactDocument,
  escapeHtml,
  imageSrc,
  parseCsv,
  prettyJson,
  withPrintTrigger,
  type Artifact,
} from "@/lib/artifacts";

interface ArtifactPanelProps {
  artifacts: Artifact[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onClose: () => void;
}

const KIND_LABEL: Record<Artifact["kind"], string> = {
  code: "Code",
  markdown: "Document",
  html: "Web page",
  svg: "Graphic",
  mermaid: "Diagram",
  react: "React component",
  pdf: "PDF",
  csv: "Table",
  json: "JSON",
  image: "Image",
};

const PREVIEWABLE = new Set<Artifact["kind"]>(["html", "svg", "markdown", "react", "pdf", "csv", "image"]);
const PRINTABLE = new Set<Artifact["kind"]>(["html", "svg", "markdown", "react", "csv", "json", "code", "mermaid"]);
const BINARY = new Set<Artifact["kind"]>(["pdf", "image"]);
const CSV_ROW_LIMIT = 500;

export function ArtifactPanel({ artifacts, activeId, onSelect, onClose }: ArtifactPanelProps) {
  const active = useMemo(
    () => artifacts.find((a) => a.id === activeId) ?? artifacts[artifacts.length - 1] ?? null,
    [artifacts, activeId],
  );
  const [view, setView] = useState<"preview" | "source">("preview");
  const [copiedId, setCopiedId] = useState<string | null>(null);
  // Local edit versions per artifact: index 0 is always the streamed original.
  const [versions, setVersions] = useState<Record<string, string[]>>({});
  const [versionIdx, setVersionIdx] = useState<Record<string, number>>({});
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const markdownRef = useRef<HTMLDivElement>(null);

  const shown = useMemo(() => {
    if (!active) return null;
    const vers = versions[active.id];
    const at = versionIdx[active.id] ?? 0;
    const content = vers && vers[at] !== undefined ? vers[at] : active.content;
    return { ...active, content };
  }, [active, versions, versionIdx]);
  const versionCount = (active && versions[active.id]?.length) || 1;

  const frameDoc = useMemo(() => (shown ? frameSource(shown) : ""), [shown]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!active || !shown) return null;

  const copied = copiedId !== null && copiedId === active.id;
  const canPreview = PREVIEWABLE.has(active.kind);
  // Streaming artifacts stay on source: re-rendering a half-written page on
  // every chunk would flicker and re-run its scripts.
  const showPreview =
    canPreview && active.complete && (view === "preview" || active.kind === "image");
  const showToggle = canPreview && active.complete && active.kind !== "image";
  const canCopy = !BINARY.has(active.kind);
  const canPrint = PRINTABLE.has(active.kind) && active.complete;
  const canOpen = BINARY.has(active.kind) && active.complete;
  const canEdit = !BINARY.has(active.kind) && active.complete;

  const copy = () => {
    const id = active.id;
    navigator.clipboard?.writeText(shown.content).then(() => setCopiedId(id)).catch(() => {});
  };

  const download = () => {
    const blob = new Blob([artifactBytes(shown) as BlobPart], { type: `${artifactMime(shown)};charset=utf-8` });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = artifactFilename(shown);
    link.click();
    URL.revokeObjectURL(url);
  };

  const openInTab = () => {
    const blob = new Blob([artifactBytes(shown) as BlobPart], { type: artifactMime(shown) });
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank", "noopener");
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };

  const print = () => printArtifact(shown, markdownRef.current?.innerHTML ?? "");

  const startEdit = () => {
    setDraft(shown.content);
    setEditing(true);
  };

  const applyEdit = () => {
    const id = active.id;
    const next = [...(versions[id] ?? [active.content]), draft];
    setVersions((prev) => ({ ...prev, [id]: next }));
    setVersionIdx((prev) => ({ ...prev, [id]: next.length - 1 }));
    setEditing(false);
  };

  const pickVersion = (at: number) => {
    setVersionIdx((prev) => ({ ...prev, [active.id]: at }));
    setEditing(false);
  };

  return (
    <aside
      aria-label="Artifacts"
      className="fixed inset-y-14 right-0 z-30 flex w-full flex-col border-l border-[var(--hairline)] bg-[var(--bg-raised)] shadow-[var(--shadow-pop)] sm:w-[min(640px,52vw)] xl:static xl:inset-auto xl:z-auto xl:h-[calc(100dvh-3.5rem)] xl:shadow-none"
    >
      <header className="flex items-center gap-2 border-b border-[var(--hairline)] px-3 py-2.5">
        <Icon name="artifact" size={15} className="shrink-0 text-[var(--accent)]" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] font-semibold tracking-tight text-[var(--text-1)]">{active.title}</p>
          <p className="font-mono text-[10.5px] text-[var(--text-3)]">
            {KIND_LABEL[active.kind]}
            {active.language && (active.kind === "code" || active.kind === "react") ? ` · ${active.language}` : ""}
            {!active.complete ? " · generating…" : ""}
            {versionCount > 1 ? ` · v${(versionIdx[active.id] ?? 0) + 1} of ${versionCount}` : ""}
          </p>
        </div>

        {showToggle && (
          <div className="flex rounded-lg border border-[var(--hairline)] p-0.5">
            <ViewToggle current={showPreview ? "preview" : "source"} value="preview" onPick={setView} label="Preview" icon="eye" />
            <ViewToggle current={showPreview ? "preview" : "source"} value="source" onPick={setView} label="Source" icon="code" />
          </div>
        )}
        {canEdit && !editing && <IconButton title="Edit source" icon="pencil" onClick={startEdit} />}
        {canCopy && <IconButton title="Copy" icon={copied ? "check" : "copy"} onClick={copy} />}
        <IconButton title="Download" icon="download" onClick={download} />
        {canPrint && <IconButton title="Print or save as PDF" icon="print" onClick={print} />}
        {canOpen && <IconButton title="Open in new tab" icon="external" onClick={openInTab} />}
        <IconButton title="Close (Esc)" icon="x" onClick={onClose} label="Close artifacts" />
      </header>

      {artifacts.length > 1 && (
        <div className="flex gap-1 overflow-x-auto border-b border-[var(--hairline)] px-3 py-2">
          {artifacts.map((a) => (
            <button
              key={a.id}
              onClick={() => {
                setEditing(false);
                onSelect(a.id);
              }}
              className={`fluid max-w-[160px] shrink-0 truncate rounded-lg px-2.5 py-1 text-[11.5px] ${
                a.id === active.id
                  ? "bg-[var(--panel-hover)] font-medium text-[var(--text-1)]"
                  : "text-[var(--text-3)] hover:bg-[var(--hover)] hover:text-[var(--text-1)]"
              }`}
            >
              {a.title}
            </button>
          ))}
        </div>
      )}

      {versionCount > 1 && (
        <div className="flex items-center gap-1 overflow-x-auto border-b border-[var(--hairline)] px-3 py-2">
          <span className="px-1 font-mono text-[10px] text-[var(--text-3)]">Versions</span>
          {Array.from({ length: versionCount }, (_, at) => (
            <button
              key={at}
              onClick={() => pickVersion(at)}
              className={`fluid shrink-0 rounded-lg px-2.5 py-1 font-mono text-[11px] ${
                (versionIdx[active.id] ?? 0) === at
                  ? "bg-[var(--panel-hover)] font-bold text-[var(--text-1)]"
                  : "text-[var(--text-3)] hover:bg-[var(--hover)] hover:text-[var(--text-1)]"
              }`}
            >
              v{at + 1}
            </button>
          ))}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-auto">
        {editing ? (
          <div className="flex h-full flex-col p-4">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              spellCheck={false}
              className="min-h-[280px] flex-1 resize-y rounded-xl border border-[var(--hairline)] bg-[var(--bg)] p-3 font-mono text-[12px] leading-relaxed outline-none focus:border-[var(--accent-border)]"
            />
            <span className="mt-2 flex justify-end gap-1.5">
              <button
                onClick={() => setEditing(false)}
                className="press rounded-lg px-3 py-1.5 text-[12px] font-medium text-[var(--text-3)] hover:text-[var(--text-1)]"
              >
                Cancel
              </button>
              <button
                onClick={applyEdit}
                className="press rounded-lg bg-[var(--accent)] px-3 py-1.5 text-[12px] font-semibold text-[var(--accent-contrast)]"
              >
                Apply as v{versionCount + 1}
              </button>
            </span>
          </div>
        ) : showPreview ? (
          <Preview artifact={shown} frameDoc={frameDoc} markdownRef={markdownRef} />
        ) : (
          <SourceBody artifact={shown} />
        )}
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Views
// ---------------------------------------------------------------------------

function Preview({
  artifact,
  frameDoc,
  markdownRef,
}: {
  artifact: Artifact;
  frameDoc: string;
  markdownRef: React.RefObject<HTMLDivElement | null>;
}) {
  switch (artifact.kind) {
    case "markdown":
      return (
        <div
          ref={markdownRef}
          className="px-6 py-5 text-[13.5px] leading-relaxed text-[var(--text-1)] [&_code]:rounded [&_code]:bg-[var(--panel-hover)] [&_code]:px-1 [&_pre]:overflow-x-auto [&_pre]:rounded-xl [&_pre]:border [&_pre]:border-[var(--hairline)] [&_pre]:bg-[var(--bg)] [&_pre]:p-3 [&_strong]:font-semibold"
        >
          <Streamdown>{artifact.content}</Streamdown>
        </div>
      );
    case "csv":
      return <CsvTable text={artifact.content} />;
    case "pdf":
      return <PdfPreview artifact={artifact} />;
    case "image":
      return (
        // eslint-disable-next-line @next/next/no-img-element -- data/blob URL, not optimizable
        <img src={imageSrc(artifact)} alt={artifact.title} className="mx-auto max-w-full p-6" />
      );
    default:
      return (
        <iframe
          key={artifact.id}
          title={artifact.title}
          sandbox="allow-scripts"
          srcDoc={frameDoc}
          className="h-full min-h-[480px] w-full bg-white"
        />
      );
  }
}

function SourceBody({ artifact }: { artifact: Artifact }) {
  if (artifact.kind === "json") return <JsonSource text={artifact.content} streaming={!artifact.complete} />;
  return (
    <pre className="min-h-full whitespace-pre-wrap break-words p-5 font-mono text-[12px] leading-relaxed text-[var(--text-2)]">
      {artifact.content}
      {!artifact.complete && <span className="caret" />}
    </pre>
  );
}

function JsonSource({ text, streaming }: { text: string; streaming: boolean }) {
  const pretty = useMemo(() => prettyJson(text), [text]);
  return (
    <div>
      {!streaming && !pretty.ok && (
        <p className="mx-5 mt-5 rounded-lg border border-[var(--warn-dim)] bg-[var(--warn-dim)] px-3 py-2 text-[11.5px] text-[var(--warn)]">
          Invalid JSON: {pretty.error}
        </p>
      )}
      <pre className="whitespace-pre-wrap break-words p-5 font-mono text-[12px] leading-relaxed text-[var(--text-2)]">
        {streaming ? text : pretty.text}
      </pre>
    </div>
  );
}

function CsvTable({ text }: { text: string }) {
  const rows = useMemo(() => parseCsv(text), [text]);
  const [query, setQuery] = useState("");
  const header = rows[0] ?? [];
  const filtered = useMemo(() => {
    const body = rows.slice(1);
    const q = query.trim().toLowerCase();
    return q ? body.filter((r) => r.some((c) => c.toLowerCase().includes(q))) : body;
  }, [rows, query]);
  const shown = filtered.slice(0, CSV_ROW_LIMIT);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-[var(--hairline)] px-4 py-2">
        <Icon name="search" size={12} className="text-[var(--text-3)]" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter rows"
          className="min-w-0 flex-1 bg-transparent text-[12px] outline-none placeholder:text-[var(--text-3)]"
        />
        <span className="tabular shrink-0 text-[10.5px] text-[var(--text-3)]">
          {filtered.length} of {Math.max(0, rows.length - 1)} rows · {header.length} columns
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full border-collapse text-[12px]">
          <thead className="sticky top-0 bg-[var(--bg-raised)]">
            <tr>
              {header.map((h, i) => (
                <th key={i} className="border-b border-[var(--hairline)] px-3 py-2 text-left font-semibold text-[var(--text-1)]">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={i} className="odd:bg-[var(--panel-hover)]/40">
                {header.map((_, j) => (
                  <td key={j} className="border-b border-[var(--hairline)] px-3 py-1.5 align-top text-[var(--text-2)]">
                    {r[j] ?? ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length > CSV_ROW_LIMIT && (
          <p className="px-4 py-3 text-[11px] text-[var(--text-3)]">
            Showing the first {CSV_ROW_LIMIT} rows. Download the file for the full data.
          </p>
        )}
      </div>
    </div>
  );
}

function PdfPreview({ artifact }: { artifact: Artifact }) {
  const src = useMemo(() => {
    try {
      const base64 = artifact.content.replace(/^data:[^;]+;base64,/, "").replace(/\s+/g, "");
      return `data:application/pdf;base64,${base64}`;
    } catch {
      return null;
    }
  }, [artifact.content]);
  if (!src) return <p className="p-6 text-[12px] text-[var(--danger)]">This PDF payload is not valid base64.</p>;
  return <iframe title={artifact.title} src={src} className="h-full min-h-[600px] w-full bg-white" />;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function frameSource(artifact: Artifact): string {
  switch (artifact.kind) {
    case "html":
      return artifact.content;
    case "svg":
      return `<!doctype html><html><body style="margin:0;display:grid;place-items:center;min-height:100vh;background:#fff">${artifact.content}</body></html>`;
    case "react":
      return buildReactDocument(artifact.content);
    default:
      return "";
  }
}

function tableHtml(rows: string[][]): string {
  const [header = [], ...body] = rows;
  const head = `<tr>${header.map((h) => `<th>${escapeHtml(h)}</th>`).join("")}</tr>`;
  const bodyRows = body
    .map((r) => `<tr>${header.map((_, j) => `<td>${escapeHtml(r[j] ?? "")}</td>`).join("")}</tr>`)
    .join("");
  return `<table><thead>${head}</thead><tbody>${bodyRows}</tbody></table>`;
}

/**
 * Print or save-as-PDF through a hidden sandboxed frame. The frame has the same
 * opaque origin as the preview, so generated scripts cannot reach the app.
 * Printing is triggered inside the frame after it loads.
 */
function printArtifact(artifact: Artifact, markdownHtml: string) {
  let doc: string;
  switch (artifact.kind) {
    case "html":
      doc = withPrintTrigger(artifact.content);
      break;
    case "svg":
    case "react":
      doc = withPrintTrigger(frameSource(artifact));
      break;
    case "markdown":
      doc = buildPrintDocument(markdownHtml, artifact.title);
      break;
    case "csv":
      doc = buildPrintDocument(tableHtml(parseCsv(artifact.content)), artifact.title);
      break;
    default:
      doc = buildPrintDocument(`<pre>${escapeHtml(artifact.content)}</pre>`, artifact.title);
  }
  const frame = document.createElement("iframe");
  frame.setAttribute("sandbox", "allow-scripts allow-modals");
  frame.setAttribute("aria-hidden", "true");
  frame.style.cssText = "position:fixed;right:0;bottom:0;width:1px;height:1px;opacity:0;border:0";
  frame.srcdoc = doc;
  document.body.appendChild(frame);
  window.setTimeout(() => frame.remove(), 120_000);
}

function IconButton({
  title,
  icon,
  onClick,
  label,
}: {
  title: string;
  icon: Parameters<typeof Icon>[0]["name"];
  onClick: () => void;
  label?: string;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      aria-label={label ?? title}
      className="press fluid grid h-8 w-8 place-items-center rounded-lg text-[var(--text-2)] hover:bg-[var(--hover)] hover:text-[var(--text-1)]"
    >
      <Icon name={icon} size={14} />
    </button>
  );
}

function ViewToggle({
  current,
  value,
  onPick,
  label,
  icon,
}: {
  current: "preview" | "source";
  value: "preview" | "source";
  onPick: (v: "preview" | "source") => void;
  label: string;
  icon: "eye" | "code";
}) {
  const on = current === value;
  return (
    <button
      onClick={() => onPick(value)}
      className={`press fluid flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium ${
        on ? "bg-[var(--panel-hover)] text-[var(--text-1)]" : "text-[var(--text-3)] hover:text-[var(--text-1)]"
      }`}
    >
      <Icon name={icon} size={11} />
      {label}
    </button>
  );
}
