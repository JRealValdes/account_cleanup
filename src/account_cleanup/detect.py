"""Agrupa correos por dominio y detecta cuentas (heurística + OpenAI)."""

from __future__ import annotations

import csv
import json
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import tldextract
from openai import OpenAI
from pydantic import BaseModel, Field
from tqdm import tqdm

from account_cleanup.config import CANDIDATES_JSON, DEFAULT_MODEL, EMAILS_JSONL, GOOGLE_EMAILS, INVENTORY_CSV

_PERSONAL_MAIL_DOMAINS = {"gmail.com", "googlemail.com"}
_OWN_ADDRESSES = {addr.lower() for addr in GOOGLE_EMAILS.values()}
# El propio proveedor del Takeout no es una cuenta a "limpiar".
_SKIP_DOMAINS = {"google.com"}
_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})")

# Los regex se aplican a texto YA pasado por normalize_text: minúsculas, sin diacríticos.
# Escribe "contrasena", "confirmacion", "ano" — nunca "contraseña", "confirmación", "año".
# Ñ/ñ → n; áéíóúü → aeiou. No uses tildes en los patrones.
#
# Recuerdo > precisión: el LLM filtra newsletters y "confirmación de pedido".
# No usamos "confirmacion" / "confirmation" sueltos (inundan con pedidos).
_SIGNAL_PATTERN_SOURCES = (
    # --- Bienvenida / alta ---
    r"\bbienvenid",
    r"\bwelcome(?:\s+(?:to|aboard))?\b",
    r"\bya formas parte",
    r"\bte has (?:registrado|unido|suscrito)",
    r"\bhas creado una cuenta",
    r"\bwe(?:['’])?ve created your account",
    r"\byou(?:['’])?ve\s+(?:joined|signed\s+up|registered|subscribed)",
    r"\byou(?:['’])?re\s+(?:in|all\s+set)\b",
    r"\btu cuenta(?:\s+esta|\s+es)?\s+(?:lista|activa|activada|creada|preparada)",
    r"\byour account (?:is )?(?:ready|active|activated|created|all set)",
    r"\bget started\b",
    r"\b(?:empieza|comienza) a (?:usar|utilizar)",
    r"\bcuenta creada\b",
    r"\baccount (?:has been )?created\b",
    r"\bnew account\b",
    r"\bcuenta nueva\b",
    r"\bthanks for (?:signing|joining|registering|creating)",
    r"\bgracias por (?:registr|crear|unirte|abrir|tu registro|suscrib)",
    r"\bcomplete(?: your)? registration",
    r"\bcompleta(?:r)? tu registro",
    r"\bfinish (?:setting up|your (?:setup|registration|account))",
    r"\btermina(?:r)? de configurar",
    r"\bregistro (?:completado|exitoso|confirmado|finalizado)",
    r"\bregistration (?:complete|successful|confirmed|finished)",
    r"\bsign[- ]?up (?:successful|complete|confirmation|confirmed)",
    r"\balta de (?:usuario|cuenta|cliente|socio)",
    r"\busuario (?:registrado|creado|nuevo)",
    r"\buser(?: account)? created",
    r"\baccount setup\b",
    r"\bset up your account",
    r"\bconfigura(?:r)? tu cuenta",
    r"\bcrear(?:te)? una cuenta",
    r"\bcreate(?: your)? (?:an )?account",
    # --- Verificar / confirmar email o cuenta ---
    r"\bconfirma(?:r)?(?: tu)? (?:correo|e-?mail|direccion|cuenta|registro|identidad|dispositivo)",
    r"\bconfirm(?: your)? (?:e-?mail|address|account|signup|sign[- ]?up|registration|subscription|device|identity)",
    r"\bconfirmacion de (?:correo|e-?mail|cuenta|registro|suscripcion|alta|usuario|identidad|dispositivo)",
    r"\bverify(?: your)? (?:e-?mail|address|account|identity|device)",
    r"\bverifica(?:r)?(?: tu)? (?:correo|e-?mail|cuenta|direccion|identidad|dispositivo)",
    r"\bverificacion de (?:correo|e-?mail|cuenta|identidad)",
    r"\be-?mail verification\b",
    r"\bplease (?:confirm|verify)\b",
    r"\bpor favor (?:confirma|verifica)",
    r"\bclick(?: here)? to (?:confirm|verify|activate)",
    r"\bhaz clic para (?:confirm|verific|activ)",
    r"\bpulsa(?: aqui)? para (?:confirm|verific|activ)",
    r"\bvalidate(?: your)? (?:e-?mail|account)",
    r"\bvalida(?:r)?(?: tu)? (?:correo|e-?mail|cuenta)",
    r"\bvalidacion de (?:correo|e-?mail|cuenta)",
    r"\bactiva(?:r)?(?: tu)? (?:cuenta|correo|e-?mail)",
    r"\bactivate your (?:account|e-?mail)",
    r"\bactivacion de (?:cuenta|correo|e-?mail|usuario)",
    r"\benlace de (?:activacion|verificacion|confirmacion)",
    r"\b(?:activation|verification|confirmation) (?:link|e-?mail)",
    r"\bdouble opt[- ]?in",
    r"\bopt[- ]?in confirmation",
    r"\bconfirm this e-?mail",
    r"\bverify it['’]?s you",
    r"\bconfirma que eres tu",
    r"\bis this you\b",
    r"\bpending verification",
    r"\bverificacion pendiente",
    r"\baction required.{0,30}(?:verif|confirm)",
    r"\bconfirma\b",
    r"\bconfirme\b",
    # --- Codigos, OTP, 2FA, magic link ---
    r"\bcodigo de (?:verificacion|confirmacion|acceso|seguridad|inicio|autenticacion|un solo uso)",
    r"\b(?:verification|confirmation|security|access|login|sign[- ]?in|auth) code",
    r"\byour login code",
    r"\bmagic link",
    r"\benlace magico",
    r"\bpasswordless",
    r"\bsign[- ]?in link",
    r"\blogin link",
    r"\benlace de (?:acceso|inicio de sesion)",
    r"\bone[- ]?time (?:password|code|pin|passcode)",
    r"\bcontrasena de un solo uso",
    r"\bcodigo (?:otp|2fa)",
    r"\botp\b",
    r"\b2[- ]?fa\b",
    r"\b2[- ]?step verification",
    r"\btwo[- ]?(?:factor|step)",
    r"\bdoble factor",
    r"\bautenticacion (?:en )?dos (?:pasos|factores)",
    r"\bmultifactor",
    r"\bmfa\b",
    r"\bpasskey",
    r"\bes tu codigo",
    r"\bis your (?:code|pin|passcode|otp)",
    r"\byour (?:code|pin) is\b",
    r"\btu codigo(?:\s+es|\s+de)",
    r"\bbackup codes",
    r"\bcodigos de (?:respaldo|recuperacion)",
    r"\bsecurity code",
    r"\bcodigo de seguridad",
    # --- Contrasena ---
    r"\brestablec",
    r"\breset(?: your)? password",
    r"\bforgot(?: your)? password",
    r"\bpassword reset",
    r"\brecupera(?:cion|r)?(?: de)? (?:contrasena|cuenta)",
    r"\bcambio de contrasena",
    r"\bcontrasena cambiada",
    r"\bpassword changed",
    r"\bchange(?:d)? your password",
    r"\bupdate(?: your)? password",
    r"\bactualiza(?:r)?(?: tu)? contrasena",
    r"\bnueva contrasena",
    r"\bnew password",
    r"\btemporary password",
    r"\bcontrasena temporal",
    r"\bolvid(?:e|aste|o)(?: mi| tu)? contrasena",
    r"\bpassword forgotten",
    r"\bsolicitud de (?:contrasena|cambio de contrasena)",
    r"\breestablecer contrasena",
    r"\bpassword (?:request|recovery)",
    # --- Login / dispositivo / bloqueo ---
    r"\bnuevo inicio de sesion",
    r"\bnew (?:sign[- ]?in|login|device)",
    r"\bunrecognized (?:device|login|sign[- ]?in)",
    r"\bdispositivo (?:no reconocido|nuevo|desconocido)",
    r"\bunusual (?:activity|sign[- ]?in|login)",
    r"\bactividad (?:inusual|sospechosa)",
    r"\bsuspicious (?:login|sign[- ]?in|activity)",
    r"\binicio de sesion sospechoso",
    r"\bwe noticed a new (?:login|sign[- ]?in|device)",
    r"\bdetectamos un nuevo (?:acceso|inicio|dispositivo)",
    r"\baccount locked",
    r"\bcuenta (?:bloqueada|suspendida)",
    r"\bunlock your account",
    r"\bdesbloquea(?:r)?(?: tu)? cuenta",
    r"\bsecurity alert",
    r"\balerta de seguridad",
    # --- Cambio de email / cuenta ---
    r"\bcambio de (?:correo|e-?mail|direccion)",
    r"\be-?mail(?: address)? changed",
    r"\bnueva direccion de (?:correo|e-?mail)",
    r"\bnew e-?mail address",
    r"\brecover(?: your)? account",
    r"\baccount recovery",
    r"\bidentity verification",
    r"\bdatos de acceso",
    r"\baccess details",
    r"\byour credentials",
    r"\btus credenciales",
    # --- Baja / cierre (sigue siendo evidencia de cuenta) ---
    r"\beliminacion de cuenta",
    r"\bdelete(?:d)? your account",
    r"\bcierre de cuenta",
    r"\bcuenta (?:eliminada|cerrada|cancelada|dada de baja)",
    r"\baccount (?:deleted|closed|cancelled|canceled|deactivated)",
    r"\bbaja de (?:cuenta|usuario|servicio)",
    r"\bcancelacion de (?:cuenta|suscripcion)",
    r"\baccount cancellation",
    # --- Suscripcion / membresia ---
    r"\bsuscripcion confirmada",
    r"\bsubscription confirm",
    r"\byou(?:['’])?ve subscribed",
    r"\bmembership (?:confirm|activated|welcome)",
    r"\bmembresia (?:confirmada|activada)",
    r"\bprueba gratuita",
    r"\bfree trial",
    r"\btrial (?:started|activated)",
)

