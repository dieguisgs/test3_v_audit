# Primer uso con datos oficiales y cómo evaluar los resultados

Guía paso a paso para la primera ejecución con el fichero real de VWAPs y para comprobar que los
resultados son buenos. El flujo se ha probado de principio a fin con un Excel en el formato real:
mismas columnas que `power_row_2025_future_spot` y fechas de Excel.

---

## 1. Preparar

```
uv sync                        # crea el entorno (pandas, numpy, openpyxl)
uv sync --group dev            # opcional: pytest y ruff
```

En `config.toml`:

```toml
[paths]
vwap_input     = "ruta/power_row_2025_future_spot.xlsx"   # admite xlsx/csv y comodines (data/vwaps_*.xlsx)
mapping        = "mappings/products.xlsx"                  # o .csv
eex_curves_dir = "ruta/eex_scraper/output/curves/POWER"
output_dir     = "output"

[vwap_columns]          # nombres de columna de tu fichero
reference_date = "reference_date"
product        = "product"
region         = "region"
unit           = "unit"
tenor          = "tenor2"
vwap           = "vwap"
volume         = "total_volume"
```

Las fechas pueden ser fechas de Excel, `AAAA-MM-DD` o `DD/MM/AAAA`.

## 2. Mapeo de productos (Excel on/off)

```
python run.py mapping
```

- La primera vez crea el Excel con **todos los productos en `use = off`**. Es a propósito: nada se
  rellena sin revisarlo.
- Para cada producto revisa:
  - `eex_file`: el fichero EEX correcto (mercado y perfil);
  - `hours`: Base o Peak;
  - `timezone`;
- Después pon **`use = fill`** en los productos que quieras rellenar.
- Vuelve a lanzar `python run.py mapping` para validar. Cada fila debe salir `OK` con el rango de
  fechas de EEX.

## 3. Comprobar convenciones de etiquetas

```
python run.py detect-conventions
```

Compara cómo interpretar D+n y WE+n (días naturales o hábiles, y desplazamiento del fin de semana)
contra EEX. **Elige la fila con la mediana más baja** y ponla en `[conventions]`. En la prueba salió
`day = "calendar"`, `weekend_offset = 0`, que son los valores por defecto.

## 4. Rellenar

```
python run.py refill                   # todo el histórico del fichero
python run.py daily                    # hoy (o --date AAAA-MM-DD)
python run.py catchup                  # solo los días pendientes
python run.py status                   # resumen por día
```

Salidas en `output/`:
- `filled_history.csv`: la curva completa, una fila por día, producto y contrato, con su explicación;
- `filled/<fecha>.csv`: lo mismo, un fichero por día;
- `enriched_history.csv`: tu fichero original intacto con las filas rellenadas añadidas;
- `consistency_history.csv`: chequeo M/Q/Cal;
- `_logs/`: avisos (EEX antiguo, filas de EEX descartadas, VWAPs excluidos…).

## 5. Cómo evaluar que está bien

Hay **cuatro comprobaciones**. La principal sigue siendo **ocultar precios**.

### 5.1 Backtest: ocultar cada VWAP y predecirlo (*leave-one-out*)

```
python run.py backtest
```

Para cada día y cada VWAP propio:
1. **Se oculta ese VWAP**, como si no lo tuvieras.
2. Se predice con el resto de VWAPs de ese día (y la historia, si está activada) de tres formas:
   - `eex`: el precio de EEX a pelo. Es la referencia mínima a batir.
   - `neighbours`: la fórmula del método aplicada directamente.
   - `pipeline_configured`: el programa completo tal como está configurado, recalculando la curva
     sin ese VWAP. **Es la cifra que importa.**
3. Se compara la predicción con el VWAP real que se había ocultado.

`output/backtest_report.csv` resume el error por método y tramo (corto, mes, quarter, largo):

| Columna | Significado |
|---|---|
| `mae` | Error medio absoluto en €/MWh. **Cuanto más bajo, mejor** |
| `bias` | Error medio con signo. Cerca de 0 = sin sesgo sistemático |
| `rmse` | Penaliza más los errores grandes |
| `mae_eex_paired` | Error de EEX a pelo en esos mismos casos |
| `mae_improvement` | `mae_eex_paired − mae`. **Positivo = el método mejora a EEX** |

