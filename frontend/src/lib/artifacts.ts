/**
 * Artifact extraction and rendering helpers for assistant replies.
 *
 * An artifact is a self-contained deliverable the user will want to inspect,
 * copy, or download on its own. Artifacts are declared in one of two ways:
 *
 * - Explicitly, by the agent: a fence whose info string starts with `artifact`,
 *   e.g. ```artifact type=react title="ROI calculator"
 * - Automatically: `html`, `svg`, `mermaid`, `jsx`/`tsx`, `csv` (3+ lines),
 *   `json` (12+ lines), or any code fence with 20+ lines.
 *
 * Parsing is line-based and tolerant of unterminated fences, so a fence that is
 * still streaming in is reported as an incomplete artifact that grows in place.
 *
 * This module is pure (no DOM access) so it can be exercised outside the browser.
 */

export type ArtifactKind =
  | "code"
  | "markdown"
  | "html"
  | "svg"
  | "mermaid"
  | "react"
  | "pdf"
  | "csv"
  | "json"
  | "image";

export interface Artifact {
  id: string;
  kind: ArtifactKind;
  title: string;
  language: string;
  content: string;
  complete: boolean;
}

export type Segment = { type: "text"; text: string } | { type: "artifact"; artifact: Artifact };

const AUTO_CODE_LINES = 20;
const AUTO_JSON_LINES = 12;
const AUTO_CSV_LINES = 3;
const FENCE_OPEN = /^( {0,3})(`{3,}|~{3,})(.*)$/;
const FENCE_CLOSE = /^ {0,3}(`{3,}|~{3,})\s*$/;

const KIND_BY_NAME: Record<string, ArtifactKind> = {
  html: "html",
  svg: "svg",
  markdown: "markdown",
  md: "markdown",
  mermaid: "mermaid",
  react: "react",
  jsx: "react",
  tsx: "react",
  pdf: "pdf",
  csv: "csv",
  json: "json",
  image: "image",
  png: "image",
  jpg: "image",
  jpeg: "image",
  webp: "image",
  gif: "image",
  code: "code",
};

const DEFAULT_TITLE: Record<ArtifactKind, string> = {
  code: "Code",
  markdown: "Document",
  html: "HTML page",
  svg: "SVG graphic",
  mermaid: "Diagram",
  react: "React component",
  pdf: "PDF document",
  csv: "Table",
  json: "JSON data",
  image: "Image",
};

const EXTENSION: Record<ArtifactKind, string> = {
  code: "txt",
  markdown: "md",
  html: "html",
  svg: "svg",
  mermaid: "mmd",
  react: "jsx",
  pdf: "pdf",
  csv: "csv",
  json: "json",
  image: "png",
};

const IMAGE_MIME: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  webp: "image/webp",
  gif: "image/gif",
};

function parseAttrs(source: string): Record<string, string> {
  const attrs: Record<string, string> = {};
  const pattern = /([a-zA-Z_]+)=(?:"([^"]*)"|'([^']*)'|(\S+))/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(source))) {
    attrs[match[1].toLowerCase()] = match[2] ?? match[3] ?? match[4] ?? "";
  }
  return attrs;
}

function autoKind(lang: string, bodyLines: number): ArtifactKind | null {
  if (lang === "html" || lang === "svg" || lang === "mermaid") return KIND_BY_NAME[lang];
  if (lang === "jsx" || lang === "tsx") return "react";
  if (lang === "csv" && bodyLines >= AUTO_CSV_LINES) return "csv";
  if (lang === "json" && bodyLines >= AUTO_JSON_LINES) return "json";
  if (bodyLines >= AUTO_CODE_LINES) return "code";
  return null;
}

function defaultTitle(kind: ArtifactKind, lang: string): string {
  if (kind === "code" && lang) return `${lang} code`;
  return DEFAULT_TITLE[kind];
}

/**
 * Split assistant text into prose and artifact segments. `keyPrefix` makes
 * artifact ids unique across messages, so the panel can address each one.
 */
