#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Crosswalk empírico de códigos comunales para la serie REM 2009-2026.

La codificación territorial cambió durante la serie (p. ej., creación de la
Región de Ñuble en 2018: comunas 084xx pasaron a 16xxx). Este script deriva
el mapeo código_antiguo → código_vigente usando IdEstablecimiento como puente:
cada establecimiento se asigna a su comuna en el año más reciente en que
aparece; toda comuna cuyo código no exista en la codificación vigente se mapea
a la comuna vigente mayoritaria de sus establecimientos.

Salida: docs/crosswalk_comunas.json  {codigo_antiguo: codigo_vigente}
"""
import json
from collections import Counter
from pathlib import Path

import polars as pl

RAIZ = Path(__file__).parent
DATA = RAIZ / "data"
SALIDA = RAIZ / "docs" / "crosswalk_comunas.json"

# comunas de la codificación vigente según el shapefile oficial
import geopandas as gpd

shp = gpd.read_file(DATA / "comunas.shp")
vigentes = {f"{c:05d}" for c in shp["cod_comuna"].astype(int)}
print(f"comunas vigentes en shapefile: {len(vigentes)}")

# (año, establecimiento, comuna) distintos por año — escaneo liviano
pares = []
for anio in range(2009, 2027):
    f = DATA / f"SerieA_{anio}.csv"
    if not f.exists():
        continue
    df = (pl.scan_csv(f, separator=";", infer_schema=False)
            .select(["IdEstablecimiento", "IdComuna"])
            .filter(pl.col("IdComuna").is_not_null() & (pl.col("IdComuna") != ""))
            .unique()
            .with_columns(pl.lit(anio).alias("Ano"))
            .collect(engine="streaming"))
    pares.append(df)
    print(f"{anio}: {df.height} pares establecimiento-comuna")

todos = pl.concat(pares)

# comuna vigente de cada establecimiento = la de su año más reciente
ultimo = (todos.sort("Ano")
               .group_by("IdEstablecimiento")
               .agg(pl.col("IdComuna").last().alias("comuna_actual")))

# para cada código de comuna NO vigente: comuna vigente mayoritaria de sus establecimientos
no_vigentes = sorted(set(todos["IdComuna"].to_list()) - vigentes)
print(f"\ncódigos no vigentes en los datos: {len(no_vigentes)}")

con_actual = todos.join(ultimo, on="IdEstablecimiento")
crosswalk = {}
for cod in no_vigentes:
    destinos = (con_actual.filter(pl.col("IdComuna") == cod)["comuna_actual"]
                .to_list())
    destinos_vigentes = [d for d in destinos if d in vigentes]
    if not destinos_vigentes:
        print(f"  [aviso] {cod}: sin destino vigente (establecimientos: {len(destinos)})")
        continue
    winner, n = Counter(destinos_vigentes).most_common(1)[0]
    pureza = n / len(destinos_vigentes)
    crosswalk[cod] = winner
    marca = "" if pureza >= 0.9 else f"  [AVISO pureza {pureza:.0%}]"
    print(f"  {cod} -> {winner} (n={n}, pureza={pureza:.0%}){marca}")

SALIDA.write_text(json.dumps(crosswalk, indent=1, ensure_ascii=False, sort_keys=True))
print(f"\ncrosswalk -> {SALIDA} ({len(crosswalk)} códigos)")
