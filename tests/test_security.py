from __future__ import annotations

from secai_gateway.agent.profiles import compose_system_prompt
from secai_gateway.security import (
    sanitize_output_language,
    strip_reasoning_leak,
)

# A representative chain-of-thought leak (shape observed live on the chat profile T10):
# planning prose + internal rule/tool names + self-instructions, then a pivot, then the answer.
LEAKED = """Ngữ cảnh: Người dùng hỏi về các CVE nghiêm trọng khác trong Langflow. \
Tôi đã có kết quả từ lần tìm kiếm đầu tiên (qdrant_search) liệt kê một số CVE.
Tuy nhiên, theo nguyên tắc tool_discipline: chỉ cần một câu trả lời dựa trên dữ liệu đã có.
Trả lời ngắn gọn, không thêm lời dẫn.
Kết thúc bằng câu hỏi hỗ trợ.
Vậy tôi sẽ trả lời:

Ngoài **CVE-2025-3248**, Langflow còn có **CVE-2026-33017** (CVSS 9.8).
Bạn cần IAS.AI hỗ trợ thêm gì nữa không?"""


def test_strip_reasoning_leak_keeps_only_the_answer() -> None:
    out = strip_reasoning_leak(LEAKED)
    assert out.startswith("Ngoài **CVE-2025-3248**")
    for banned in ("Ngữ cảnh:", "tool_discipline", "qdrant_search", "Tôi sẽ trả lời",
                   "Vậy tôi sẽ trả lời", "Kết thúc bằng câu hỏi hỗ trợ",
                   "Trả lời ngắn gọn"):
        assert banned not in out


def test_strip_reasoning_leak_leaves_normal_answer_untouched() -> None:
    normal = ("## CVE-2025-3248\n\n**CVE-2025-3248** là lỗ hổng code injection trong "
              "Langflow (CVSS 9.8).\n\nBạn cần IAS.AI hỗ trợ thêm gì nữa không?")
    assert strip_reasoning_leak(normal) == normal.strip()


def test_strip_reasoning_leak_without_pivot_is_not_truncated() -> None:
    # Marker present (qdrant_search) but no answer pivot → do not cut, only think-strip.
    text = "Kết quả từ qdrant_search cho thấy CVE-2025-3248 ảnh hưởng Langflow < 1.3.0."
    assert strip_reasoning_leak(text) == text


def test_strip_reasoning_leak_removes_think_blocks() -> None:
    text = "<think>tôi cần tra cứu</think>CVE-2025-3248 là lỗ hổng nghiêm trọng."
    assert strip_reasoning_leak(text) == "CVE-2025-3248 là lỗ hổng nghiêm trọng."


def test_sanitize_output_language_strips_reasoning_then_cjk() -> None:
    out = sanitize_output_language(LEAKED + "\n讲讲")
    assert out.startswith("Ngoài **CVE-2025-3248**")
    assert "讲" not in out
    assert "tool_discipline" not in out


def test_new_prompt_sections_present_and_wellformed() -> None:
    # Composed prompt must stay valid XML (parsed in test_prompts) and carry the new rules.
    from secai_gateway.agent.profiles import PROFILES

    chat_prompt = compose_system_prompt(PROFILES["chat"])
    assert "<output_integrity" in chat_prompt           # anti CoT-leak (base)
    assert "<memory_and_summary_policy>" in chat_prompt  # anti context-poisoning (chat)
