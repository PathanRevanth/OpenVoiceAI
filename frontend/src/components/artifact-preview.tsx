"use client";

/**
 * ArtifactPreview — renders an artifact's finished view.
 *
 * Shared by the chat artifact canvas and public shared links so a delivered
 * page looks the same everywhere. HTML/SVG/React run in an opaque-origin
 * sandbox; nothing generated can reach the app's origin.
 */

import { useMemo, useState, type RefObject } from "react";
import { Streamdown } from "streamdown";
import "streamdown/styles.css";
import { Icon } from "@/components/icons";
import {
  artifactFrameSource,
  imageSrc,
  parseCsv,
  withPreviewScrollReset,
  type Artifact,
} from "@/lib/artifacts";

const CSV_ROW_LIMIT = 500;

interface ArtifactPreviewProps {
  artifact: Artifact;
  markdownRef?: RefObject<HTMLDivElement | null>;
  className?: string;
}

export function ArtifactPreview({ artifact, markdownRef, className = "" }: ArtifactPreviewProps) {
  const frameDoc = useMemo(() => {
    if (artifact.kind !== "html" && artifact.kind !== "svg" && artifact.kind !== "react") return "";
    return withPreviewScrollReset(artifactFrameSource(artifact));
  }, [artifact]);

  switch (artifact.kind) {
    case "markdown":
      return (
        <div
          ref={markdownRef}
          className={`px-6 py-5 text-[13.5px] leading-relaxed text-[var(--text-1)] [&_code]:rounded [&_code]:bg-[var(--panel-hover)] [&_code]:px-1 [&_pre]:overflow-x-auto [&_pre]:rounded-xl [&_pre]:border [&_pre]:border-[var(--hairline)] [&_pre]:bg-[var(--bg)] [&_pre]:p-3 [&_strong]:font-semibold ${className}`}
        >
          <Streamdown>{artifact.content}</Streamdown>
        </div>
      );
    case "csv":
      return <CsvTable text={artifact.content} className={className} />;
    case "pdf":
      return <PdfPreview artifact={artifact} className={className} />;
    case "image":
      return (
        // eslint-disable-next-line @next/next/no-img-element -- data/blob URL, not optimizable
        <img
          src={imageSrc(artifact)}
          alt={artifact.title}
          className={`mx-auto max-w-full p-6 ${className}`}
        />
      );
    default:
      return (
        <iframe
          key={artifact.id}
          title={artifact.title}
          sandbox="allow-scripts"
          srcDoc={frameDoc}
          className={`block h-full w-full bg-white ${className}`}
        />
      );
  }
}

function CsvTable({ text, className = "" }: { text: string; className?: string }) {
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
    <div className={`flex h-full flex-col ${className}`}>
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
                <th
                  key={i}
                  className="border-b border-[var(--hairline)] px-3 py-2 text-left font-semibold text-[var(--text-1)]"
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={i} className="odd:bg-[var(--panel-hover)]/40">
                {header.map((_, j) => (
                  <td
                    key={j}
                    className="border-b border-[var(--hairline)] px-3 py-1.5 align-top text-[var(--text-2)]"
                  >
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

function PdfPreview({ artifact, className = "" }: { artifact: Artifact; className?: string }) {
  const src = useMemo(() => {
    try {
      const base64 = artifact.content.replace(/^data:[^;]+;base64,/, "").replace(/\s+/g, "");
      return `data:application/pdf;base64,${base64}`;
    } catch {
      return null;
    }
  }, [artifact.content]);
  if (!src) return <p className="p-6 text-[12px] text-[var(--danger)]">This PDF payload is not valid base64.</p>;
  return <iframe title={artifact.title} src={src} className={`block h-full w-full bg-white ${className}`} />;
}
