// Extract ```mermaid fenced blocks from the design doc into numbered .mmd files.
const fs = require('fs');
const path = require('path');

const docPath = path.resolve(__dirname, '..', 'DESIGN_DOCUMENT.md');
const outDir = __dirname;
const md = fs.readFileSync(docPath, 'utf8');

const re = /```mermaid\s*\n([\s\S]*?)```/g;
let m, i = 0;
const names = [];
while ((m = re.exec(md)) !== null) {
  i += 1;
  const id = String(i).padStart(2, '0');
  const file = path.join(outDir, `diagram-${id}.mmd`);
  fs.writeFileSync(file, m[1].trim() + '\n', 'utf8');
  names.push(`diagram-${id}`);
}
console.log(JSON.stringify(names));
