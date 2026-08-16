"""Extrae cabeceras de mbox de Takeout a un JSONL ligero."""

from __future__ import annotations

import json
from email.parser import BytesHeaderParser
from email.policy import default as email_policy
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from tqdm import tqdm

from account_cleanup.config import DATA_INTERIM, DATA_RAW, EMAILS_JSONL, GOOGLE_EMAILS

_HEADER_PARSER = BytesHeaderParser(policy=email_policy)


def discover_mboxes() -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    if not DATA_RAW.exists():
        return found
    for account_dir in sorted(p for p in DATA_RAW.iterdir() if p.is_dir()):
        for mbox in sorted(account_dir.glob("*.mbox")):
            found.append((account_dir.name, mbox))
    return found


def _decode_header_block(raw: bytes):
    if not raw.strip():
        return None
    try:
        return _HEADER_PARSER.parsebytes(raw)
    except Exception:
        return None


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            return dt.isoformat()
        return dt.isoformat()
    except (TypeError, ValueError, OverflowError):
        return value.strip() or None


def headers_to_record(account: str, raw_headers: bytes) -> dict | None:
    msg = _decode_header_block(raw_headers)
    if msg is None:
        return None

    from_name, from_email = parseaddr(msg.get("From", "") or "")
    delivered_to = (msg.get("Delivered-To") or "").strip()
    google_email = GOOGLE_EMAILS.get(account, delivered_to or account)

    subject = msg.get("Subject")
    if subject is None:
        subject = ""
    else:
        subject = str(subject)

    labels = msg.get("X-Gmail-Labels")
    record = {
        "google_account": account,
        "google_email": google_email,
        "date": _iso_date(msg.get("Date")),
        "from_name": from_name or None,
        "from_email": from_email.lower() if from_email else None,
        "subject": subject,
        "labels": str(labels) if labels else None,
    }
    if not record["from_email"] and not record["subject"] and not record["date"]:
        return None
    return record


def iter_mbox_header_blocks(path: Path):
    """Recorre un mbox y cede solo el bloque de cabeceras de cada mensaje."""
    with path.open("rb") as handle:
        buf = bytearray()
        in_headers = False
        while True:
            line = handle.readline()
            if not line:
                if in_headers and buf:
                    yield bytes(buf), handle.tell()
                break
            if line.startswith(b"From "):
                if in_headers and buf:
                    yield bytes(buf), handle.tell()
                buf = bytearray()
                in_headers = True
                continue
            if not in_headers:
                continue
            if line in (b"\n", b"\r\n"):
                yield bytes(buf), handle.tell()
                buf = bytearray()
                in_headers = False
            else:
                buf.extend(line)


def extract_mbox(account: str, path: Path, writer, limit: int | None = None) -> int:
    size = path.stat().st_size
    written = 0
    with tqdm(
        total=size,
        unit="B",
        unit_scale=True,
        desc=f"extract:{account}",
        leave=True,
    ) as pbar:
        last_pos = 0
        for raw_headers, pos in iter_mbox_header_blocks(path):
            pbar.update(pos - last_pos)
            last_pos = pos
            record = headers_to_record(account, raw_headers)
            if record is None:
                continue
            writer.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1
            if limit is not None and written >= limit:
                break
        if last_pos < size and (limit is None):
            pbar.update(size - last_pos)
    return written


def extract_all(limit_per_account: int | None = None, output: Path | None = None) -> Path:
    mboxes = discover_mboxes()
    if not mboxes:
        raise FileNotFoundError(
            f"No se han encontrado .mbox en {DATA_RAW}. "
            "Coloca cada Takeout en data/raw/<cuenta_google>/mail.mbox"
        )

    out = output or EMAILS_JSONL
    out.parent.mkdir(parents=True, exist_ok=True)

    totals: dict[str, int] = {}
    with out.open("w", encoding="utf-8") as writer:
        for account, path in mboxes:
            n = extract_mbox(account, path, writer, limit=limit_per_account)
            totals[f"{account}:{path.name}"] = n

    summary = DATA_INTERIM / "extract_summary.json"
    summary.write_text(
        json.dumps({"output": str(out), "counts": totals, "total": sum(totals.values())}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return out
