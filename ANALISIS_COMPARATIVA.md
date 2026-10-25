# Análisis comparativo: VWAPS (original) vs VWAPS_auditable

> **Resumen:** con el mismo input y el mismo EEX, VWAPS_auditable rellena los huecos de la curva con
> una precisión **igual o mejor** que el VWAPS original:
> - un **16–19 % menos de error con offset 0**;
> - empate con offset −1;
> - **menos errores grandes** y **más celdas cubiertas**.
>
> Lo hace con una sola fórmula y sin estado entre días. El VWAPS original gana cuando se ocultan
> VWAPs que están rodeados de otros (backtest *leave-one-out*), gracias a su capa de histórico.
>
> **Actualización: el auditable ya tiene una capa de historia sencilla y sin estado (sección 9).**
> Con ella:
> - mejora los huecos un 18–22 % con offset 0 y un 1–2 % con −1;
> - **empata con el original en el leave-one-out additive** (0.499 frente a 0.494) y reduce la
>   distancia en ratio (0.549 frente a 0.495).

Fecha: 25/10/2026. Datos de EEX hasta el 22/10/2026 (54 días, 8 curvas: DE/FR/NL/AT × Base/Peak).

---

## 1. Cómo se ha comparado

Se han usado **tres pruebas complementarias**:

| Prueba | Qué mide | Ventaja | Limitación |
|---|---|---|---|
| **A. Banco con dinámica real** (`research/bench.py` y siguientes) | Error al rellenar huecos con VWAPs simulados a partir de **movimientos reales** de EEX (8 mercados) | Los VWAPs no siguen el modelo de ningún método | Reproduce la lógica principal de VWAPS, no el programa completo |
| **B. Programa completo, huecos frente a la verdad** (`compare_methods.py --synthetic` + `research/analyze_comparison.py`) | Los dos programas reales, mismo input, misma copia limpia de EEX; error en cada hueco frente a la verdad sintética | Compara los outputs reales celda a celda | La verdad sintética es **EEX + basis AR(1) persistente + ruido**, justo el supuesto de VWAPS: favorece a su capa de histórico |
| **C. Programa completo, leave-one-out** (mismo comando) | Se oculta cada VWAP observado y cada programa lo predice | Es el examen que se hará con datos reales | Los VWAPs ocultos suelen estar rodeados de otros VWAPs; no representa bien los huecos lejanos |

Las dos últimas se han ejecutado con datos sintéticos de los dos tipos (`ratio` y `additive`) y con
`offset_days` 0 y −1. Ningún repositorio se ha modificado: cada uno corre con su código y su
configuración.

## 2. Resultado A: banco con dinámica real

Error medio en €/MWh, menos es mejor, VWAPs con ruido realista del 1 %:

| Método | offset 0 | offset −1 |
|---|---|---|
| EEX a pelo | 1.79 | 1.80 |
| VWAPS2 (spreads + suavizado de 5 días) | 1.91 | 3.35 |
| Lógica principal de VWAPS (aditivo / auto) | 1.41 / 1.38 | 1.44 / 1.43 |
| **VWAPS_auditable** | **1.37** | **1.41** |

Aquí las desviaciones de un día **no** persisten al siguiente, porque son movimientos reales de
mercado. Por eso un histórico no aporta y el método simple queda a la par o ligeramente por delante.

## 3. Resultado B: outputs reales, huecos rellenados frente a la verdad

Se cuentan solo las celdas que rellenan **los dos** programas y que no son VWAP propio (~9 200–9 400
por caso). Error medio absoluto en €/MWh:

| Caso | EEX a pelo | VWAPS | **auditable** | Mejora | p95 VWAPS | p95 auditable |
|---|---|---|---|---|---|---|
| ratio, offset 0 | 0.646 | 0.653 | **0.547** | **+16 %** | 1.87 | **1.57** |
| additive, offset 0 | 0.677 | 0.701 | **0.566** | **+19 %** | 1.76 | **1.43** |
| ratio, offset −1 | 3.640 | **2.709** | 2.715 | −0.2 % | 10.70 | **10.00** |
| additive, offset −1 | 3.685 | 2.756 | **2.747** | +0.3 % | 10.69 | **9.96** |