export function parseSegments(text: string, keyPrefix = "a"): Segment[] {
  const lines = text.split("\n");
  const segments: Segment[] = [];
  let prose: string[] = [];
  let count = 0;

  const flushProse = () => {
    if (prose.length) {
      segments.push({ type: "text", text: prose.join("\n") });
      prose = [];
    }
  };

  let i = 0;
  while (i < lines.length) {
    const open = FENCE_OPEN.exec(lines[i]);
    if (!open || (open[2][0] === "`" && open[3].includes("`"))) {
      prose.push(lines[i]);
      i++;
      continue;
    }

    const fence = open[2];
    const info = open[3].trim();
    const firstToken = info.split(/\s+/)[0]?.toLowerCase() ?? "";
    const explicit = firstToken === "artifact";
    const attrs = explicit ? parseAttrs(info.slice("artifact".length)) : {};
    const lang = explicit
      ? (attrs.lang ?? attrs.language ?? attrs.type ?? "").toLowerCase()
      : firstToken;

    let end = -1;
    for (let j = i + 1; j < lines.length; j++) {
      const close = FENCE_CLOSE.exec(lines[j]);
      if (close && close[1][0] === fence[0] && close[1].length >= fence.length) {
        end = j;
        break;
      }
    }

    const bodyEnd = end === -1 ? lines.length : end;
    const body = lines.slice(i + 1, bodyEnd).join("\n");
    const bodyLines = bodyEnd - (i + 1);
    const kind = explicit
      ? (KIND_BY_NAME[(attrs.type ?? lang).toLowerCase()] ?? "code")
      : autoKind(lang, bodyLines);

    if (kind === null) {
      const stop = end === -1 ? lines.length : end + 1;
      prose.push(...lines.slice(i, stop));
      i = stop;
      continue;
    }

    flushProse();
    count++;
    segments.push({
      type: "artifact",
      artifact: {
        id: `${keyPrefix}-${count}`,
        kind,
        title: attrs.title || defaultTitle(kind, lang),
        language: lang || (kind === "image" ? "png" : kind),
        content: body.replace(/^\n+/, "").replace(/\s+$/, ""),
        complete: end !== -1,
      },
    });
    i = end === -1 ? lines.length : end + 1;
  }

  flushProse();
  return segments;
}

function slugify(title: string): string {
  return title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
}

export function artifactExtension(artifact: Artifact): string {
  if (artifact.kind === "code") return (artifact.language || "txt").replace(/[^a-z0-9]/gi, "") || "txt";
  if (artifact.kind === "react") return artifact.language === "tsx" ? "tsx" : "jsx";
  if (artifact.kind === "image") return (artifact.language || "png").replace(/[^a-z0-9]/gi, "") || "png";
  return EXTENSION[artifact.kind];
}

export function artifactFilename(artifact: Artifact): string {
  return `${slugify(artifact.title) || "artifact"}.${artifactExtension(artifact)}`;
}

export function artifactMime(artifact: Artifact): string {
  switch (artifact.kind) {
    case "html":
      return "text/html";
    case "svg":
      return "image/svg+xml";
    case "markdown":
      return "text/markdown";
    case "react":
      return "text/jsx";
    case "pdf":
      return "application/pdf";
    case "csv":
      return "text/csv";
    case "json":
      return "application/json";
    case "image":
      return IMAGE_MIME[artifactExtension(artifact)] ?? "image/png";
    default:
      return "text/plain";
  }
}

/** Binary-safe payload for download and object URLs. PDFs and images are base64. */
export function artifactBytes(artifact: Artifact): Uint8Array {
  if (artifact.kind === "pdf" || artifact.kind === "image") {
    const raw = artifact.content.replace(/^data:[^;]+;base64,/, "").replace(/\s+/g, "");
    const binary = atob(raw);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }
  return new TextEncoder().encode(artifact.content);
}

/** `src` for an image artifact: a data/http URL is used as-is, otherwise base64. */
export function imageSrc(artifact: Artifact): string {
  const c = artifact.content.trim();
  if (c.startsWith("data:") || c.startsWith("http://") || c.startsWith("https://")) return c;
  return `data:${artifactMime(artifact)};base64,${c.replace(/\s+/g, "")}`;
}

