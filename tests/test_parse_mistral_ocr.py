"""Phase 2b — Mistral OCR adapter unit tests.

RED at G3: imports from `kb.parsers.mistral_ocr_parser` land at G4.

All tests run against a mock HTTP client — no real Mistral API calls.

Spec: tests/specs/phase_2b.md §4.3.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio


class MockMistralHTTPClient:
    """Minimal mock — record the request and return a canned response."""

    def __init__(self, response_json: dict, status_code: int = 200):
        self.response_json = response_json
        self.status_code = status_code
        self.last_request: dict | None = None

    async def post(self, url: str, *, headers: dict, files: dict | None = None,
                   json: dict | None = None) -> "MockResponse":
        self.last_request = {"url": url, "headers": headers, "files": files, "json": json}
        return MockResponse(self.status_code, self.response_json)


class MockResponse:
    def __init__(self, status_code: int, json_data: dict):
        self.status_code = status_code
        self._json = json_data

    def json(self) -> dict:
        return self._json

    @property
    def text(self) -> str:
        import json as _json
        return _json.dumps(self._json)


# Canned response shaped like Mistral OCR's per-page format (single page).
_ONE_PAGE_RESPONSE = {
    "pages": [
        {"index": 0, "markdown": "Page 1 text from OCR", "images": []},
    ],
}

_THREE_PAGE_RESPONSE = {
    "pages": [
        {"index": 0, "markdown": "Page 1", "images": []},
        {"index": 1, "markdown": "Page 2", "images": []},
        {"index": 2, "markdown": "Page 3", "images": []},
    ],
}


# ===========================================================================
# can_handle gating on KB_MISTRAL_API_KEY (decision #9)
# ===========================================================================


async def test_mistral_can_handle_when_api_key_present(monkeypatch):
    from kb.parsers.mistral_ocr_parser import MistralOCRParser

    monkeypatch.setenv("KB_MISTRAL_API_KEY", "fake-key-for-test")
    parser = MistralOCRParser()
    assert parser.can_handle(
        mime_type="application/pdf", magic_bytes=b"%PDF-1.4",
    ) is True


async def test_mistral_cannot_handle_when_api_key_absent(monkeypatch):
    from kb.parsers.mistral_ocr_parser import MistralOCRParser

    monkeypatch.delenv("KB_MISTRAL_API_KEY", raising=False)
    parser = MistralOCRParser()
    assert parser.can_handle(
        mime_type="application/pdf", magic_bytes=b"%PDF-1.4",
    ) is False


# ===========================================================================
# Mock-driven parse (decision #8)
# ===========================================================================


async def test_mistral_parses_via_mock_response(monkeypatch):
    """Inject a mock client; assert ParsedDocument with the right shape."""
    from kb.parsers.mistral_ocr_parser import MistralOCRParser

    monkeypatch.setenv("KB_MISTRAL_API_KEY", "fake-key")
    mock = MockMistralHTTPClient(_ONE_PAGE_RESPONSE)
    parser = MistralOCRParser(http_client=mock)

    doc = await parser.parse(b"%PDF-fake bytes", file_id="t", workspace_id="ws")
    assert len(doc.pages) == 1
    assert doc.pages[0].page_number == 1
    assert "Page 1 text from OCR" in doc.pages[0].text
    # Mock was called with auth header
    assert mock.last_request is not None
    assert "fake-key" in str(mock.last_request["headers"])


async def test_mistral_returns_one_page_per_response_page(monkeypatch):
    from kb.parsers.mistral_ocr_parser import MistralOCRParser

    monkeypatch.setenv("KB_MISTRAL_API_KEY", "fake-key")
    parser = MistralOCRParser(http_client=MockMistralHTTPClient(_THREE_PAGE_RESPONSE))

    doc = await parser.parse(b"%PDF-fake", file_id="t", workspace_id="ws")
    assert len(doc.pages) == 3
    assert [p.page_number for p in doc.pages] == [1, 2, 3]
    assert "Page 1" in doc.pages[0].text
    assert "Page 3" in doc.pages[2].text


async def test_mistral_raises_parse_error_on_4xx(monkeypatch):
    """Upstream 4xx/5xx → ParseError with the status code in the payload."""
    from kb.parsers import ParseError
    from kb.parsers.mistral_ocr_parser import MistralOCRParser

    monkeypatch.setenv("KB_MISTRAL_API_KEY", "fake-key")
    mock = MockMistralHTTPClient(
        response_json={"error": "Unauthorized"}, status_code=401,
    )
    parser = MistralOCRParser(http_client=mock)

    with pytest.raises(ParseError) as exc_info:
        await parser.parse(b"%PDF-fake", file_id="t", workspace_id="ws")
    # Status code surfaced in the error message
    assert "401" in str(exc_info.value)
