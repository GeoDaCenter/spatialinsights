#!/usr/bin/env python3

import argparse
import csv
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass
class StreetSegment:
    segment_id: str
    name_canonical: str
    original_name: str
    from_addr: Optional[int]
    to_addr: Optional[int]

    def contains(self, number: int) -> bool:
        if self.from_addr is None or self.to_addr is None:
            return False
        low = min(self.from_addr, self.to_addr)
        high = max(self.from_addr, self.to_addr)
        return low <= number <= high

    def midpoint_distance(self, number: int) -> float:
        if self.from_addr is None or self.to_addr is None:
            return math.inf
        mid = (self.from_addr + self.to_addr) / 2.0
        return abs(number - mid)


DIRECTIONAL_TOKENS = {
    "N": "N",
    "S": "S",
    "E": "E",
    "W": "W",
    "NE": "NE",
    "NW": "NW",
    "SE": "SE",
    "SW": "SW",
    "NORTH": "N",
    "SOUTH": "S",
    "EAST": "E",
    "WEST": "W",
}

# USPS-style suffix canonicalization; not exhaustive but covers common cases
SUFFIX_NORMALIZATION = {
    "ALY": "ALLEY",
    "ALLEY": "ALLEY",
    "AVE": "AVENUE",
    "AV": "AVENUE",
    "AVEN": "AVENUE",
    "AVENUE": "AVENUE",
    "BND": "BEND",
    "BEND": "BEND",
    "BLF": "BLUFF",
    "BLUFF": "BLUFF",
    "BLVD": "BOULEVARD",
    "BOULEVARD": "BOULEVARD",
    "BR": "BRANCH",
    "BRG": "BRIDGE",
    "BRIDGE": "BRIDGE",
    "CIR": "CIRCLE",
    "CIRCLE": "CIRCLE",
    "CT": "COURT",
    "COURT": "COURT",
    "CV": "COVE",
    "COVE": "COVE",
    "DR": "DRIVE",
    "DRIVE": "DRIVE",
    "EXPY": "EXPRESSWAY",
    "EXPRESSWAY": "EXPRESSWAY",
    "HWY": "HIGHWAY",
    "HIGHWAY": "HIGHWAY",
    "LN": "LANE",
    "LANE": "LANE",
    "PKWY": "PARKWAY",
    "PARKWAY": "PARKWAY",
    "PL": "PLACE",
    "PLACE": "PLACE",
    "RD": "ROAD",
    "ROAD": "ROAD",
    "SQ": "SQUARE",
    "SQUARE": "SQUARE",
    "ST": "STREET",
    "STR": "STREET",
    "STREET": "STREET",
    "TER": "TERRACE",
    "TERRACE": "TERRACE",
    "TRL": "TRAIL",
    "TRAIL": "TRAIL",
    "WAY": "WAY",
}

APT_TOKENS = {
    "APT",
    "UNIT",
    "STE",
    "SUITE",
    "#",
    "FLOOR",
    "FL",
}


def strip_after_comma(address: str) -> str:
    if not address:
        return address
    idx = address.find(",")
    return address if idx == -1 else address[:idx]


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def canonicalize_street_name(raw_name: str) -> str:
    if raw_name is None:
        return ""
    name = str(raw_name).upper()
    name = strip_after_comma(name)
    # Remove punctuation except hyphen which can be part of names (e.g., "MLK Jr.-Drive")
    name = re.sub(r"[\.,]", " ", name)
    name = normalize_whitespace(name)

    tokens = name.split(" ")
    # Remove apartment/unit information if present after a token like APT/UNIT/STE/#
    result_tokens: List[str] = []
    stop = False
    for token in tokens:
        t = token.strip()
        if not t:
            continue
        if t in APT_TOKENS:
            stop = True
        if stop:
            break
        result_tokens.append(t)

    # Normalize directional tokens but keep them as part of the name, since some datasets include them in NAME
    normalized_tokens: List[str] = []
    for token in result_tokens:
        if token in DIRECTIONAL_TOKENS:
            normalized_tokens.append(DIRECTIONAL_TOKENS[token])
            continue
        # Normalize common suffixes if token looks like suffix
        normalized_tokens.append(SUFFIX_NORMALIZATION.get(token, token))

    # Handle SAINT abbreviation when used as a full word at start
    if normalized_tokens and normalized_tokens[0] == "ST":
        normalized_tokens[0] = "SAINT"

    return " ".join(normalized_tokens)