/** RFC 4180-style CSV parser: quoted fields, escaped quotes, CRLF or LF rows. */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        field += '"';
        i++;
      } else if (ch === '"') {
        quoted = false;
      } else {
        field += ch;
      }
    } else if (ch === '"') {
      quoted = true;
    } else if (ch === ",") {
      row.push(field);
      field = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += ch;
    }
  }
  if (field !== "" || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((r) => !(r.length === 1 && r[0] === ""));
}

export function prettyJson(text: string): { ok: boolean; text: string; error?: string } {
  try {
    return { ok: true, text: JSON.stringify(JSON.parse(text), null, 2) };
  } catch (err) {
    return { ok: false, text, error: err instanceof Error ? err.message : "Invalid JSON" };
  }
}

export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Transpile-and-mount document for a React/JSX/TSX artifact. The component's
 * default export is rendered into #root. Imports resolve against a small
 * built-in map (react, react-dom); unknown modules fail with a clear message
 * instead of a blank frame. Runs inside a sandboxed iframe.
 */
export function buildReactDocument(source: string): string {
  const payload = JSON.stringify(source).replace(/</g, "\\u003c");
  return `<!doctype html>
<html><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<script src="https://cdn.tailwindcss.com"></script>
<script crossorigin src="https://unpkg.com/react@18/umd/react.development.js"></script>
<script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>
<script src="https://unpkg.com/@babel/standalone@7/babel.min.js"></script>
<style>html,body{margin:0;min-height:100%;font-family:system-ui,-apple-system,sans-serif}#root{min-height:100vh}</style>
</head><body><div id="root"></div>
<pre id="err" style="display:none;margin:0;padding:16px;color:#b91c1c;font:12.5px/1.5 ui-monospace,monospace;white-space:pre-wrap"></pre>
<script>
(function () {
  function fail(e) {
    var el = document.getElementById("err");
    el.style.display = "block";
    el.textContent = String((e && e.stack) || e);
  }
  window.addEventListener("error", function (ev) { fail(ev.error || ev.message); });
  try {
    var source = ${payload};
    var compiled = Babel.transform(source, {
      filename: "artifact.tsx",
      presets: [["typescript", { isTSX: true, allExtensions: true }], "react"],
      plugins: ["transform-modules-commonjs"],
    }).code;
    var modules = { react: React, "react-dom": ReactDOM, "react-dom/client": ReactDOM };
    var exportsObj = {};
    var moduleObj = { exports: exportsObj };
    var req = function (name) {
      if (name in modules) return modules[name];
      throw new Error("Module not available in preview: " + name);
    };
    new Function("require", "module", "exports", "React", compiled)(req, moduleObj, exportsObj, React);
    var Component = moduleObj.exports.default;
    if (!Component) throw new Error("Export a default component (export default function App() { ... }).");
    ReactDOM.createRoot(document.getElementById("root")).render(React.createElement(Component));
  } catch (e) {
    fail(e);
  }
})();
</script></body></html>`;
}

const PRINT_TRIGGER =
  '<script>window.addEventListener("load",function(){setTimeout(function(){window.print();},700);});</script>';

/** Insert a load-time print trigger into a complete HTML document. */
export function withPrintTrigger(doc: string): string {
  return doc.includes("</body>")
    ? doc.replace("</body>", `${PRINT_TRIGGER}</body>`)
    : `${doc}${PRINT_TRIGGER}`;
}

/** Print-ready document for text-based artifacts (markdown, tables, code). */
export function buildPrintDocument(bodyHtml: string, title: string): string {
  return withPrintTrigger(`<!doctype html><html><head><meta charset="utf-8" /><title>${escapeHtml(title)}</title>
<style>
body{font:14px/1.6 system-ui,-apple-system,sans-serif;color:#16171c;margin:40px auto;max-width:780px;padding:0 24px}
h1,h2,h3{line-height:1.25}pre{white-space:pre-wrap;word-break:break-word;background:#f4f4f5;padding:12px;border-radius:8px}
code{font-family:ui-monospace,monospace}table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{border:1px solid #d4d4d8;padding:6px 8px;text-align:left;vertical-align:top}th{background:#f4f4f5}
@media print{body{margin:0;max-width:none}}
</style></head><body>${bodyHtml}</body></html>`);
}
