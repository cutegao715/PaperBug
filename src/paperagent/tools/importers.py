"""File import tools for local research materials."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from pypdf import PdfReader

from paperagent.schemas.references import ReferenceCandidate, make_ref_id

MAX_TEXT_CHARS = 30_000


def run_file_import(input: dict, resolve, timeout: float = 20.0) -> dict[str, Any]:
    path = resolve(input["path"])
    if not path.exists():
        return {"error": f"file not found: {path}"}
    if not path.is_file():
        return {"error": f"not a file: {path}"}

    suffix = path.suffix.lower()
    base = _metadata(path)
    if suffix == ".pdf":
        base.update(_read_pdf(path))
    elif suffix in {".md", ".txt"}:
        base.update(_read_text(path))
    elif suffix == ".docx":
        base.update(_read_docx(path))
    elif suffix == ".doc":
        converted = _convert_doc_to_docx(
            path,
            Path(input.get("conversion_dir") or path.parent),
            timeout=timeout,
        )
        if converted.get("error"):
            return {**base, **converted}
        converted_path = Path(converted["converted_path"])
        docx_data = _read_docx(converted_path)
        if docx_data.get("error"):
            return {**base, **converted, **docx_data}
        base.update(docx_data)
        base["kind"] = "docx"
        base["original_kind"] = "doc"
        base["converted_path"] = str(converted_path)
        base["conversion"] = converted
    elif suffix == ".wps":
        converted = _convert_wps_to_docx(
            path,
            Path(input.get("conversion_dir") or path.parent),
            timeout=timeout,
        )
        if converted.get("error"):
            return {**base, **converted}
        converted_path = Path(converted["converted_path"])
        docx_data = _read_docx(converted_path)
        if docx_data.get("error"):
            return {**base, **converted, **docx_data}
        base.update(docx_data)
        base["kind"] = "docx"
        base["original_kind"] = "wps"
        base["converted_path"] = str(converted_path)
        base["conversion"] = converted
    elif suffix == ".bib":
        base.update(_read_bibtex(path))
    elif suffix == ".ris":
        base.update(_read_ris(path))
    else:
        return {"error": f"unsupported file type: {suffix or '<none>'}",
                **base}
    base["provenance"] = f"local:{path}"
    return base


def _convert_doc_to_docx(path: Path, output_dir: Path, timeout: float = 20.0) -> dict[str, Any]:
    # WPS is more reliable for the legacy .doc files produced by WPS/Kingsoft
    # templates. Try it first; Word COM may hang on hidden conversion dialogs.
    wps_result = _convert_word_like_to_docx(
        path,
        output_dir,
        progid="KWPS.Application",
        converter="WPS COM",
        timeout=timeout,
    )
    if not wps_result.get("error"):
        return wps_result
    word_result = _convert_word_like_to_docx(
        path,
        output_dir,
        progid="Word.Application",
        converter="Microsoft Word COM",
        timeout=timeout,
    )
    if not word_result.get("error"):
        word_result["fallback_from"] = wps_result["error"]
        return word_result
    return {
        "error": (
            "legacy .doc conversion failed. "
            f"WPS: {wps_result['error']}; Word: {word_result['error']}"
        )
    }


def _convert_wps_to_docx(path: Path, output_dir: Path, timeout: float = 20.0) -> dict[str, Any]:
    return _convert_word_like_to_docx(
        path,
        output_dir,
        progid="KWPS.Application",
        converter="WPS COM",
        timeout=timeout,
    )


def _convert_word_like_to_docx(
    path: Path,
    output_dir: Path,
    *,
    progid: str,
    converter: str,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Convert legacy Word .doc to .docx using local Microsoft Word COM.

    This is intentionally optional. If the app/pywin32 is unavailable, callers get
    a clear error and can show the material as pending conversion.
    """
    if sys.platform != "win32" and os.name != "nt":
        return {"error": f"conversion requires {converter} on Windows"}
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{path.stem}.docx"
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "paperagent.tools.office_convert_worker",
                progid,
                converter,
                str(path),
                str(target),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(5.0, timeout),
        )
    except subprocess.TimeoutExpired:
        return {"error": f"conversion timed out with {converter} after {timeout}s"}
    except Exception as e:
        return {"error": f"conversion failed to start with {converter}: {e}"}
    out = (proc.stdout or "").strip().splitlines()
    if out:
        try:
            data = json.loads(out[-1])
            if not data.get("error") and target.exists():
                data["original_path"] = str(path)
                data["converted_path"] = str(target)
            return data
        except json.JSONDecodeError:
            pass
    if proc.returncode == 0 and target.exists():
        return {
            "status": "converted",
            "converter": converter,
            "original_path": str(path),
            "converted_path": str(target),
        }
    return {"error": f"conversion failed with {converter}: {(proc.stderr or proc.stdout or '')[:500]}"}


