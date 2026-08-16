# account_cleanup

Inventario de cuentas online a partir de un Google Takeout, para poder ir cerrándolas después.

Este repositorio irá acumulando más herramientas de limpieza. La primera extrae metadatos del correo, resume remitentes y asuntos, y detecta sitios en los que parece que llegaste a registrarte.

## Datos (privados)

Los Takeout viven en `data/` y **no se suben a git**:

```
data/
  raw/<cuenta_google>/mail.mbox     # correo exportado (incluye spam y papelera)
  raw/<cuenta_google>/*.zip         # resto del Takeout (mapas, contactos, …)
  interim/emails.jsonl              # resumen: fecha, from, subject
  processed/accounts_inventory.csv  # inventario final, una fila por cuenta
  reviewed.json                     # cuentas ya repasadas (local, no se sube)
  reviewed.example.json             # plantilla del formato
```

Cuentas actuales:

| Carpeta | Correo |
|---|---|
| `javivireal` | javivireal@gmail.com |
| `jrealvaldes` | jrealvaldes@gmail.com |

Si añades otra cuenta, crea `data/raw/<nombre>/mail.mbox` y vuelve a ejecutar el pipeline. El nombre de carpeta es el valor de `cuenta_google` en el CSV.

## Cómo funciona

1. **`extract`** recorre cada `.mbox` en streaming (solo cabeceras: no carga el cuerpo) y escribe un JSONL con fecha, remitente y asunto.
2. **`detect`** agrupa por dominio del remitente y se queda con grupos cuyos asuntos parecen de alta de cuenta (bienvenida, confirma email, verifica, restablece contraseña, códigos de acceso, etc.).
3. Un modelo de OpenAI (`gpt-5.6-luna` por defecto; se cambia en `.env`) clasifica esos candidatos y rellena nombre y descripción. El CSV final **no duplica** el mismo servicio en la misma cuenta Google.

No uso el SDK de Cursor para esto: esa API está pensada para agentes sobre un repo, no para clasificar miles de filas. Sale más caro y más lento. Con una `OPENAI_API_KEY` basta. `--no-llm` deja un inventario heurístico si no quieres gastar tokens.

## Requisitos

- [uv](https://docs.astral.sh/uv/) (ya usado en esta máquina)
- Python 3.11+ (uv lo gestiona)
- Clave de OpenAI para `detect` y `score` (salvo `--no-llm`)

```bash
uv sync
copy .env.example .env
```

Edita `.env` y pon `OPENAI_API_KEY`.

## Uso

```bash
uv run account-cleanup extract
uv run account-cleanup detect
```

O en un paso:

```bash
uv run account-cleanup run
```

Prueba rápida (N mensajes por mbox, sin LLM):

```bash
uv run account-cleanup extract --limit 500
uv run account-cleanup detect --no-llm
```

Prueba del LLM con un subconjunto de candidatos (dominios, no correos sueltos):

```bash
uv run account-cleanup detect --max-candidates 30
```

Si ya tienes el CSV y solo quieres (re)calcular la gravedad, sin volver a parsear el correo:

```bash
uv run account-cleanup score
uv run account-cleanup score --no-llm
```

Por defecto `score` usa el LLM. `--no-llm` aplica la heurística de palabras clave. `detect` pide `gravedad` en la misma clasificación; si el modelo no la trae, se rellena con la heurística.

El listado de cuentas ya repasadas está en `data/reviewed.json` (cópialo de `reviewed.example.json`; no se sube a git). Por defecto: contraseña cambiada; `PIN cambiado` = PIN; `cuenta eliminada` / `borrada además` / `eliminada` = baja; `no era mía` = el email se usó en una cuenta ajena; `no existe` = el servicio ya no tiene cuenta. `detect`, `score` y `review` marcan la columna `resuelto` en el CSV. Si el nombre no encaja, el modelo intenta el alias (Sony → PlayStation, Car2go → SHARE NOW); `--no-llm` se queda en nombre y dominio.

```bash
uv run account-cleanup review
uv run account-cleanup review --no-llm
```

El CSV ordena primero lo **no** resuelto (por gravedad) y después lo ya resuelto (por gravedad).

Salida: `data/processed/accounts_inventory.csv` (UTF-8 con BOM, se abre bien en Excel).

Columnas:

| Columna | Contenido |
|---|---|
| `cuenta` | Sitio o servicio detectado |
| `cuenta_google` | `javivireal` o `jrealvaldes` |
| `gravedad` | 0–100: impacto si esa cuenta se hackea |
| `resuelto` | `No`, `Sí - Contraseña cambiada`, `Sí - PIN cambiado`, `Sí - Cuenta eliminada`, `Sí - No era mía`, `Sí - No existe` u `Otros` |
| `descripcion` | De qué va, para reconocerlo |
| `fecha_primer_correo` / `fecha_ultimo_correo` | Rango visto en el Takeout |
| `dominio` | Dominio registrable del remitente |
| `remitente_habitual` | From más frecuente |
| `n_correos` | Correos de ese dominio |
| `n_correos_senal` | Correos con asunto de alta/verificación |
| `tipo` | `cuenta_usuario` o `newsletter` (con LLM) |
| `confianza` | 0–1 según el modelo |
| `ejemplos_asuntos` | Asuntos que dispararon la detección |

Si te registraste en el mismo sitio con **ambas** Gmail, verás **dos filas** (una por cuenta Google): son cuentas distintas. El mismo sitio no se parte en una fila por cada correo.

## Notas

- El `.mbox` de Takeout puede incluir spam y papelera; eso ayuda a no perder altas antiguas, pero también mete ruido. El LLM intenta filtrar marketing puro.
- Los zips de Takeout de esta exportación traen mapas, contactos y algo de configuración de Gmail, no más correo. El inventario sale del `mail.mbox`.
- `data/interim/` y `data/processed/` también están ignorados por git: el inventario lista sitios privados.