def extract_house_number(address: str) -> Optional[int]:
    if address is None:
        return None
    work = strip_after_comma(str(address)).upper()
    work = normalize_whitespace(work)

    # Queens-style hyphenated numbers like 45-12 -> 4512
    match = re.match(r"^\s*(\d+)[- ]?(\d+)?", work)
    if not match:
        return None
    primary = match.group(1)
    secondary = match.group(2)
    number_str = (primary or "") + (secondary or "")
    # Remove any trailing letters like 123A -> 123
    m2 = re.match(r"(\d+)", number_str) if number_str else None
    number_str = m2.group(1) if m2 else ""
    if not number_str:
        return None
    try:
        return int(number_str)
    except ValueError:
        return None


def load_street_segments(
    geojson_path: Path,
    id_field: str,
    name_field: str,
    from_field: str,
    to_field: str,
) -> List[StreetSegment]:
    with geojson_path.open("r", encoding="utf-8") as f:
        gj = json.load(f)

    features = gj.get("features", [])
    segments: List[StreetSegment] = []
    for feat in features:
        props = feat.get("properties", {}) or {}
        try:
            seg_id = str(props.get(id_field, "")).strip()
            original_name = str(props.get(name_field, "")).strip()
        except Exception:
            continue

        # Skip if missing critical fields
        if not seg_id or not original_name:
            continue

        def to_int_safe(val) -> Optional[int]:
            if val is None:
                return None
            # Values may be strings like "00123" or "123A" or "45-12"
            s = str(val).strip().upper()
            if s == "":
                return None
            # Join hyphenated pieces and drop alpha suffixes
            s = re.sub(r"[^0-9]", "", s)
            if s == "":
                return None
            try:
                return int(s)
            except Exception:
                return None

        from_num = to_int_safe(props.get(from_field))
        to_num = to_int_safe(props.get(to_field))

        name_canon = canonicalize_street_name(original_name)
        if not name_canon:
            continue

        segments.append(
            StreetSegment(
                segment_id=seg_id,
                name_canonical=name_canon,
                original_name=original_name,
                from_addr=from_num,
                to_addr=to_num,
            )
        )

    return segments


def build_name_index(segments: Iterable[StreetSegment]) -> Dict[str, List[StreetSegment]]:
    index: Dict[str, List[StreetSegment]] = {}
    for seg in segments:
        index.setdefault(seg.name_canonical, []).append(seg)
    return index


def choose_best_segment(segments: List[StreetSegment], number: int) -> Optional[StreetSegment]:
    # Filter by range containment
    candidates = [s for s in segments if s.contains(number)]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    # Break ties by midpoint distance, then by shortest range, then by numeric segment_id if possible
    def key_fn(s: StreetSegment) -> Tuple[float, int, float]:
        midpoint_dist = s.midpoint_distance(number)
        range_len = math.inf
        if s.from_addr is not None and s.to_addr is not None:
            range_len = abs(s.to_addr - s.from_addr)
        try:
            seg_id_num = float(re.sub(r"[^0-9]", "", s.segment_id))
        except Exception:
            seg_id_num = math.inf
        return (midpoint_dist, range_len, seg_id_num)

    return sorted(candidates, key=key_fn)[0]


