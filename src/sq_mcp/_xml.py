"""Centralized hardened XML parser.

Every parse of an XML payload in this project — CFX archives, SQX
settings, optimizer reports, broker XML — MUST route through
``safe_fromstring`` / ``safe_parse``. The default lxml parser resolves
external entities and DTDs, which exposes us to XXE (XML External
Entity), billion-laughs, and SSRF style attacks if a hostile .cfx or
.sqx file is fed to a tool.

The hardened parser disables:

- External entity resolution (``resolve_entities=False``) — no fetching
  of remote DTDs, no expansion of `&xxe;` payloads.
- DTD loading (``load_dtd=False``) — no parsing of `<!DOCTYPE>` blocks.
- Network access (``no_network=True``) — defense in depth even if a
  DTD slipped through.
- ``huge_tree=False`` — keeps the libxml2 parser limits engaged
  (10MB attr/text, 16M nodes) to bound billion-laughs amplification.

A fresh parser is created per call because lxml parsers are not
thread-safe and the MCP server may run tool handlers concurrently.
The overhead is sub-millisecond and dwarfed by the actual parse.
"""

from __future__ import annotations

from typing import Any

from lxml import etree


def _safe_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=False,
    )


def safe_fromstring(data: bytes | str, **kwargs: Any) -> Any:
    """Drop-in replacement for ``lxml.etree.fromstring`` with hardened defaults."""
    parser = _safe_parser()
    return etree.fromstring(data, parser, **kwargs)


def safe_parse(source: Any, **kwargs: Any) -> Any:
    """Drop-in replacement for ``lxml.etree.parse`` with hardened defaults."""
    parser = _safe_parser()
    return etree.parse(source, parser, **kwargs)


__all__ = ["safe_fromstring", "safe_parse"]
