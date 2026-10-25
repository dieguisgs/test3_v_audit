"""Analyse the outputs written by compare_methods.py --synthetic.

For each case (synthetic_ratio / synthetic_additive x offset 0 / -1) it compares the
filled curves of VWAPS (original) and VWAPS_auditable against the synthetic truth:
coverage, error by curve section, error by how each method filled the cell,
paired win share, and one example day.

    uv run python research/analyze_comparison.py [--out comparison]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
KEYS = ["reference_date", "product", "region", "unit", "tenor"]
GROUP = {
    "Day": "corto",
    "Weekend": "corto",
    "BOW": "corto",
    "Week": "corto",
    "BOM": "mes",
    "Month": "mes",
    "Quarter": "quarter",
    "Season": "largo",
    "Year": "largo",
}


def read(path: Path) -> pd.DataFrame:
    f = pd.read_csv(path, encoding="utf-8-sig", keep_default_na=False, na_values=[""], low_memory=False)
    for c in ("region", "unit"):
        if c in f:
            f[c] = f[c].fillna("").astype(str)
    return f


def table(frame: pd.DataFrame) -> str:
    def cell(v):
        if isinstance(v, float):
            return "" if v != v else f"{v:,.3f}" if abs(v) < 100 else f"{v:,.0f}"
        return str(v)

    head = "| " + " | ".join(map(str, frame.columns)) + " |"
    rule = "|" + "---|" * len(frame.columns)
    return "\n".join([head, rule] + ["| " + " | ".join(cell(v) for v in r) + " |" for r in frame.itertuples(index=False)])


def case_frames(out: Path, case: str, truth_path: Path):
    a = read(out / case / "vwaps" / "filled_history.csv")
    b = read(out / case / "auditable" / "filled_history.csv")
    t = read(truth_path).rename(columns={"date": "reference_date"})
    m = (
        a[KEYS + ["kind", "source", "price", "eex_settle"]]
        .rename(columns={"source": "src_vwaps", "price": "vwaps", "eex_settle": "eex_vwaps"})
        .merge(
            b[KEYS + ["source", "price", "eex_settle", "trust", "missing_reason"]].rename(
                columns={"source": "src_aud", "price": "auditable", "eex_settle": "eex_aud"}
            ),
            on=KEYS,
            how="outer",
        )
        .merge(t[KEYS + ["truth"]], on=KEYS, how="left")
    )
    m["group"] = m["kind"].map(GROUP)
    return a, b, m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "comparison")
    a = ap.parse_args()
    out = a.out.resolve()
    lines = []
    summary = []
    for data in ("ratio", "additive"):
        truth = out / f"data_{data}" / "synthetic_truth.csv"
        for offset in (0, -1):
            case = f"synthetic_{data}_offset{offset}"
            if not (out / case / "auditable" / "filled_history.csv").exists():
                continue
            fa, fb, m = case_frames(out, case, truth)
            lines += [f"## {case}", ""]

            # coverage
            cov = (
                pd.concat([fa["source"].value_counts().rename("VWAPS"), fb["source"].value_counts().rename("auditable")], axis=1)
                .fillna(0)
                .astype(int)
            )
            cov.loc["TOTAL celdas"] = cov.sum()
            lines += ["**Celdas por origen**", "", table(cov.reset_index(names="source")), ""]

            # cells both methods filled (excluding own), scored against truth
            est = m[(m["src_vwaps"] != "own") & m["vwaps"].notna() & m["auditable"].notna() & m["truth"].notna()].copy()
            est["err_v"] = (est["vwaps"] - est["truth"]).abs()
            est["err_a"] = (est["auditable"] - est["truth"]).abs()
            est["err_eex"] = (est["eex_aud"] - est["truth"]).abs()
            rows = []
            for g, x in [("TODO", est), *est.groupby("group")]:
                rows.append(
                    {
                        "tramo": g,
                        "celdas": len(x),
                        "EEX a pelo": x["err_eex"].mean(),
                        "VWAPS": x["err_v"].mean(),
                        "auditable": x["err_a"].mean(),
                        "mejora %": 100 * (1 - x["err_a"].mean() / x["err_v"].mean()),
                        "auditable gana": f"{(x['err_a'] < x['err_v']).mean():.0%}",
                    }
                )
            by_group = pd.DataFrame(rows)
            lines += ["**Error medio absoluto frente a la verdad (€/MWh), huecos rellenados por ambos**", "", table(by_group), ""]
            summary.append(
                {
                    "caso": case,
                    "celdas": len(est),
                    "EEX a pelo": est["err_eex"].mean(),
                    "VWAPS": est["err_v"].mean(),
                    "auditable": est["err_a"].mean(),
                    "mejora %": 100 * (1 - est["err_a"].mean() / est["err_v"].mean()),
                    "p95 VWAPS": est["err_v"].quantile(0.95),
                    "p95 auditable": est["err_a"].quantile(0.95),
                }
            )

            # by how VWAPS filled it
            rows = []
            for s, x in est.groupby("src_vwaps"):
                rows.append({"origen en VWAPS": s, "celdas": len(x), "VWAPS": x["err_v"].mean(), "auditable": x["err_a"].mean()})
            lines += ["**Según cómo lo rellenó VWAPS**", "", table(pd.DataFrame(rows)), ""]

            # cells only one method filled
            only_v = m[(m["src_vwaps"] != "missing") & (m["src_aud"] == "missing")]
            only_a = m[(m["src_vwaps"] == "missing") & (m["src_aud"] != "missing")]
            lines += [
                f"Celdas que solo rellena VWAPS: **{len(only_v)}** "
                f"(por origen: {only_v['src_vwaps'].value_counts().to_dict()}; "
                f"motivo del hueco en auditable: {only_v['missing_reason'].value_counts().to_dict()}). "
                f"Celdas que solo rellena auditable: **{len(only_a)}**.",
                "",
            ]
            if len(only_v) and only_v["truth"].notna().any():
                x = only_v[only_v["truth"].notna()]
                lines += [
                    f"Error de VWAPS en esas celdas que solo él rellena: {(x['vwaps'] - x['truth']).abs().mean():.3f} €/MWh (n={len(x)}).",
                    "",
                ]

            # example day
            if data == "additive" and offset == 0:
                day = sorted(m["reference_date"].unique())[len(m["reference_date"].unique()) // 2]
                ex = m[(m["reference_date"] == day) & (m["product"] == "DE_Base load")].copy()
                order = {"corto": 0, "mes": 1, "quarter": 2, "largo": 3}
                ex = ex.assign(o=ex["group"].map(order)).sort_values(["o", "tenor"])
                ex = ex[["tenor", "src_vwaps", "vwaps", "src_aud", "auditable", "trust", "truth"]]
                lines += [f"**Ejemplo: DE Base, {day}**", "", table(ex.round(2)), ""]
    s = pd.DataFrame(summary)
    text = [
        "# Comparativa de outputs: VWAPS (original) vs VWAPS_auditable",
        "",
        "Generado por `research/analyze_comparison.py` a partir de `compare_methods.py --synthetic`.",
        "",
        "## Resumen",
        "",
        table(s),
        "",
        *lines,
    ]
    (out / "ANALISIS_OUTPUT.md").write_text("\n".join(text), encoding="utf-8")
    s.to_csv(out / "analisis_resumen.csv", index=False)
    print("\n".join(text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
