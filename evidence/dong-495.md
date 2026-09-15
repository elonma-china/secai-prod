Hỗ trợ Pentest: Hỗ trợ định hướng và tư vấn Pentest
Bộ kiểm thử chất lượng gồm 1000 câu hỏi–đáp thật đã chạy trên hệ thống, log tại `HO-SO-GOC-NGHIEM-THU-AI/03. Kpi_results/Model/quality/raw-logs/answers.jsonl`. Dưới đây là bản ghi hệ thống đã trả lời đúng chủ đề của chỉ tiêu này, trích nguyên văn câu hỏi và câu trả lời của hệ thống.

**Bản ghi `acc_th_0802`** — capability `pentest`, nhóm `tinh_huong`; công cụ hệ thống đã gọi khi trả lời: trả lời trực tiếp, không gọi công cụ RAG.

Câu hỏi (trích nguyên văn):

Tôi chuẩn bị pentest một ứng dụng web nội bộ. Trình bày các giai đoạn theo PTES và mục tiêu từng giai đoạn.

Trả lời của hệ thống (trích nguyên văn, đã lược bớt phần giữa và đánh dấu `[...]`):

Dưới đây là quy trình pentest web ứng dụng nội bộ theo **PTES (Penetration Testing Execution Standard)** — 7 giai đoạn chính:

---

## 1. Pre-engagement (Lên kế hoạch & thỏa thuận)

**Mục tiêu:** Xác định phạm vi, luật chơi, và mục tiêu trước khi bắt đầu.

- Xác định **scope**: domain, subdomain, IP, API endpoints nào được test.
- Xác định **out-of-scope**: hệ thống không được touching (production DB, third-party service).
- Thỏa thuận **rules of engagement (RoE)**: thời gian test, tần suất request, có đượcDoS/không, có cần off-business hours không.
- Xác định **loại test**: black-box / gray-box / white-box.
- Kí **NDA / authorization letter** — bắt buộc, không test khi chưa có chữ ký.

---

## 2. Intelligence Gathering (Thu thập thông tin)

**Mục tiêu:** Thu thập mọi thông tin công khai về target để xây dựng attack surface.

**Active reconnaissance:**
- `nmap` scan — port, service version, OS fingerprint
- `subfinder` / `httprobe` — subdomain enumeration
- `wappalyzer` / `whatweb` — nhận diện công nghệ (framework, CMS, web server)

**Passive reconnaissance:**
- `waybackurls`, `gau` — lấy URL từ archive
- Shodan, Censy, Shodan.io
- Google dorking: `site:target.com filetype:pdf`, `inurl:admin`, `intitle:"index of"`

[...]

1. Pre-engagement      → Scope, RoE, authorization
2. Intelligence Gather → Recon, attack surface mapping
3. Threat Modeling     → Attack vector, prioritization
4. Vulnerability Analysis → Scan + manual testing
5. Vulnerability Assessment → Triage, CWE mapping, severity
6. Exploitation         → PoC, proof of impact
7. Reporting            → Technical + executive report
```

---

Muốn tôi đi sâu vào phase nào (ví dụ: chi tiết từng kỹ thuật recon, hay template report pentest), hoặc cần setup tool chain cho phase cụ thể?

---

**Kết luận:** Hệ thống đưa ra kế hoạch pentest ứng dụng web theo đủ 7 giai đoạn PTES (phạm vi/RoE, thu thập thông tin, threat modeling, phân tích và đánh giá lỗ hổng, khai thác chứng minh PoC, báo cáo) và không hướng dẫn thao tác phá hoại — đạt yêu cầu hỗ trợ định hướng và tư vấn Pentest.
