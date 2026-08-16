"""CLI: extract → detect → inventario de cuentas."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from account_cleanup.config import CANDIDATES_JSON, EMAILS_JSONL, INVENTORY_CSV
from account_cleanup.detect import _write_csv, detect_accounts
from account_cleanup.extract import extract_all
from account_cleanup.severity import score_csv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="account-cleanup",
        description="Inventario de cuentas online a partir del correo de Google Takeout.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    extract_p = sub.add_parser("extract", help="Parsea los .mbox y escribe data/interim/emails.jsonl")
    extract_p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Máximo de mensajes por fichero mbox (útil para pruebas)",
    )

    detect_p = sub.add_parser(
        "detect",
        help="Detecta cuentas a partir del JSONL y escribe data/processed/accounts_inventory.csv",
    )
    detect_p.add_argument(
        "--no-llm",
        action="store_true",
        help="Solo heurística de asuntos, sin llamadas a OpenAI",
    )
    detect_p.add_argument(
        "--min-signals",
        type=int,
        default=1,
        help="Mínimo de correos con señal de cuenta por dominio",
    )
    detect_p.add_argument(
        "--max-candidates",
        type=int,
        default=None,
        help="Máximo de candidatos (dominios) que se envían al LLM; útil para pruebas",
    )

    run_p = sub.add_parser("run", help="extract + detect")
    run_p.add_argument("--limit", type=int, default=None)
    run_p.add_argument("--no-llm", action="store_true")
    run_p.add_argument("--min-signals", type=int, default=1)
    run_p.add_argument("--max-candidates", type=int, default=None)

    score_p = sub.add_parser(
        "score",
        help="Recalcula la columna gravedad de un CSV de inventario ya generado (sin reparsear mbox)",
    )
    score_p.add_argument(
        "--input",
        type=str,
        default=None,
        help="Ruta del CSV (por defecto data/processed/accounts_inventory.csv)",
    )

    score_p.add_argument(
        "--no-llm",
        action="store_true",
        help="Usa la heurística de palabras clave en lugar del modelo",
    )

    review_p = sub.add_parser(
        "review",
        help="Aplica data/reviewed.json al CSV (columna resuelto) sin reparsear ni rescorear",
    )
    review_p.add_argument(
        "--input",
        type=str,
        default=None,
        help="Ruta del CSV (por defecto data/processed/accounts_inventory.csv)",
    )
    review_p.add_argument(
        "--no-llm",
        action="store_true",
        help="Solo coincidencia por nombre/dominio, sin el modelo para alias",
    )

    args = parser.parse_args(argv)

    if args.command == "extract":
        out = extract_all(limit_per_account=args.limit)
        print(f"Escrito {out}")
        return 0

    if args.command == "detect":
        if not EMAILS_JSONL.exists():
            raise SystemExit("No hay emails.jsonl. Ejecuta primero: account-cleanup extract")
        out = detect_accounts(
            use_llm=not args.no_llm,
            min_signal_emails=args.min_signals,
            max_candidates=args.max_candidates,
        )
        _print_detect_summary()
        print(f"Escrito {out}")
        return 0

    if args.command == "score":
        target = Path(args.input) if args.input else INVENTORY_CSV
        if not target.exists():
            raise SystemExit(f"No existe {target}. Ejecuta primero: account-cleanup detect")
        out = score_csv(target, use_llm=not args.no_llm)
        print(f"Escrito {out}")
        return 0

    if args.command == "review":
        target = Path(args.input) if args.input else INVENTORY_CSV
        if not target.exists():
            raise SystemExit(f"No existe {target}. Ejecuta primero: account-cleanup detect")
        with target.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        _write_csv(rows, target, use_llm=not args.no_llm)
        print(f"Escrito {target}")
        return 0

    extract_all(limit_per_account=args.limit)
    out = detect_accounts(
        use_llm=not args.no_llm,
        min_signal_emails=args.min_signals,
        max_candidates=args.max_candidates,
    )
    _print_detect_summary()
    print(f"Escrito {out}")
    return 0


def _print_detect_summary() -> None:
    if CANDIDATES_JSON.exists():
        payload = json.loads(CANDIDATES_JSON.read_text(encoding="utf-8"))
        print(
            f"Candidatos: {payload.get('n_candidates')} "
            f"(clusters totales: {payload.get('n_clusters')}"
            + (
                f", enviados al LLM: {payload.get('n_llm_candidates')}"
                if payload.get("n_llm_candidates") is not None
                else ""
            )
            + ")"
        )
    if INVENTORY_CSV.exists():
        n_rows = max(sum(1 for _ in INVENTORY_CSV.open(encoding="utf-8-sig")) - 1, 0)
        print(f"Filas en inventario: {n_rows}")


if __name__ == "__main__":
    raise SystemExit(main())
