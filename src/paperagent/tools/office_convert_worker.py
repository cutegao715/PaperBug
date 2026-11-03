"""Subprocess worker for Office/WPS COM conversion.

COM automation can hang on legacy files or hidden conversion dialogs. Running
it in a child process lets the caller enforce a hard timeout.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 5:
        print(json.dumps({"error": "usage: progid converter input output"}), flush=True)
        return 2
    progid, converter, input_path, output_path = sys.argv[1:]
    result = convert(progid, converter, Path(input_path), Path(output_path))
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if not result.get("error") else 1


def convert(progid: str, converter: str, input_path: Path, output_path: Path) -> dict:
    try:
        import pythoncom
        import win32com.client
    except Exception as e:
        return {"error": f"conversion unavailable: {e}"}

    app = None
    doc = None
    pythoncom.CoInitialize()
    try:
        app = win32com.client.DispatchEx(progid)
        app.Visible = False
        try:
            app.DisplayAlerts = 0
        except Exception:
            pass
        doc = app.Documents.Open(
            str(input_path),
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            doc.SaveAs2(str(output_path), FileFormat=16)
        except Exception:
            doc.SaveAs(str(output_path), FileFormat=16)
        return {
            "status": "converted",
            "converter": converter,
            "original_path": str(input_path),
            "converted_path": str(output_path),
        }
    except Exception as e:
        return {"error": f"conversion failed with {converter}: {e}"}
    finally:
        try:
            if doc is not None:
                doc.Close(False)
        except Exception:
            pass
        try:
            if app is not None:
                app.Quit()
        except Exception:
            pass
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
