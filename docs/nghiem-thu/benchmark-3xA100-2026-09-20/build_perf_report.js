const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell,
  WidthType, BorderStyle, ShadingType, AlignmentType, convertInchesToTwip,
  Header, Footer, PageNumber, VerticalAlign,
} = require("docx");

const FULL_WIDTH = convertInchesToTwip(6.3);
const GRAY = "F2F2F2";
const DARK = "1F1F1F";
const ACCENT = "2E5C8A";
const GOOD = "1E7B34";
const WARN = "B45309";

function h1(text) {
  return new Paragraph({ text, heading: HeadingLevel.HEADING_1, spacing: { before: 340, after: 150 } });
}
function h2(text) {
  return new Paragraph({ text, heading: HeadingLevel.HEADING_2, spacing: { before: 220, after: 100 } });
}
function p(text, opts = {}) {
  return new Paragraph({ spacing: { after: 120 }, children: [new TextRun({ text, ...opts })] });
}
function spacer(h = 140) {
  return new Paragraph({ spacing: { after: h }, children: [] });
}
function codeBlock(lines) {
  return new Table({
    width: { size: FULL_WIDTH, type: WidthType.DXA },
    columnWidths: [FULL_WIDTH],
    rows: [new TableRow({ children: [new TableCell({
      width: { size: FULL_WIDTH, type: WidthType.DXA },
      shading: { type: ShadingType.CLEAR, fill: "0D1B2A" },
      margins: { top: 140, bottom: 140, left: 180, right: 180 },
      children: lines.map((line, i) => new Paragraph({
        spacing: { after: i === lines.length - 1 ? 0 : 40 },
        children: [new TextRun({ text: line, font: "Consolas", size: 17, color: "D6E4F0" })],
      })),
    })] })],
  });
}
function infoTable(rows) {
  return new Table({
    width: { size: FULL_WIDTH, type: WidthType.DXA },
    columnWidths: [1900, FULL_WIDTH - 1900],
    rows: rows.map(([k, v]) => new TableRow({ children: [
      new TableCell({ width: { size: 1900, type: WidthType.DXA }, shading: { type: ShadingType.CLEAR, fill: GRAY },
        margins: { top: 80, bottom: 80, left: 120, right: 120 },
        children: [new Paragraph({ children: [new TextRun({ text: k, bold: true, size: 20 })] })] }),
      new TableCell({ width: { size: FULL_WIDTH - 1900, type: WidthType.DXA },
        margins: { top: 80, bottom: 80, left: 120, right: 120 },
        children: [new Paragraph({ children: [new TextRun({ text: v, size: 20 })] })] }),
    ] })),
  });
}
function dataTable(headers, rows, colWidths) {
  const n = headers.length;
  const widths = colWidths || headers.map(() => Math.floor(FULL_WIDTH / n));
  return new Table({
    width: { size: FULL_WIDTH, type: WidthType.DXA },
    columnWidths: widths,
    rows: [
      new TableRow({ children: headers.map((h, i) => new TableCell({
        width: { size: widths[i], type: WidthType.DXA }, shading: { type: ShadingType.CLEAR, fill: ACCENT },
        margins: { top: 90, bottom: 90, left: 100, right: 100 },
        children: [new Paragraph({ children: [new TextRun({ text: h, color: "FFFFFF", bold: true, size: 18 })] })],
      })) }),
      ...rows.map((row, ri) => new TableRow({ children: row.map((cell, i) => {
        const isNote = typeof cell === "object" && cell !== null;
        const text = isNote ? cell.text : String(cell);
        const color = isNote && cell.color ? cell.color : undefined;
        return new TableCell({
          width: { size: widths[i], type: WidthType.DXA },
          shading: { type: ShadingType.CLEAR, fill: ri % 2 === 0 ? "FFFFFF" : "F7F9FB" },
          margins: { top: 80, bottom: 80, left: 100, right: 100 },
          children: [new Paragraph({ children: [new TextRun({ text, size: 18, bold: i === 0, color })] })],
        });
      }) })),
    ],
  });
}

