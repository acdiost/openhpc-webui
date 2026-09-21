"""Safe CSV serialization for files commonly opened in spreadsheet software."""

import csv
import io
from typing import Iterable


_FORMULA_PREFIXES = ("=", "+", "-", "@")


def spreadsheet_safe_cell(value: object) -> str:
    """Force formula-like values to remain text when a spreadsheet opens CSV."""
    text = str(value)
    candidate = text.lstrip()
    if candidate.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def render_spreadsheet_safe_csv(
    header: Iterable[object],
    rows: Iterable[Iterable[object]],
) -> str:
    """Serialize rows using standard quoting and spreadsheet formula protection."""
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(spreadsheet_safe_cell(value) for value in header)
    for row in rows:
        writer.writerow(spreadsheet_safe_cell(value) for value in row)
    return output.getvalue()
