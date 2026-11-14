"""Import frequent clients from a CSV using AppBoletas' real client model.

The command is intentionally dry-run by default. Use ``--apply`` to commit
only NEW, valid clients. No existing row is updated.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Client
from app.schemas.client import ClientCreate
from app.utils.rut import clean_rut


REGION_CODES = {
    "Arica y Parinacota": 15,
    "Tarapacá": 1,
    "Antofagasta": 2,
    "Atacama": 3,
    "Coquimbo": 4,
    "Valparaíso": 5,
    "O'Higgins": 6,
    "Maule": 7,
    "Ñuble": 16,
    "Biobío": 8,
    "La Araucanía": 9,
    "Los Ríos": 14,
    "Los Lagos": 10,
    "Aysén": 11,
    "Magallanes y de la Antártica Chilena": 12,
    "Metropolitana de Santiago": 13,
}

EXPECTED_HEADERS = [
    "Nombre de fantasía (opcional)",
    "Razón social / Nombre",
    "Rut",
    "Direccion",
    "comuna",
    "region",
]


def detect_encoding(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return "utf-16"
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return "cp850"
    return "utf-8"


def read_csv(path: Path) -> tuple[str, str, list[str], list[tuple[int, list[str]]]]:
    raw = path.read_bytes()
    encoding = detect_encoding(raw)
    text = raw.decode(encoding)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";"
    reader = csv.reader(text.splitlines(), delimiter=delimiter)
    try:
        headers = next(reader)
    except StopIteration as error:
        raise ValueError("CSV vacío: falta la fila de encabezados") from error
    rows = [(line_no, row) for line_no, row in enumerate(reader, start=2)]
    return encoding, delimiter, headers, rows


def _field(row: list[str], index: int) -> str:
    return row[index].strip() if index < len(row) else ""


def parse_rows(headers: list[str], rows: list[tuple[int, list[str]]]) -> list[dict[str, Any]]:
    if headers != EXPECTED_HEADERS:
        raise ValueError(
            "Encabezados inesperados. "
            f"Esperados={EXPECTED_HEADERS!r}; encontrados={headers!r}"
        )
    parsed: list[dict[str, Any]] = []
    for line_no, row in rows:
        item: dict[str, Any] = {"line": line_no, "raw": row}
        if len(row) != len(EXPECTED_HEADERS):
            item["error"] = f"se esperaban 6 columnas y se encontraron {len(row)}"
            parsed.append(item)
            continue
        item.update(
            nombre_fantasia=_field(row, 0) or None,
            razon_social=_field(row, 1),
            rut=_field(row, 2),
            direccion=_field(row, 3),
            comuna=_field(row, 4),
            region_name=_field(row, 5),
        )
        item["values"] = {
            "rut": item["rut"],
            "razon_social": item["razon_social"],
            "nombre_fantasia": item["nombre_fantasia"],
            "giro": None,
            "direccion": item["direccion"],
            "region": REGION_CODES.get(item["region_name"]),
            "comuna": item["comuna"],
            "email": None,
            "detalle_default": None,
        }
        parsed.append(item)
    return parsed


def validate_rows(parsed: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for item in parsed:
        if "error" in item:
            invalid.append(item)
            continue
        try:
            model = ClientCreate(**item["values"])
        except ValidationError as error:
            item["error"] = "; ".join(
                f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in error.errors()
            )
            invalid.append(item)
            continue
        item["values"] = model.model_dump()
        item["normalized_rut"] = model.rut
        valid.append(item)
    return valid, invalid


def duplicate_groups(valid: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in valid:
        groups[item["normalized_rut"]].append(item)
    usable: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for rut, items in groups.items():
        if len(items) == 1:
            usable.append(items[0])
        elif all(item["values"] == items[0]["values"] for item in items[1:]):
            usable.append(items[0])
            for item in items[1:]:
                item["duplicate_of"] = items[0]["line"]
        else:
            for item in items:
                item["error"] = f"RUT repetido con datos diferentes ({rut})"
                conflicts.append(item)
    return {rut: items for rut, items in groups.items() if len(items) > 1}, usable + conflicts


def compare_db(session: Session, items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result = {"NEW": [], "ALREADY_EXISTS_IDENTICAL": [], "ALREADY_EXISTS_DIFFERENT": [], "CSV_DUPLICATE_CONFLICT": []}
    for item in items:
        if "duplicate_of" in item or "error" in item:
            if "error" in item and "RUT repetido" in item["error"]:
                result["CSV_DUPLICATE_CONFLICT"].append(item)
            continue
        existing = session.scalar(select(Client).where(Client.rut == item["normalized_rut"]))
        if existing is None:
            result["NEW"].append(item)
            continue
        fields = ("rut", "razon_social", "nombre_fantasia", "giro", "direccion", "region", "comuna", "email", "detalle_default")
        if all(getattr(existing, field) == item["values"][field] for field in fields):
            result["ALREADY_EXISTS_IDENTICAL"].append(item)
        else:
            item["existing_id"] = existing.id
            result["ALREADY_EXISTS_DIFFERENT"].append(item)
    return result


def report(path: Path, encoding: str, delimiter: str, headers: list[str], parsed: list[dict[str, Any]], invalid: list[dict[str, Any]], duplicates: dict[str, list[dict[str, Any]]], comparison: dict[str, list[dict[str, Any]]]) -> None:
    identical_dupes = [items for items in duplicates.values() if all(item.get("values") == items[0].get("values") for item in items[1:])]
    conflicts = [item for items in duplicates.values() if items not in identical_dupes for item in items]
    print(json.dumps({
        "csv_path": str(path.resolve()), "csv_encoding": encoding, "csv_delimiter": delimiter,
        "csv_headers": headers, "csv_rows": len(parsed),
        "valid_rows": len(parsed) - len(invalid), "invalid_rows": len(invalid),
        "csv_duplicate_ruts": sorted(duplicates), "csv_duplicate_rows": sum(len(x) for x in duplicates.values()),
        "csv_duplicates_identical": sum(len(x) - 1 for x in identical_dupes),
        "csv_duplicate_conflicts": len(conflicts),
        "new_clients": len(comparison["NEW"]),
        "already_exists_identical": len(comparison["ALREADY_EXISTS_IDENTICAL"]),
        "already_exists_different": len(comparison["ALREADY_EXISTS_DIFFERENT"]),
        "clients_to_insert": len(comparison["NEW"]),
        "invalid_rows_detail": [{"line": x["line"], "error": x.get("error")} for x in invalid],
        "conflict_rows_detail": [{"line": x["line"], "error": x.get("error")} for x in conflicts],
    }, ensure_ascii=False, indent=2))
    print("CLIENTS_TO_INSERT_TABLE")
    for item in comparison["NEW"]:
        v = item["values"]
        print(json.dumps({"nombre_fantasia": v["nombre_fantasia"], "razon_social": v["razon_social"], "RUT": v["rut"], "comuna": v["comuna"], "region": v["region"]}, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--dry-run", action="store_true", help="simulate; default mode")
    parser.add_argument("--apply", action="store_true", help="commit NEW clients")
    args = parser.parse_args(argv)
    if args.apply and args.dry_run:
        parser.error("--apply y --dry-run son excluyentes")
    if not args.csv_path.is_file():
        parser.error(f"CSV no existe: {args.csv_path}")
    encoding, delimiter, headers, rows = read_csv(args.csv_path)
    parsed = parse_rows(headers, rows)
    valid, invalid = validate_rows(parsed)
    duplicates, deduped = duplicate_groups(valid)
    database_url = args.database_url or get_settings().database_url
    engine = create_engine(database_url, future=True)
    with Session(engine) as session:
        before = session.scalar(select(func.count()).select_from(Client)) or 0
        comparison = compare_db(session, deduped)
        report(args.csv_path, encoding, delimiter, headers, parsed, invalid, duplicates, comparison)
        print(json.dumps({"clients_before": before, "database_url": database_url}, ensure_ascii=False))
        if not args.apply:
            print("IMPORT_TRANSACTION=NOT_RUN")
            return 0
        try:
            for item in comparison["NEW"]:
                session.add(Client(**item["values"]))
            session.commit()
            print(f"IMPORT_TRANSACTION=COMMITTED CLIENTS_INSERTED={len(comparison['NEW'])}")
        except Exception:
            session.rollback()
            print("IMPORT_TRANSACTION=ROLLED_BACK", file=sys.stderr)
            raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
