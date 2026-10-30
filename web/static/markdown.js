/* Small, safe Markdown renderer for chat replies.
   Everything is HTML-escaped first; only a fixed set of tags is ever produced.
   Handles partial input (an unclosed ``` while streaming renders as an open code block). */
(function () {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  function inline(text) {
    const codes = [];
    let s = text.replace(/`([^`\n]+)`/g, (_, c) => { codes.push(c); return "\u0000" + (codes.length - 1) + "\u0000"; });
    s = esc(s);
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, t, u) => `<a href="${u}" target="_blank" rel="noopener noreferrer">${t}</a>`);
    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:!?])/g, (_, p, u) => `${p}<a href="${u}" target="_blank" rel="noopener noreferrer">${u}</a>`);
    s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>").replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>").replace(/(^|[^_\w])_([^_\n]+)_(?!\w)/g, "$1<em>$2</em>");
    s = s.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${esc(codes[+i])}</code>`);
  }

  const COPY_ICON = '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/></svg>';

  function codeBlock(lang, body) {
    const l = esc((lang || "").trim().split(/\s+/)[0] || "text");
    return `<div class="code"><div class="code-head"><span>${l}</span><button type="button" data-copy-code>${COPY_ICON}Copy</button></div><pre><code>${esc(body)}</code></pre></div>`;
  }

  function table(lines) {
    const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
    const head = cells(lines[0]);
    const rows = lines.slice(2).map(cells);
    return "<table><thead><tr>" + head.map((c) => `<th>${c}</th>`).join("") + "</tr></thead><tbody>" +
      rows.map((r) => "<tr>" + r.map((c) => `<td>${c}</td>`).join("") + "</tr>").join("") + "</tbody></table>";
  }

  function render(src) {
    const lines = (src || "").replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let i = 0;
    let para = [];
    const flush = () => { if (para.length) { out.push("<p>" + para.map(inline).join("<br>") + "</p>"); para = []; } };

    while (i < lines.length) {
      const line = lines[i];
      const fence = line.match(/^\s*(```|~~~)(.*)$/);
      if (fence) {
        flush();
        const body = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith(fence[1])) body.push(lines[i++]);
        i++; // closing fence (or end of a still-streaming block)
        out.push(codeBlock(fence[2], body.join("\n")));
        continue;
      }
      if (/^\s*$/.test(line)) { flush(); i++; continue; }
      let m;
      if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
        flush();
        const n = Math.min(4, m[1].length + 1);
        out.push(`<h${n}>${inline(m[2])}</h${n}>`); i++; continue;
      }
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); out.push("<hr>"); i++; continue; }
      if (/^\s*>/.test(line)) {
        flush();
        const q = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) q.push(lines[i++].replace(/^\s*>\s?/, ""));
        out.push("<blockquote>" + render(q.join("\n")) + "</blockquote>"); continue;
      }
      if (/\|/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
        flush();
        const t = [];
        while (i < lines.length && /\|/.test(lines[i])) t.push(lines[i++]);
        out.push(table(t)); continue;
      }
      if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
        flush();
        out.push(list(lines, i, (next) => { i = next; }));
        continue;
      }
      para.push(line.trim());
      i++;
    }
    flush();
    return out.join("");
  }

  function list(lines, start, done) {
    const indent = (l) => l.match(/^\s*/)[0].length;
    const base = indent(lines[start]);
    const ordered = /^\s*\d+[.)]/.test(lines[start]);
    let html = ordered ? "<ol>" : "<ul>";
    let i = start;
    while (i < lines.length) {
      const l = lines[i];
      if (/^\s*$/.test(l)) {
        if (i + 1 < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i + 1]) && indent(lines[i + 1]) >= base) { i++; continue; }
        break;
      }
      const m = l.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
      if (!m) {
        if (indent(l) > base) { html = html.replace(/<\/li>$/, "") + " " + inline(l.trim()) + "</li>"; i++; continue; }
        break;
      }
      if (m[1].length < base) break;
      if (m[1].length > base) {
        html = html.replace(/<\/li>$/, "");
        html += list(lines, i, (n) => { i = n; }) + "</li>";
        continue;
      }
      html += "<li>" + inline(m[3]) + "</li>";
      i++;
    }
    done(i);
    return html + (ordered ? "</ol>" : "</ul>");
  }

  window.renderMarkdown = render;
  window.escapeHtml = esc;
})();
