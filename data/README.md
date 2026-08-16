# Datos (privados, no versionados)

```
data/
  raw/<cuenta_google>/     # Takeout original. Hoy: mail.mbox (+ zips auxiliares)
  interim/                 # Resumen de correos (JSONL) generado por `extract`
  processed/               # Inventario final (CSV) generado por `detect`
```

Las carpetas `raw/`, `interim/` y `processed/` están en `.gitignore`.
