"""The lift rule's PII patterns: conservative, deterministic, and silent on estate identifiers."""

from mlpal_memory_graph.services.pii import classify_pii, pii_in_node


def test_finds_email_phone_national_id_and_luhn_cards():
    assert classify_pii("owner is priya.k@ledgerly.io, call her") == ["email"]
    assert classify_pii("pager: +1 415-555-0142 after hours") == ["phone"]
    assert classify_pii("pager: (415) 555-0142") == ["phone"]
    assert classify_pii("ssn 123-45-6789") == ["national-id"]
    assert classify_pii("card 4111 1111 1111 1111 on file") == ["card"]          # Luhn-valid test number
    assert classify_pii("ref 4111 1111 1111 1112") == []                          # fails Luhn: not a card


def test_silent_on_estate_identifiers_and_ordinary_numbers():
    for text in (
        "account 024249678939 region us-east-2 arn:aws:iam::024249678939:role/hop-eval-ec2",
        "vol-0c493e116e469dfa9 10 GiB since 2026-07-26; rv 29230739; ready 2/2",
        "run-rate $1,196/mo; MTD $398.08; 2026-09-15T21:28Z",
        "10.0.4.17/32 to the Grafana SG; fs-0ea24d541ffdb05dd",
    ):
        assert classify_pii(text) == [], text
    assert classify_pii("") == [] and classify_pii(None) == []


def test_node_view_covers_name_summary_and_string_props():
    assert pii_in_node("owner", None, {"value": "marco@ledgerly.io"}) == ["email"]
    assert pii_in_node("payments-worker owner is the backend team", "", {"value": "backend"}) == []
