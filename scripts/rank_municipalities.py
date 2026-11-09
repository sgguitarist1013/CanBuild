"""Convert the FY2025 DOR single-family assessment table to a rollout queue."""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

INVENTORY = Path(__file__).resolve().parents[1] / "docs" / "massachusetts_municipalities.json"
ASSESSMENT_ROW = re.compile(
    r"^\s*(\d{3})\s+(.+?)\s+2025\s+[\d,]+\s+([\d,]+)\s+([\d,]+)\s+",
    re.MULTILINE,
)


def town_key(name):
    return re.sub(r"[^A-Z]", "", name.upper())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("table_text", type=Path, help="Output of pdftotext -layout on the source PDF")
    args = parser.parse_args()
    inventory = {town_key(name): name for name in json.loads(INVENTORY.read_text())}
    rows = []
    for code, name, count, value in ASSESSMENT_ROW.findall(args.table_text.read_text()):
        key = town_key(name)
        if key not in inventory:
            parser.error(f"Unknown municipality in assessment table: {name}")
        rows.append((inventory[key], code, int(value.replace(",", "")), int(count.replace(",", ""))))

    expected = len(inventory)
    towns = {town_key(row[0]) for row in rows}
    codes = {int(row[1]) for row in rows}
    if len(rows) != expected or towns != inventory.keys() or codes != set(range(1, expected + 1)):
        parser.error(f"Expected exactly {expected} unique municipality rows and DOR codes")
    if any(value <= 0 or count <= 0 for _, _, value, count in rows):
        parser.error("Expected a positive assessment and parcel count for every municipality")

    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(["rank", "municipality", "dor_code", "fiscal_year", "mean_assessed_value_usd", "parcel_count"])
    for rank, (town, code, value, count) in enumerate(sorted(rows, key=lambda row: (-row[2], row[0])), 1):
        writer.writerow([rank, town, code, 2025, value, count])


if __name__ == "__main__":
    main()
