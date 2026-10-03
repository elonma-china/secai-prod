from __future__ import annotations


STATUS = {
    "INVALID_REQUEST": 400, "UNSUPPORTED_LANGUAGE": 400, "UNSUPPORTED_PAIR": 400,
    "LANGUAGE_DISABLED": 403, "TEXT_TOO_LONG": 413, "TOKEN_LIMIT_EXCEEDED": 413,
    "SOURCE_LANGUAGE_MISMATCH": 422, "UNSAFE_UNICODE": 422, "RATE_LIMITED": 429,
    "EMPTY_TRANSLATION": 502, "OUTPUT_VALIDATION_FAILED": 502,
    "MODEL_UNAVAILABLE": 503, "TRANSLATION_TIMEOUT": 504,
}

MESSAGES = {
    "INVALID_REQUEST": "Yêu cầu JSON không hợp lệ.",
    "UNSUPPORTED_LANGUAGE": "Ngôn ngữ không được hỗ trợ.",
    "UNSUPPORTED_PAIR": "Cặp ngôn ngữ không được hỗ trợ.",
    "LANGUAGE_DISABLED": "Ngôn ngữ này chưa được bật.",
    "TEXT_TOO_LONG": "Văn bản vượt giới hạn độ dài.",
    "TOKEN_LIMIT_EXCEEDED": "Văn bản vượt giới hạn token.",
    "SOURCE_LANGUAGE_MISMATCH": "Ngôn ngữ nguồn không khớp.",
    "UNSAFE_UNICODE": "Văn bản chứa ký tự điều khiển không an toàn.",
    "RATE_LIMITED": "Quá nhiều yêu cầu; vui lòng thử lại sau.",
    "EMPTY_TRANSLATION": "Mô hình không trả bản dịch.",
    "OUTPUT_VALIDATION_FAILED": "Bản dịch không qua kiểm tra an toàn.",
    "MODEL_UNAVAILABLE": "Mô hình dịch chưa sẵn sàng.",
    "TRANSLATION_TIMEOUT": "Yêu cầu dịch quá thời gian.",
}


class TranslationError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
