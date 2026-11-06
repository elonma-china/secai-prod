# Dynamic Intelligent Template Report Implementation Plan

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** Enable the AI template report generator to dynamically expand arbitrary freeform ideas into full report outlines, and automatically determine and fill content for any custom sections in uploaded docx templates.

**Architecture:** A unified one-shot intelligent prompt that instructs the LLM to inspect the user's input/file, classify static vs fillable sections, expand brief ideas into full professional cybersecurity reports, and generate structured content and markdown tables for any arbitrary or custom section. The backend pipeline handles both freeform text-driven report construction and docx template in-place insertion.

**Tech Stack:** Python 3.10+, FastAPI, python-docx, docxtpl, Qwen2.5/LLM completion API.

---

### Task 1: Prompt & Dynamic Schema Enhancement in routes.py

**Files:**
- Modify: `secai/secai-agent-gateway/src/secai_gateway/api/routes.py:2550-2720`

**Step 1: Update prompt to support freeform idea expansion and arbitrary heading inspection**
- Instruct LLM that if the user input is a brief/freeform idea (e.g. a single request or loose notes), it should act as a cybersecurity lead architect to plan and generate a complete multi-section report outline with headings, tables, and sub-points.
- Instruct LLM that if the input is a structured template or docx file with custom headings, it must semantically map each heading to appropriate parts of `scan_data` (overview stats table, vuln details table, remediation, technical analysis, custom topic) and generate content for every section.
- Output schema accepts both `{"sections": [{"heading": "...", "content": "..."}]}` and flat dictionary `{ "Heading": "Content" }`.

**Step 2: Parse and normalize LLM output into unified data structure**
- Support both list of section objects and dict of headings to content.

---

### Task 2: Freeform Idea Expansion in doc_init

**Files:**
- Modify: `secai/secai-agent-gateway/src/secai_gateway/api/routes.py:1900-1980` and `2520-2560`

**Step 1: Detect freeform idea vs explicit template**
- If no `.docx` file is uploaded (`file_bytes` empty):
  - Check if `effective_template_text` has 2 or more distinct numbered headings (`extract_section_number`).
  - If not (user entered an idea, topic, or brief), pass it to the LLM to dynamically generate the complete report schema (Title, Recipient, Sections, Signatures).
  - Construct `doc_init` from the dynamically generated structure, including formal header, title, recipient, headings, tables, sub-points, and signature block.

**Step 2: Preserve structured templates**
- If `effective_template_text` already has explicit structured headings (e.g. 1. Thời gian, 2. Tổng quan...), preserve them and generate content under each heading.

---

### Task 3: Intelligent Section Mapping for Uploaded docx Files

**Files:**
- Modify: `secai/secai-agent-gateway/src/secai_gateway/api/routes.py:2725-2830`

**Step 1: Generalized section matching without hardcoded synonym limits**
- Remove the strict restriction that required headings to match one of the 5 hardcoded names (`SEMANTIC_SYNONYMS`).
- For ANY heading in the docx, match it with AI generated content via `find_matching_content`.
- If a section asks for overview or statistics, ensure a 3-col summary table is provided.
- If a section asks for details or vulnerabilities, ensure a 5-col vuln table is provided.
- For custom headings, insert the AI generated analysis directly.

**Step 2: Fallback for empty or single-heading docx templates**
- If an uploaded docx contains fewer than 2 content headings, dynamically insert all generated report sections into the document.

---

### Task 4: Verification and Testing

**Files:**
- Test: Scratch test script using `test_report.py`
- Target container: `secai-docker-gateway`

**Step 1: Compile and sync replica**
- Run `python3 -m py_compile`
- Copy to `/home/ict/AIEngines/src/secai_gateway/api/routes.py`
- Restart `docker restart secai-docker-gateway`

**Step 2: Test Case A - Freeform idea prompt**
- Input: `"Báo cáo tóm tắt an toàn thông tin máy chủ Web, chú trọng lỗ hổng SQL Injection cho ban giám đốc"`
- Verify: Full report is generated with Title, Kính gửi, sections with 1.1, 2.1 (3-col table), 3.1 (5-col table), 4.1 (a,b), 5.1, and signatures.

**Step 3: Test Case B - Custom structured template**
- Input template with custom headings:
  ```
  Kính gửi: Ban Giám đốc
  1. Mục tiêu khảo sát hệ thống
  2. Các điểm yếu an ninh mạng phát hiện
  3. Phương án xử lý và lộ trình khắc phục
  ```
- Verify: Content is generated for all 3 custom headings, with tables and clean formatting.
