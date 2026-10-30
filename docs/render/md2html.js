// Minimal, dependency-free Markdown -> HTML converter tailored for the design
// document: handles headings, tables, fenced code (incl. mermaid), lists,
// blockquotes, inline code/bold/links, and horizontal rules.
const fs = require('fs');
const path = require('path');

function esc(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function inline(s) {
  // inline code first (protect), then bold, then links
  const codes = [];
  s = s.replace(/`([^`]+)`/g, (_, c) => {
    codes.push(c);
    return `\u0000${codes.length - 1}\u0000`;
  });
  s = esc(s);
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');
  s = s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${esc(codes[+i])}</code>`);
  return s;
}

function convert(md) {
  const lines = md.split(/\r?\n/);
  const out = [];
  let i = 0;
  let diagram = 0;
  while (i < lines.length) {
    let line = lines[i];

    // fenced code
    const fence = line.match(/^```(\w*)/);
    if (fence) {
      const lang = fence[1];
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i++; }
      i++; // closing fence
      if (lang === 'mermaid') {
        diagram++;
        out.push(`<div class="diagram"><div class="diagram-cap">Diagram ${diagram}</div><pre class="mermaid">${esc(buf.join('\n'))}</pre></div>`);
      } else {
        out.push(`<pre class="code"><code>${esc(buf.join('\n'))}</code></pre>`);
      }
      continue;
    }

    // headings
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      const lvl = h[1].length;
      const id = h[2].toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
      out.push(`<h${lvl} id="${id}">${inline(h[2])}</h${lvl}>`);
      i++;
      continue;
    }

    // horizontal rule
    if (/^---+\s*$/.test(line)) { out.push('<hr/>'); i++; continue; }

    // table
    if (/^\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const header = line.split('|').slice(1, -1).map(c => c.trim());
      i += 2; // skip header + separator
      const rows = [];
      while (i < lines.length && /^\|.*\|\s*$/.test(lines[i])) {
        rows.push(lines[i].split('|').slice(1, -1).map(c => c.trim()));
        i++;
      }
      let t = '<table><thead><tr>' + header.map(c => `<th>${inline(c)}</th>`).join('') + '</tr></thead><tbody>';
      for (const r of rows) t += '<tr>' + r.map(c => `<td>${inline(c)}</td>`).join('') + '</tr>';
      t += '</tbody></table>';
      out.push(t);
      continue;
    }

    // blockquote
    if (/^>\s?/.test(line)) {
      const buf = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) { buf.push(lines[i].replace(/^>\s?/, '')); i++; }
      out.push(`<blockquote>${inline(buf.join(' '))}</blockquote>`);
      continue;
    }

    // unordered list
    if (/^[-*]\s+/.test(line)) {
      out.push('<ul>');
      while (i < lines.length && /^[-*]\s+/.test(lines[i])) {
        out.push(`<li>${inline(lines[i].replace(/^[-*]\s+/, ''))}</li>`);
        i++;
      }
      out.push('</ul>');
      continue;
    }
    // ordered list
    if (/^\d+\.\s+/.test(line)) {
      out.push('<ol>');
      while (i < lines.length && /^\d+\.\s+/.test(lines[i])) {
        out.push(`<li>${inline(lines[i].replace(/^\d+\.\s+/, ''))}</li>`);
        i++;
      }
      out.push('</ol>');
      continue;
    }

    // blank
    if (/^\s*$/.test(line)) { i++; continue; }

    // paragraph (gather until blank/structural)
    const buf = [line];
    i++;
    while (i < lines.length && !/^\s*$/.test(lines[i]) && !/^(#{1,6}\s|```|\||>|[-*]\s|\d+\.\s|---+\s*$)/.test(lines[i])) {
      buf.push(lines[i]); i++;
    }
    out.push(`<p>${inline(buf.join(' '))}</p>`);
  }
  return { html: out.join('\n'), diagrams: diagram };
}

const docPath = path.resolve(__dirname, '..', 'DESIGN_DOCUMENT.md');
const md = fs.readFileSync(docPath, 'utf8');
const { html, diagrams } = convert(md);
const mermaidJs = fs.readFileSync(path.join(__dirname, 'node_modules', 'mermaid', 'dist', 'mermaid.min.js'), 'utf8');

