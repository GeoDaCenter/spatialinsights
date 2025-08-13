import argparse
import csv
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


DIRECTION_MAP = {
    "N": "NORTH",
    "S": "SOUTH",
    "E": "EAST",
    "W": "WEST",
    "NE": "NORTHEAST",
    "NW": "NORTHWEST",
    "SE": "SOUTHEAST",
    "SW": "SOUTHWEST",
}

STREET_TYPE_MAP = {
    "ST": "STREET",
    "STREET": "STREET",
    "AVE": "AVENUE",
    "AV": "AVENUE",
    "AVENUE": "AVENUE",
    "RD": "ROAD",
    "ROAD": "ROAD",
    "BLVD": "BOULEVARD",
    "BOULEVARD": "BOULEVARD",
    "DR": "DRIVE",
    "DRIVE": "DRIVE",
    "LN": "LANE",
    "LANE": "LANE",
    "HWY": "HIGHWAY",
    "HIGHWAY": "HIGHWAY",
    "PKWY": "PARKWAY",
    "PARKWAY": "PARKWAY",
    "CT": "COURT",
    "COURT": "COURT",
    "CIR": "CIRCLE",
    "CIRCLE": "CIRCLE",
    "PL": "PLACE",
    "PLACE": "PLACE",
    "TER": "TERRACE",
    "TERRACE": "TERRACE",
    "WAY": "WAY",
    "SQ": "SQUARE",
    "SQUARE": "SQUARE",
    "PK": "PARK",
    "PARK": "PARK",
    "PIKE": "PIKE",
    "TRL": "TRAIL",
    "TRAIL": "TRAIL",
    "EXPY": "EXPRESSWAY",
    "EXPRESSWAY": "EXPRESSWAY",
    "FWY": "FREEWAY",
    "FREEWAY": "FREEWAY",
}

HOUSE_NUM_TOKEN_PATTERN = re.compile(r"^\s*(\d{1,6})(?:[\-\s]?(\d{1,5}))?")
FIRST_INTEGER_PATTERN = re.compile(r"(\d+)")
NON_ALNUM_PATTERN = re.compile(r"[^A-Z0-9]+")

NOISE_TOKENS = {
    "APT", "APARTMENT", "UNIT", "STE", "SUITE", "FL", "FLOOR", "BLDG", "BUILDING", "#",
}


@dataclass
class StreetSegment:
    segment_id: str
    name_original: str
    range_from: Optional[int]
    range_to: Optional[int]

    @property
    def low(self) -> Optional[int]:
        if self.range_from is None or self.range_to is None:
            return None
        return min(self.range_from, self.range_to)

    @property
    def high(self) -> Optional[int]:
        if self.range_from is None or self.range_to is None:
            return None
        return max(self.range_from, self.range_to)

    def contains(self, house_number: int) -> bool:
        if self.low is None or self.high is None:
            return False
        return self.low <= house_number <= self.high


def parse_house_number(address_text: str) -> Optional[int]:
    if not isinstance(address_text, str):
        return None
    text = address_text.strip()
    if not text:
        return None
    match = HOUSE_NUM_TOKEN_PATTERN.match(text)
    if match:
        primary = match.group(1)
        try:
            return int(primary)
        except ValueError:
            return None
    match2 = FIRST_INTEGER_PATTERN.search(text)
    if match2:
        try:
            return int(match2.group(1))
        except ValueError:
            return None
    return None


def extract_street_only(address_text: str) -> str:
    if not isinstance(address_text, str):
        return ""
    working = address_text.strip().upper()
    working = re.sub(r"^\s*\d+[A-Z]?[-\s]?\d*[A-Z]?\s+", "", working)
    tokens = working.split()
    street_tokens: List[str] = []
    for token in tokens:
        if token in NOISE_TOKENS or token.startswith("#"):
            break
        street_tokens.append(token)
    return " ".join(street_tokens)


def normalize_street_name(name: str) -> Tuple[str, str]:
    if not isinstance(name, str):
        return "", ""
    working = name.upper()
    working = NON_ALNUM_PATTERN.sub(" ", working)
    tokens: List[str] = [t for t in working.split() if t]

    normalized_tokens: List[str] = []
    for token in tokens:
        if token in NOISE_TOKENS:
            continue
        if token in DIRECTION_MAP:
            normalized_tokens.append(DIRECTION_MAP[token])
            continue
        if token in STREET_TYPE_MAP:
            normalized_tokens.append(STREET_TYPE_MAP[token])
            continue
        if token.isdigit():
            continue
        normalized_tokens.append(token)

    normalized_full = " ".join(normalized_tokens).strip()

    core_tokens = list(normalized_tokens)
    if core_tokens and core_tokens[0] in set(DIRECTION_MAP.values()):
        core_tokens = core_tokens[1:]
    if core_tokens and core_tokens[-1] in set(DIRECTION_MAP.values()):
        core_tokens = core_tokens[:-1]
    if core_tokens and core_tokens[-1] in set(STREET_TYPE_MAP.values()):
        core_tokens = core_tokens[:-1]

    normalized_core = " ".join(core_tokens).strip()

    return normalized_full, normalized_core


