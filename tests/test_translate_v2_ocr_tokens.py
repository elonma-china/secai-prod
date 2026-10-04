import pytest

from secai_gateway.translation.technical_tokens import literal_spans, protect_literals, restore_literals
from secai_gateway.translation.types import TranslationError


def test_round_trip_occurrences_and_valid_networks():
    source = "CVE-2024-1234 từ 192.0.2.1/24; CVE-2024-1234 ở 999.0.2.1."
    protected = protect_literals(source)
    assert "CVE-2024-1234" not in protected.text
    assert "192.0.2.1/24" not in protected.text
    assert "999.0.2.1" in protected.text
    assert restore_literals(protected.text, protected) == source
    assert protected.text.count("⟦T") == 3


@pytest.mark.parametrize("mutation", ["delete", "duplicate", "reverse", "unknown", "malformed"])
def test_bad_placeholder_stream_is_rejected(mutation):
    protected = protect_literals("CVE-2024-1234 and CVE-2024-1235")
    first, second = list(__import__("re").findall(r"⟦T[0-9a-f]+:\d+⟧", protected.text))
    output = {
        "delete": first,
        "duplicate": first + first + second,
        "reverse": second + first,
        "unknown": first + second.replace(":1⟧", ":9⟧"),
        "malformed": first + second[:-1],
    }[mutation]
    with pytest.raises(TranslationError) as exc:
        restore_literals(output, protected)
    assert exc.value.code == "TOKEN_INTEGRITY_FAILED"


def test_existing_placeholder_and_overlapping_url_are_lossless():
    source = "⟦Tbad:0⟧ https://example.test/CVE-2024-1234 and `curl https://example.test`"
    protected = protect_literals(source)
    assert restore_literals(protected.text, protected) == source
    assert "https://example.test/CVE-2024-1234" not in protected.text
    assert "`curl https://example.test`" not in protected.text


def test_secret_never_reaches_model():
    source = "password: sk_live_" + "A" * 20
    protected = protect_literals(source)
    assert "sk_live_" not in protected.text
    assert restore_literals(protected.text, protected) == source


@pytest.mark.parametrize("left,right", [("中", "中"), ("Ж", "Я"), ("ا", "ب")])
def test_technical_tokens_adjacent_to_non_ascii_script(left, right):
    source = f"{left}CVE-2024-1234{right} {left}CWE-79{right} {left}192.0.2.1{right}"
    protected = protect_literals(source)
    for literal in ("CVE-2024-1234", "CWE-79", "192.0.2.1"):
        assert literal not in protected.text
    assert restore_literals(protected.text, protected) == source
    assert len(literal_spans(source)) == 3


@pytest.mark.parametrize("source", [
    "preCVE-2024-1234", "CVE-2024-1234suffix", "CVE-2024-1234_more",
    "CVE-2024-1234.txt", "x192.0.2.1", "192.0.2.1suffix",
    "xCWE-79", "CWE-79suffix",
])
def test_technical_tokens_inside_longer_ascii_identifiers_are_not_masked(source):
    assert literal_spans(source) == ()


@pytest.mark.parametrize("left,right", [("中", "文"), ("نص", "عربي"), ("текст", "слово")])
@pytest.mark.parametrize("literal", [
    "CVE-2024-1234", "CWE-79", "192.0.2.1", "192.0.2.1/24",
    "https://example.com/a", "abcdef1234567890", "OpenSSL 3.0.1",
    "/usr/bin/openssl", "C:\\Windows\\System32\\cmd.exe",
    "curl --help", "`nmap -sV 192.0.2.1`",
    "sk_live_ABCDEFGHIJKLMNOP",
])
def test_script_adjacent_literal_matrix_preserves_exact_occurrence(left, right, literal):
    source = f"{left}{literal}{right} and {left}{literal}{right}"
    spans = literal_spans(source)
    assert [source[start:end] for start, end in spans] == [literal, literal]
    protected = protect_literals(source)
    assert literal not in protected.text
    assert restore_literals(protected.text, protected) == source


@pytest.mark.parametrize("source,literal", [
    ("中https://example.com/a?x=1&y=%E4%B8%AD中", "https://example.com/a?x=1&y=%E4%B8%AD"),
    ("نصhttps://xn--fsqu00a.xn--0zwm56d/aعربي", "https://xn--fsqu00a.xn--0zwm56d/a"),
    ("See https://例子.测试/路径 here", "https://例子.测试/路径"),
    ("See https://example.com/路径 here", "https://example.com/路径"),
    ("See https://example.com/a. Next", "https://example.com/a"),
    ("请看https://example.com/a。下一步", "https://example.com/a"),
    ("راجعhttps://example.com/a،ثم", "https://example.com/a"),
    ("中https://[2001:db8::1]/a中", "https://[2001:db8::1]/a"),
])
def test_url_termination_and_unicode_iri(source, literal):
    assert [source[start:end] for start, end in literal_spans(source)] == [literal]


