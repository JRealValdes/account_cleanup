# Datos (privados, no versionados)

```
data/
  raw/<cuenta_google>/     # Takeout original. Hoy: mail.mbox (+ zips auxiliares)
  interim/                 # Resumen de correos (JSONL) generado por `extract`
  processed/               # Inventario final (CSV) generado por `detect`
  reviewed.json            # Notas de limpieza (local, no versionado)
  reviewed.example.json    # Plantilla del formato
```

Las carpetas `raw/`, `interim/` y `processed/`, y `reviewed.json`, están en `.gitignore`.
