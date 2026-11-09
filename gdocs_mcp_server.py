"""MCP server exposing read-only access to Google Docs + creation of NEW docs.

IMPORTANT: never print() to stdout here, stdout is the MCP stdio channel.
"""
import logging
import re
import sys

from googleapiclient.discovery import build
from mcp.server.fastmcp import FastMCP

from google_auth import get_credentials

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
log = logging.getLogger("gdocs-mcp")

mcp = FastMCP("google-docs-resume")

_HEADING_PREFIX = {
    "TITLE": "# ",
    "HEADING_1": "# ",
    "HEADING_2": "## ",
    "HEADING_3": "### ",
    "HEADING_4": "### ",
}
_PREFIX_TO_STYLE = {"H1": "HEADING_1", "H2": "HEADING_2", "H3": "HEADING_3"}


def _docs_service():
    return build("docs", "v1", credentials=get_credentials(interactive=False),
                 cache_discovery=False)


def _extract_doc_id(document: str) -> str:
    m = re.search(r"/document/d/([a-zA-Z0-9_-]+)", document)
    return m.group(1) if m else document.strip()


def _utf16_len(s: str) -> int:
    # Google Docs indexes are in UTF-16 code units
    return len(s.encode("utf-16-le")) // 2


def _elements_to_lines(elements: list) -> list[str]:
    lines: list[str] = []
    for el in elements:
        if "paragraph" in el:
            p = el["paragraph"]
            text = "".join(
                r.get("textRun", {}).get("content", "") for r in p.get("elements", [])
            ).rstrip("\n")
            if not text.strip():
                lines.append("")
                continue
            style = p.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")
            if style in _HEADING_PREFIX:
                lines.append(_HEADING_PREFIX[style] + text.strip())
            elif "bullet" in p:
                lines.append("- " + text.strip())
            else:
                lines.append(text)
        elif "table" in el:
            for row in el["table"].get("tableRows", []):
                for cell in row.get("tableCells", []):
                    lines.extend(_elements_to_lines(cell.get("content", [])))
    return lines


@mcp.tool()
def read_google_doc(document: str) -> str:
    """Read a Google Doc (READ ONLY) and return its text.

    Args:
        document: The Google Doc URL or document ID.

    Returns text where '# ', '## ', '### ' mark headings and '- ' marks bullets.
    """
    doc_id = _extract_doc_id(document)
    log.info("Reading doc %s", doc_id)
    doc = _docs_service().documents().get(documentId=doc_id).execute()
    lines = _elements_to_lines(doc.get("body", {}).get("content", []))
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return f"TITLE: {doc.get('title', '')}\n\n{text}"


def _build_requests(content: str) -> list[dict]:
    segments, styled = [], []
    index = 1
    for raw in content.replace("**", "").splitlines():
        line = raw.rstrip()
        kind, text = "NORMAL", line
        if line.startswith("### "):
            kind, text = "H3", line[4:]
        elif line.startswith("## "):
            kind, text = "H2", line[3:]
        elif line.startswith("# "):
            kind, text = "H1", line[2:]
        elif line.lstrip().startswith(("- ", "* ", "• ")):
            kind, text = "BULLET", line.lstrip()[2:]
        seg = text.strip() + "\n"
        length = _utf16_len(seg)
        styled.append((kind, index, index + length))
        segments.append(seg)
        index += length

    requests: list[dict] = [
        {"insertText": {"location": {"index": 1}, "text": "".join(segments)}}
    ]

    for kind, start, end in styled:
        if kind in _PREFIX_TO_STYLE:
            requests.append({
                "updateParagraphStyle": {
                    "range": {"startIndex": start, "endIndex": end},
                    "paragraphStyle": {"namedStyleType": _PREFIX_TO_STYLE[kind]},
                    "fields": "namedStyleType",
                }
            })

    # group consecutive bullet lines into one bullet list request
    run_start = run_end = None
    for kind, start, end in styled + [("END", 0, 0)]:
        if kind == "BULLET":
            run_start = start if run_start is None else run_start
            run_end = end
        elif run_start is not None:
            requests.append({
                "createParagraphBullets": {
                    "range": {"startIndex": run_start, "endIndex": run_end},
                    "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                }
            })
            run_start = run_end = None
    return requests


@mcp.tool()
def create_google_doc(title: str, content: str) -> str:
    """Create a BRAND NEW Google Doc. Never modifies existing documents.

    Args:
        title: Name of the new Google Doc.
        content: Text using '# ' headings and '- ' bullets.

    Returns the URL of the new document.
    """
    service = _docs_service()
    new_doc = service.documents().create(body={"title": title}).execute()
    doc_id = new_doc["documentId"]
    service.documents().batchUpdate(
        documentId=doc_id, body={"requests": _build_requests(content)}
    ).execute()
    log.info("Created doc %s", doc_id)
    return f"https://docs.google.com/document/d/{doc_id}/edit"


if __name__ == "__main__":
    mcp.run(transport="stdio")