def safe_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            digits = FIRST_INTEGER_PATTERN.search(value)
            if digits:
                return int(digits.group(1))
            return None
        return int(value)
    except Exception:
        return None


def load_street_segments(
    streets_path: str,
    id_field: str,
    name_field: str,
    from_field: str,
    to_field: str,
    streets_encoding: str = "utf-8",
) -> Tuple[Dict[str, List[StreetSegment]], Dict[str, List[StreetSegment]]]:
    with open(streets_path, "r", encoding=streets_encoding) as f:
        data = json.load(f)

    if isinstance(data, dict) and "features" in data:
        features = data.get("features", [])
    elif isinstance(data, list):
        features = data
    else:
        raise ValueError("Unsupported GeoJSON structure. Expected FeatureCollection or list of features.")

    name_to_segments: Dict[str, List[StreetSegment]] = {}
    core_to_segments: Dict[str, List[StreetSegment]] = {}

    for feat in features:
        props = feat.get("properties", {}) if isinstance(feat, dict) else {}
        if not props:
            continue
        seg_id = str(props.get(id_field, "")).strip()
        street_name_raw = props.get(name_field)
        from_val = props.get(from_field)
        to_val = props.get(to_field)

        if not seg_id:
            continue

        from_num = safe_int(from_val)
        to_num = safe_int(to_val)

        seg = StreetSegment(
            segment_id=seg_id,
            name_original=str(street_name_raw) if street_name_raw is not None else "",
            range_from=from_num,
            range_to=to_num,
        )

        full, core = normalize_street_name(seg.name_original)
        if not full and not core:
            continue

        if full:
            name_to_segments.setdefault(full, []).append(seg)
        if core:
            core_to_segments.setdefault(core, []).append(seg)

    return name_to_segments, core_to_segments