### Por tramo de curva (additive; ratio da lo mismo con ±2 puntos)

| Tramo | offset 0: VWAPS | offset 0: auditable | offset −1: VWAPS | offset −1: auditable |
|---|---|---|---|---|
| Corto (D/WE/W/BOW) | 0.661 | **0.542** | 6.597 | **6.426** |
| Meses (BOM/M) | 0.902 | **0.552** | 2.394 | **2.332** |
| Quarters | **0.625** | 0.634 | **1.302** | 1.423 |
| Largo (Season/Cal) | 0.599 | **0.532** | **1.043** | 1.108 |

### ¿De dónde sale la diferencia?

El error depende de cómo rellenó cada celda el VWAPS original (additive, offset 0):

| Cómo la rellenó VWAPS | Celdas | VWAPS | auditable |
|---|---|---|---|
| `eex+local` (anclas del día) | 7 356 | **0.520** | 0.538 |
| `eex+hist` (histórico) | 1 558 | **0.647** | 0.658 |
| `eex+smooth` (EEX suavizado de respaldo) | 524 | 3.396 | **0.686** |

- Con anclas del día o con histórico, el original es ligeramente mejor (~3 %).
- **Su respaldo con EEX suavizado es muy malo** (3.4 €/MWh, cinco veces peor). Es el mismo defecto que
  se vio en VWAPS2: suavizar va con retraso. VWAPS_auditable usa EEX del día y no tiene ese problema.
- Con offset −1, el histórico del original ayuda en quarters y Cal. El dato sintético tiene un basis
  persistente, que es el supuesto de VWAPS, y eso compensa su respaldo malo: empate global.

### Cobertura y huecos vacíos

| Caso | Celdas vacías VWAPS | Celdas vacías auditable | Solo las rellena auditable |
|---|---|---|---|
| offset 0 | 3 193 | **3 061** | 132 |
| offset −1 | 3 410 | **3 386** | 24 |

- **No hay ninguna celda que rellene VWAPS y deje vacía el auditable.**
- Las 132 extra son días sin VWAPs propios (por ejemplo, el primer día del histórico) en los que
  **EEX sí tiene precio**. El original las deja vacías porque su histórico o su suavizado aún no tiene
  datos. El auditable pone EEX, con un error de 0.48 €/MWh en esas celdas.
- Las celdas vacías que quedan en el auditable son todas legítimas (ver sección 6).

## 4. Resultado C: leave-one-out (ocultar VWAPs observados)

Error medio absoluto en €/MWh sobre las mismas observaciones ocultas:

| Caso | EEX a pelo | VWAPS | auditable | auditable gana en |
|---|---|---|---|---|
| ratio, offset 0 | 0.723 | **0.495** | 0.561 | 40 % |
| additive, offset 0 | 0.685 | **0.494** | 0.518 | 44 % |
| ratio, offset −1 | 5.290 | **3.935** | 3.971 | 43 % |
| additive, offset −1 | 5.296 | **3.936** | 3.969 | 43 % |

- Aquí **gana el original** (entre un 1 % y un 13 %). Pierde el auditable sobre todo en meses y
  quarters con offset −1. En el tramo corto con offset −1 gana el auditable (7.42 frente a 7.75).
- El motivo: un VWAP oculto casi siempre está rodeado de otros VWAPs, y ahí el original combina
  anclas del día con su **histórico**. Como el dato sintético tiene basis persistente, el histórico
  acierta.
- En el banco con dinámica real (prueba A), donde las desviaciones no persisten, esa ventaja
  desaparece.

## 5. Ejemplo de output: DE Base, 16/09/2026 (additive, offset 0)

| Contrato | VWAPS | auditable | λ (trust) | Verdad |
|---|---|---|---|---|
| D+1 | 143 (eex+local) | 143 (eex_adjusted) | 0.53 | 143 |
| WE | 66.58 | **66.69** | 0.72 | 66.87 |
| W+3 | 162 | 162 | 0.49 | 163 |
| M+1 | 162 | 162 | 0.51 | 163 |
| M+4 | 183 | 183 | 0.72 | 183 |
| M+8 | 101 | 101 | 0.80 | 101 |
| WE+2, WE+3 | vacío | vacío (`eex_not_quoted`) | | |

