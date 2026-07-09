#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETL: panel agregado de lactancia materna a partir de los REM Serie A.

Lee data/SerieA_{año}.csv (2009-2026, ya normalizados por download_rem.py),
filtra los códigos de prestación de la sección de lactancia del REM-A03 y
agrega a nivel (Ano, Mes, IdRegion, IdComuna, CodigoPrestacion), conservando
las columnas crudas Col01..Col20 como sumas. La traducción columna→significado
(que varía por año) se aplica aguas abajo con docs/mapeo_columnas_rem.json.

Salida: data/procesados/panel_lactancia.parquet (+ resumen de control de calidad).

Uso: python3 etl_lactancia.py
"""
import json
import sys
from pathlib import Path

import polars as pl

RAIZ = Path(__file__).parent
DATA = RAIZ / "data"
OUT_DIR = DATA / "procesados"
OUT = OUT_DIR / "panel_lactancia.parquet"
QC = OUT_DIR / "panel_lactancia_qc.json"

CODIGOS = ["A0200001", "A0200002", "A0200003",
           "03500359", "03500360", "03600140", "03600150"]
NCOLS = 20
COLS = [f"Col{i:02d}" for i in range(1, NCOLS + 1)]


def procesar_anio(anio: int) -> tuple[pl.DataFrame, dict] | None:
    f = DATA / f"SerieA_{anio}.csv"
    if not f.exists():
        return None
    lf = (
        pl.scan_csv(f, separator=";", infer_schema=False)
        .filter(pl.col("CodigoPrestacion").is_in(CODIGOS))
        .with_columns(
            pl.col("Mes").cast(pl.Int32, strict=False),
            pl.col("Ano").cast(pl.Int32, strict=False),
            pl.col("IdRegion").cast(pl.Int32, strict=False),
            pl.col("IdComuna").str.strip_chars().alias("IdComuna"),
            *[pl.col(c).cast(pl.Float64, strict=False) for c in COLS],
        )
    )
    df = (
        lf.group_by(["Ano", "Mes", "IdRegion", "IdComuna", "CodigoPrestacion"])
        .agg(
            pl.len().alias("n_registros"),
            pl.col("IdEstablecimiento").n_unique().alias("n_establecimientos"),
            *[pl.col(c).sum().alias(c) for c in COLS],
        )
        .collect(engine="streaming")
    )
    qc = {
        "filas_agregadas": df.height,
        "ano_distinto_al_archivo": df.filter(pl.col("Ano") != anio).height,
        "mes_fuera_de_rango": df.filter(~pl.col("Mes").is_between(1, 12)).height,
        "comuna_vacia": df.filter(
            pl.col("IdComuna").is_null() | (pl.col("IdComuna") == "")
        ).height,
        "codigos": sorted(df["CodigoPrestacion"].unique().to_list()),
    }
    # descarta filas con año inconsistente o mes inválido (se reportan en QC)
    df = df.filter(
        (pl.col("Ano") == anio) & pl.col("Mes").is_between(1, 12)
    )
    return df, qc


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    partes, qc_total = [], {}
    for anio in range(2009, 2027):
        res = procesar_anio(anio)
        if res is None:
            continue
        df, qc = res
        partes.append(df)
        qc_total[str(anio)] = qc
        print(f"{anio}: {df.height:,} filas agregadas | QC: {qc}", flush=True)
    panel = pl.concat(partes)
    panel = panel.sort(["Ano", "Mes", "IdComuna", "CodigoPrestacion"])
    panel.write_parquet(OUT, compression="zstd")
    QC.write_text(json.dumps(qc_total, indent=1, ensure_ascii=False))
    print(f"\nPanel: {panel.height:,} filas -> {OUT}")
    print(f"QC -> {QC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
