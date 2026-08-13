import React, { useEffect, useState } from "react";

// P1-R：标题 slug 生成（与 buildToc 共用，保证目录与正文锚点 id 一致）
function slugify(text, seen) {
  const base = String(text || "")
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "");
  const id0 = base || "section";
  const n = seen[id0] || 0;
  seen[id0] = n + 1;
  return n ? `${id0}-${n + 1}` : id0;
}

// P1-R：解析 # / ## / ### 标题，生成目录数据（含锚点 id）
export function buildToc(text) {
  const lines = String(text || "").split(/\r?\n/);
  const seen = {};
  const toc = [];
  for (const line of lines) {
    const m = line.match(/^(#{1,3})\s+(.*)$/);
    if (!m) continue;
    const raw = String(m[2] || "").trim();
    if (!raw) continue;
    toc.push({ level: m[1].length, text: raw, id: slugify(raw, seen) });
  }
  return toc;
}

function inline(text, keyPrefix = "t") {
  const parts = [];
  const re = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let last = 0;
  let m;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const tok = m[0];
    const key = `${keyPrefix}-${i++}`;
    if (tok.startsWith("**")) {
      parts.push(<strong key={key}>{tok.slice(2, -2)}</strong>);
    } else if (tok.startsWith("`")) {
      // 行内代码不替换（防误伤：统计/示例数字保持原样）
      parts.push(<code key={key}>{tok.slice(1, -1)}</code>);
    } else if (tok.startsWith("[")) {
      const mm = tok.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      parts.push(
        <a key={key} href={mm[2]} target="_blank" rel="noreferrer">
          {mm[1]}
        </a>
      );
    } else if (tok.startsWith("*")) {
      parts.push(<em key={key}>{tok.slice(1, -1)}</em>);
    }
    last = m.index + tok.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

export default function MarkdownView({ text }) {
  const raw = String(text || "");

  if (!text) return <div className="md-empty">暂无报告内容</div>;
  const lines = raw.split(/\r?\n/);
  const seen = {};
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^```/.test(line)) {
      const buf = [];
      i += 1;
      while (i < lines.length && !/^```/.test(lines[i])) {
        buf.push(lines[i]);
        i += 1;
      }
      i += 1;
      blocks.push({ type: "code", text: buf.join("\n") });
      continue;
    }
    if (/^\|/.test(line)) {
      const tbl = [];
      while (i < lines.length && /^\|/.test(lines[i])) {
        tbl.push(lines[i]);
        i += 1;
      }
      blocks.push({ type: "table", rows: tbl });
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      blocks.push({
        type: `h${h[1].length}`,
        text: h[2],
        // P1-R：仅 1-3 级标题挂锚点 id（与 buildToc 一致）
        id: h[1].length <= 3 ? slugify(h[2], seen) : undefined,
      });
      i += 1;
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
        i += 1;
      }
      blocks.push({ type: "ul", items });
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+[.)]\s+/, ""));
        i += 1;
      }
      blocks.push({ type: "ol", items });
      continue;
    }
    if (/^>\s?/.test(line)) {
      const buf = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^>\s?/, ""));
        i += 1;
      }
      blocks.push({ type: "quote", text: buf.join(" ") });
      continue;
    }
    if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) {
      blocks.push({ type: "hr" });
      i += 1;
      continue;
    }
    if (line.trim() === "") {
      i += 1;
      continue;
    }
    blocks.push({ type: "p", text: line });
    i += 1;
  }

  return (
    <div className="md">
      {blocks.map((b, bi) => {
        const key = `b-${bi}`;
        switch (b.type) {
          case "h1":
            return <h1 key={key} id={b.id}>{inline(b.text, key)}</h1>;
          case "h2":
            return <h2 key={key} id={b.id}>{inline(b.text, key)}</h2>;
          case "h3":
            return <h3 key={key} id={b.id}>{inline(b.text, key)}</h3>;
          case "h4":
          case "h5":
          case "h6":
            return <h4 key={key}>{inline(b.text, key)}</h4>;
          case "p":
            return <p key={key}>{inline(b.text, key)}</p>;
          case "ul":
            return (
              <ul key={key}>
                {b.items.map((it, ii) => (
                  <li key={ii}>{inline(it, `${key}-${ii}`)}</li>
                ))}
              </ul>
            );
          case "ol":
            return (
              <ol key={key}>
                {b.items.map((it, ii) => (
                  <li key={ii}>{inline(it, `${key}-${ii}`)}</li>
                ))}
              </ol>
            );
          case "quote":
            return <blockquote key={key}>{inline(b.text, key)}</blockquote>;
          case "code":
            return (
              <pre key={key}>
                <code>{b.text}</code>
              </pre>
            );
          case "hr":
            return <hr key={key} />;
          case "table": {
            const rows = b.rows.map((r) => r.split("|").slice(1, -1).map((c) => c.trim()));
            const hasSep = rows.length > 1 && /^[\s\-:|]+$/.test(rows[1].join("|"));
            const head = hasSep ? rows[0] : null;
            // P1-Y：无分隔行时首行是数据而非表头，必须保留
            const body = hasSep ? rows.slice(2) : rows;
            return (
              <table key={key}>
                {head ? (
                  <thead>
                    <tr>
                      {head.map((c, ci) => (
                        <th key={ci}>{inline(c, `${key}-h${ci}`)}</th>
                      ))}
                    </tr>
                  </thead>
                ) : null}
                <tbody>
                  {body.map((r, ri) => (
                    <tr key={ri}>
                      {r.map((c, ci) => (
                        <td key={ci}>{inline(c, `${key}-${ri}-${ci}`)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            );
          }
          default:
            return null;
        }
      })}
    </div>
  );
}
