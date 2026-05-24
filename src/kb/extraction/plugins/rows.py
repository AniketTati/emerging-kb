"""Phase 5c — xlsx row plugin.

Each xlsx sheet's tab-separated rows from `raw_pages.text` (per Phase 2b
xlsx_parser output) becomes one atomic_unit of unit_type='row'. No LLM
call — the parser already extracted the rows; we just transcribe them.

`parameters` jsonb shape:
  {
    "sheet_name": "Vendors",
    "row_index": 7,
    "cells": ["ACME", "123 Main St", "555-1234"],
    "header": ["name", "address", "phone"]
  }
"""

from __future__ import annotations

from kb.extraction.plugins import AtomicUnit, FileMeta


UNIT_TYPE = "row"


_XLSX_MIMES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/x-xlsx",
}


class RowsPlugin:
    UNIT_TYPE = "row"

    def matches(self, file_meta: FileMeta) -> bool:
        return file_meta.mime_type in _XLSX_MIMES or (
            file_meta.inferred_doc_type or ""
        ).endswith(("spreadsheet", "xlsx", "csv"))

    async def extract(
        self,
        *,
        file_meta: FileMeta,
        doc_text: str,
        raw_pages: list[tuple[int, str, dict]],
    ) -> list[AtomicUnit]:
        units: list[AtomicUnit] = []
        for page_number, page_text, layout_json in raw_pages:
            sheet_name = (layout_json or {}).get("sheet_name") or f"Sheet{page_number}"
            lines = [ln for ln in (page_text or "").splitlines() if ln.strip()]
            # First line is "# Sheet: <name>" header from xlsx_parser.
            if lines and lines[0].startswith("# Sheet:"):
                lines = lines[1:]
            if not lines:
                continue
            # Second line (now first after stripping header) is the column header.
            header_cells = [c.strip() for c in lines[0].split("\t")]
            for row_idx, line in enumerate(lines[1:], start=1):
                cells = [c.strip() for c in line.split("\t")]
                units.append(AtomicUnit(
                    unit_type=UNIT_TYPE,
                    parameters={
                        "sheet_name": sheet_name,
                        "row_index": row_idx,
                        "cells": cells,
                        "header": header_cells,
                    },
                ))
        return units


PLUGIN = RowsPlugin()
