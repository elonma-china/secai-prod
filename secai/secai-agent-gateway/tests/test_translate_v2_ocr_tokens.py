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