def _metadata(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    stat = path.stat()
    content_type, _ = mimetypes.guess_type(str(path))
    return {
        "path": str(path),
        "filename": path.name,
        "content_type": content_type or "application/octet-stream",
        "size": stat.st_size,
        "mtime": str(stat.st_mtime),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _read_text(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "kind": "text",
        "text": text[:MAX_TEXT_CHARS],
        "truncated": len(text) > MAX_TEXT_CHARS,
    }


def _read_pdf(path: Path) -> dict[str, Any]:
    reader = PdfReader(str(path))
    chunks = []
    for i, page in enumerate(reader.pages[:10], start=1):
        text = (page.extract_text() or "").strip()
        if text:
            chunks.append({"page": i, "text": text[:3500]})
    return {
        "kind": "pdf",
        "total_pages": len(reader.pages),
        "pages": chunks,
    }


def _read_docx(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml")
    except Exception as e:
        return {"error": f"docx read failed: {e}"}
    root = ElementTree.fromstring(xml)
    texts = [node.text or "" for node in root.iter()
             if node.tag.endswith("}t") and node.text]
    text = "\n".join(t.strip() for t in texts if t.strip())
    return {
        "kind": "docx",
        "text": text[:MAX_TEXT_CHARS],
        "truncated": len(text) > MAX_TEXT_CHARS,
    }


def _read_bibtex(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    candidates = [_bib_entry_to_candidate(entry)
                  for entry in re.findall(r"@\w+\s*\{[^@]+", text, flags=re.S)]
    candidates = [c for c in candidates if c is not None]
    return {
        "kind": "bibtex",
        "references": [c.model_dump() for c in candidates],
        "text": text[:MAX_TEXT_CHARS],
        "truncated": len(text) > MAX_TEXT_CHARS,
    }


def _bib_entry_to_candidate(entry: str) -> ReferenceCandidate | None:
    fields = {k.lower(): v.strip()
              for k, v in re.findall(r"(\w+)\s*=\s*[{\"]([^{}\"]+)[}\"]", entry)}
    title = fields.get("title")
    if not title:
        return None
    authors = [a.strip() for a in re.split(r"\s+and\s+", fields.get("author", ""))
               if a.strip()]
    year = _int(fields.get("year"))
    first = authors[0].split()[-1] if authors else "anon"
    first_word = (title.split() or ["untitled"])[0]
    return ReferenceCandidate(
        ref_id=make_ref_id(first, year, first_word),
        title=title,
        authors=authors,
        year=year,
        venue=fields.get("journal") or fields.get("booktitle"),
        doi=fields.get("doi"),
        url=fields.get("url"),
        source="bibtex",
    )


def _read_ris(path: Path) -> dict[str, Any]:
    records = path.read_text(encoding="utf-8", errors="replace").split("\nER  -")
    candidates = [_ris_record_to_candidate(record) for record in records]
    candidates = [c for c in candidates if c is not None]
    return {
        "kind": "ris",
        "references": [c.model_dump() for c in candidates],
    }


def _ris_record_to_candidate(record: str) -> ReferenceCandidate | None:
    fields: dict[str, list[str]] = {}
    for line in record.splitlines():
        match = re.match(r"([A-Z0-9]{2})  - (.*)", line.strip())
        if match:
            fields.setdefault(match.group(1), []).append(match.group(2).strip())
    title = (fields.get("TI") or fields.get("T1") or [""])[0]
    if not title:
        return None
    authors = fields.get("AU", [])
    year = _int((fields.get("PY") or fields.get("Y1") or [""])[0][:4])
    first = authors[0].split()[-1] if authors else "anon"
    first_word = (title.split() or ["untitled"])[0]
    return ReferenceCandidate(
        ref_id=make_ref_id(first, year, first_word),
        title=title,
        authors=authors,
        year=year,
        venue=(fields.get("JO") or fields.get("JF") or [""])[0] or None,
        doi=(fields.get("DO") or [""])[0] or None,
        url=(fields.get("UR") or [""])[0] or None,
        source="ris",
    )


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None
