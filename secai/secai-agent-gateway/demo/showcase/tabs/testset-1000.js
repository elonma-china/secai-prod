import { mdToHtml } from "../md.js";

// Tách 1 file markdown "hồ sơ chuẩn hoá" thành: phần mở đầu (trước heading ## đầu tiên,
// gồm H1 + blockquote) + danh sách mục theo từng heading ##. Mỗi mục render thành 1 card
// riêng thay vì đổ nguyên khối markdown — theo đúng yêu cầu hiển thị "thành mục".
function splitSections(md) {
  const lines = md.replace(/\r\n?/g, "\n").split("\n");
  let i = 0;
  const introLines = [];
  while (i < lines.length && !/^##\s+/.test(lines[i])) introLines.push(lines[i++]);

  const sections = [];
  while (i < lines.length) {
    const h = lines[i].match(/^##\s+(.*)$/);
    const title = h ? h[1] : "";
    i++;
    const body = [];
    while (i < lines.length && !/^##\s+/.test(lines[i])) body.push(lines[i++]);
    sections.push({ title, body: body.join("\n") });
  }
  return { intro: introLines.join("\n"), sections };
}

export async function render(container, api) {
  const data = await api.get("/showcase-api/testset/standardization");
  const { intro, sections } = splitSections(data.content || "");

  const cards = sections
    .map(
      (s) => `
    <div class="card">
      <h3>${s.title}</h3>
      <div class="md">${mdToHtml(s.body)}</div>
    </div>`
    )
    .join("\n");

  container.innerHTML = `
    <div class="card">
      <div class="md">${mdToHtml(intro)}</div>
    </div>
    ${cards}
    <p class="hint">Nguồn: <code>${data.source ?? ""}</code></p>
  `;
}