@pytest.mark.parametrize("source", [
    "prefixhttps://example.com/a", "xabcdef1234567890suffix",
    "preOpenSSL 3.0.1suffix", "xsk_live_ABCDEFGHIJKLMNOPsuffix",
])
def test_ascii_identifier_substrings_are_not_literals(source):
    assert literal_spans(source) == ()


@pytest.mark.parametrize("left,right", [("中", "文"), ("نص", "عربي"), ("текст", "слово")])
@pytest.mark.parametrize("literal", [
    "2001:db8::1", "2001:db8::/64", "::1", "::ffff:192.0.2.1",
    "fe80::1%eth0", "C:\\Program Files\\Vendor\\app.exe",
    "https://example.com/search?q=a(b)c",
    "https://example.com/(outer(inner))/end",
])
def test_full_ipv6_url_and_windows_path_occurrences(left, right, literal):
    source = f"{left}{literal}{right} then {left}{literal}{right}"
    spans = literal_spans(source)
    assert [source[start:end] for start, end in spans] == [literal, literal]
    protected = protect_literals(source)
    assert restore_literals(protected.text, protected) == source


def test_quoted_windows_path_with_spaces_in_final_name():
    literal = r"C:\Program Files\Vendor Suite\app name.exe"
    source = f'Type "{literal}" now'
    spans = literal_spans(source)
    assert any(source[start:end] == literal for start, end in spans)
    protected = protect_literals(source)
    assert restore_literals(protected.text, protected) == source


@pytest.mark.parametrize("source", [
    "2001:db8::zz", "2001:db8::1/129", "12345:db8::1",
    "x2001:db8::1", "2001:db8::1suffix",
])
def test_invalid_or_embedded_ipv6_not_partially_masked(source):
    assert literal_spans(source) == ()


def test_generated_valid_ipv6_and_cidr_roundtrip():
    import ipaddress
    import random

    rng = random.Random(80419)
    for index in range(100):
        address = ipaddress.IPv6Address(rng.getrandbits(128))
        literal = (str(address) if index % 2 else
                   str(ipaddress.IPv6Network((address, rng.randrange(0, 129)), strict=False)))
        source = f"中{literal}文"
        assert [source[start:end] for start, end in literal_spans(source)] == [literal]
        protected = protect_literals(source)
        assert restore_literals(protected.text, protected) == source


@pytest.mark.parametrize("literal", [
    "2001:0db8:0000:0000:0000:0000:0000:0001",
    "2001:db8::1/128", "::/0", "::ffff:192.0.2.1/128",
])
def test_ipv6_full_and_prefix_extremes(literal):
    source = f"راجع{literal}ثم"
    assert [source[a:b] for a, b in literal_spans(source)] == [literal]


@pytest.mark.parametrize("literal", [
    r"\\server\share\Program Files\tool.exe",
    r"C:\Program Files\Vendor\tool.exe",
    "/opt/My App/bin/tool",
])
def test_unquoted_paths_with_spaced_directory_segments(literal):
    source = f"Run {literal} now"
    assert [source[a:b] for a, b in literal_spans(source)] == [literal]


@pytest.mark.parametrize("literal", [
    r"\\server\share\Program Files\my tool.exe",
    r"C:\Program Files\Vendor\my tool.exe",
    "/opt/My App/bin/my tool",
    "/my tool",
])
def test_quoted_paths_with_spaced_final_segment(literal):
    source = f'Run "{literal}" now'
    assert [source[a:b] for a, b in literal_spans(source)] == [literal]


@pytest.mark.parametrize("literal", [
    "https://example.com/search?q=a(b)c",
    "https://example.com/(a(b)c)/tail",
    "https://example.com/search?q=a%28b%29c",
])
def test_complete_url_after_balanced_or_percent_encoded_parentheses(literal):
    source = f"See ({literal})."
    assert [source[a:b] for a, b in literal_spans(source)] == [literal]


@pytest.mark.parametrize("source,literal", [
    ("Reach 192.0.2.1. Next", "192.0.2.1"),
    ("CIDR: 192.0.2.0/24. Next", "192.0.2.0/24"),
    ("Reach 2001:db8::1. Next", "2001:db8::1"),
    ("Allow 2001:db8::/64. Next", "2001:db8::/64"),
    ("Node 2001:db8::1: next", "2001:db8::1"),
])
def test_ip_sentence_punctuation_is_not_part_of_address(source, literal):
    assert [source[a:b] for a, b in literal_spans(source)] == [literal]


def test_generated_balanced_uri_groups_keep_every_suffix():
    for depth in range(5):
        for suffix in ("c", "/tail", "%29c"):
            literal = "https://example.com/search?q=" + "(" * depth + "a" + ")" * depth + suffix
            for left, right in (("See (", ")."), ("中", "文"), ("نص", "عربي")):
                source = left + literal + right
                assert [source[a:b] for a, b in literal_spans(source)] == [literal]
                protected = protect_literals(source)
                assert restore_literals(protected.text, protected) == source
