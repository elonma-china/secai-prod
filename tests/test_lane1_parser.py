"""parse_action: chịu được JSON bẩn của Cyber-F1 (fence/prose), reject rác + tool ngoài menu."""
from __future__ import annotations

import secai_gateway.poc.lane1 as lane1


def test_parse_clean_json():
    a = lane1.parse_action('{"thought":"probe","tool":"http_request","args":{"path":"/"},"done":false}')
    assert a is not None and a.tool == "http_request" and a.args["path"] == "/" and a.done is False


def test_parse_json_in_fence():
    txt = "Đây là bước tiếp:\n```json\n{\"tool\":\"nmap\",\"args\":{\"target\":\"h\"}}\n```\nxong."
    a = lane1.parse_action(txt)
    assert a is not None and a.tool == "nmap" and a.args["target"] == "h"


def test_parse_json_in_prose():
    a = lane1.parse_action('Tôi sẽ gửi payload {"tool":"http_request","args":{"method":"POST","body":"x"}} nhé')
    assert a is not None and a.tool == "http_request" and a.args["method"] == "POST"


def test_parse_garbage_returns_none():
    assert lane1.parse_action("không có json ở đây") is None
    assert lane1.parse_action("") is None
    assert lane1.parse_action('{"tool": broken') is None


def test_parse_tool_not_in_menu():
    assert lane1.parse_action('{"tool":"metasploit","args":{}}') is None
    assert lane1.parse_action('{"tool":"poc_search","args":{"query":"x"}}') is None


def test_parse_args_defaults_to_dict():
    a = lane1.parse_action('{"tool":"searchsploit"}')
    assert a is not None and a.args == {}


def test_parse_nested_json_body():
    # payload chứa dấu ngoặc nhọn trong string phải không phá được brace-scan
    a = lane1.parse_action('{"tool":"http_request","args":{"body":"a={b:1}"},"done":true}')
    assert a is not None and a.args["body"] == "a={b:1}" and a.done is True
