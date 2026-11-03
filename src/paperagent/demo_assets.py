"""Demo assets: a tiny hand-built text PDF so the offline demo and tests
can exercise pdf_reader + verbatim quote verification without network
or external files. Raw PDF construction, no extra dependencies.
"""
from __future__ import annotations

from pathlib import Path


def _escape(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def make_text_pdf(path: Path, lines: list[str]) -> Path:
    """Write a one-page Helvetica PDF containing ``lines`` of text."""
    content_parts = ["BT", "/F1 12 Tf", "72 720 Td"]
    for i, line in enumerate(lines):
        content_parts.append(f"({_escape(line)}) Tj")
        if i < len(lines) - 1:
            content_parts.append("0 -16 Td")
    content_parts.append("ET")
    content = "\n".join(content_parts).encode("latin-1", errors="replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
         b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for num, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF").encode()

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


SAMPLE_PDF_LINES = [
    "Sample Working Paper for Offline Demo",
    "This study demonstrates that offline verification of agent",
    "behavior is feasible without any network access.",
    "The experimental design includes three controlled conditions.",
    "Results show that deterministic kernels catch fabricated quotes.",
    "We discuss implications for research writing assistants.",
]


def make_sample_pdf(workspace_root: Path) -> Path:
    return make_text_pdf(workspace_root / "files" / "sample.pdf", SAMPLE_PDF_LINES)
