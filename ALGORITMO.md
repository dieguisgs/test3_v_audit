# VWAPS_auditable — Algoritmo de relleno de curvas

> **En una frase:** EEX nos da la **forma** de la curva; nuestros VWAPs nos dan el **nivel**.
> Cada hueco se rellena con el precio de EEX más la desviación media (VWAP − EEX) de nuestros VWAPs
> **cercanos en la curva**. Cuanto más cerca está un VWAP, más pesa. Si no hay ninguno cerca, el
> ajuste se apaga solo y nos quedamos en EEX.

Versión en inglés: [ALGORITHM.md](ALGORITHM.md). Comparativa con el método original:
[ANALISIS_COMPARATIVA.md](ANALISIS_COMPARATIVA.md).

---

## Índice

1. [Qué problema resuelve](#1-qué-problema-resuelve)
2. [La idea](#2-la-idea)
3. [La fórmula](#3-la-fórmula)
4. [Paso a paso](#4-paso-a-paso)
5. [Ejemplo numérico completo](#5-ejemplo-numérico-completo)
6. [Cómo se comporta](#6-cómo-se-comporta)
   - [6b. Capa de historia (opcional)](#6b-capa-de-historia-opcional-activada-con-10-sesiones)
   - [6c. Último recurso: que no quede ningún hueco vacío](#6c-último-recurso-que-no-quede-ningún-hueco-vacío)
7. [Casos especiales y huecos que quedan vacíos](#7-casos-especiales-y-huecos-que-quedan-vacíos)
8. [Qué NO hace (y por qué)](#8-qué-no-hace-y-por-qué)
9. [Por qué este método (evidencia)](#9-por-qué-este-método-evidencia)
10. [Auditoría: cómo leer el output](#10-auditoría-cómo-leer-el-output)
11. [Configuración](#11-configuración)
12. [Comandos](#12-comandos)
13. [Garantías y comprobaciones](#13-garantías-y-comprobaciones)
14. [Limitaciones conocidas](#14-limitaciones-conocidas)

---

## 1. Qué problema resuelve

Cada día tenemos VWAPs propios solo para **parte** de los contratos de cada producto (por ejemplo DE
Base: M+1, Q+1 y Cal+1, pero no M+2 ni Q+3). El objetivo es la curva **completa** de cada día:

| Tipo | Contratos objetivo |
|---|---|
| Día | D+1 … D+3 |
| Fin de semana | WE … WE+3 |
| Semana | BOW, W+1 … W+4 |
| Mes | BOM, M+1 … M+10 |
| Quarter | Q+1 … Q+8 |
| Season | Sum/Win +1 … +3 |
| Año | Cal+1 … Cal+3 |

La fuente para los huecos son los settlements de EEX de ese mercado y perfil.

## 2. La idea

Un VWAP propio y el settlement de EEX del mismo contrato casi nunca coinciden. El VWAP se forma con
operaciones durante el día; el settlement es el precio de cierre. Hay ruido de pocas operaciones, y
con `offset_days = -1` el EEX es del día anterior. La diferencia **VWAP − EEX** es la *desviación*.

Observación clave: las desviaciones de contratos **cercanos** se parecen. Si hoy el M+1 y el M+3 se han
negociado ~1 €/MWh por encima de EEX, lo más probable es que el M+2 también esté por encima. Un Cal+2
se parece mucho menos a un mes delantero.

Así que cada hueco se rellena con:

```
precio = EEX del hueco + (parte de la desviación de los VWAPs cercanos)
```

¿Qué parte? Depende de **cuán cerca** están los VWAPs y de **cuántos** hay. Eso lo deciden dos
parámetros.

## 3. La fórmula

Para un contrato objetivo **t** sin VWAP propio, con los VWAPs del día **i = 1…n**:

```
desviación_i = VWAP_i − EEX_i                                   (€/MWh)

x            = ln(1 + días hasta la mitad de la entrega)        (posición en la curva)
distancia_i  = | x_i − x_t |

w_i          = exp( − distancia_i / tau_log )                   (cercanía: 1 = pegado, → 0 = lejos)
               × (volumen_i / volumen mediano del día) ^ volume_weight   (más volumen, menos ruido)

local        = Σ w_i · desviación_i  /  Σ w_i                   (desviación media ponderada)
confianza λ  = Σ w_i  /  ( Σ w_i + shrink_k )                   (entre 0 y 1)

precio_t     = EEX_t + λ · local
```

- **tau_log = 0.7:** el alcance a lo largo de la curva.
- **shrink_k = 1.0:** cuánta evidencia hace falta para fiarse.
- **volume_weight = 0.5:** un VWAP con 4 veces el volumen mediano del día pesa el doble, porque un VWAP
  de pocas operaciones tiene más ruido. Sin volumen conocido, factor 1.
- Sin ninguna información propia (ni VWAPs hoy ni en la historia) **no se publica el precio oficial de EEX tal cual**:
  se publica una EWMA corta de EEX (sección 6c).

### Por qué la posición se mide en logaritmo de días

En la parte delantera, una semana de diferencia cambia mucho las cosas (D+1 frente a D+8). En la parte
trasera, un año casi no (Cal+2 frente a Cal+3). El logaritmo refleja eso: la distancia entre dos
contratos depende de la **proporción** entre sus plazos, no de los días absolutos.

| Contrato (día 30/09) | Días a la mitad de la entrega | x | Distancia |
|---|---|---|---|
| M+1 (oct) | 16.5 | 2.86 | — |
| M+2 (nov) | 47 | 3.87 | 1.01 de M+1 |
| M+3 (dic) | 77.5 | 4.36 | 0.49 de M+2 |
| Q+2 (ene–mar 27) | 138 | 4.93 | 0.57 de M+3 |
| Cal+1 (2027) | 275 | 5.62 | 1.26 de M+3 |

### Por qué es aditivo (en €) y no un ratio

- **No hay problema con precios cero o negativos.** Días y fines de semana pueden ser negativos. Un
  ratio VWAP/EEX explota cerca de cero; una diferencia en € no.
- **Es más fácil de explicar:** "+1.15 €/MWh" en lugar de "×1.0097".
- **Probado:** en las pruebas, el ratio y el modo "auto" (elegir ratio o aditivo según el caso) no
  mejoran al aditivo de forma apreciable (sección 9).

## 4. Paso a paso

Para cada día de referencia **T**, cada producto activo (`use = fill` en el mapeo) y cada contrato
objetivo:

0. **¿Se genera este tenor?** Solo si EEX lo ofrece en ese mercado: si lo ha cotizado (directo o
   construido por horas) en alguna de sus últimas 20 publicaciones. Si EEX no ofrece M+8 en NL, M+8 no
   forma parte de la curva de NL (se anota en el log). Un VWAP propio de ese tenor sí se publica.
1. **Etiqueta a fechas de entrega.** `M+2`, `WE+1`, `Q+3`… se convierten en un periodo absoluto
   (inicio y fin de entrega) respecto a **T**. Nuestros VWAPs y EEX se cruzan por periodo, nunca por
   etiqueta. Así se evitan errores cuando EEX cambia de nombre o cascadea contratos.
2. **Publicación de EEX permitida.** La última publicación con fecha ≤ `T + offset_days`:
   - con `0`, la de hoy (o la última anterior si hoy no hay);
   - con `-1`, nunca la de hoy.

   Si la publicación es antigua, se avisa en el log.
3. **Precio EEX de cada contrato.** Si EEX lo cotiza, directo (`eex_method = exact`). Si no, se
   construye ponderando por **horas** desde contratos que lo componen: meses → quarter, quarters →
   season o año, días restantes → BOM (`strip` / `residual`). Si no se puede construir, ese contrato no
   tiene EEX (ver sección 7).
4. **Nuestros VWAPs del día.**
   - Si hay varias filas para el mismo periodo (alias como D+1 y BOM el último día de mes, o
     duplicados), se agregan ponderando por volumen.
   - Si el contrato tiene VWAP, **se publica tal cual** (`source = own`).
5. **Qué VWAPs pueden mover a otros (anclas).** Un VWAP es ancla si:
   - tiene volumen ≥ `min_volume`. Si hay un mínimo (> 0) y el volumen es desconocido, no puede
     demostrar que lo cumple y no mueve a otros (se publica igual);
   - EEX tiene precio para su mismo contrato (si no, no se puede medir su desviación);
   - si `max_anchor_dev > 0`, no se aleja de EEX más de esa fracción.

   Los que no cumplen se siguen publicando, pero no mueven a nadie (se anota en el log y en `flag`).
6. **Rellenar cada hueco** con la fórmula de la sección 3, usando **todas** las anclas del día del
   mismo producto. No se distingue tipo de contrato: un mes puede mover un quarter y viceversa. La
   cercanía ya decide cuánto.
7. **Arbitraje (opcional, apagado por defecto).** Si EEX no tiene precio para un hueco y
   `layers.arbitrage = true`, se intenta reconstruir desde contratos ya rellenados (por ejemplo un
   quarter desde sus tres meses).
8. **Lo que queda vacío** se marca `source = missing` con su motivo en `missing_reason`.
9. **Chequeo de consistencia** (solo informe, no modifica precios): Cal frente a la media de sus Q, Q
   frente a la de sus meses, etc. → `consistency_report.csv`.

## 5. Ejemplo numérico completo

**Día 30/09/2026, DE Base, `offset_days = 0`.** Tenemos dos VWAPs propios:

| Contrato | VWAP | EEX | Desviación |
|---|---|---|---|
| M+1 (oct) | 105.00 | 104.00 | **+1.00** |
| M+3 (dic) | 121.20 | 120.00 | **+1.20** |

**Hueco M+2 (nov), EEX = 118.00.**

| Ancla | Distancia | w = exp(−d / 0.7) | Peso relativo |
|---|---|---|---|
| M+3 | 0.492 | 0.4952 | 68 % |
| M+1 | 1.009 | 0.2366 | 32 % |
| **Σ w** | | **0.7318** | |

- local = (0.4952·1.20 + 0.2366·1.00) / 0.7318 = **+1.135**
- λ = 0.7318 / (0.7318 + 1) = **0.423**
- precio = 118.00 + 0.423 · 1.135 = **118.48**

Columna `explanation` del output:
`EEX 118.00 + 0.42 x +1.14 [M+3 +1.20 (68%), M+1 +1.00 (32%)] = 118.48`

**Mismo día, otros huecos:**

| Hueco | EEX | λ | Ajuste | Precio | Lectura |
|---|---|---|---|---|---|
| M+2 | 118.00 | 0.42 | +0.48 | 118.48 | Entre dos VWAPs cercanos: se traslada ~40 % de su desviación |
| Q+2 | 95.00 | 0.33 | +0.39 | 95.39 | Cerca de M+3 solamente |
| Cal+1 | 90.00 | 0.16 | +0.18 | 90.18 | Lejos de todo: casi EEX |

**Sin ningún VWAP ese día:** si los hubo en los últimos días, manda la historia (sección 6b). Si no
hay ninguna información propia, se publica una EWMA corta de EEX, nunca el precio oficial tal cual
(sección 6c).

**Con `offset_days = -1`:** exactamente igual, pero tanto el EEX del hueco como el EEX con el que se
miden las desviaciones son los de la publicación del 29/09. Las dos cosas siempre salen de la misma
publicación.

## 6. Cómo se comporta

**Confianza λ según la distancia, con una sola ancla** (tau_log = 0.7, shrink_k = 1):

| Distancia al VWAP | Ejemplo aproximado | w | λ (se traslada…) |
|---|---|---|---|
| 0.1 | Q+1 frente a su mes central | 0.87 | 46 % de la desviación |
| 0.25 | M+3 frente a M+4 | 0.70 | 41 % |
| 0.5 | M+2 frente a M+3 | 0.49 | 33 % |
| 1.0 | M+1 frente a M+2 | 0.24 | 19 % |
| 2.0 | M+1 frente a Q+2 | 0.06 | 5 % |

- **Más VWAPs cercanos → más confianza.** Con un solo VWAP pegado se traslada como mucho ~50 % de su
  desviación; con tres pegados, ~75 %. Un VWAP aislado puede ser ruido (pocas operaciones), varios
  coincidiendo no.
- **`tau_log` más grande** → VWAPs lejanos cuentan más (más alcance).
- **`shrink_k` más grande** → hace falta más evidencia; se queda más cerca de EEX.
- Los valores por defecto salen de las pruebas (sección 9). Con datos reales se pueden recalibrar con
  `python run.py tune --tau-logs 0.5,0.7,1 --shrink-ks 0.5,1,2` (sección 12).

## 6b. Capa de historia (opcional, activada con 10 sesiones)

**Qué resuelve:** un día sin VWAPs de un producto, o con VWAPs solo en una parte de la curva, el método
sin historia vuelve a EEX en los huecos lejanos. Si tus VWAPs suelen desviarse de EEX de forma
**persistente** (por ejemplo, +0.5 € varios días seguidos en los meses), esa información se perdería.

**Idea:** el trozo de confianza que hoy no cubren tus VWAPs (la `shrink_k`) se reparte entre "lo que
dijeron tus VWAPs sobre **ese mismo contrato** en las últimas N sesiones", en lugar de dárselo a cero.

```
para cada día pasado d (últimas history_days sesiones hábiles con VWAPs):
    local_d, W_d = la misma cuenta "local" para el MISMO contrato de entrega,
                   con los VWAPs de ese día y el EEX permitido ese día

hist   = Σ W_d · local_d  /  ( Σ W_d + shrink_k )          (media prudente)

precio = EEX + ( W · local_hoy  +  shrink_k · hist ) / ( W + shrink_k )
       = EEX + trust · local_hoy  +  hist_share · hist        con trust + hist_share = 1
```

- Sin historia disponible, `hist` no se usa y la fórmula es exactamente la de la sección 3.
- **No hay estado guardado.** La historia se recalcula siempre desde tu fichero de VWAPs y desde EEX,
  nunca desde salidas anteriores. Por eso `daily` sigue siendo igual que `refill`: comprobado en los
  54 días con historia de 10 sesiones y `offset_days = -1`, con 0 diferencias.
- Un VWAP pasado del **mismo** contrato cuenta con peso máximo: es información legítima del pasado.
- La ventana son **sesiones hábiles** (lunes a viernes). Tras un parón largo sin datos, la historia
  antigua no entra.

**Ejemplo (30/09, DE Base, hueco M+2 = nov, EEX 118.00):**

| | Datos | Resultado |
|---|---|---|
| Hoy | M+1 +1.00, M+3 +1.20 → local +1.14, W = 0.73 | trust = 0.42 |
| 28/09 | VWAP nov +0.60 (W = 1) | |
| 29/09 | VWAP nov +0.80 (W = 1) | hist = (0.60 + 0.80) / (2 + 1) = **+0.47**, hist_share = 0.58 |
| **Precio** | 118.00 + 0.42 · 1.14 + 0.58 · 0.47 | **118.75** (sin historia: 118.48) |

`explanation`:
`EEX 118.00 + 0.42 x +1.14 [M+3 +1.20 (68%), M+1 +1.00 (32%)] + 0.58 x +0.47 [history 2 days] = 118.75`

Sin ningún VWAP hoy: `EEX 118.00 + 1.00 x +0.47 [history 2 days] = 118.47`, con `source = eex_history`.

**Evidencia** (error medio en €/MWh al rellenar huecos):

| Prueba | Sin historia | 5 sesiones | **10 sesiones** | 20 sesiones |
|---|---|---|---|---|
| Banco, mercado real, offset 0 (ruido 1 %) | **1.385** | 1.427 | 1.398 | 1.397 |
| Banco, mercado real, offset −1 | **1.414** | 1.439 | 1.424 | 1.421 |
| Banco, desviación persistente, offset 0 | 0.694 | 0.549 | **0.580** | 0.619 |
| Banco, desviación persistente, offset −1 | 2.720 | 2.760 | 2.734 | 2.736 |
| Programa completo, sintético additive, offset 0 | 0.566 | 0.545 | **0.544** | 0.553 |
| Programa completo, sintético additive, offset −1 | 2.747 | 2.756 | **2.712** | 2.731 |
| Programa completo, sintético ratio, offset 0 | 0.547 | 0.536 | **0.534** | 0.538 |
| Programa completo, sintético ratio, offset −1 | 2.715 | 2.728 | **2.685** | 2.704 |

Lectura:
- Si las desviaciones **no se repiten** (mercado real simulado), la historia cuesta menos de un 1 %.
- Si **se repiten**, ayuda entre un 1 % y un 16 %.
- **10 sesiones** es el valor más equilibrado en todas las pruebas, y es el que viene activado.
- Con tus datos reales, `python run.py tune --history-days-list 0,5,10,20` dice cuál conviene.

## 6c. Reglas de cobertura y último recurso

**Qué se genera.** La curva de cada producto son sus tenors objetivo **que EEX ofrece en ese mercado**
(`[targets] only_eex_offered = true`). Un tenor se considera ofrecido si EEX lo ha cotizado, directo o
construido por horas, en alguna de sus últimas `offered_lookback` = 20 publicaciones. En la práctica:
- si EEX no ofrece M+8 en NL o WE+3 en AT, esa celda **no existe** en el output; el log dice qué
  tenors se omiten y cuántos días;
- si EEX lo ofrece normalmente pero falta en la publicación de hoy, es un hueco puntual y se rellena
  con el escalón 1.

**Nunca se publica el precio oficial de EEX tal cual.** Con información propia (VWAPs de hoy o de la
historia) el precio ya es una transformación: EEX más un ajuste. Sin ninguna información propia, se
publica una **EWMA corta** del precio de referencia: últimas `no_info_window` = 2 publicaciones,
semivida 0.5, es decir 80 % hoy y 20 % el día anterior (`source = eex_smoothed`).

Es lo mismo que pedía el EWMA del original, pero corto. El de 5 publicaciones del original es el peor
en todas las pruebas, porque va con retraso. Con offset 0, la EWMA corta incluso mejora a EEX crudo,
porque un VWAP se forma durante el día, entre el cierre de ayer y el de hoy:

| Días sin VWAPs propios (banco, 8 países) | EEX crudo | **EWMA 2 publ., semivida 0.5** | EWMA 5 publ. (original) |
|---|---|---|---|
| Mercado real, offset 0 | 1.90 | **1.14** | 1.86 |
| Mercado real, offset −1 | **1.90** | 2.13 | 3.98 |
| Desviación persistente, offset 0 | **0.93** | 1.22 | 3.28 |
| Desviación persistente, offset −1 | **4.07** | 4.19 | 5.37 |

Excepción inevitable: el primer día del histórico de un mercado solo hay una publicación y la EWMA es
igual a ella. En el histórico de prueba son 102 celdas de 10 354 estimadas.

### Escalón 1: EEX no cotiza hoy un contrato que sí ofrece → proxy desde EEX (`source = eex_proxy`)

Por ejemplo WE+2/WE+3, Sum+3/Win+3, meses lejanos en mercados pequeños o Q+8. Se reconstruye desde
EEX con la misma idea del EWMA del original, pero aplicada a la **forma**, que sí se puede observar:

```
padre  = precio EEX (ponderado por horas) del contrato más pequeño que contiene al hueco
         semana para días, fines de semana y BOW · mes para semanas y BOM
         quarter para meses · año para quarters y seasons

contratos cortos (día, fin de semana, semana, BOW, BOM):
    proxy = padre + EWMA( hermano − su padre )        spread en €
contratos largos (quarter, season):
    proxy = padre × EWMA( hermano / su padre )        ratio (el padre es un año, nunca cerca de 0)
meses:
    proxy = quarter × ( mes / quarter en un mercado vecino que sí lo cotiza )
    o el quarter tal cual si no hay vecino
```

- **Hermanos:** contratos del mismo tipo y en la misma posición estacional que sí cotizan (otros fines
  de semana; el mismo quarter de otro año; la misma season de otro año). Se promedian los 2 más
  cercanos en cada publicación.
- **EWMA:** últimas `window` = 20 publicaciones permitidas, con `halflife` = 8.
- **Ejemplo:** WE+2 = semana W+2 (144.86) + EWMA del spread fin de semana − semana (−15.66) =
  **129.20**.
  `explanation`: `EEX proxy 129.20 (no own VWAP nearby) = 129.20  [proxy: week 144.86 -15.66 EWMA sibling spread]`
- Sobre el proxy se aplica igual el ajuste de tus VWAPs y de la historia.
- **Columnas:** `eex_proxy` (precio), `eex_proxy_rule` (regla) y `eex_proxy_detail` (JSON con el
  padre, la forma, el vecino y el número de publicaciones).

**Precisión medida** con EEX real de 8 mercados, ocultando contratos que sí cotizan y
reconstruyéndolos (`research/fallback_test2.py`). Error medio en €/MWh:

| Contrato | Solo padre | EWMA del original sobre la forma (spread, 5 publicaciones) | **Regla elegida** |
|---|---|---|---|
| Season | 16.8 | 4.6 | **3.4** (ratio, 20 publicaciones) |
| Quarter | 20.7 | 7.9 | **5.6** (ratio, 20) |
| Mes | 9.8 | 12.7 | **6.1** (forma del vecino) |
| Fin de semana | 32.2 | 17.3 | **14.8** (spread, 20) |

Son errores mayores que los del método normal (0.5–2.5 €/MWh). Por eso estos precios llevan
**confianza baja** (0.2 + 0.3·trust) y van marcados. Son mucho mejores que dejar el hueco vacío o
copiar el padre.

### Escalón 2 (solo con `only_eex_offered = false`): el mercado no tiene ninguna publicación EEX ese día → curva vecina (`source = eex_donor`)

Por ejemplo, días anteriores al inicio del histórico de EEX de ese mercado:
- Se usa la curva EEX del primer mercado vecino con publicación (`donors`, por defecto DE, FR, AT)
  como forma.
- **Las desviaciones de tus VWAPs se miden contra esa misma curva**, así que el nivel lo siguen
  poniendo tus VWAPs del mercado.
- No se mezcla con la historia, que está medida contra el EEX propio.
- La columna `eex_reference` dice qué curva se usó. Confianza 0.15 + 0.3·trust.

### Lo único que queda vacío

| Motivo | Por qué |
|---|---|
| `zero_delivery_hours` | El contrato no tiene horas de entrega (p. ej. BOW Peak que solo cubre fin de semana): no existe precio posible |
| `no_eex_publication` | Solo con `only_eex_offered = false`: ninguna publicación EEX permitida ni del mercado ni de ningún vecino |

En el histórico de prueba (55 días, 8 curvas, 14 920 celdas de tenors ofrecidos) solo quedan vacías
las celdas sin horas de entrega (0.3 %).

## 7. Casos especiales y huecos que quedan vacíos

| # | Caso | Resultado | `source` / columna |
|---|---|---|---|
| 1 | Hay VWAP propio del contrato | Se publica tal cual | `own` |
| 2 | Hay VWAPs comparables ese día | EEX + λ · desviación local | `eex_adjusted` |
| 3 | Ningún VWAP comparable ese día, pero sí en las últimas sesiones | EEX + historia (6b) | `eex_history` |
| 3b | Ningún VWAP comparable ni hoy ni en la ventana de historia | EWMA corta de EEX, nunca el precio oficial tal cual (6c) | `eex_smoothed` |
| 4 | EEX no cotiza el contrato pero se puede construir por horas | Igual que 2/3 sobre el precio construido | `eex_method = strip/residual` |
| 5 | VWAP sin EEX del mismo contrato | Se publica, no mueve a otros | `own` |
| 6 | VWAP con poco volumen (`min_volume`) | Se publica, no mueve a otros | `own` + `flag = anchor_excluded` |
| 7 | Hoy no hay publicación EEX | Se usa la última permitida y se avisa | `eex_asof` < fecha; confianza × 0.9 |
| 8 | **EEX no cotiza el contrato ni se puede construir** | Proxy desde EEX (6c) | `eex_proxy` (vacío solo con `[fallback] enabled = false`) |
| 9 | **El mercado no tiene publicación EEX** (antes de su histórico) | Curva de un mercado vecino (6c) | `eex_donor`; vacío (`no_eex_publication`) solo si ningún vecino tiene datos |
| 10 | Contrato sin horas de entrega (p. ej. BOW Peak en fin de semana) | Vacío | `missing`, `missing_reason = zero_delivery_hours` |

### ¿Por qué no basta con copiar el contrato que lo contiene?

Usar el padre tal cual (el mes ≈ su quarter, el fin de semana ≈ su semana) falla mucho: 8.8, 20.7 y
32.2 €/MWh. Por eso el proxy de la sección 6c le añade la **forma** de los contratos hermanos (EWMA) o
la de un mercado vecino, y baja a 3–15 €/MWh.

## 8. Qué NO hace (y por qué)

| Técnica del método original | Aquí | Motivo |
|---|---|---|
| Ratio VWAP/EEX y modo `auto` | ❌ | No mejora de forma apreciable (< 2 %); problemas con ceros/negativos |
| Histórico con estado (EWMA acumulado, warmup, caducidad) | ➖ sustituido | Por una historia **sin estado** y auditable (6b): media de las últimas N sesiones, recalculada desde el input |
| Correlaciones y ayuda entre productos | ❌ | Complejidad alta y difícil de explicar |
| Shape (ajuste de forma posterior) | ❌ | Retirado por decisión de negocio |
| Suavizado EWMA de EEX (método de spreads) | ❌ | Va con retraso: es lo peor con `offset_days = -1` |
| Pesos distintos por tipo de contrato | ❌ | La distancia en la curva ya lo recoge; un parámetro menos |
| Fallback EWMA del precio del mismo contrato | ➖ sustituido | Por un EWMA de la **forma** (hermano frente a su padre), que también sirve para contratos que EEX nunca ha cotizado (6c) |

Resultado: **un solo cálculo, tres parámetros (`tau_log`, `shrink_k`, `history_days`), sin estado entre días.** Ejecutar `daily` de un día da
exactamente lo mismo que ese día dentro de un `refill`.

## 9. Por qué este método (evidencia)

Los VWAPs reales no están disponibles fuera del trabajo, así que se construyó un banco de pruebas con
**dinámica real de mercado**:

- Datos: curvas de EEX de 8 mercados (DE, FR, NL, AT, BE, CH, ES, IT), ~50 días cada una.
- "VWAPs propios" simulados a partir de movimientos reales:
  - **offset 0:** precio a mitad de camino entre el cierre de ayer y el de hoy, más ruido; la
    referencia es el EEX de hoy.
  - **offset −1:** el mismo VWAP; la referencia es el EEX de ayer.
- Cada día se "negocia" un subconjunto aleatorio de contratos (más en la parte delantera, como en la
  realidad). El resto se rellena y se compara con la verdad.

Error medio en €/MWh, menos es mejor (ruido realista del 1 % en los VWAPs):

| Método | offset 0 | offset −1 |
|---|---|---|
| EEX a pelo | 1.79 | 1.80 |
| Spreads + suavizado de 5 días (VWAPS2) | 1.91 | 3.35 |
| Lógica principal del método original (aditivo/auto) | 1.38–1.41 | 1.43–1.44 |
| **Este método** | **1.37** | **1.41** |

- Se probaron ~60 variantes:
  - ancla más cercana;
  - interpolación;
  - regresión;
  - ratio;
  - auto;
  - movimiento general del día;
  - suavizados.
- Las variantes con "movimiento general del día" ganan ligeramente si los VWAPs casi no tienen ruido,
  pero empeoran con ruido realista y en los Cal.
- Este método es el más estable y el más sencillo.
- Scripts y resultados: carpeta `research/` (`bench.py`, `sweep.py`, `robust.py`, `modes.py`,
  `final.py`, `explain.py`).

La comparación del programa completo frente al VWAPS original, sobre los mismos datos, está en
[ANALISIS_COMPARATIVA.md](ANALISIS_COMPARATIVA.md).

### 9b. Sensibilidad: qué capa rellena cada hueco y en qué país funciona mejor

Banco con dinámica real de EEX en 8 países, VWAPs con un 1 % de ruido y un 25 % de días sin ningún
VWAP (`research/sensitivity.py`). Error medio en €/MWh:

| Capa que rellena el hueco | % de huecos (offset 0 / −1) | Método (offset 0) | EEX crudo (offset 0) |
|---|---|---|---|
| Anclas de hoy (+ historia) | 75 % / 66 % | **1.42** | 1.83 |
| Solo historia | 24 % / 33 % | 1.96 | 1.93 |
| Sin información propia (EWMA corta) | < 1 % | 1.15 | 1.15 |

- **Las anclas de hoy son la capa que más aporta:** un 22 % menos error que EEX.
- **La historia** empata con EEX cuando las desviaciones no persisten (mercado real simulado) y mejora
  un 5–7 % cuando persisten.

| País | Mejora sobre EEX, offset 0 | Mejora sobre EEX, offset −1 | Error del tramo corto (offset 0) |
|---|---|---|---|
| AT | 15 % | 13 % | 3.09 |
| BE | 17 % | 12 % | 2.63 |
| CH | 19 % | 20 % | 2.19 |
| DE | 14 % | 14 % | 3.22 |
| ES | 18 % | 8 % | 2.64 |
| FR | 14 % | 21 % | 3.53 |
| IT | 21 % | 17 % | 2.10 |
| NL | 15 % | 16 % | 3.11 |

- El método mejora a EEX **en los 8 países y con los dos offsets**.
- El tramo corto (días, fines de semana, semanas) es el más difícil en todos; meses, quarters y años
  quedan entre 0.4 y 1.4 €/MWh.

**Calibración de parámetros** (`research/param_study.py`, `research/param_combo.py`):
- Subir `tau_log` de 0.5 a **0.7** mejora entre un 1 % y un 6 % en todos los escenarios. Es el valor
  por defecto actual.
- Separar el alcance del tramo corto y el largo, recortar VWAPs raros o hacer que la historia decaiga
  dan mejoras pequeñas (0–3 %), o solo en un escenario. No se añaden, para no complicar.
- **Pesar por volumen** (`volume_weight = 0.5`, `research/volume_study.py`), cuando el ruido de un
  VWAP depende de su volumen como en la realidad: un 1.7–3.4 % mejor con mercado real y hasta un 12 %
  con desviaciones persistentes. Nunca empeora; si todos los volúmenes son iguales, no cambia nada.
  Se añade.

## 10. Auditoría: cómo leer el output

`output/filled_history.csv` y `output/filled/<fecha>.csv` tienen una fila por (día, producto,
contrato). Columnas del método:

| Columna | Significado |
|---|---|
| `price` | Precio final |
| `source` | `own`, `eex_adjusted`, `eex_history`, `eex`, `eex_proxy`, `eex_donor`, `arbitrage` o `missing` |
| `eex_proxy`, `eex_proxy_rule`, `eex_proxy_detail` | Precio de referencia reconstruido cuando EEX no cotiza el contrato, regla y detalle (6c) |
| `eex_reference` | Vacío = EEX del propio mercado; si no, la curva vecina usada ese día (6c) |
| `eex_settle`, `eex_method`, `eex_asof` | Precio EEX usado, cómo se obtuvo y de qué publicación |
| `anchors` | VWAPs que han influido, del más al menos influyente |
| `local_deviation` | Desviación media ponderada (€/MWh) |
| `trust` | λ, la fracción de esa desviación que se aplica |
| `adjustment` | Precio publicado − referencia usada (EEX, proxy o curva vecina) = trust · local + hist_share · hist. Con EWMA corta (sin información propia), es la diferencia EWMA − EEX del día |
| `hist_deviation`, `hist_share`, `hist_days` | Desviación histórica (prudente), parte de confianza que recibe y días usados |
| `hist_audit` | JSON con cada día pasado: fecha, desviación local de ese día y su peso |
| `explanation` | Una línea legible con el cálculo completo |
| `audit` | JSON con cada VWAP: precio, EEX, desviación, distancia, peso y % de influencia |
| `confidence` | **Puntuación orientativa de 0 a 1 para ordenar o filtrar celdas, no una probabilidad de acierto.** 1 si es propio; 0.4 + 0.5·trust (+ 0.1·hist_share) si se estima; 0.2 + 0.3·trust con proxy; 0.15 + 0.3·trust con curva vecina; × 0.9 si EEX no es del día. La precisión real de cada capa está en las secciones 6c y 9b |
| `missing_reason` | Por qué está vacío (solo en celdas `missing`) |
| `flag` | Avisos: `anchor_excluded`, `zero_delivery_hours`… |

Cualquier precio se puede recalcular a mano con
`eex_settle + trust · local_deviation + hist_share · hist_deviation`, y cada parte con los JSON
`audit` y `hist_audit`.

Además:
- `output/enriched_history.csv` es tu fichero original intacto, con las filas rellenadas añadidas.
- `consistency_report.csv` contiene el chequeo M/Q/Cal.
- Los logs están en `output/_logs/` (avisos de EEX antiguo, anclas excluidas…).

## 11. Configuración

### Resumen: todos los parámetros

| # | Parámetro | Defecto | Qué controla |
|---|---|---|---|
| 1 | `[method] tau_log` | 0.7 | **Alcance:** hasta dónde en la curva influye un VWAP (mínimo 0.05) |
| 2 | `[method] shrink_k` | 1.0 | **Prudencia:** cuánta evidencia hace falta para fiarse de los VWAPs de hoy |
| 3 | `[method] history_days` | 10 | **Memoria:** sesiones de historia (0 = sin historia) |
| 3b | `[method] volume_weight` | 0.5 | **Fiabilidad:** un VWAP con más volumen pesa más (4× volumen → 2× peso; 0 = apagado) |
| 4 | `[method] min_volume` | 0 | Volumen mínimo para que un VWAP mueva a otros; con mínimo > 0, un volumen desconocido no lo cumple |
| 5 | `[method] max_anchor_dev` | 0 | Si > 0, un VWAP muy alejado de EEX no mueve a otros |
| 6 | `[targets] only_eex_offered` / `offered_lookback` | true / 20 | Generar solo tenors que EEX ofrece en ese mercado |
| 7 | `[fallback] enabled`, `window`, `halflife`, `donors` | true, 20, 8, DE/FR/AT | Proxy para contratos que faltan hoy en EEX |
| 8 | `[fallback] no_info_window` / `no_info_halflife` | 2 / 0.5 | EWMA corta cuando no hay información propia |
| — | `[eex] offset_days` | 0 | Publicación del día (0) o hasta ayer (−1). Es operativo, no del método |

- **El método en sí tiene 4 parámetros** (1–3b).
- **2 son filtros opcionales** (4–5, apagados por defecto).
- **El resto son reglas de cobertura** (6–8), con valores medidos y documentados.


`config.toml` (mismas secciones que VWAPS salvo las del método):

```toml
[method]
tau_log        = 0.7   # alcance a lo largo de la curva
shrink_k       = 1.0   # evidencia necesaria para fiarse de los VWAPs cercanos
volume_weight  = 0.5   # más volumen, más peso (4× volumen → 2× peso); 0 = apagado
history_days   = 10    # 0 = sin historia; N = últimas N sesiones (sección 6b)
min_volume     = 0     # volumen mínimo para que un VWAP mueva a otros
max_anchor_dev = 0     # si > 0, un VWAP más lejos de EEX que esta fracción no mueve a otros

[layers]
arbitrage = false      # reconstruir huecos sin EEX desde contratos ya rellenados

[targets]
only_eex_offered = true   # solo tenors que EEX ofrece en ese mercado
offered_lookback = 20     # publicaciones revisadas para decidir si se ofrece

[fallback]               # último recurso (6c)
enabled  = true
no_info_window   = 2   # EWMA corta sin información propia (nunca EEX tal cual)
no_info_halflife = 0.5
window   = 20          # publicaciones del EWMA de la forma de los hermanos
halflife = 8.0         # semivida del EWMA, en publicaciones
donors   = ["DE", "FR", "AT"]   # mercados vecinos para meses y para días sin EEX propio

[eex]
offset_days     = 0    # 0 = publicación del día; -1 = hasta ayer
max_stale_days  = 0    # antigüedad máxima (0 = sin límite)
warn_stale_days = 3
```

El resto (`[paths]`, `[targets]`, `[conventions]`, `[timezones]`, `[run]`, `[vwap_columns]`) es
idéntico a VWAPS.

**Mapeo Excel/CSV con on/off:** `mappings/products.csv` o `.xlsx`, una fila por producto con
`use = fill` / `off`. Con `python run.py mapping --with-parameters` se añaden columnas por producto
(`tau_log`, `shrink_k`, `history_days`, `min_volume`, `max_anchor_dev`, `layer_arbitrage`, `tenors`). Se aplican con
`[run] configuration_mode = "individual"`; una celda vacía hereda el TOML.

**Por ejecución:** `--tau-log`, `--shrink-k`, `--history-days`, `--eex-offset-days`, `--configuration-mode`.

## 12. Comandos

```
python run.py mapping                       # crear/revisar el mapeo (on/off por producto)
python run.py refill --from 2026-08-10      # histórico completo
python run.py daily                         # el día de hoy (o --date AAAA-MM-DD)
python run.py catchup                       # rellenar los días pendientes
python run.py status                        # estado de las salidas
python run.py backtest                      # oculta cada VWAP y lo predice (eex, neighbours, pipeline)
python run.py tune --tau-logs 0.5,0.7,1 --shrink-ks 0.5,1,2 --history-days-list 0,5,10,20   # calibrar
python run.py detect-conventions            # comprobar convenciones D+n / WE+n
python compare_methods.py --vwap "ruta/vwaps.xlsx"           # comparar con el VWAPS original
python research/check_daily.py --offset 0 --history-days 10   # daily == refill y huecos justificados
```

## 13. Garantías y comprobaciones

- **Daily = refill.** Comprobado en los 54 días del histórico, con offset 0 y −1, y también con
  historia de 10 sesiones: 17 264 celdas, 0 diferencias (`research/check_daily.py`).
- **Ningún hueco con precio EEX se queda vacío.** Toda celda vacía tiene `missing_reason`
  (comprobado).
- **Nada posterior al corte de EEX se usa** (`offset_days`). Tests incluidos.
- **Tus datos originales nunca se modifican.** El enriquecido conserva todas las filas y columnas.
- **Calidad:** 480 tests pasan (`uv run pytest`), 20 de ellos específicos del método y de la
  historia (`tests/test_neighbours_method.py`). `ruff check` y `ruff format --check` limpios.

## 14. Limitaciones conocidas

- **Parte corta de la curva (días, fines de semana, semanas).** Es la más difícil con cualquier
  método: ~2.5 €/MWh de error frente a ~3.0 de EEX a pelo. Sus precios cambian mucho de un día a otro.
- **Validado con VWAPs simulados** a partir de dinámica real de EEX. La validación definitiva es con
  tu fichero real: `python run.py backtest` y `python compare_methods.py --vwap ...`.
- **Historia.** Viene activada con 10 sesiones porque cuesta poco si no ayuda y ayuda si las
  desviaciones persisten. Con datos reales conviene confirmarlo con
  `python run.py tune --history-days-list 0,5,10,20`.
- **Contratos que EEX no cotiza** (WE+2/WE+3, seasons lejanas, meses lejanos en algunos mercados) se
  rellenan con el proxy de la sección 6c. Es menos preciso (3–15 €/MWh) y va marcado con confianza
  baja.
