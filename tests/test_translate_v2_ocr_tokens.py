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
