"""Gravedad si la cuenta se hackea: 0–100, más alto = peor impacto."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel, Field
from tqdm import tqdm

from account_cleanup.config import CANDIDATES_JSON, DEFAULT_MODEL

GRAVEDAD_SCALE = """
Evalúa el SERVICIO real, no coincidencias de texto (Audible no es la tienda Amazon; un portal de empleo de Sanitas no es el seguro).
- 95-100: banco, inversión, hacienda, seguridad social, gestor de contraseñas
- 88-94: salud, seguros médicos, operadora, suministros, sede electrónica
- 80-87: identidad SSO (Microsoft, GitHub), redes sociales, nube de archivos
- 70-79: comercio con pago guardado, movilidad con tarjeta
- 55-69: juegos con wallet, streaming de pago, portales de empleo (CV)
- 40-54: retail / fidelización / apps menores con login
- 20-38: newsletter o suscripción sin cuenta clara
- 0-19: ruido
""".strip()

_SCORE_PROMPT = f"""Asigna un entero gravedad 0-100: impacto si un atacante entra en esa cuenta.

{GRAVEDAD_SCALE}

Conserva el campo id de cada fila. No inventes cuentas.
"""

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
            "adobe",
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
            "audible",
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


def _has_gravedad(row: dict) -> bool:
    value = row.get("gravedad")
    if value is None or value == "":
        return False
    try:
        int(value)
    except (TypeError, ValueError):
        return False
    return True


def attach_gravedad(rows: list[dict], overwrite: bool = False) -> list[dict]:
    """Rellena gravedad. Por defecto no pisa un valor ya asignado (p. ej. por el LLM)."""
    for row in rows:
        if overwrite or not _has_gravedad(row):
            row["gravedad"] = heuristic_gravedad(row)
        else:
            row["gravedad"] = int(row["gravedad"])
    return rows


class _SeverityItem(BaseModel):
    id: int
    gravedad: int = Field(ge=0, le=100)


class _SeverityBatch(BaseModel):
    scores: list[_SeverityItem]


def _llm_score_rows(rows: list[dict], batch_size: int = 20) -> None:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "Falta OPENAI_API_KEY. Copia .env.example a .env o usa --no-llm."
        )

    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    client = OpenAI(api_key=api_key)
    scored: dict[int, int] = {}

    for start in tqdm(range(0, len(rows), batch_size), desc="gravedad", unit="batch"):
        chunk = rows[start : start + batch_size]
        payload = [
            {
                "id": start + offset,
                "cuenta": row.get("cuenta"),
                "cuenta_google": row.get("cuenta_google"),
                "tipo": row.get("tipo"),
                "dominio": row.get("dominio"),
                "descripcion": row.get("descripcion"),
            }
            for offset, row in enumerate(chunk)
        ]
        completion = client.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": _SCORE_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            response_format=_SeverityBatch,
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            continue
        for item in parsed.scores:
            scored[item.id] = item.gravedad

    detections_path = CANDIDATES_JSON.with_name("llm_severity.json")
    detections_path.parent.mkdir(parents=True, exist_ok=True)
    detections_path.write_text(
        json.dumps(scored, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    for index, row in enumerate(rows):
        if index in scored:
            row["gravedad"] = scored[index]


def score_csv(path: Path, use_llm: bool = True) -> Path:
    """Añade/recalcula `gravedad` en un inventario ya generado y lo reordena."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    if use_llm:
        _llm_score_rows(rows)
        attach_gravedad(rows, overwrite=False)
    else:
        attach_gravedad(rows, overwrite=True)

    from account_cleanup.detect import _write_csv

    _write_csv(rows, path, use_llm=use_llm)
    return path
