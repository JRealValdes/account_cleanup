"""Gravedad si la cuenta se hackea: 0–100, más alto = peor impacto."""

from __future__ import annotations

import csv
from pathlib import Path

# (puntos, palabras clave). Primera coincidencia gana; van de más grave a menos.
_RULES: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        98,
        (
            "seguridad social",
            "seg-social",
            "hacienda",
            "taxdown",
            "trade republic",
            "etoro",
            "santander",
            "bbva",
            "abanca",
            "banco",
            "smartbank",
        ),
    ),
    (
        93,
        (
            "adeslas",
            "asisa",
            "sanitas",
            "quironsalud",
            "rempe",
            "democratest",
            "historia clinica",
            "area privada",
        ),
    ),
    (
        90,
        (
            "vodafone",
            "movistar",
            "iberdrola",
            "canal de isabel",
            "canal.madrid",
            "sector alarm",
            "linea movil",
            "suministro",
        ),
    ),
    (
        88,
        (
            "ayuntamiento",
            "comunidad de madrid",
            "madrid.org",
            "cuenta digital",
        ),
    ),
    (
        86,
        (
            "github",
            "gitlab",
            "bitbucket",
            "aws",
            "amazon web services",
            "microsoft",
            "dropbox",
            "mega.nz",
            "onedrive",
        ),
    ),
    (
        82,
        (
            "facebook",
            "instagram",
            "linkedin",
            "whatsapp",
            "x.com",
            "discord",
            "bumble",
            "tinder",
        ),
    ),
    (
        78,
        (
            "paypal",
            "bizum",
            "samsung account",
            "xiaomi",
        ),
    ),
    (
        56,
        (
            "amazon.jobs",
            "amazon jobs",
            "infojobs",
            "tecnoempleo",
            "workday",
            "successfactors",
            "portal de empleo",
            "candidat",
        ),
    ),
    (
        74,
        (
            "amazon",
            "ebay",
            "wallapop",
            "aliexpress",
            "el corte ingles",
            "carrefour",
            "alcampo",
        ),
    ),
    (
        70,
        (
            "uber",
            "cabify",
            "share now",
            "free2move",
            "zity",
            "airbnb",
            "amovens",
        ),
    ),
    (
        64,
        (
            "steam",
            "playstation",
            "nintendo",
            "epic games",
            "ubisoft",
            "xbox",
            "ea account",
            "twitch",
        ),
    ),
    (
        58,
        (
            "spotify",
            "netflix",
            "disney+",
            "hbo",
            "prime video",
            "scribd",
        ),
    ),
)

_NEWSLETTER_CAP = 38
_DEFAULT_ACCOUNT = 48
_DEFAULT_NEWSLETTER = 28
_DEFAULT_CANDIDATE = 40
_JOB_HINTS = ("candidat", "empleo", "seleccion", "workday", "portal de empleo", "entrevista")


def heuristic_gravedad(row: dict) -> int:
    """Impacto estimado si un atacante entra en esa cuenta (0–100)."""
    from account_cleanup.detect import normalize_text

    tipo = (row.get("tipo") or "").strip().lower()
    blob = normalize_text(
        " ".join(str(row.get(key) or "") for key in ("cuenta", "descripcion", "dominio"))
    )

    score = None
    for points, keywords in _RULES:
        if any(keyword in blob for keyword in keywords):
            score = points
            break

    if score is None:
        if tipo == "newsletter":
            score = _DEFAULT_NEWSLETTER
        elif tipo == "candidato":
            score = _DEFAULT_CANDIDATE
        else:
            score = _DEFAULT_ACCOUNT

    if any(hint in blob for hint in _JOB_HINTS) and score >= 88:
        score = 56

    if tipo == "newsletter":
        score = min(score, _NEWSLETTER_CAP)

    return int(score)


def attach_gravedad(rows: list[dict]) -> list[dict]:
    for row in rows:
        row["gravedad"] = heuristic_gravedad(row)
    return rows


def score_csv(path: Path) -> Path:
    """Añade/recalcula `gravedad` en un inventario ya generado y lo reordena."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    attach_gravedad(rows)

    from account_cleanup.detect import _write_csv

    _write_csv(rows, path)
    return path
