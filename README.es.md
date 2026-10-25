# VWAPS_auditable — Relleno de curvas VWAP, simple y auditable

Hermano de `VWAPS` con **la misma infraestructura**: comandos, `config.toml`, mapeo Excel/CSV con
`use = fill/off`, configuración por producto, salidas, backtest, tune y MLflow. Cambia el **método de
relleno**, que es más simple:

```
precio_hueco = EEX_hueco + λ · media ponderada por cercanía de (VWAP − EEX) de tus VWAPs del día
```

- Solo tres parámetros (`tau_log`, `shrink_k` y `history_days`, que controla una historia opcional sin estado).
- Sin ratio ni modo auto, sin correlaciones, sin shape y sin suavizado.
- Cada precio lleva su explicación (`explanation`) y su desglose (`audit`).
- Cada hueco vacío dice por qué está vacío (`missing_reason`).

Detalle completo: [ALGORITMO.md](ALGORITMO.md) (en inglés, [ALGORITHM.md](ALGORITHM.md)). Resultados
frente al método original: [ANALISIS_COMPARATIVA.md](ANALISIS_COMPARATIVA.md). Primera ejecución con
datos oficiales y cómo evaluar: [PRIMER_USO.md](PRIMER_USO.md).

## Arranque

```
uv sync
python run.py mapping                 # crea/revisa mappings/products.csv (o .xlsx)
python run.py refill --from 2026-08-10
python run.py daily
python run.py backtest
```

`[eex] offset_days = 0` usa la publicación del día; `-1` solo hasta ayer.

## Comprobaciones

```
uv run pytest                                   # tests
python research/check_daily.py --offset 0 --history-days 10   # daily == refill y huecos justificados
python compare_methods.py --vwap "ruta/a/tus_vwaps.xlsx"   # comparar con VWAPS con datos reales
python compare_methods.py --synthetic                       # comparar con datos sintéticos
```

`compare_methods.py` ejecuta los dos repositorios, cada uno con su código y su configuración, sobre
el mismo input y la misma copia limpia de EEX. No modifica ninguno.

## Documentos heredados

`BACKTEST`, `CONFIGURATION`, `OUTPUT`, `MLFLOW` y `NOTEBOOK` vienen de VWAPS y siguen valiendo para
la infraestructura. Llevan un aviso arriba con lo que no aplica.