_SIGNAL_PATTERNS = [re.compile(p) for p in _SIGNAL_PATTERN_SOURCES]


def normalize_text(value: str) -> str:
    """Minúsculas y sin diacríticos, para que los regex no dependan de tildes ni Ñ.

    Ejemplos: «Contraseña» → «contrasena», «España» → «espana», «ÑOÑO» → «nono».
    """
    nfkd = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    lowered = stripped.lower()
    return lowered.replace("’", "'").replace("‘", "'").replace("´", "'")


def subject_signals(subject: str) -> list[str]:
    text = normalize_text(subject)
    hits = []
    for pattern in _SIGNAL_PATTERNS:
        if pattern.search(text):
            hits.append(pattern.pattern)
    return hits


def registrable_domain(email_addr: str | None) -> str | None:
    if not email_addr or "@" not in email_addr:
        return None
    host = email_addr.rsplit("@", 1)[-1].lower().strip().rstrip(">")
    extracted = tldextract.extract(host)
    if extracted.domain and extracted.suffix:
        return f"{extracted.domain}.{extracted.suffix}"
    return host or None


def _parse_sort_date(value: str | None) -> datetime | None:
    if not value:
        return None
    dt: datetime | None = None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        match = _DATE_PREFIX.match(value)
        if match:
            try:
                dt = datetime.fromisoformat(match.group(1))
            except ValueError:
                return None
        else:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _date_only(value: str | None) -> str:
    if not value:
        return ""
    match = _DATE_PREFIX.match(value)
    return match.group(1) if match else value[:10]


