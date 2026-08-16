"""Marca en el inventario las cuentas ya revisadas (contraseña / baja)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import tldextract
from openai import OpenAI
from pydantic import BaseModel, Field

from account_cleanup.config import DEFAULT_MODEL, REVIEW_MATCHES_JSON, REVIEWED_JSON

ESTADO_NO = "No"
ESTADO_PASSWORD = "Sí - Contraseña cambiada"
ESTADO_DELETED = "Sí - Cuenta eliminada"
ESTADO_OTROS = "Otros"

_GOOGLE_HINTS = {
    "javi": "javivireal",
    "javivireal": "javivireal",
    "papa": "jrealvaldes",
    "papá": "jrealvaldes",
    "jrealvaldes": "jrealvaldes",
}

_SCOPE_ALL = {"todos", "todas", "varios", "varias", "todas las cuentas"}

_PAREN = re.compile(r"\(([^)]*)\)")
_DOMAINISH = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")

_MATCH_PROMPT = """Empareja notas de limpieza de cuentas con filas de un inventario.

Cada nota tiene un id. Conserva ese id. Cada nota va a CERO o MÁS filas.
No mezcles marcas distintas: Amazon no es Audible ni Amazon Jobs ni AWS;
Xiaomi no es Smart Life; un portal de empleo de Sanitas no es el seguro Adeslas.
Club de Benefits / Plexus no es Wellflex ni Club·by ni Carne Joven.
SanidadMadrid.org no es Comunidad de Madrid (madrid.org es Cuenta Digital).
Vodafone no es Wellflex (beneficios de empleado), aunque la descripción mencione Vodafone.
Si el query es un dominio, usa el campo dominio (o la parte registrable: edenred.com → edenred.info).
(todos) / (varias) / (varios) = todas las filas de ese servicio.
Javi = javivireal. Papá = jrealvaldes.
Equivalencias habituales: Car2go = SHARE NOW; Pottermore = Wizarding World;
Twitter = X; Sony = PlayStation; IUBH / iubh.de = IU International University;
Ycombinator = YC Work at a Startup; Madrid.org = Comunidad de Madrid;
Tplink = TP-Link; HBO = HBO Max; Xiaomi Home = Xiaomi Account.
Si no hay fila razonable, row_ids vacío. No inventes ids.
"""


class ReviewItem:
    def __init__(
        self,
        query: str,
        estado: str = ESTADO_PASSWORD,
        nota: str = "",
        cuenta_google: str | None = None,
        scope_all: bool = False,
    ) -> None:
        self.query = query
        self.estado = estado
        self.nota = nota
        self.cuenta_google = cuenta_google
        self.scope_all = scope_all
        self.aliases = []
        self.search = _search_text(query)
        self.source = query

    def as_dict(self) -> dict:
        payload = {"query": self.query, "estado": self.estado}
        if self.nota:
            payload["nota"] = self.nota
        if self.cuenta_google:
            payload["cuenta_google"] = self.cuenta_google
        if self.scope_all:
            payload["scope"] = "all"
        return payload


class _MatchItem(BaseModel):
    id: int
    query: str
    row_ids: list[int] = Field(default_factory=list)


class _MatchBatch(BaseModel):
    matches: list[_MatchItem]


def is_unresolved(estado: str | None) -> bool:
    value = (estado or ESTADO_NO).strip()
    return value == "" or value.lower() == ESTADO_NO.lower()


def infer_estado(raw: str) -> str:
    """Por defecto contraseña cambiada; solo baja si lo dice explícitamente."""
    from account_cleanup.detect import normalize_text

    text = normalize_text(raw)
    if "contrasena" in text and "borrad" in text:
        return ESTADO_PASSWORD
    if "cuenta eliminada" in text or "borrada ademas" in text:
        return ESTADO_DELETED
    if re.search(r"\beliminad", text) and "contrasena" not in text:
        return ESTADO_DELETED
    return ESTADO_PASSWORD


def parse_review_line(raw: str, default_estado: str = ESTADO_PASSWORD) -> ReviewItem:
    inferred = infer_estado(raw)
    estado = inferred if inferred != ESTADO_PASSWORD else default_estado

    cuenta_google = None
    scope_all = False
    notes: list[str] = []
    aliases: list[str] = []
    for chunk in _PAREN.findall(raw):
        from account_cleanup.detect import normalize_text

        key = normalize_text(chunk).strip()
        if key in _SCOPE_ALL:
            scope_all = True
            continue
        if key in _GOOGLE_HINTS:
            cuenta_google = _GOOGLE_HINTS[key]
            continue
        if inferred == ESTADO_DELETED or (
            "contrasena" in key and "borrad" in key
        ):
            notes.append(chunk.strip())
            continue
        aliases.append(chunk.strip())

    query = _PAREN.sub("", raw).strip()
    query = re.sub(r"\s+", " ", query)
    item = ReviewItem(
        query=query,
        estado=estado,
        nota="; ".join(notes),
        cuenta_google=cuenta_google,
        scope_all=scope_all,
    )
    item.aliases = aliases
    item.source = raw.strip()
    return item


def load_reviewed(path: Path | None = None) -> list[ReviewItem]:
    target = path or REVIEWED_JSON
    if not target.exists():
        return []
    payload = json.loads(target.read_text(encoding="utf-8"))
    default = payload.get("default_estado") or ESTADO_PASSWORD
    items: list[ReviewItem] = []
    for raw in payload.get("items", []):
        if isinstance(raw, str):
            items.append(parse_review_line(raw, default))
            continue
        query = str(raw.get("query") or "").strip()
        if not query:
            continue
        item = parse_review_line(query, default)
        if raw.get("estado"):
            item.estado = str(raw["estado"])
        if raw.get("nota"):
            item.nota = str(raw["nota"])
        if raw.get("cuenta_google"):
            item.cuenta_google = str(raw["cuenta_google"])
        if str(raw.get("scope") or "").lower() == "all":
            item.scope_all = True
        items.append(item)
    return items


def sort_inventory_rows(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda r: (
            0 if is_unresolved(r.get("resuelto")) else 1,
            -int(r.get("gravedad") or 0),
            (r.get("cuenta") or "").lower(),
            r.get("cuenta_google") or "",
        ),
    )


def apply_review(
    rows: list[dict],
    items: list[ReviewItem] | None = None,
    use_llm: bool = True,
) -> dict:
    """Rellena `resuelto` y devuelve un resumen de emparejamientos."""
    persist = items is None
    source = items if items is not None else load_reviewed()
    for row in rows:
        row["resuelto"] = ESTADO_NO

    matched_queries: list[str] = []
    unmatched_queries: list[str] = []
    assignments: dict[int, str] = {}

    cache = _load_match_cache() if persist else {}
    pending_llm: list[ReviewItem] = []

    for item in source:
        indices = _indices_from_cache(rows, item, cache)
        if not indices:
            indices = match_item(item, rows)
        if indices:
            matched_queries.append(item.source)
            _assign(assignments, indices, item.estado)
            cache[_item_key(item)] = [_row_spec(rows[i]) for i in indices]
        else:
            pending_llm.append(item)

    llm_hits: dict[int, list[int]] = {}
    if use_llm and pending_llm:
        llm_hits = _llm_match(pending_llm, rows)

    for offset, item in enumerate(pending_llm):
        indices = llm_hits.get(offset) or []
        if indices:
            matched_queries.append(item.source)
            _assign(assignments, indices, item.estado)
            cache[_item_key(item)] = [_row_spec(rows[i]) for i in indices]
        else:
            unmatched_queries.append(item.source)

    for index, estado in assignments.items():
        rows[index]["resuelto"] = estado

    if persist:
        _save_match_cache(cache)

    marked = sum(1 for row in rows if not is_unresolved(row.get("resuelto")))
    return {
        "marked_rows": marked,
        "matched_queries": matched_queries,
        "unmatched_queries": unmatched_queries,
        "n_items": len(source),
    }


def print_review_report(report: dict) -> None:
    n_items = report.get("n_items") or 0
    if not n_items:
        return
    matched = len(report.get("matched_queries") or [])
    print(
        f"Revisión: {report.get('marked_rows', 0)} filas marcadas "
        f"({matched}/{n_items} notas)"
    )
    unmatched = report.get("unmatched_queries") or []
    if unmatched:
        print("Sin coincidencia: " + ", ".join(unmatched))


def match_item(item: ReviewItem, rows: list[dict]) -> list[int]:
    """Empareja una nota con filas: nombre exacto, dominio, tokens unívocos."""
    from account_cleanup.detect import normalize_text

    search = item.search
    if not search:
        return []

    needles = [search, *[ _search_text(alias) for alias in item.aliases if alias]]
    exact: list[int] = []
    domain: list[int] = []
    token: list[int] = []

    for index, row in enumerate(rows):
        if item.cuenta_google and row.get("cuenta_google") != item.cuenta_google:
            continue
        name = normalize_text(row.get("cuenta") or "")
        dominio = normalize_text(row.get("dominio") or "")
        row_tokens = _tokens(name)
        hit_exact = False
        hit_domain = False
        hit_token = False
        for needle in needles:
            if not needle:
                continue
            query_tokens = _tokens(needle)
            if _alnum(name) == _alnum(needle) or name == needle:
                hit_exact = True
                break
            if _domain_hit(needle, dominio):
                hit_domain = True
                continue
            if (
                query_tokens
                and len(_alnum(needle)) >= 3
                and (
                    all(tok in row_tokens for tok in query_tokens)
                    or (
                        len(row_tokens) >= 2
                        and all(tok in query_tokens for tok in row_tokens)
                    )
                )
            ):
                hit_token = True
        if hit_exact:
            exact.append(index)
        elif hit_domain:
            domain.append(index)
        elif hit_token:
            token.append(index)

    if exact:
        return exact
    if domain:
        return domain
    if not token:
        return []
    if item.scope_all or len(token) == 1:
        return token
    domains = {(rows[i].get("dominio") or "") for i in token}
    if len(domains) == 1:
        return token
    return []


def _search_text(query: str) -> str:
    from account_cleanup.detect import normalize_text

    return normalize_text(_PAREN.sub("", query)).strip()


def _alnum(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value)


def _tokens(value: str) -> list[str]:
    return [tok for tok in re.split(r"[^a-z0-9]+", value) if tok]


def _domain_hit(query: str, dominio: str) -> bool:
    q = query.strip().rstrip(".")
    d = dominio.strip().rstrip(".")
    if not q or not d:
        return False
    if q == d:
        return True
    if _DOMAINISH.match(q) or "." in q:
        q_ext = tldextract.extract(q)
        d_ext = tldextract.extract(d)
        q_reg = ".".join(p for p in (q_ext.domain, q_ext.suffix) if p)
        d_reg = ".".join(p for p in (d_ext.domain, d_ext.suffix) if p)
        if q_ext.domain and q_ext.domain == d_ext.domain:
            return True
        if q_reg and q_reg == d_reg:
            return True
    return _alnum(q) == _alnum(d)


def _assign(assignments: dict[int, str], indices: list[int], estado: str) -> None:
    rank = {
        ESTADO_NO: 0,
        ESTADO_OTROS: 1,
        ESTADO_PASSWORD: 2,
        ESTADO_DELETED: 3,
    }
    for index in indices:
        previous = assignments.get(index, ESTADO_NO)
        if rank.get(estado, 1) >= rank.get(previous, 0):
            assignments[index] = estado


def _row_spec(row: dict) -> dict:
    return {
        "cuenta": row.get("cuenta"),
        "cuenta_google": row.get("cuenta_google"),
        "dominio": row.get("dominio"),
    }


def _item_key(item: ReviewItem) -> str:
    return f"{item.query}\t{item.cuenta_google or ''}\t{int(item.scope_all)}"


def _indices_from_cache(rows: list[dict], item: ReviewItem, cache: dict) -> list[int]:
    specs = cache.get(_item_key(item)) or []
    if not specs:
        return []
    found: list[int] = []
    for spec in specs:
        for index, row in enumerate(rows):
            if item.cuenta_google and row.get("cuenta_google") != item.cuenta_google:
                continue
            if (
                row.get("cuenta") == spec.get("cuenta")
                and row.get("cuenta_google") == spec.get("cuenta_google")
            ):
                found.append(index)
                break
    return found


def _load_match_cache() -> dict:
    if not REVIEW_MATCHES_JSON.exists():
        return {}
    try:
        payload = json.loads(REVIEW_MATCHES_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload.get("queries") or {}


def _save_match_cache(cache: dict) -> None:
    REVIEW_MATCHES_JSON.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_MATCHES_JSON.write_text(
        json.dumps({"queries": cache}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _llm_match(items: list[ReviewItem], rows: list[dict]) -> dict[int, list[int]]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {}

    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    client = OpenAI(api_key=api_key)
    row_payload = [
        {
            "id": index,
            "cuenta": row.get("cuenta"),
            "cuenta_google": row.get("cuenta_google"),
            "dominio": row.get("dominio"),
            "descripcion": (row.get("descripcion") or "")[:180],
        }
        for index, row in enumerate(rows)
    ]
    query_payload = [
        {
            "id": offset,
            "query": item.query,
            "cuenta_google": item.cuenta_google,
            "scope_all": item.scope_all,
        }
        for offset, item in enumerate(items)
    ]
    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": _MATCH_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"queries": query_payload, "rows": row_payload},
                    ensure_ascii=False,
                ),
            },
        ],
        response_format=_MatchBatch,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        return {}

    allowed = set(range(len(rows)))
    by_id = {offset: item for offset, item in enumerate(items)}
    hits: dict[int, list[int]] = {}
    for match in parsed.matches:
        item = by_id.get(match.id)
        if item is None:
            continue
        indices = []
        for row_id in match.row_ids:
            if row_id not in allowed:
                continue
            if item.cuenta_google and rows[row_id].get("cuenta_google") != item.cuenta_google:
                continue
            indices.append(row_id)
        hits[match.id] = indices
    return hits
