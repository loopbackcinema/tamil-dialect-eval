// node build_adjudication_docx.js <dir>   -> <dir>/<V>_adjudication_priority.docx for every <V>_priority.json
const fs = require('fs'), path = require('path');
const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, WidthType, ShadingType,
        AlignmentType, BorderStyle } = require('docx');
const TF = "Noto Sans Tamil";
const dir = process.argv[2] || '.';

const runs = (text, size, color) => String(text).split(/(【[^】]*】)/).filter(x => x !== '').map(seg =>
  new TextRun({ text: seg, font: TF, size, color, bold: seg.startsWith('【') }));
const P = (text, o = {}) => new Paragraph({ spacing: { after: o.after ?? 50, line: 260 },
  children: runs(text, o.size || 19, o.color || "000000") });
const W = [1250, 7216, 2000];
const cell = (children, w, fill) => new TableCell({ width: { size: w, type: WidthType.DXA },
  margins: { top: 50, bottom: 50, left: 80, right: 80 },
  shading: fill ? { type: ShadingType.CLEAR, fill } : undefined, children });
const hdr = cols => new TableRow({ tableHeader: true, children: cols.map((c, i) => cell(
  [new Paragraph({ children: [new TextRun({ text: c, font: TF, size: 19, bold: true, color: "FFFFFF" })] })], W[i], "2F5496")) });
const row = (id, lines, answer) => new TableRow({ cantSplit: true, children: [
  cell([P(id, { size: 18, color: "1F3864" })], W[0]),
  cell(lines, W[1]),
  cell([P(answer, { size: 20 })], W[2]) ] });

for (const f of fs.readdirSync(dir).filter(x => x.endsWith('_priority.json'))) {
  const d = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
  const kids = [];
  kids.push(new Paragraph({ spacing: { after: 120 }, children: [new TextRun({
    text: d.heading || `${d.title} வழக்கு — தீர்ப்புத் தாள்`, font: TF, size: 30, bold: true, color: "1F3864" })] }));
  d.readme.forEach(l => kids.push(l ? P(l.replace(/^ {2,}/, m => '\u00a0'.repeat(m.length)), { size: 19, after: 30 })
                                     : new Paragraph({ children: [] })));
  if (d.A.length) {
  kids.push(new Paragraph({ spacing: { before: 240, after: 100 }, children: [new TextRun({
    text: `பகுதி A — சொற்கள் (முதல் ${d.priority_n})`, font: TF, size: 24, bold: true, color: "1F3864" })] }));
  kids.push(new Table({ columnWidths: W, rows: [hdr(["எண்", "சூழல் — AI கேள்வி கேட்ட சொல் 【 】-க்குள்", "பதில்"]),
    ...d.A.map(x => row(x.id, [P(x.context),
      P(`AI கேட்டது: ${x.flagged}   →   ${x.suggested}`, { size: 17, color: "555555" })], "☐S  ☐E  ☐?"))] }));
  }
  kids.push(new Paragraph({ spacing: { before: 300, after: 100 }, children: [new TextRun({
    text: (d.b_heading || `பகுதி B — திருத்தங்கள்`) + ` (${d.B.length})`, font: TF, size: 24, bold: true, color: "1F3864" })] }));
  kids.push(new Table({ columnWidths: W, rows: [hdr(["எண்", "செருகிய பிழை 【 】-க்குள் · நாங்கள் · AI", "பதில்"]),
    ...d.B.map(x => row(x.id, [P(x.context),
      P(`வகை: ${x.type}   ·   நாங்கள்: ${x.reference}   ·   AI: ${x.model_form || '(தெளிவாக இல்லை)'}`, { size: 17, color: "555555" }),
      P(`AI வாக்கியம்: ${x.model_sentence}`, { size: 17, color: "555555" })], "☐A  ☐B  ☐C  ☐?"))] }));
  const doc = new Document({ sections: [{ properties: { page: { size: { width: 11906, height: 16838 },
    margin: { top: 720, bottom: 720, left: 720, right: 720 } } }, children: kids }] });
  Packer.toBuffer(doc).then(b => fs.writeFileSync(path.join(dir, f.replace('_priority.json', '_adjudication_priority.docx')), b));
}
