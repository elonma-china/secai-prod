import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

export type PocFile = {
  path: string;
  lang: string;
  code: string;
  truncated?: boolean;
};

export type PocArtifacts = {
  tree?: string;
  files: PocFile[];
  /** Câu "chưa có PoC…" khi không có evidence */
  emptyNote?: string;
};

const NO_POC = "Chưa có thông tin PoC được xác thực trong dữ liệu nội bộ cho lỗ hổng này.";

/** Bỏ sha256 / ghi chú cắt bớt khỏi dòng header file do backend inject. */
export function stripShaNoise(text: string): string {
  return (text || "")
    .replace(/\s*—\s*sha256\s*`[^`]+`…?/gi, "")
    .replace(/\s*—\s*\*\(đã cắt bớt[^)]*\)\*/gi, "");
}

/** Tách Mục 1 (PoC inject) khỏi phần hướng dẫn ## 2.+ — tránh dump PoC lẫn prose. */
export function splitSection1(text: string): { section1: string | null; guide: string } {
  const src = text || "";
  const h1 = /(^|\n)[ \t]*#{2,6}[ \t]*1\.[ \t]*\S[^\n]*\n?/;
  const m = h1.exec(src);
  if (!m) return { section1: null, guide: src };
  const bodyStart = m.index + m[0].length;
  const next = /(^|\n)[ \t]*#{2,6}[ \t]*[2-9]\.[ \t]*\S/.exec(src.slice(bodyStart));
  const bodyEnd = next ? bodyStart + next.index : src.length;
  const section1 = src.slice(bodyStart, bodyEnd).trim();
  const guide = (src.slice(0, m.index) + src.slice(bodyEnd)).replace(/^\n+/, "").trim();
  return { section1: section1 || null, guide };
}

/**
 * Parse body Mục 1 / `section1` SSE → tree + danh sách file.
 * Fence không có path ngay trước = tree (hoặc code lẻ → file `snippet`).
 */
export function parsePocArtifacts(raw: string): PocArtifacts | null {
  let text = stripShaNoise(raw || "").trim();
  if (!text) return null;

  // Bỏ heading ## 1. nếu lỡ còn trong body
  text = text.replace(/^#{2,6}[ \t]*1\.[ \t]*\S[^\n]*\n+/, "").trim();

  if (text === NO_POC || text.startsWith(NO_POC)) {
    return { files: [], emptyNote: NO_POC };
  }

  // Gỡ nhãn "Cấu trúc PoC…" — fence kế tiếp là tree
  text = text.replace(/^Cấu trúc PoC[^\n]*\n+/i, "");

  const files: PocFile[] = [];
  let tree: string | undefined;
  const re = /(?:(?:^|\n)[ \t]*\*\*`([^`]+)`\*\*[^\n]*\n+)?[ \t]*```(\w*)\r?\n([\s\S]*?)```/g;
  let m: RegExpExecArray | null;
  let sawFence = false;
  while ((m = re.exec(text)) !== null) {
    sawFence = true;
    const path = (m[1] || "").trim();
    const lang = (m[2] || "").trim();
    const code = m[3] ?? "";
    const headerExtra = (m[0].match(/^\s*\*\*`[^`]+`\*\*([^\n]*)/) || [])[1] || "";
    const truncated = /đã cắt bớt/i.test(headerExtra);

    if (!path && !lang && !tree) {
      // Fence không ngôn ngữ + không path → cấu trúc repo
      tree = code.replace(/\n$/, "");
      continue;
    }
    files.push({
      path: path || (lang ? `snippet.${lang}` : "snippet"),
      lang: lang || guessLang(path),
      code,
      truncated,
    });
  }

  if (!sawFence && text.trim()) {
    // Không fence — có thể là note ngắn
    return { files: [], emptyNote: text.trim() };
  }
  if (!files.length && !tree) return null;
  return { tree, files };
}

function guessLang(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() || "";
  const map: Record<string, string> = {
    py: "python", ts: "typescript", js: "javascript", rb: "ruby", go: "go",
    rs: "rust", sh: "bash", md: "markdown", yml: "yaml", yaml: "yaml",
  };
  return map[ext] || "";
}

function basename(path: string): string {
  const p = path.replace(/\\/g, "/");
  return p.split("/").filter(Boolean).pop() || path;
}

function FileCard({ file }: { file: PocFile }) {
  const [open, setOpen] = useState(false);
  const lines = file.code.length === 0 ? 0 : file.code.replace(/\n$/, "").split("\n").length;
  const title = basename(file.path);
  const sub = file.path !== title ? file.path : undefined;

  async function copy(e: React.MouseEvent) {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(file.code);
    } catch {
      /* ignore */
    }
  }

  return (
    <div className={"file-card" + (open ? " open" : "")}>
      <button type="button" className="file-card-head" onClick={() => setOpen((v) => !v)}>
        <span className="file-card-chevron" aria-hidden>{open ? "▾" : "▸"}</span>
        <span className="file-card-title">
          <span className="file-card-name">{title}</span>
          {sub && <span className="file-card-path">{sub}</span>}
        </span>
        <span className="file-card-meta">
          {file.truncated && <span className="file-card-trunc">truncated</span>}
          {file.lang && <span className="file-card-lang">{file.lang}</span>}
          <span>{lines} dòng</span>
        </span>
      </button>
      <div className="file-card-actions">
        <button type="button" className="btn ghost sm" onClick={copy}>Copy</button>
      </div>
      {open && (
        <pre className="file-card-body">
          <code className={file.lang ? `language-${file.lang}` : undefined}>{file.code}</code>
        </pre>
      )}
    </div>
  );
}

function TreeCard({ tree }: { tree: string }) {
  const [open, setOpen] = useState(true);
  return (
    <div className={"file-card tree-card" + (open ? " open" : "")}>
      <button type="button" className="file-card-head" onClick={() => setOpen((v) => !v)}>
        <span className="file-card-chevron" aria-hidden>{open ? "▾" : "▸"}</span>
        <span className="file-card-title">
          <span className="file-card-name">Cấu trúc PoC</span>
        </span>
      </button>
      {open && (
        <pre className="file-card-body tree-body">
          <code>{tree}</code>
        </pre>
      )}
    </div>
  );
}

/** Panel Mục 1 — danh sách file, không dump markdown dài. */
export function PocFilePanel({
  artifacts,
  preview,
}: {
  artifacts: PocArtifacts;
  preview?: boolean;
}) {
  if (artifacts.emptyNote && !artifacts.files.length && !artifacts.tree) {
    return (
      <div className="poc-files">
        <div className="poc-files-label">Mục 1 · PoC</div>
        <div className="poc-empty">{artifacts.emptyNote}</div>
      </div>
    );
  }
  return (
    <div className="poc-files">
      <div className="poc-files-label">
        {preview && <span className="spin" />}
        Mục 1 · Mã khai thác
        {artifacts.files.length > 0 && (
          <span className="poc-files-count">{artifacts.files.length} file</span>
        )}
        {preview && <span className="poc-files-preview">đang tải phần còn lại…</span>}
      </div>
      {artifacts.tree && <TreeCard tree={artifacts.tree} />}
      {artifacts.files.map((f) => (
        <FileCard key={f.path + ":" + f.code.length} file={f} />
      ))}
    </div>
  );
}

function Prose({ text }: { text: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
      {text}
    </ReactMarkdown>
  );
}

export type MdSeg =
  | { kind: "md"; text: string }
  | { kind: "file"; path?: string; lang: string; code: string; truncated?: boolean };

/** Markdown thường; fence trong guide (Mục 2+) vẫn thu gọn kiểu file. */
export function splitCodeFiles(raw: string): MdSeg[] {
  const text = stripShaNoise(raw);
  if (!text.trim()) return [];
  const re =
    /(?:(?:^|\n)[ \t]*\*\*`([^`]+)`\*\*[^\n]*\n+)?[ \t]*```(\w*)\r?\n([\s\S]*?)```/g;
  const segs: MdSeg[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const before = text.slice(last, m.index);
    if (before.trim()) segs.push({ kind: "md", text: before.replace(/^\n+/, "") });
    const path = (m[1] || "").trim() || undefined;
    const lang = (m[2] || "").trim();
    const code = m[3] ?? "";
    const headerLine = m[0].match(/^\s*\*\*`[^`]+`\*\*([^\n]*)/);
    const truncated = /đã cắt bớt/i.test(headerLine?.[1] || "");
    segs.push({ kind: "file", path, lang, code, truncated });
    last = m.index + m[0].length;
  }
  const tail = text.slice(last);
  if (tail.trim()) segs.push({ kind: "md", text: tail.replace(/^\n+/, "") });
  return segs;
}

/** Markdown cho phần hướng dẫn (sau khi đã tách Mục 1). */
export default function FileAwareMarkdown({ children }: { children: string }) {
  const cleaned = stripShaNoise(children || "");
  const segs = splitCodeFiles(cleaned);
  if (!segs.length) return <div className="md" />;

  if (segs.every((s) => s.kind === "md")) {
    return (
      <div className="md">
        <Prose text={cleaned} />
      </div>
    );
  }

  return (
    <div className="md md-files">
      {segs.map((s, i) =>
        s.kind === "md" ? (
          <Prose key={i} text={s.text} />
        ) : (
          <FileCard
            key={i}
            file={{
              path: s.path || (s.lang ? `snippet.${s.lang}` : "snippet"),
              lang: s.lang,
              code: s.code,
              truncated: s.truncated,
            }}
          />
        )
      )}
    </div>
  );
}