En un día normal los dos programas dan prácticamente lo mismo. Las diferencias grandes aparecen en
los días o tramos donde el original tira de su respaldo suavizado.

Cada celda del auditable lleva además su explicación, por ejemplo:
`EEX 118.00 + 0.42 x +1.14 [M+3 +1.20 (68%), M+1 +1.00 (32%)] = 118.48` (con `tau_log = 0.7`).

## 6. Comprobación de huecos en modo daily

Con `research/check_daily.py` se ejecutó `daily` día a día (54 días) en una carpeta aparte y se
comparó con el `refill`:

| | offset 0 | offset −1 |
|---|---|---|
| Celdas | 17 264 | 17 264 |
| Diferencias daily frente a refill | **0** | **0** |
| Celdas vacías con precio EEX disponible | **0** | **0** |
| Celdas vacías sin motivo | **0** | **0** |
| `eex_not_quoted` (EEX no cotiza ni se puede construir) | 2 024 | 2 086 |
| `no_eex_publication` (días antes del histórico EEX) | 997 | 1 260 |
| `zero_delivery_hours` (BOW Peak en fin de semana) | 40 | 40 |

Las celdas `eex_not_quoted` son WE+2/WE+3, Sum+3/Win+3, M+8–M+10 en NL y algún BOM/BOW.

- **Actualización:** ahora se rellenan con el último recurso de ALGORITMO.md (sección 6c):
  - proxy desde EEX: padre + EWMA de la forma de los hermanos, o la forma de un mercado vecino;
  - curva de un mercado vecino los días sin EEX propio.

  Con offset 0 solo quedan vacías las 44 celdas sin horas de entrega.

**Incidencia de datos detectada:** desde el 19/10, el scraper de EEX genera filas `Day` sin
`deliveryStart`. VWAPS_auditable las descarta con aviso en el log. El VWAPS original aborta con ellas,
así que `compare_methods.py` usa una copia limpia. El scraper no se ha tocado.

## 7. Simplicidad y explicabilidad

| | VWAPS (original) | VWAPS_auditable |
|---|---|---|
| Parámetros del método | ~25 (modo, tau, pesos por tipo, shrink, histórico ×5, ratio ×2, fallback ×4, correlación, cross, shape ×7…) | **3** (`tau_log`, `shrink_k`, `history_days`) |
| Capas | local, hist (auto), correlación, cross, fallback suavizado, arbitraje, shape | **una fórmula** + arbitraje opcional |
| Estado entre días / warmup | Sí (histórico EWMA acumulado) | **No**: la historia se recalcula desde el input (daily = refill siempre) |
| Ratio, auto, ceros/negativos | Lógica de seguridad específica | **No aplica** (aditivo) |
| Explicación por celda | Fuente y método | **Línea con el cálculo + JSON con cada VWAP y su peso** |
| Motivo de cada hueco vacío | No | **`missing_reason`** |
| Código del método | `basis.py` + `comove.py` + `eex_fallback.py` + `shape.py` + fill | `neighbours.py` (~100 líneas) + fill |

## 8. Conclusión y recomendación

1. **Para producción y para explicarlo: VWAPS_auditable.**
   - Rellena los huecos igual o mejor que el original: +16–19 % con offset 0 y empate con −1.
   - Tiene menos errores grandes y no deja vacías celdas que tienen precio de EEX.
   - Se explica en una frase y cada número se puede recalcular a mano.
2. **La única ventaja del original es el histórico.** Aparece cuando las desviaciones VWAP − EEX se
   repiten de un día a otro. No se puede saber sin datos reales.
3. **Siguiente paso, en el trabajo, con el fichero real:**
   ```
   python compare_methods.py --vwap "ruta/a/tus_vwaps.xlsx"
   ```
   Hay que mirar el bloque *leave-one-out* de `comparison/SUMMARY.md`:
   - si el original gana por menos de un ~5 %, VWAPS_auditable es claramente la mejor opción;
   - si gana por mucho en meses y quarters, señal de desviaciones persistentes, se puede añadir al
     auditable un único término opcional de "desviación de ayer". Sería un parámetro más, igual de
     explicable. No se ha añadido ahora para no complicar sin evidencia.