def select_best_segment(candidates: List[StreetSegment], house_number: Optional[int]) -> Optional[StreetSegment]:
    if not candidates or house_number is None:
        return None
    containing = [s for s in candidates if s.contains(house_number)]
    if not containing:
        return None

    def interval_width(seg: StreetSegment) -> int:
        return (seg.high - seg.low) if seg.low is not None and seg.high is not None else 10**9

    containing.sort(key=lambda s: (interval_width(s), abs(((s.low + s.high) // 2) - house_number) if s.low is not None and s.high is not None else 10**9))
    return containing[0]


def read_addresses_csv(path: str, encoding: str = "utf-8") -> List[Dict[str, object]]:
    with open(path, "r", encoding=encoding, newline="") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def write_csv(path: str, rows: List[Dict[str, object]], encoding: str = "utf-8") -> None:
    if not rows:
        # Write empty file with no headers
        open(path, "w", encoding=encoding).close()
        return
    # Collect headers from first row plus any additional keys found
    fieldnames = list(rows[0].keys())
    seen = set(fieldnames)
    for r in rows[1:]:
        for k in r.keys():
            if k not in seen:
                fieldnames.append(k)
                seen.add(k)
    with open(path, "w", encoding=encoding, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def assign_segments(
    addresses_rows: List[Dict[str, object]],
    address_column: str,
    name_to_segments: Dict[str, List[StreetSegment]],
    core_to_segments: Dict[str, List[StreetSegment]],
    street_column: Optional[str] = None,
    housenum_column: Optional[str] = None,
) -> List[Dict[str, object]]:
    results: List[Dict[str, object]] = []

    for row in addresses_rows:
        if street_column and street_column in row and row.get(street_column) not in (None, ""):
            street_raw = row.get(street_column)
            street_text = str(street_raw) if street_raw is not None else ""
        else:
            addr_text = row.get(address_column)
            street_text = extract_street_only(str(addr_text) if addr_text is not None else "")

        if housenum_column and housenum_column in row and row.get(housenum_column) not in (None, ""):
            house_num = safe_int(row.get(housenum_column))
            if house_num is None:
                addr_text = row.get(address_column)
                house_num = parse_house_number(str(addr_text) if addr_text is not None else "")
        else:
            addr_text = row.get(address_column)
            house_num = parse_house_number(str(addr_text) if addr_text is not None else "")

        full_name, core_name = normalize_street_name(street_text)

        matched_segment: Optional[StreetSegment] = None
        match_method = ""
        name_match_count = 0

        if full_name:
            candidates = name_to_segments.get(full_name, [])
            name_match_count = len(candidates)
            matched_segment = select_best_segment(candidates, house_num)
            match_method = "full"

        if matched_segment is None and core_name:
            candidates = core_to_segments.get(core_name, [])
            name_match_count = max(name_match_count, len(candidates))
            matched_segment = select_best_segment(candidates, house_num)
            if matched_segment is not None:
                match_method = "core"

        out_row = dict(row)
        out_row["parsed_house_number"] = house_num
        out_row["street_input"] = street_text
        out_row["normalized_street_full"] = full_name
        out_row["normalized_street_core"] = core_name
        out_row["match_method"] = match_method
        out_row["name_match_count"] = name_match_count

        if matched_segment is not None:
            out_row["segment_id"] = matched_segment.segment_id
            out_row["segment_from"] = matched_segment.range_from
            out_row["segment_to"] = matched_segment.range_to
            out_row["segment_name"] = matched_segment.name_original
        else:
            out_row["segment_id"] = None
            out_row["segment_from"] = None
            out_row["segment_to"] = None
            out_row["segment_name"] = None

        results.append(out_row)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Assign street segment IDs to addresses by matching street name and house number range.")
    parser.add_argument("--addresses", required=True, help="Path to addresses CSV (e.g., /workspace/addresses.csv)")
    parser.add_argument("--streets", required=True, help="Path to streets GeoJSON (e.g., /workspace/streets.geojson)")
    parser.add_argument("--out", required=True, help="Path to write output CSV with segment IDs")

    parser.add_argument("--address-column", default="address", help="Column in addresses CSV containing full address text (default: address)")
    parser.add_argument("--street-column", default=None, help="Optional column with just the street name; if not provided, will be extracted from the address column")
    parser.add_argument("--housenum-column", default=None, help="Optional column with house number; if not provided, will be parsed from the address column")

    parser.add_argument("--id-field", default="ID", help="Property name for segment ID in streets GeoJSON (default: ID)")
    parser.add_argument("--name-field", default="NAME", help="Property name for street name in streets GeoJSON (default: NAME)")
    parser.add_argument("--from-field", default="FROM", help="Property name for starting address number in streets GeoJSON (default: FROM)")
    parser.add_argument("--to-field", default="TO", help="Property name for ending address number in streets GeoJSON (default: TO)")

    parser.add_argument("--addresses-encoding", default="utf-8", help="Encoding of addresses CSV (default: utf-8)")
    parser.add_argument("--streets-encoding", default="utf-8", help="Encoding of streets GeoJSON (default: utf-8)")

    args = parser.parse_args()

    if not os.path.exists(args.addresses):
        raise FileNotFoundError(f"Addresses CSV not found: {args.addresses}")
    if not os.path.exists(args.streets):
        raise FileNotFoundError(f"Streets GeoJSON not found: {args.streets}")

    addr_rows = read_addresses_csv(args.addresses, encoding=args.addresses_encoding)

    # Validate address column presence when needed
    needed_cols: List[str] = []
    if not args.street_column or args.street_column not in (addr_rows[0].keys() if addr_rows else []):
        needed_cols.append(args.address_column)
    if needed_cols and addr_rows and any(col not in addr_rows[0] for col in needed_cols):
        present = list(addr_rows[0].keys()) if addr_rows else []
        raise KeyError(f"Required column(s) {needed_cols} not found in CSV. Available columns: {present}")

    name_to_segments, core_to_segments = load_street_segments(
        streets_path=args.streets,
        id_field=args.id_field,
        name_field=args.name_field,
        from_field=args.from_field,
        to_field=args.to_field,
        streets_encoding=args.streets_encoding,
    )

    out_rows = assign_segments(
        addresses_rows=addr_rows,
        address_column=args.address_column,
        name_to_segments=name_to_segments,
        core_to_segments=core_to_segments,
        street_column=args.street_column,
        housenum_column=args.housenum_column,
    )

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    write_csv(args.out, out_rows, encoding="utf-8")
    print(f"Wrote {len(out_rows)} rows to: {args.out}")


if __name__ == "__main__":
    main()