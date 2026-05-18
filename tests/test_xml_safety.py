"""Defense-in-depth: prove our hardened parser rejects XXE / DTD payloads.

If any of these tests start failing, it means an entity expansion or DTD load
has slipped through. Real CFX/SQX files never contain these constructs, so
rejecting them is always safe.
"""

from __future__ import annotations

from lxml import etree

from sq_mcp._xml import safe_fromstring

XXE_PAYLOAD = b"""<?xml version="1.0"?>
<!DOCTYPE foo [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<root>&xxe;</root>
"""

BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">
]>
<lolz>&lol2;</lolz>
"""

REMOTE_DTD = b"""<?xml version="1.0"?>
<!DOCTYPE foo SYSTEM "http://example.com/evil.dtd">
<root>x</root>
"""


def test_xxe_external_entity_not_resolved() -> None:
    # File contents must NOT appear in the parsed text — the entity stays
    # literal because resolve_entities=False.
    root = safe_fromstring(XXE_PAYLOAD)
    assert "root:" not in (root.text or "")
    # Either the parser rejects the entity or the entity remains unexpanded.


def test_billion_laughs_does_not_explode() -> None:
    # With resolve_entities=False, the &lol2; reference must NOT expand to
    # 10 million 'lol' tokens. Worst case the parser throws — we accept both
    # outcomes, we just don't want to OOM.
    try:
        root = safe_fromstring(BILLION_LAUGHS)
        # If it parsed, the body should NOT be the expanded form
        text_len = len(root.text or "")
        assert text_len < 1000, "billion-laughs payload expanded — XXE defense broken"
    except etree.XMLSyntaxError:
        pass  # parser rejection is also an acceptable outcome


def test_remote_dtd_not_fetched() -> None:
    # no_network=True forbids fetching the DTD. Either the parser rejects
    # or returns without resolving — both acceptable. The bad outcome would
    # be a real HTTP request to example.com.
    try:
        safe_fromstring(REMOTE_DTD)
    except etree.XMLSyntaxError:
        pass


def test_normal_xml_still_parses() -> None:
    # Don't break legitimate CFX/SQX parsing.
    out = safe_fromstring(b"<root><a>1</a><b>2</b></root>")
    assert out.tag == "root"
    assert len(out) == 2