const doc = new Document({
  styles: {
    default: { document: { run: { font: "Calibri", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 30, bold: true, color: ACCENT }, paragraph: { spacing: { before: 300, after: 150 } } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 24, bold: true, color: DARK }, paragraph: { spacing: { before: 220, after: 100 } } },
    ],
  },
  sections: [{
    properties: { page: { size: { width: 11906, height: 16838 } } },
    headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT,
      children: [new TextRun({ text: "SecAI — Báo cáo hiệu năng serving", size: 16, color: "999999" })] })] }) },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
      children: [new TextRun({ text: "Trang ", size: 16, color: "999999" }),
        new TextRun({ children: [PageNumber.CURRENT], size: 16, color: "999999" }),
        new TextRun({ text: " / ", size: 16, color: "999999" }),
        new TextRun({ children: [PageNumber.TOTAL_PAGES], size: 16, color: "999999" })] })] }) },
    children: [
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 200, after: 100 },
        children: [new TextRun({ text: "BÁO CÁO HIỆU NĂNG SERVING", bold: true, size: 34, color: ACCENT })] }),
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 260 },
        children: [new TextRun({ text: "Cyber-F1-AWQ / sec-embedding / sec-rerank — cấu hình 3× NVIDIA A100 80GB", size: 24, color: DARK })] }),
      infoTable([
        ["Cấu hình đo", "3× NVIDIA A100 80GB (giới hạn ở tầng nginx LB)"],
        ["Hạ tầng", "vast.ai, Nhật Bản — GPU A100 SXM4 80GB, môi trường production"],
        ["Phương pháp", "Per-model: đo tại-host (loopback). Load test: toàn bộ pipeline (gateway+RAG+guard+LLM), 30 CCU"],
        ["Script đo", "bench_full.py (per-model) · bench_fullpipeline.py + locustfile_30ccu.py (full-pipeline, 30 CCU)"],
        ["Model", "LLM: DuyTa/Cyber-F1-AWQ  |  Embedding: DuyTa/sec-embedding  |  Reranker: DuyTa/sec-rerank"],
        ["Ngày đo", "20/09/2026, 12:00-20:05 UTC (19:00-03:05 giờ VN)"],
      ]),
      spacer(240),

      h1("1. Tóm tắt điều hành"),
      p("Cấu hình đo: 3×A100 80GB. Workload: câu hỏi bảo mật thực tế, lấy từ gold-set độc lập của dự án. Load test đi qua toàn bộ hệ thống (gateway → RAG retrieval → guard → LLM)."),
      dataTable(
        ["Chỉ số", "Giá trị đo được"],
        [
          ["LLM TPS (decode)", "139.01 tok/s (p50)"],
          ["LLM TTFT (raw model)", "55.4 ms (p50)"],
          ["RPS @ 30 CCU (full pipeline: gateway+RAG+guard+LLM)", "1.516 req/s"],
          ["Lỗi @ 30 CCU (full pipeline)", "0 / 90"],
        ],
        [3900, 2400]
      ),
      spacer(240),

      h1("2. Số liệu riêng từng model (raw, đúng 3 GPU)"),
      h2("2.1. LLM — DuyTa/Cyber-F1-AWQ"),
      dataTable(
        ["Chỉ số", "p50", "Mean", "Min", "Max"],
        [
          ["TPS decode (300 token/prompt)", "139.01 tok/s", "139.02", "138.14", "140.04"],
          ["TTFT (streaming, 50 token)", "55.4 ms", "55.6 ms", "—", "—"],
        ],
        [2400, 1500, 1500, 1300, 1300]
      ),
      spacer(120),
      h2("2.2. Embedding — DuyTa/sec-embedding"),
      p("Đơn vị: mỗi request = 1 embedding (mô phỏng embed 1 câu truy vấn tại thời điểm hỏi — không batch)."),
      dataTable(
        ["Điều kiện", "p50", "p95", "p99", "Throughput"],
        [
          ["1 request đơn (tuần tự)", "14.09 ms", "17.86 ms", "20.37 ms", "—"],
          ["Tải đồng thời (conc=16)", "14.40 ms", "32.01 ms", "35.95 ms", "702.3 req/s"],
        ],
        [2300, 1300, 1300, 1300, 1700]
      ),
      spacer(120),
      h2("2.3. Reranker — DuyTa/sec-rerank"),
      p("Đơn vị: mỗi request rerank 20 tài liệu ứng viên (candidates = merged[:20], retrieval/qdrant.py). Văn bản lấy từ Qdrant (cve_kb_hybrid), độ dài trung bình 405 ký tự (phân bố toàn KB: mean=432 ký tự)."),
      dataTable(
        ["Điều kiện", "p50", "p95", "p99", "Throughput"],
        [
          ["1 request đơn (tuần tự)", "90.67 ms", "93.25 ms", "98.63 ms", "—"],
          ["Tải đồng thời (conc=8)", "69.57 ms", "106.90 ms", "119.34 ms", "96.2 req/s (≈1924 doc/s)"],
        ],
        [2300, 1300, 1300, 1300, 1700]
      ),
      spacer(240),

      h1("3. Load test đúng 30 CCU — toàn bộ hệ thống (gateway + RAG + guard + LLM)"),
      p("Phạm vi: endpoint /ai/chat của gateway, đi qua RAG retrieval (Qdrant) + guard/kiểm duyệt đầu vào + LLM. Đo trên production, cấu hình 3×A100.", { italics: true, color: "555555" }),
      p("Workload: 90 câu hỏi bảo mật khác nhau, lấy mẫu (seed=42, tái lập được) từ gold-set độc lập testset-v2/acceptance_indep_*.jsonl (1000 câu, mỗi câu gắn CVE/CWE/CVSS). 90 request, 30 luồng đồng thời:"),
      dataTable(
        ["Concurrent Users", "RPS", "p50", "p95", "p99", "Lỗi"],
        [["30", "1.516 req/s", "15.87 s", "25.58 s", "28.25 s", "0 / 90"]],
        [1600, 1700, 1050, 1050, 1050, 950]
      ),
      spacer(100),
      p(
        "Đối chiếu tài liệu chào giá ban đầu (0.6 RPS, cùng loại phép đo — full pipeline, cấu hình 3×A100, 30 CCU): " +
          "1.516 RPS, tăng 2.53 lần; 0 lỗi trên 90 request.",
        { color: "555555" }
      ),
      spacer(140),
      h2("3.1. Đối chiếu bằng Locust"),
      p("Cùng workload, cùng endpoint /ai/chat, cùng 30 CCU, chạy liên tục 120 giây:"),
      dataTable(
        ["Công cụ", "Requests", "RPS", "p50", "p95", "p99", "Lỗi"],
        [
          ["Locust (--headless -u 30 -r 6 -t 120s)", "221", "1.864 req/s", "15.0 s", "26.0 s", "36.0 s", "0 / 221"],
        ],
        [2600, 900, 1100, 800, 800, 800, 700]
      ),
      spacer(100),
      p(
        "2 phương pháp đo độc lập cho kết quả cùng bậc độ lớn (1.516 vs 1.864 RPS). Báo cáo HTML đầy đủ của Locust " +
          "(biểu đồ RPS theo thời gian, phân bố response time) đính kèm: load_30ccu_report.html.",
        { color: "555555" }
      ),
      spacer(240),

      h1("4. Phương pháp đo + script"),
      p("3 script độc lập, dữ liệu test lưu trong thư mục data/:"),
      codeBlock([
        "# Per-model (tại-host, 3 GPU):",
        "ssh -p 13548 root@ssh3.vast.ai \"python3 -\" < bench_full.py",
        "",
        "# Full-pipeline 30 CCU (script Python):",
        "python3 bench_fullpipeline.py",
        "",
        "# Full-pipeline 30 CCU (Locust):",
        ".venv/bin/locust -f locustfile_30ccu.py --headless -u 30 -r 6 -t 120s \\",
        "  --host http://localhost:8131 --csv=load_30ccu --html=load_30ccu_report.html",
      ]),
      p("Full-pipeline test giới hạn 3 GPU bằng cách bớt 1 upstream server khỏi nginx LB (vast/on_start.sh sinh ra), khôi phục sau khi đo xong.", { italics: true, color: "555555" }),
      spacer(120),
      h2("Bằng chứng thô đính kèm"),
      p("bench_full.py + bench_full_result.json (per-model) · bench_fullpipeline.py + bench_fullpipeline_result.json (full-pipeline) · bench_fullpipeline_gateway.log (log gateway — 91 request, timestamp) · locustfile_30ccu.py + load_30ccu_report.html + load_30ccu_stats.csv (Locust) · data/gold_questions_91.json + data/rerank_docs_60.json (dữ liệu test).", { color: "555555" }),
    ],
  }],
});

Packer.toBuffer(doc).then((buf) => {
  require("fs").writeFileSync("perf-report-3v4-a100.docx", buf);
  console.log("written");
});