const page = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>ACE Migration Factory — Design Document</title>
<style>
  :root{--cvs:#cc0000;--ink:#1a2330;--muted:#5b6673;--line:#e2e8f0;--bg:#ffffff;--soft:#f7f9fc;--accent:#eef4ff;}
  *{box-sizing:border-box;}
  body{font-family:"Segoe UI",Roboto,Arial,sans-serif;color:var(--ink);background:var(--bg);margin:0;line-height:1.6;font-size:15px;}
  .wrap{max-width:1100px;margin:0 auto;padding:48px 56px;}
  .cover{background:linear-gradient(135deg,#8a0000,#cc0000);color:#fff;padding:60px 56px;border-radius:0;margin:-48px -56px 40px;}
  .cover h1{border:0;color:#fff;font-size:38px;margin:0 0 8px;}
  .cover .sub{font-size:17px;opacity:.95;max-width:760px;}
  .cover .meta{margin-top:22px;font-size:13px;opacity:.9;}
  h1,h2,h3,h4,h5,h6{color:var(--ink);line-height:1.25;margin:1.8em 0 .6em;}
  h1{font-size:30px;border-bottom:3px solid var(--cvs);padding-bottom:8px;}
  h2{font-size:24px;border-bottom:2px solid var(--line);padding-bottom:6px;}
  h3{font-size:19px;}
  h4{font-size:16px;color:var(--muted);}
  p{margin:.6em 0;}
  a{color:var(--cvs);text-decoration:none;}
  code{background:var(--soft);border:1px solid var(--line);border-radius:4px;padding:1px 5px;font-family:"Cascadia Code",Consolas,monospace;font-size:13px;color:#b1003a;}
  pre.code{background:#0f172a;color:#e2e8f0;border-radius:8px;padding:16px 18px;overflow:auto;font-size:13px;line-height:1.5;}
  pre.code code{background:none;border:0;color:inherit;padding:0;}
  blockquote{margin:1em 0;padding:12px 18px;background:var(--accent);border-left:4px solid var(--cvs);border-radius:0 6px 6px 0;color:#2b3a4e;}
  table{border-collapse:collapse;width:100%;margin:1.2em 0;font-size:14px;box-shadow:0 1px 3px rgba(0,0,0,.06);}
  th,td{border:1px solid var(--line);padding:9px 12px;text-align:left;vertical-align:top;}
  th{background:var(--cvs);color:#fff;font-weight:600;}
  tr:nth-child(even) td{background:var(--soft);}
  hr{border:0;border-top:1px solid var(--line);margin:2.4em 0;}
  ul,ol{margin:.6em 0 .6em 1.2em;}
  li{margin:.25em 0;}
  .diagram{margin:1.6em 0;padding:20px;background:var(--soft);border:1px solid var(--line);border-radius:10px;text-align:center;page-break-inside:avoid;}
  .diagram-cap{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:12px;font-weight:600;}
  .mermaid{display:flex;justify-content:center;}
  .mermaid svg{max-width:100%;height:auto;}
  @media print{
    .wrap{max-width:none;padding:0 12mm;}
    .cover{margin:0 -12mm 28px;border-radius:0;}
    h2{page-break-before:auto;}
    .diagram{break-inside:avoid;}
    pre.code{break-inside:avoid;}
    table{break-inside:avoid;}
  }
</style>
</head>
<body>
<div class="wrap">
  <div class="cover">
    <h1>ACE Migration Factory</h1>
    <div class="sub">Design Document — High-Level &amp; Low-Level Design for the governed, agentic IBM ACE → Spring Boot migration platform.</div>
    <div class="meta">${diagrams} rendered diagrams · Generated from source analysis · CVS Health AI Platform</div>
  </div>
  ${html}
</div>
<script>${mermaidJs}</script>
<script>
  mermaid.initialize({startOnLoad:false,theme:'default',securityLevel:'loose',
    themeVariables:{fontFamily:'Segoe UI, Arial, sans-serif',primaryColor:'#eef4ff',primaryBorderColor:'#cc0000',lineColor:'#555',fontSize:'15px'},
    flowchart:{htmlLabels:true,curve:'basis',useMaxWidth:true},
    sequence:{useMaxWidth:true}});
  window.__mermaidDone=false;
  (async()=>{try{await mermaid.run({querySelector:'.mermaid'});}catch(e){console.error('mermaid',e);}finally{window.__mermaidDone=true;}})();
</script>
</body>
</html>`;

fs.writeFileSync(path.resolve(__dirname, '..', 'DESIGN_DOCUMENT.html'), page, 'utf8');
console.log('HTML written with', diagrams, 'diagrams');