@dataclass
class Cluster:
    google_account: str
    google_email: str
    domain: str
    n_emails: int = 0
    from_emails: Counter = field(default_factory=Counter)
    from_names: Counter = field(default_factory=Counter)
    subjects: list[str] = field(default_factory=list)
    signal_subjects: list[str] = field(default_factory=list)
    signal_patterns: Counter = field(default_factory=Counter)
    n_signal_emails: int = 0
    first_date: str | None = None
    last_date: str | None = None
    _first_dt: datetime | None = field(default=None, repr=False)
    _last_dt: datetime | None = field(default=None, repr=False)

    def add(self, record: dict, signals: list[str]) -> None:
        self.n_emails += 1
        if record.get("from_email"):
            self.from_emails[record["from_email"]] += 1
        if record.get("from_name"):
            self.from_names[record["from_name"]] += 1

        subject = (record.get("subject") or "").strip()
        if subject and len(self.subjects) < 40:
            self.subjects.append(subject)
        if signals:
            self.n_signal_emails += 1
            if len(self.signal_subjects) < 25:
                self.signal_subjects.append(subject)
            for item in signals:
                self.signal_patterns[item] += 1

        dt = _parse_sort_date(record.get("date"))
        raw_date = record.get("date")
        if dt is not None:
            if self._first_dt is None or dt < self._first_dt:
                self._first_dt = dt
                self.first_date = raw_date
            if self._last_dt is None or dt > self._last_dt:
                self._last_dt = dt
                self.last_date = raw_date

    def has_account_signal(self) -> bool:
        return bool(self.signal_subjects)

    def typical_from_name(self) -> str:
        return self.from_names.most_common(1)[0][0] if self.from_names else ""

    def typical_from_email(self) -> str:
        return self.from_emails.most_common(1)[0][0] if self.from_emails else ""

    def to_candidate(self) -> dict:
        return {
            "google_account": self.google_account,
            "google_email": self.google_email,
            "domain": self.domain,
            "n_emails": self.n_emails,
            "from_name": self.typical_from_name(),
            "from_email": self.typical_from_email(),
            "fecha_primer_correo": _date_only(self.first_date),
            "fecha_ultimo_correo": _date_only(self.last_date),
            "signal_subjects": self.signal_subjects[:12],
            "sample_subjects": self.subjects[:12],
            "n_signal_emails": self.n_signal_emails,
        }


