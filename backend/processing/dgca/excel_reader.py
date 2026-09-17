"""Lightweight zero-dependency OpenXML (.xlsx) reader for DGCA route basket data.

Extracts tabular data from Excel spreadsheets using Python standard library
(zipfile and xml.etree.ElementTree) supporting both inline strings and shared strings.

DATA PROVENANCE & SCOPE:
- Input: data/dgca/dgca_july_2026_top25_route_basket.xlsx containing the already-derived
  Top-25 route basket for July 2026 (not the full 600+ route national dataset).
- Source Organization: Directorate General of Civil Aviation (DGCA)
- Access Layer / Cross-Check: Dataful (dataset #23652)
- Weights: "DGCA-traffic-derived prototype route weights" (NOT official CPI weights).
"""

from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import xml.etree.ElementTree as ET
import zipfile

# OpenXML Namespaces
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS = {"main": MAIN_NS}

REQUIRED_COLUMNS = [
    "Rank",
    "City 1",
    "City 2",
    "Passengers To City 2",
    "Passengers From City 2",
    "Total Route Traffic",
    "Share of Total Traffic",
    "Basket Weight",
    "Reference Period",
    "Source Organization",
    "Access Layer",
]


class DGCAParsingError(Exception):
    """Raised when parsing DGCA Excel spreadsheet fails."""
    pass


def _read_shared_strings(zf: zipfile.ZipFile) -> List[str]:
    """Extract shared strings table if present."""
    shared_strings: List[str] = []
    if "xl/sharedStrings.xml" not in zf.namelist():
        return shared_strings

    xml_content = zf.read("xl/sharedStrings.xml")
    root = ET.fromstring(xml_content)
    for si in root.findall("main:si", NS):
        # Text can be in si/t or scattered in r/t runs
        t_elem = si.find("main:t", NS)
        if t_elem is not None and t_elem.text:
            shared_strings.append(t_elem.text)
        else:
            text_parts = [r_t.text for r_t in si.findall(".//main:t", NS) if r_t.text]
            shared_strings.append("".join(text_parts))

    return shared_strings


def read_excel_rows(file_path: Union[str, Path], sheet_name: str = "xl/worksheets/sheet1.xml") -> List[List[str]]:
    """Read all rows from an .xlsx worksheet as lists of string values.

    Args:
        file_path: Path to the .xlsx file.
        sheet_name: Internal zip path to worksheet XML.

    Returns:
        List of rows, where each row is a list of cell values.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Excel file not found: {path}")

    with zipfile.ZipFile(path, "r") as zf:
        shared_strings = _read_shared_strings(zf)

        if sheet_name not in zf.namelist():
            # Try finding the first sheet if default is missing
            sheet_files = [f for f in zf.namelist() if f.startswith("xl/worksheets/sheet") and f.endswith(".xml")]
            if not sheet_files:
                raise DGCAParsingError(f"No worksheet found in Excel file: {path}")
            sheet_name = sheet_files[0]

        sheet_xml = zf.read(sheet_name)

    root = ET.fromstring(sheet_xml)
    rows_data: List[List[str]] = []

    sheet_data = root.find("main:sheetData", NS)
    if sheet_data is None:
        return rows_data

    for row_elem in sheet_data.findall("main:row", NS):
        row_vals: List[str] = []
        for cell_elem in row_elem.findall("main:c", NS):
            cell_type = cell_elem.get("t")
            val = ""

            if cell_type == "inlineStr":
                t_node = cell_elem.find("main:is/main:t", NS)
                if t_node is not None and t_node.text:
                    val = t_node.text
            elif cell_type == "s":
                v_node = cell_elem.find("main:v", NS)
                if v_node is not None and v_node.text:
                    idx = int(v_node.text.strip())
                    if 0 <= idx < len(shared_strings):
                        val = shared_strings[idx]
            else:
                v_node = cell_elem.find("main:v", NS)
                if v_node is not None and v_node.text:
                    val = v_node.text

            row_vals.append(val.strip())

        if any(row_vals):  # Skip completely empty rows
            rows_data.append(row_vals)

    return rows_data


def load_raw_route_basket(file_path: Union[str, Path]) -> List[Dict[str, Any]]:
    """Read and validate raw route basket rows from the DGCA Excel spreadsheet.

    Args:
        file_path: Path to the .xlsx file.

    Returns:
        List of dictionaries with validated columns and typed values.
    """
    rows = read_excel_rows(file_path)
    if not rows:
        raise DGCAParsingError("Spreadsheet contains no data rows.")

    header = rows[0]

    # Verify all required columns are present
    missing_cols = [col for col in REQUIRED_COLUMNS if col not in header]
    if missing_cols:
        raise DGCAParsingError(
            f"Missing required columns in Excel: {missing_cols}. Found: {header}"
        )

    col_indices = {col: header.index(col) for col in REQUIRED_COLUMNS}

    records: List[Dict[str, Any]] = []
    for row_idx, row in enumerate(rows[1:], start=2):
        # Skip rows that don't have enough columns
        if len(row) < len(REQUIRED_COLUMNS):
            continue

        try:
            rank = int(row[col_indices["Rank"]])
            city_1 = row[col_indices["City 1"]].strip()
            city_2 = row[col_indices["City 2"]].strip()
            to_city_2 = int(row[col_indices["Passengers To City 2"]])
            from_city_2 = int(row[col_indices["Passengers From City 2"]])
            total_traffic = int(row[col_indices["Total Route Traffic"]])
            share_traffic = Decimal(row[col_indices["Share of Total Traffic"]])
            basket_weight = Decimal(row[col_indices["Basket Weight"]])
            ref_period = row[col_indices["Reference Period"]].strip()
            source_org = row[col_indices["Source Organization"]].strip()
            access_layer = row[col_indices["Access Layer"]].strip()

            # Bidirectional consistency check: To + From = Total
            if to_city_2 + from_city_2 != total_traffic:
                raise ValueError(
                    f"Traffic mismatch on row {row_idx} ({city_1}-{city_2}): "
                    f"{to_city_2} + {from_city_2} != {total_traffic}"
                )

            records.append({
                "rank": rank,
                "city_1": city_1,
                "city_2": city_2,
                "passengers_to_city_2": to_city_2,
                "passengers_from_city_2": from_city_2,
                "total_route_traffic": total_traffic,
                "share_of_total_traffic": share_traffic,
                "basket_weight": basket_weight,
                "reference_period": ref_period,
                "source_organization": source_org,
                "access_layer": access_layer,
            })
        except (ValueError, InvalidOperation) as e:
            raise DGCAParsingError(f"Error parsing row {row_idx}: {e}") from e

    return records