def read_addresses(addresses_csv: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    with addresses_csv.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = [row for row in reader]
    return fieldnames, rows


def write_output(
    output_csv: Path,
    fieldnames_in: List[str],
    rows: List[Dict[str, str]],
    appended_fieldnames: List[str],
) -> None:
    # Preserve input order and append new columns at the end
    fieldnames_out = list(fieldnames_in) + [fn for fn in appended_fieldnames if fn not in fieldnames_in]
    with output_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames_out, quoting=csv.QUOTE_NONNUMERIC)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def process(
    addresses_csv: Path,
    streets_geojson: Path,
    output_csv: Path,
    address_column: str,
    id_field: str,
    name_field: str,
    from_field: str,
    to_field: str,
) -> None:
    segments = load_street_segments(
        streets_geojson, id_field=id_field, name_field=name_field, from_field=from_field, to_field=to_field
    )
    if not segments:
        print("No street segments loaded. Check field names and file.", file=sys.stderr)
        sys.exit(2)

    index = build_name_index(segments)

    input_fieldnames, input_rows = read_addresses(addresses_csv)
    if address_column not in input_fieldnames:
        print(f"Address column '{address_column}' not found in {addresses_csv}.", file=sys.stderr)
        print(f"Available columns: {input_fieldnames}", file=sys.stderr)
        sys.exit(2)

    out_rows: List[Dict[str, str]] = []

    for row in input_rows:
        raw_addr = row.get(address_column, "")
        house_num = extract_house_number(raw_addr)
        street_name_canon = canonicalize_street_name(raw_addr)

        match_status = ""
        matched_segment_id: Optional[str] = None
        matched_segment_name: Optional[str] = None
        matched_from: Optional[int] = None
        matched_to: Optional[int] = None

        if not street_name_canon:
            match_status = "no_street_name"
        elif house_num is None:
            match_status = "no_house_number"
        else:
            candidates = index.get(street_name_canon, [])
            if not candidates:
                match_status = "no_name_match"
            else:
                best = choose_best_segment(candidates, house_num)
                if best is None:
                    match_status = "no_range_match"
                else:
                    matched_segment_id = best.segment_id
                    matched_segment_name = best.original_name
                    matched_from = best.from_addr
                    matched_to = best.to_addr
                    match_status = "matched"

        out_record: Dict[str, str] = dict(row)
        # Ensure types are strings for CSV
        out_record.update(
            {
                "house_number_extracted": "" if house_num is None else str(house_num),
                "street_name_canonical": street_name_canon,
                "segment_id": "" if matched_segment_id is None else str(matched_segment_id),
                "segment_name": "" if matched_segment_name is None else str(matched_segment_name),
                "segment_from": "" if matched_from is None else str(matched_from),
                "segment_to": "" if matched_to is None else str(matched_to),
                "match_status": match_status,
            }
        )
        out_rows.append(out_record)

    appended_cols = [
        "house_number_extracted",
        "street_name_canonical",
        "segment_id",
        "segment_name",
        "segment_from",
        "segment_to",
        "match_status",
    ]
    write_output(output_csv, input_fieldnames, out_rows, appended_cols)
    print(f"Wrote {len(out_rows)} rows to {output_csv}")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assign street segment IDs to addresses by matching street name and house number to segment FROM/TO ranges."
    )
    parser.add_argument("--addresses", required=True, type=Path, help="Path to addresses CSV file")
    parser.add_argument(
        "--address-column",
        default="address",
        help="Column name in the addresses CSV that contains the full address string (default: address)",
    )
    parser.add_argument("--streets", required=True, type=Path, help="Path to streets GeoJSON file")
    parser.add_argument("--output", required=True, type=Path, help="Path to write the output CSV with matches")

    parser.add_argument("--seg-id-col", default="ID", help="Field name in streets for segment ID (default: ID)")
    parser.add_argument(
        "--seg-name-col", default="NAME", help="Field name in streets for street name/address name (default: NAME)"
    )
    parser.add_argument("--seg-from-col", default="FROM", help="Field name in streets for FROM address (default: FROM)")
    parser.add_argument("--seg-to-col", default="TO", help="Field name in streets for TO address (default: TO)")

    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()

    # Validate paths
    for p in [args.addresses, args.streets]:
        if not Path(p).exists():
            print(f"File not found: {p}", file=sys.stderr)
            sys.exit(2)

    process(
        addresses_csv=args.addresses,
        streets_geojson=args.streets,
        output_csv=args.output,
        address_column=args.address_column,
        id_field=args.seg_id_col,
        name_field=args.seg_name_col,
        from_field=args.seg_from_col,
        to_field=args.seg_to_col,
    )


if __name__ == "__main__":
    main()