def load_clusters(emails_path: Path | None = None) -> list[Cluster]:
    path = emails_path or EMAILS_JSONL
    if not path.exists():
        raise FileNotFoundError(
            f"No existe {path}. Ejecuta primero: uv run account-cleanup extract"
        )

    clusters: dict[tuple[str, str], Cluster] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in tqdm(handle, desc="cluster", unit="msg"):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            from_email = (record.get("from_email") or "").lower()
            if from_email in _OWN_ADDRESSES:
                continue
            domain = registrable_domain(from_email)
            if not domain or domain in _PERSONAL_MAIL_DOMAINS or domain in _SKIP_DOMAINS:
                continue
            account = record.get("google_account") or "unknown"
            key = (account, domain)
            cluster = clusters.get(key)
            if cluster is None:
                cluster = Cluster(
                    google_account=account,
                    google_email=record.get("google_email") or account,
                    domain=domain,
                )
                clusters[key] = cluster
            cluster.add(record, subject_signals(record.get("subject") or ""))
    return list(clusters.values())


class DetectedAccount(BaseModel):
    is_account: bool = Field(
        description="True si hay evidencia de que el usuario tiene (o tuvo) una cuenta o registro en ese sitio"
    )
    tipo: str = Field(
        description="cuenta_usuario | newsletter | transaccional | no_relevante"
    )
    service_name: str = Field(description="Nombre corto del servicio o sitio, p.ej. LinkedIn")
    description: str = Field(
        description="Qué es el sitio y para qué sirve, en una o dos frases en español"
    )
    google_account: str
    domain: str
    confidence: float = Field(ge=0, le=1)


class DetectionBatch(BaseModel):
    accounts: list[DetectedAccount]


