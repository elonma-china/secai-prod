"""A MODEST, fixed set of real security questions (VI + EN) used for the gateway
full-chatflow measurements (R2-RESP15, R2-TTFT, R2-CHATFLOW). Deliberately small (the
gateway is PRODUCTION, :8131) and run sequentially — this is a latency/throughput
sample, not a load test (see performance/load_ramp.py for capacity probing, which is
env-gated separately).
"""
QUERIES: list[str] = [
    "CVE-2024-3094 là gì và mức độ nghiêm trọng ra sao?",
    "What is SQL injection and how can it be prevented?",
    "Giải thích CWE-79 (Cross-Site Scripting) một cách ngắn gọn.",
    "What is the difference between CVSS 3.1 and CVSS 4.0 severity scoring?",
    "Cách phát hiện lỗ hổng Log4Shell (CVE-2021-44228) trên hệ thống Linux?",
    "Explain what a buffer overflow vulnerability is in simple terms.",
    "CVE-2023-38831 ảnh hưởng đến phần mềm nào và cách khai thác cơ bản?",
    "What is the purpose of a Web Application Firewall (WAF)?",
]
