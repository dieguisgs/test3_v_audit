# VWAPS_auditable — Relleno de curvas VWAP, simple y auditable

Hermano de `VWAPS` con **la misma infraestructura**: comandos, `config.toml`, mapeo Excel/CSV con
`use = fill/off`, configuración por producto, salidas, backtest, tune y MLflow. Cambia el **método de
relleno**, que es más simple:

```
precio_hueco = EEX_hueco + λ · media ponderada por cercanía de (VWAP − EEX) de tus VWAPs del día
```

- Cuatro parámetros principales: `tau_log`, `shrink_k`, `volume_weight` y `history_days` (historia opcional sin estado).
- Núcleo aditivo sin modo auto, correlaciones ni shape; el respaldo sin información propia usa una EWMA corta de EEX.
- Cada precio lleva su explicación (`explanation`) y su desglose (`audit` o `aggregation_audit`).
- Cada hueco vacío dice por qué está vacío (`missing_reason`).

## Coherencia entre trimestres y meses

Con la capa `layers.coherence` activada, si un trimestre **no tiene VWAP propio** y sus tres meses
completos tienen precio en la misma curva y fecha, su precio final es la **media ponderada por
horas de entrega** de esos meses:
`precio_Q = Σ(precio_mes × horas_mes) / Σ(horas_mes)`. Los meses pueden ser propios o estimados;
deben cubrir exactamente el trimestre, tener horas positivas y no estar excluidos como anclas por
los filtros. Si falta alguno de estos requisitos, se conserva el resultado del método habitual.

La capa es opcional y está activada por defecto para conservar el comportamiento anterior. En `config.toml`:

```toml
[layers]
coherence = true  # false: conservar la estimación habitual del trimestre
arbitrage = false # reconstrucción independiente de huecos sin referencia EEX
```

`true` activa la capa y `false` la desactiva, sin comillas en TOML. Con `coherence = false` no se
sustituye la estimación habitual por la media de los meses.
Si se activa `arbitrage`, esa otra capa puede seguir reconstruyendo huecos con los contratos disponibles.
En modo `individual`, la columna `layer_coherence` del mapeo permite activar o desactivar la capa
por producto; una celda vacía hereda el TOML.
El cambio se aplica a los siguientes cálculos. Para recalcular fechas ya publicadas, usa
`python run.py refill --from AAAA-MM-DD`; `catchup` solo procesa fechas pendientes.

La agregación se identifica con `source = months_aggregate`, `estimation_method = quarter_from_months` y el desglose
`aggregation_audit`. No ajusta los meses ni impone coherencia a días, semanas, seasons o años.
Puede usar meses propios fuera de los tenors solicitados, pero no estima meses no pedidos ni
amplía los tenors ofrecidos. El flujo es: relleno habitual → coherencia opcional → arbitraje opcional
→ informe de consistencia. Aplicar la coherencia antes del arbitraje evita reconstrucciones circulares.

**Si el trimestre y sus meses tienen VWAP propio y no cuadran, todos se conservan.** Las operaciones
pueden haberse realizado en momentos distintos: no es posible conservar esos precios y exigir
igualdad simultáneamente. El informe de consistencia muestra el descuadre cuando el trimestre y sus
tres meses están publicados en la curva; funciona también con `coherence = false` y no modifica los
VWAPs propios. Esta regla garantiza la relación trimestre–meses solo en los casos agregados;
no garantiza coherencia simultánea de toda la curva ni, por sí sola, menor error de estimación.

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
