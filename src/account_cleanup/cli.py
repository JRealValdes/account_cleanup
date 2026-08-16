"""CLI: extract → detect → inventario de cuentas."""

from __future__ import annotations

import argparse
import json

from account_cleanup.config import CANDIDATES_JSON, EMAILS_JSONL, INVENTORY_CSV
from account_cleanup.detect import detect_accounts
from account_cleanup.extract import extract_all


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