`output/backtest_loo.csv` tiene cada predicción individual (día, contrato, VWAP real, predicción y
error), para mirar casos concretos.

**Qué es "estar bien":**
- `pipeline_configured` con `mae_improvement` positivo en todos los tramos.
- `bias` pequeño comparado con `mae`.

En la prueba con el formato real:

| Tramo | EEX a pelo | Método | Mejora |
|---|---|---|---|
| Todo | 0.68 | **0.50** | +0.19 €/MWh |
| Corto | 0.71 | **0.43** | +0.28 |
| Meses | 0.63 | **0.52** | +0.11 |
| Quarters | 0.74 | **0.62** | +0.13 |
| Largo | 0.65 | **0.52** | +0.13 |

### 5.2 Comparar con el VWAPS original sobre las mismas observaciones ocultas

```
python compare_methods.py --vwap "ruta/power_row_2025_future_spot.xlsx" --mapping mappings/products.xlsx
```

- Ejecuta el VWAPS original y este, cada uno con su código y su configuración, con el mismo fichero
  y la misma copia limpia de EEX, para `offset_days` 0 y −1.
- En `comparison/SUMMARY.md`, la tabla *leave-one-out* compara los errores de los dos sobre
  **exactamente las mismas observaciones ocultas**.
- La columna `auditable_better_share` dice en qué porcentaje de casos gana el nuevo.

### 5.3 Calibrar los parámetros con validación en fechas posteriores

```
python run.py tune --tau-logs 0.3,0.5,1 --shrink-ks 0.5,1,2 --history-days-list 0,5,10,20
```

- Prueba todas las combinaciones **eligiendo solo con las fechas antiguas**, y valida la elegida en
  las últimas fechas (`--validation-days`, 20 por defecto). Así se evita elegir parámetros que solo
  funcionan en el pasado.
- **No cambia `config.toml`:** escribe la propuesta en `output/tuning_selected.json` y las tablas en
  `tuning_calibration.csv` / `tuning_validation.csv`.
- Si la validación mejora a la configuración actual, copia los valores a `[method]`.

### 5.4 Comprobaciones de integridad

```
python research/check_daily.py --vwap "ruta/fichero.xlsx" --offset 0 --history-days 10
```

- Ejecuta `daily` día a día y comprueba que da **lo mismo que `refill`**.
- Comprueba que **ninguna celda queda vacía si EEX tiene precio** y que toda celda vacía tiene
  `missing_reason`.
- Debe terminar en `RESULT: OK`.

```
uv run pytest            # 480 tests
uvx ruff check .         # lint
uvx ruff format --check .
```

## 6. Problemas típicos

| Síntoma | Causa probable | Solución |
|---|---|---|
| `Curves to fill: 0` / "No valid VWAPs from active products" | Mapeo con todo en `use = off` | Poner `use = fill` (paso 2) |
| Un producto no aparece en el mapeo | Nombre/región/unidad distintos en el fichero | Revisar `[vwap_columns]` y los valores de `product`, `region`, `unit` |
| Aviso "excluded N EEX settlements without deliveryStart" | El scraper genera filas Day sin fecha desde el 19/10 | Ninguna: se descartan y se avisa |
| Muchas celdas `eex_proxy` | EEX no cotiza ese contrato (WE+2/3, seasons lejanas…): se reconstruye desde EEX (ALGORITMO 6c) | Esperado; van con confianza baja. `[fallback] enabled = false` para dejarlas vacías |
| Celdas `eex_donor` | Ese día el mercado no tenía publicación EEX: se usa la curva de un vecino | Esperado al inicio del histórico; revisa `donors` en `[fallback]` si el mercado no tiene vecinos DE/FR/AT |
| Celdas `missing` con `no_eex_publication` | Días antes del inicio del histórico de EEX | Esperado |
| D+n o WE+n con errores grandes en el backtest | Convención de etiquetas incorrecta | Paso 3 |
| Un VWAP raro mueve demasiado a sus vecinos | VWAP de muy poco volumen | `min_volume` o `max_anchor_dev` en `[method]` |
