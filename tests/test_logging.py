from griddesk.logging import redact_value, regex_redact


def test_regex_redact_masks_contact_details():
    out = regex_redact("call 617-555-0142 or mail jo.doe@example.com, ssn 123-45-6789")
    assert "617-555-0142" not in out
    assert "jo.doe@example.com" not in out
    assert "123-45-6789" not in out
    assert "<PHONE>" in out and "<EMAIL>" in out and "<SSN>" in out


def test_redact_value_walks_nested_structures():
    out = redact_value({"a": ["x@y.com", {"b": "555-123-4567"}], "n": 3})
    assert out == {"a": ["<EMAIL>", {"b": "<PHONE>"}], "n": 3}