4. **Calibración:** con datos reales,
   `python run.py tune --tau-logs 0.3,0.5,1 --shrink-ks 0.5,1,2 --history-days-list 0,5,10,20` ajusta
   los tres parámetros con validación en fechas posteriores.

## 9. Capa de historia (añadida después)

Una historia **sin estado**: el trozo de confianza que hoy no cubren tus VWAPs se da a lo que dijeron
tus VWAPs sobre el mismo contrato en las últimas N sesiones (detalle en ALGORITMO.md, sección 6b).
Se recalcula desde el input en cada ejecución, así que `daily` sigue siendo igual que `refill`
(comprobado con historia de 10 sesiones: 0 diferencias en 17 264 celdas).

Programa completo, mismos datos y mismas observaciones ocultas que las secciones 3 y 4
(`research/history_program.py`):

| Caso | Huecos VWAPS | Huecos auditable sin historia | **Huecos auditable 10 sesiones** | LOO VWAPS | LOO auditable sin historia | **LOO auditable 10 sesiones** |
|---|---|---|---|---|---|---|
| ratio, offset 0 | 0.653 | 0.547 | **0.534** | 0.495 | 0.561 | 0.549 |
| additive, offset 0 | 0.701 | 0.566 | **0.544** | 0.494 | 0.517 | **0.499** |
| ratio, offset −1 | 2.709 | 2.715 | **2.685** | 3.935 | 3.971 | 3.958 |
| additive, offset −1 | 2.756 | 2.747 | **2.712** | 3.936 | 3.969 | 3.957 |

En el banco con mercado real (desviaciones que no persisten), la historia de 10 sesiones cuesta
menos de un 1 % (1.398 frente a 1.385 con offset 0). Con desviaciones persistentes ayuda hasta un
16 % (0.580 frente a 0.694). Por eso **viene activada con 10 sesiones**, y se apaga con
`history_days = 0`.

Con la historia, las celdas que antes salían como EEX a pelo por no tener VWAPs ese día
(~2 000 por caso) pasan a `eex_history`. Llevan su desglose por día en la columna `hist_audit`.

### Ficheros de esta comparativa

- `comparison/SUMMARY.md`, `summary_backtest.csv`, `summary_truth.csv`, `summary_coverage.csv`:
  salida de `compare_methods.py`.
- `comparison/ANALISIS_OUTPUT.md`, `analisis_resumen.csv`: análisis celda a celda
  (`research/analyze_comparison.py`).
- `research/*_summary.csv`: banco con dinámica real.
- `comparison/` y `output*/` no se versionan (se regeneran con los comandos anteriores).

## 10. Cifras de la versión final (25/10/2026)

Configuración final:
- `tau_log = 0.7`, `shrink_k = 1`, historia de 10 sesiones;
- solo tenors que EEX ofrece;
- proxy para contratos que faltan hoy;
- EWMA corta en lugar de publicar EEX tal cual.

Mismos datos y mismo examen que en las secciones 3 y 4 (`compare_methods.py --synthetic`).

| Caso | Huecos: VWAPS | **Huecos: auditable** | p95 VWAPS | p95 auditable | LOO: VWAPS | LOO: auditable |
|---|---|---|---|---|---|---|
| ratio, offset 0 | 0.654 | **0.536** (−18 %) | 1.87 | **1.50** | **0.497** | 0.543 |
| additive, offset 0 | 0.704 | **0.543** (−23 %) | 1.77 | **1.39** | 0.494 | **0.489** |
| ratio, offset −1 | 2.712 | **2.691** (−1 %) | 10.69 | **10.00** | **3.976** | 3.991 |
| additive, offset −1 | 2.764 | **2.713** (−2 %) | 10.74 | **9.98** | **3.976** | 3.988 |

Por tramo, rellenando huecos:
- **Gana el auditable** en corto, meses y largo con offset 0, y en meses y corto con offset −1. En meses
  llega a un 34–42 % menos de error.
- **El original es mejor en quarters con offset −1** (un 8–10 %), gracias a su histórico acumulado
  sobre un dato sintético con basis persistente.
