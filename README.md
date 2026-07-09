# Lactancia materna en Chile · Breastfeeding in Chile

## 🇪🇸 Español

Análisis doctoral de la **serie nacional completa 2009–2026** de lactancia materna en
Chile, construida desde los microdatos abiertos de los Resúmenes Estadísticos
Mensuales (REM-A03) del DEIS-MINSAL (~77,5 millones de registros, agregados a un
panel comuna×mes×año armonizado entre 18 años de formularios cambiantes).

**Documentos** (GitHub Pages):

- **[Etapa 4 — Análisis completo, versión en español](https://amarusimonaguerojimenez.github.io/Breastfeeding-Chile/stage4.html)**
- **[Stage 4 — Full analysis, English version](https://amarusimonaguerojimenez.github.io/Breastfeeding-Chile/stage4_en.html)**
- [Índice bilingüe](https://amarusimonaguerojimenez.github.io/Breastfeeding-Chile/)

Contenido: armonización longitudinal validada contra los diccionarios oficiales DEIS;
series de tiempo (STL, quiebres PELT, series interrumpidas COVID-19 con errores
Newey–West); análisis geoespacial comunal (suavizamiento bayesiano empírico, I de
Moran por permutaciones, clústeres LISA con FDR, regresión espacial ML-Lag/ML-Error);
desigualdad territorial (Gini y Theil ponderados); lactancia continuada al año y a
los dos años; retención 1.er→6.º mes; composición de la alimentación; brechas por
sexo, pueblos originarios y migrantes; mapa interactivo. Cada figura tiene botón de
descarga en PNG a 600 dpi y cada tabla en Excel.

## 🇬🇧 English

Doctoral-level analysis of Chile's **complete 2009–2026 national breastfeeding
series**, built from the open microdata of the Monthly Statistical Summaries
(REM-A03) of DEIS-MINSAL (~77.5 million records, aggregated into a
municipality×month×year panel harmonized across 18 years of changing forms).

**Documents** (GitHub Pages):

- **[Stage 4 — Full analysis, English version](https://amarusimonaguerojimenez.github.io/Breastfeeding-Chile/stage4_en.html)**
- **[Etapa 4 — Análisis completo, versión en español](https://amarusimonaguerojimenez.github.io/Breastfeeding-Chile/stage4.html)**
- [Bilingual index](https://amarusimonaguerojimenez.github.io/Breastfeeding-Chile/)

Contents: longitudinal harmonization validated against the official DEIS code
dictionaries; time series (STL, PELT structural breaks, COVID-19 interrupted time
series with Newey–West errors); municipal geospatial analysis (empirical Bayes
smoothing, permutation-based Moran's I, FDR-controlled LISA clusters, ML-Lag/ML-Error
spatial regression); territorial inequality (weighted Gini and Theil); continued
breastfeeding at 1 and 2 years; 1st→6th month retention; feeding composition; gaps by
sex, Indigenous peoples and migrants; interactive map. Every figure has a 600-dpi PNG
download button and every table an Excel download button.

## Pipeline (reproducible)

```bash
python3 download_rem.py            # descarga y normaliza los datos REM del DEIS
                                   # downloads & normalizes DEIS REM open data
python3 etl_lactancia.py           # panel agregado comuna×mes×año (parquet)
python3 crosswalk_comunas.py       # armonización territorial (Ñuble 2018, Marga Marga 2010)
python3 build_mapeo.py <bruto.json>  # mapeo columna→significado por año
quarto render docs/stage4.qmd      # documento en español
quarto render docs/stage4_en.qmd   # English document
```

## Estructura · Structure

| Ruta / Path | Contenido / Contents |
|---|---|
| `docs/stage4.qmd` / `docs/stage4_en.qmd` | Análisis principal (ES/EN) · Main analysis |
| `docs/mapeo_columnas_rem.json` | Mapeo columna→significado por año · Per-year column mapping |
| `docs/crosswalk_comunas.json` | Códigos comunales antiguos→vigentes · Municipality code crosswalk |
| `docs/stage4_recursos*/` | Figuras 600 dpi y tablas Excel · 600-dpi figures & Excel tables |
| `download_rem.py`, `etl_lactancia.py`, `build_mapeo.py`, `crosswalk_comunas.py` | Pipeline de datos · Data pipeline |
| `output_files/stage4*/` | CSV de resultados y mapa interactivo · Result CSVs & interactive map |
| `other_scripts/` | Etapas 1–3 (análisis exploratorios previos, RM 2019–2024) · Legacy stages 1–3 |

**Fuente de datos · Data source**: [DEIS-MINSAL datos abiertos](https://deis.minsal.cl/#datosabiertos) — `SERIE_REM_{año}.zip`, sección de lactancia del REM-A03.