_SYSTEM_PROMPT = """Eres un analista que reconstruye un inventario de cuentas online a partir de metadatos de correo.

El usuario quiere saber EN QUÉ SITIOS se ha llegado a registrar, para poder hacer limpieza de cuentas (cerrarlas, borrar datos, darse de baja).

Para cada candidato (dominio + cuenta Google) decide:
- is_account: true si hay evidencia de registro, login, verificación de email, reset de contraseña, códigos de acceso, portal de cliente o de empleado, o relación de cuenta de usuario. No basta con marketing genérico ni un boletín suelto sin registro.
- tipo:
  - cuenta_usuario: hay cuenta/login (red social, tienda, banco, SaaS, foro, operadora, administración, portal de empleado, proceso de selección con acceso, etc.)
  - newsletter: principalmente lista de correo / suscripción, sin cuenta clara
  - transaccional: recibos, envíos, avisos puntuales sin evidencia de cuenta
  - no_relevante: spam, notificaciones ajenas, o no se puede afirmar nada
- service_name: marca o producto corto (Spotify, GitHub, SHARE NOW). No uses la dirección de no-reply. Si un mismo dominio agrupa productos distintos y los asuntos lo dejan claro, un nombre conjunto breve ("Uber / Uber Eats").
- description: en español, 1-2 frases sobre QUÉ ES el sitio y para qué se usa, para que el usuario lo reconozca. No enumeres los tipos de correo recibidos.
- confidence: 0 a 1.

Criterios límite:
- Un "bienvenido al equipo / sesión de acogida / welcome kit" laboral, por sí solo, no es una cuenta de usuario.
- Si en el mismo dominio también hay login, "tu cuenta", línea móvil, factura de cliente, códigos de acceso o portal, entonces SÍ es cuenta_usuario (aunque mezcle onboarding laboral).
- Códigos OTP, passkeys, 2FA o reset de contraseña cuentan como cuenta_usuario.
- No inventes servicios que no se sostengan en los asuntos. Conserva google_account y domain tal cual.
- Incluye cuenta_usuario y newsletter con is_account=true. El resto, is_account=false.
"""


def _heuristic_row(cluster: Cluster) -> dict:
    name = cluster.typical_from_name() or cluster.domain.split(".")[0].title()
    name = re.sub(r"\s*(no-?reply|noreply|notifications?|mailer|support)\s*", " ", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip() or cluster.domain
    return {
        "cuenta": name,
        "cuenta_google": cluster.google_account,
        "descripcion": (
            f"Candidato heurístico a partir de {cluster.domain}. "
            "Revisar: no se ha clasificado con LLM."
        ),
        "fecha_primer_correo": _date_only(cluster.first_date),
        "fecha_ultimo_correo": _date_only(cluster.last_date),
        "dominio": cluster.domain,
        "remitente_habitual": cluster.typical_from_email(),
        "n_correos": cluster.n_emails,
        "n_correos_senal": cluster.n_signal_emails,
        "tipo": "candidato",
        "confianza": "",
        "ejemplos_asuntos": " | ".join(cluster.signal_subjects[:5] or cluster.subjects[:5]),
    }


def _select_candidates(candidates: list[dict], max_candidates: int | None) -> list[dict]:
    """Prioriza variedad entre cuentas Google y, dentro de cada una, más señales."""
    if max_candidates is None or max_candidates >= len(candidates):
        return candidates

    grouped: dict[str, list[dict]] = {}
    for item in candidates:
        grouped.setdefault(item["google_account"], []).append(item)
    for bucket in grouped.values():
        bucket.sort(key=lambda c: (c["n_signal_emails"], c["n_emails"]), reverse=True)

    selected: list[dict] = []
    indexes = {account: 0 for account in grouped}
    accounts = list(grouped.keys())
    while len(selected) < max_candidates and any(
        indexes[account] < len(grouped[account]) for account in accounts
    ):
        for account in accounts:
            idx = indexes[account]
            if idx < len(grouped[account]):
                selected.append(grouped[account][idx])
                indexes[account] = idx + 1
            if len(selected) >= max_candidates:
                break
    return selected


def _llm_batches(candidates: list[dict], batch_size: int = 12) -> list[DetectedAccount]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "Falta OPENAI_API_KEY. Copia .env.example a .env y añade tu clave, "
            "o exporta la variable de entorno."
        )

    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    client = OpenAI(api_key=api_key)
    detected: list[DetectedAccount] = []

    for i in tqdm(range(0, len(candidates), batch_size), desc="llm", unit="batch"):
        chunk = candidates[i : i + batch_size]
        completion = client.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(chunk, ensure_ascii=False, indent=2),
                },
            ],
            response_format=DetectionBatch,
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            continue
        detected.extend(parsed.accounts)
    return detected


