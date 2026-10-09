#!/usr/bin/env python3
"""Build knowledge_eval_set.jsonl (concept / remediation / entity-remediation / control).

Gold is the PARENT document of the chunk that should be retrieved (cheat sheet file, CWE/CAPEC/
ATT&CK id, ASVS section), so it survives re-chunking. `key_points` are alternatives-groups
("a|b" = either spelling) that a correct answer must mention; they are facts any competent
answer has, not sentences lifted from the chunks. Controls have no gold: they must NOT trigger
knowledge routing. Validated against secai-data/knowledge/chunks.jsonl when it exists.

  python3 build_knowledge_eval_set.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
CHUNKS = Path("/home/ict/AIEngines/secai/secai-data/knowledge/chunks.jsonl")


def cs(name: str) -> str:
    return f"owasp-cs:{name}_Cheat_Sheet"


# slug, cheat sheet, extra gold parents, concept question, remediation question, concept kp, remediation kp
T = [
    ("sqli", "SQL_Injection_Prevention", ["CWE-89", "CAPEC-66"], "SQL injection là gì và xảy ra như thế nào?",
     "Làm sao để chống SQL injection trong ứng dụng web?", ["truy vấn|query", "đầu vào|input|người dùng|user"], ["prepared statement|parameterized|tham số hóa", "allow-list|allowlist|danh sách cho phép|validation"]),
    ("xss", "Cross_Site_Scripting_Prevention", ["CWE-79"], "What is cross-site scripting and what are its types?",
     "Cách phòng chống XSS khi hiển thị dữ liệu người dùng nhập?", ["script", "browser|trình duyệt"], ["encod|mã hóa|escape", "context|ngữ cảnh|csp|content security policy"]),
    ("csrf", "Cross-Site_Request_Forgery_Prevention", ["CWE-352"], "CSRF là gì?",
     "How do I protect a web app against CSRF?", ["request|yêu cầu", "session|cookie|phiên"], ["token", "samesite"]),
    ("ssrf", "Server_Side_Request_Forgery_Prevention", ["CWE-918"], "SSRF là gì, tại sao nguy hiểm với cloud?",
     "Biện pháp phòng chống SSRF hiệu quả là gì?", ["server|máy chủ", "request|yêu cầu|url"], ["allow-list|allowlist|danh sách cho phép", "metadata|nội bộ|internal|private"]),
    ("xxe", "XML_External_Entity_Prevention", ["CWE-611"], "What is an XXE attack?",
     "Cấu hình parser XML như thế nào để tránh XXE?", ["xml", "entity|thực thể"], ["dtd|doctype", "disable|tắt|vô hiệu"]),
    ("deser", "Deserialization", ["CWE-502"], "Insecure deserialization là gì?",
     "How to safely handle deserialization of untrusted data?", ["object|đối tượng", "untrusted|không tin cậy"], ["integrity|chữ ký|signature|kiểm tra", "allow|whitelist|danh sách cho phép|avoid|tránh"]),
    ("jwt", "JSON_Web_Token", [], "JWT gồm những phần nào và có rủi ro gì?",
     "Best practices to use JWT securely?", ["header", "signature|chữ ký"], ["algorithm|thuật toán|alg", "expir|hết hạn|exp"]),
    ("oauth", "OAuth2", [], "OAuth 2.0 authorization code flow hoạt động ra sao?",
     "How to secure an OAuth2 implementation?", ["authorization code|mã ủy quyền", "token"], ["pkce", "redirect"]),
    ("saml", "SAML_Security", [], "SAML là gì và dùng để làm gì?",
     "Những lưu ý bảo mật khi triển khai SAML?", ["assertion", "sso|single sign|đăng nhập một lần|identity provider|idp"], ["signature|chữ ký", "xml|validat"]),
    ("session", "Session_Management", [], "Session ID là gì và vì sao cần quản lý an toàn?",
     "How do I secure session cookies?", ["session|phiên", "cookie|id"], ["httponly", "secure"]),
    ("pwdstore", "Password_Storage", ["owasp-asvs:6.2"], "Tại sao không được lưu mật khẩu dạng plaintext?",
     "Which algorithm should I use to store passwords?", ["hash|băm", "salt"], ["argon2|bcrypt|scrypt|pbkdf2", "work factor|cost|iteration|vòng lặp|hệ số"]),
    ("mfa", "Multifactor_Authentication", [], "MFA là gì?",
     "Cách triển khai xác thực đa yếu tố an toàn?", ["factor|yếu tố", "password|mật khẩu|biometric|sinh trắc"], ["fido|passkey|totp|authenticator|webauthn", "sms|recovery|khôi phục|phishing"]),
    ("credstuff", "Credential_Stuffing_Prevention", ["CAPEC-600"], "Credential stuffing là gì?",
     "How to defend against credential stuffing attacks?", ["credential|thông tin đăng nhập", "breach|rò rỉ|leak|reuse|tái sử dụng"], ["mfa|multi-factor|đa yếu tố", "rate|giới hạn|captcha|breached"]),
    ("forgotpw", "Forgot_Password", [], "Quy trình quên mật khẩu hoạt động thế nào?",
     "Thiết kế chức năng quên mật khẩu sao cho an toàn?", ["email|reset|đặt lại", "token"], ["generic|chung|không tiết lộ|same response", "expire|hết hạn|single use|một lần"]),
    ("authn", "Authentication", [], "Authentication khác authorization ở điểm nào?",
     "Những nguyên tắc xác thực người dùng an toàn cần tuân thủ?", ["identity|danh tính|xác thực", "password|credential|mật khẩu"], ["https|tls", "lockout|rate|brute|khóa"]),
    ("authz", "Authorization", ["CWE-862"], "Broken access control nghĩa là gì?",
     "How to implement authorization correctly?", ["permission|quyền", "access|truy cập"], ["least privilege|đặc quyền tối thiểu", "deny by default|mặc định từ chối|every request|mọi request|server-side"]),
    ("idor", "Insecure_Direct_Object_Reference_Prevention", ["CWE-639"], "IDOR là lỗi gì?",
     "Cách ngăn chặn IDOR trong REST API?", ["object|đối tượng|id", "authoriz|quyền|truy cập"], ["access control|kiểm tra quyền|authorization check", "indirect|unpredictable|uuid|không đoán"]),
    ("massassign", "Mass_Assignment", ["CWE-915"], "Mass assignment vulnerability là gì?",
     "How can I prevent mass assignment in my framework?", ["bind|gán|field|trường", "request|object"], ["allow|whitelist|danh sách cho phép|dto", "block|readonly|denylist|không cho"]),
    ("cmdinj", "OS_Command_Injection_Defense", ["CWE-78"], "OS command injection là gì?",
     "Cách tránh command injection khi gọi lệnh hệ thống?", ["command|lệnh", "shell"], ["avoid|tránh|api|library|thư viện", "parameter|tham số|argument|validat|allow"]),
    ("ldapinj", "LDAP_Injection_Prevention", ["CWE-90"], "LDAP injection là gì?",
     "How to prevent LDAP injection?", ["ldap", "filter|query|truy vấn"], ["escap", "allow|validat|parameter"]),
    ("upload", "File_Upload", ["CWE-434"], "Rủi ro khi cho người dùng upload file là gì?",
     "Best practices for secure file upload?", ["file", "extension|đuôi|content|mime|type"], ["allow|danh sách cho phép", "webroot|outside|ngoài|rename|đổi tên|antivirus|scan"]),
    ("csp", "Content_Security_Policy", [], "Content Security Policy dùng để làm gì?",
     "How to deploy a strict CSP?", ["header", "script|source|nguồn"], ["nonce|hash", "report|unsafe-inline|script-src"]),
    ("clickjack", "Clickjacking_Defense", ["CWE-1021"], "Clickjacking là gì?",
     "Cách chống clickjacking?", ["iframe|frame", "click"], ["frame-ancestors|x-frame-options", "csp|samesite|header"]),
    ("headers", "HTTP_Headers", [], "HTTP security headers là gì?",
     "Những HTTP security header nào nên bật?", ["header", "browser|trình duyệt|response"], ["x-content-type-options|nosniff", "strict-transport-security|hsts|content-security-policy"]),
    ("hsts", "HTTP_Strict_Transport_Security", [], "HSTS là gì?",
     "Cấu hình HSTS thế nào cho đúng?", ["https", "browser|trình duyệt"], ["max-age", "includesubdomains|preload"]),
    ("tls", "Transport_Layer_Security", [], "TLS dùng để làm gì?",
     "Which TLS versions and settings should I use?", ["encrypt|mã hóa", "transport|truyền|network|mạng"], ["tls 1.2|tls 1.3|1.2|1.3", "cipher|certificate|chứng chỉ|hsts"]),
    ("cryptostore", "Cryptographic_Storage", [], "Lưu trữ dữ liệu nhạy cảm bằng mã hóa nghĩa là gì?",
     "Which encryption algorithms are recommended for data at rest?", ["encrypt|mã hóa", "key|khóa"], ["aes", "key management|quản lý khóa|gcm|authenticated"]),
    ("keymgmt", "Key_Management", [], "Key management gồm những công việc gì?",
     "Best practices for cryptographic key rotation and storage?", ["key|khóa", "lifecycle|vòng đời|generate|sinh"], ["rotation|xoay vòng|rotate", "hsm|vault|kms|separate|tách"]),
    ("secrets", "Secrets_Management", [], "Secrets management là gì?",
     "Làm sao quản lý secrets như API key, mật khẩu DB an toàn?", ["secret|bí mật|credential", "api key|password|token|mật khẩu"], ["vault|kms|secret manager", "hard-?code|rotation|xoay vòng|source|git"]),
    ("docker", "Docker_Security", [], "Docker container bảo mật khác máy ảo ở điểm nào?",
     "Hardening Docker containers: cần làm gì?", ["container", "image|host|kernel"], ["non-root|user|không dùng root|root", "privileged|read-only|capabilit|cap-drop"]),
    ("k8s", "Kubernetes_Security", [], "Các lớp bảo mật chính của Kubernetes?",
     "How to harden a Kubernetes cluster?", ["cluster|pod", "api|node"], ["rbac", "network polic|pod security|secrets"]),
    ("cicd", "CI_CD_Security", [], "Rủi ro bảo mật của pipeline CI/CD?",
     "Làm sao bảo vệ pipeline CI/CD?", ["pipeline", "build|deploy|triển khai"], ["least privilege|đặc quyền tối thiểu|permission|quyền", "secret|branch|review|signed|ký"]),
    ("ghactions", "GitHub_Actions_Security", [], "GitHub Actions có những rủi ro bảo mật nào?",
     "Best practices to secure GitHub Actions workflows?", ["workflow", "action|secret|token"], ["pin|sha|commit", "permissions|github_token|least privilege"]),
    ("supplychain", "Software_Supply_Chain_Security", [], "Software supply chain attack là gì?",
     "Cách giảm rủi ro chuỗi cung ứng phần mềm?", ["dependency|phụ thuộc|thư viện|package", "build|attack|tấn công"], ["sbom", "verify|signature|ký|pin|lock|provenance"]),
    ("vulndep", "Vulnerable_Dependency_Management", [], "Vulnerable dependency là gì?",
     "How to manage vulnerable third-party dependencies?", ["dependency|phụ thuộc|thư viện|library", "vulnerab|lỗ hổng|cve"], ["scan|quét|sca|dependabot|audit", "update|cập nhật|patch|upgrade"]),
    ("inputval", "Input_Validation", [], "Input validation để làm gì?",
     "Nguyên tắc validate dữ liệu đầu vào đúng cách?", ["input|đầu vào", "validat|kiểm tra"], ["allow|danh sách cho phép|whitelist", "server-side|phía server|syntactic|semantic|regex"]),
    ("errors", "Error_Handling", [], "Error handling an toàn là gì?",
     "Xử lý lỗi thế nào để không lộ thông tin nhạy cảm?", ["error|lỗi", "exception|ngoại lệ|message|thông báo"], ["generic|chung|không lộ|stack trace|không hiển thị", "log|ghi"]),
    ("logging", "Logging", [], "Tại sao cần ghi log bảo mật?",
     "What should and should not be logged for security?", ["log|nhật ký", "event|sự kiện"], ["sensitive|nhạy cảm|password|mật khẩu|pii", "integrity|tamper|toàn vẹn|timestamp|time"]),
    ("threatmodel", "Threat_Modeling", [], "Threat modeling là gì?",
     "Cách thực hiện threat modeling cho một hệ thống mới?", ["threat|mối đe dọa", "model|mô hình|system|hệ thống"], ["stride|data flow|dfd|luồng dữ liệu", "mitigat|giảm thiểu|asset|tài sản"]),
    ("rest", "REST_Security", [], "REST API có những rủi ro bảo mật phổ biến nào?",
     "Best practices for securing REST APIs?", ["api", "http|rest"], ["https|tls", "authentic|jwt|api key|rate|validat"]),
    ("graphql", "GraphQL", [], "GraphQL có điểm yếu bảo mật nào đặc thù?",
     "How to secure a GraphQL API?", ["query|truy vấn", "schema|introspection|resolver"], ["depth|complexity|độ sâu|limit|giới hạn", "introspection|authoriz|quyền"]),
    ("websocket", "WebSocket_Security", [], "WebSocket khác HTTP thế nào về bảo mật?",
     "Cách bảo mật kết nối WebSocket?", ["websocket|ws", "connection|kết nối"], ["origin", "wss|tls|authenticat|xác thực"]),
    ("dos", "Denial_of_Service", ["CWE-400"], "Denial of service là gì?",
     "How to mitigate DoS attacks on an application?", ["service|dịch vụ", "resource|tài nguyên|availability|sẵn sàng"], ["rate|giới hạn|limit", "cdn|scal|timeout|cache|filter"]),
    ("llminj", "LLM_Prompt_Injection_Prevention", [], "Prompt injection trong LLM là gì?",
     "Cách giảm thiểu prompt injection cho ứng dụng LLM?", ["prompt", "llm|model|mô hình"], ["input|filter|validat|lọc", "privilege|quyền|human|output|đầu ra"]),
    ("ssti", "Server_Side_Template_Injection_Prevention", [], "Server-side template injection là gì?",
     "How to prevent SSTI?", ["template", "server"], ["sandbox|logic-less|không cho|avoid|tránh", "validat|allow|escap"]),
    ("protopoll", "Prototype_Pollution_Prevention", ["CWE-1321"], "Prototype pollution trong JavaScript là gì?",
     "Cách ngăn chặn prototype pollution?", ["prototype", "object|javascript"], ["freeze|map|null|object.create", "validat|schema|allow|__proto__"]),
    ("zerotrust", "Zero_Trust_Architecture", [], "Zero trust là gì?",
     "Các bước triển khai kiến trúc zero trust?", ["trust|tin cậy", "verify|xác minh|identity|danh tính"], ["least privilege|đặc quyền tối thiểu|microsegment|phân đoạn", "continuous|liên tục|device|thiết bị|policy|chính sách"]),
    ("webhook", "Webhook_Security", [], "Webhook là gì và rủi ro bảo mật?",
     "How do I verify and secure incoming webhooks?", ["webhook", "request|callback|http"], ["signature|hmac|chữ ký", "replay|timestamp|ip|https"]),
    ("nosql", "NoSQL_Security", [], "NoSQL injection là gì?",
     "Cách bảo mật cơ sở dữ liệu NoSQL?", ["nosql|mongodb", "query|truy vấn"], ["authenticat|xác thực|access control", "validat|sanitiz|encrypt|parameter"]),
]

# ASVS / ATT&CK / CAPEC / CWE direct remediation (no cheat sheet needed).
ENTITY = [
    ("e-cwe89", "Cách khắc phục CWE-89 là gì?", ["CWE-89", cs("SQL_Injection_Prevention")], ["parameterized|prepared|tham số hóa", "validat|input"]),
    ("e-cwe79", "How to mitigate CWE-79?", ["CWE-79", cs("Cross_Site_Scripting_Prevention")], ["encod|escap", "validat|csp|framework"]),
    ("e-cwe22", "Biện pháp phòng chống CWE-22 path traversal?", ["CWE-22"], ["validat|canonical|chuẩn hóa|allow", "path|đường dẫn"]),
    ("e-cwe78", "Mitigations for CWE-78 OS command injection?", ["CWE-78", cs("OS_Command_Injection_Defense")], ["avoid|library|parameter", "validat|allow"]),
    ("e-capec66", "Cách phòng chống CAPEC-66 SQL injection?", ["CAPEC-66", cs("SQL_Injection_Prevention")], ["parameter|prepared|validat", "privilege|least|quyền"]),
    ("e-t1190", "How to mitigate T1190 exploit public-facing application?", ["T1190"], ["patch|update|scan|vulnerab", "segment|waf|application isolation|exploit protection"]),
    ("e-t1059", "Biện pháp giảm thiểu cho T1059 command and scripting interpreter?", ["T1059"], ["execution|signed|code signing|allow|disable|restrict", "antivirus|behavior|privileged"]),
    ("e-t1566", "How can organizations mitigate T1566 phishing?", ["T1566"], ["training|awareness|đào tạo|user", "email|antivirus|filter|network"]),
    ("e-t1078", "Mitigation for T1078 valid accounts?", ["T1078"], ["mfa|multi-factor|password|privileged|least", "audit|policy|account"]),
    ("e-cwe502", "Cách khắc phục CWE-502 deserialization of untrusted data?", ["CWE-502", cs("Deserialization")], ["untrusted|không tin cậy|avoid|tránh", "integrity|signature|allow|validat"]),
]
ASVS = [
    ("a-pwlen", "ASVS yêu cầu độ dài mật khẩu tối thiểu là bao nhiêu?", ["owasp-asvs:6.2"], ["8", "15"]),
    ("a-session", "What does ASVS require for session token generation and logout?", ["owasp-asvs:7.2", "owasp-asvs:7.4", "owasp-asvs:7.1"], ["session|token", "random|entropy|terminat|logout|invalid"]),
    ("a-crypto", "ASVS 5.0 có yêu cầu gì về thuật toán mã hóa cho dữ liệu?", ["owasp-asvs:11.3", "owasp-asvs:11.2", "owasp-asvs:11.4", "owasp-asvs:11.1"], ["algorithm|thuật toán|cipher", "aes|key|khóa|approved"]),
    ("a-fileup", "What are the ASVS requirements for file upload handling?", ["owasp-asvs:5.2", "owasp-asvs:5.3", "owasp-asvs:5.1", "owasp-asvs:5.4"], ["file", "size|type|extension|scan|content|storage"]),
    ("a-oauth", "ASVS yêu cầu gì với OAuth và OIDC?", ["owasp-asvs:10.4", "owasp-asvs:10.2", "owasp-asvs:10.3", "owasp-asvs:10.1", "owasp-asvs:10.5", "owasp-asvs:10.6", "owasp-asvs:10.7"], ["oauth|oidc", "client|redirect|token|pkce|authorization"]),
    ("a-logging", "ASVS says what about security logging and error handling?", ["owasp-asvs:16.2", "owasp-asvs:16.3", "owasp-asvs:16.5", "owasp-asvs:16.1", "owasp-asvs:16.4"], ["log", "sensitive|error|event|tamper|protect"]),
]
CONTROLS = ["xin chào", "hôm nay trời thế nào nhỉ", "bạn là ai", "bạn có thể giúp được những gì", "dữ liệu của hệ thống cập nhật đến khi nào",
            "viết giúp tôi một bài thơ về mùa thu", "công thức nấu phở bò", "1+1 bằng mấy", "cảm ơn bạn nhé", "tôi đang buồn quá"]


def main() -> int:
    rows = []
    for slug, sheet, extra, cq, rq, ckp, rkp in T:
        gold = [cs(sheet), *extra]
        rows.append({"id": f"c-{slug}", "group": "concept", "query": cq, "gold_parents": gold, "key_points": ckp})
        rows.append({"id": f"r-{slug}", "group": "remediation", "query": rq, "gold_parents": gold, "key_points": rkp})
    for id_, q, gold, kp in ENTITY + ASVS:
        rows.append({"id": id_, "group": "entity_remediation" if id_.startswith("e-") else "asvs", "query": q, "gold_parents": gold, "key_points": kp})
    for i, q in enumerate(CONTROLS, 1):
        rows.append({"id": f"x-{i:02d}", "group": "control", "query": q, "gold_parents": [], "key_points": []})
    for r in rows:
        r["lang"] = "vi" if any(ch in r["query"].lower() for ch in "ăâêôơưđáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ") or r["id"].startswith("x-") else "en"
    if CHUNKS.exists():
        parents = {json.loads(l)["parent_id"] for l in CHUNKS.open(encoding="utf-8")}
        prefixes = {p.rsplit(".", 1)[0] if p.startswith("owasp-asvs:") else p for p in parents}
        bad = [(r["id"], g) for r in rows for g in r["gold_parents"] if g not in parents and g not in prefixes]
        if bad:
            print("UNKNOWN GOLD:", bad, file=sys.stderr)
            return 1
    out = HERE / "knowledge_eval_set.jsonl"
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    from collections import Counter
    print(len(rows), dict(Counter(r["group"] for r in rows)), dict(Counter(r["lang"] for r in rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