def _merge_inventory(clusters: list[Cluster], detections: list[DetectedAccount] | None) -> list[dict]:
    by_key = {(c.google_account, c.domain): c for c in clusters}
    rows: list[dict] = []

    if detections is None:
        for cluster in clusters:
            if cluster.has_account_signal():
                rows.append(_heuristic_row(cluster))
        return rows

    for item in detections:
        if not item.is_account:
            continue
        key = (item.google_account, item.domain)
        cluster = by_key.get(key)
        if cluster is None:
            continue
        rows.append(
            {
                "cuenta": item.service_name.strip() or cluster.typical_from_name() or item.domain,
                "cuenta_google": item.google_account,
                "descripcion": item.description.strip(),
                "fecha_primer_correo": _date_only(cluster.first_date),
                "fecha_ultimo_correo": _date_only(cluster.last_date),
                "dominio": item.domain,
                "remitente_habitual": cluster.typical_from_email(),
                "n_correos": cluster.n_emails,
                "n_correos_senal": cluster.n_signal_emails,
                "tipo": item.tipo,
                "confianza": f"{item.confidence:.2f}",
                "ejemplos_asuntos": " | ".join(cluster.signal_subjects[:5] or cluster.subjects[:5]),
            }
        )

    # Una fila por (cuenta normalizada, google_account): si el LLM duplica el mismo servicio, nos quedamos
    # con la de más correos.
    collapsed: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (normalize_text(row["cuenta"]), row["cuenta_google"])
        previous = collapsed.get(key)
        if previous is None or row["n_correos"] > previous["n_correos"]:
            collapsed[key] = row
    return list(collapsed.values())


def _write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "cuenta",
        "cuenta_google",
        "descripcion",
        "fecha_primer_correo",
        "fecha_ultimo_correo",
        "dominio",
        "remitente_habitual",
        "n_correos",
        "n_correos_senal",
        "tipo",
        "confianza",
        "ejemplos_asuntos",
    ]
    rows_sorted = sorted(
        rows,
        key=lambda r: (r["cuenta_google"], r["cuenta"].lower()),
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_sorted)


def detect_accounts(
    emails_path: Path | None = None,
    use_llm: bool = True,
    min_signal_emails: int = 1,
    max_candidates: int | None = None,
) -> Path:
    clusters = load_clusters(emails_path)
    candidates = [
        c.to_candidate()
        for c in clusters
        if c.has_account_signal() and c.n_signal_emails >= min_signal_emails
    ]
    llm_candidates = _select_candidates(candidates, max_candidates)
    CANDIDATES_JSON.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES_JSON.write_text(
        json.dumps(
            {
                "n_clusters": len(clusters),
                "n_candidates": len(candidates),
                "n_llm_candidates": len(llm_candidates),
                "candidates": candidates,
                "llm_candidates": llm_candidates if max_candidates is not None else None,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    detections: list[DetectedAccount] | None
    detections_path = CANDIDATES_JSON.with_name("llm_detections.json")
    if use_llm:
        detections = _llm_batches(llm_candidates)
        detections_path.write_text(
            json.dumps([item.model_dump() for item in detections], ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
    else:
        detections = None
        if detections_path.exists():
            detections_path.unlink()

    rows = _merge_inventory(
        [c for c in clusters if c.has_account_signal()],
        detections,
    )
    _write_csv(rows, INVENTORY_CSV)
    return INVENTORY_CSV
