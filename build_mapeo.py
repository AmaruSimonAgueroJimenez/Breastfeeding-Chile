#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Construye el mapeo armonizado columna→significado de la sección de lactancia
del REM-A03 (2009-2026) a partir de la extracción de los diccionarios DEIS.

Entrada : scratchpad/mapeo_bruto.json (salida del workflow de extracción:
          lista de {anio, archivo, codigos:[{codigo, descripcion,
          columnas:[{col, significado}]}], advertencias})
Salida  : docs/mapeo_columnas_rem.json con, por año y código:
          - edad_cols: {"1m"|"3m"|"6m"|"12m"|"24m": [columnas a sumar]}
          - total_cols, po_cols, mig_cols, diada_cols
          - sexo_cols: {"H"/"M": {edad: [cols]}} (eras con desagregación por sexo)
Valida que 1m/3m/6m/12m existan para A0200001 y A0200002 en todos los años.
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).parent
ENTRADA = Path(sys.argv[1]) if len(sys.argv) > 1 else None
SALIDA = RAIZ / "docs" / "mapeo_columnas_rem.json"

EDADES = {"1": "1m", "3": "3m", "6": "6m", "12": "12m", "24": "24m"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.upper())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


def clasificar(significado: str):
    """Devuelve (clave_semantica, sexo|None). clave: total|po|mig|diada_*|<edad>m|None."""
    t = norm(significado)
    sexo = "H" if "HOMBRE" in t else ("M" if "MUJER" in t else None)
    if "PUEBLOS ORIGINARIOS" in t:
        return "po", None
    if "MIGRANTE" in t:
        return "mig", None
    if "DIADA" in t or "DÍADA" in t:
        if "10 DIAS" in t or "10 D" in t:
            return "diada_0_10", None
        return "diada_11_28", None
    m = re.search(r"DEL\s*(\d+)\s*°?\s*MES", t)
    if m and m.group(1) in EDADES:
        return EDADES[m.group(1)], sexo
    if "TOTAL" in t:
        # 'TOTAL - AMBOS SEXOS' | 'TOTAL - HOMBRES' | 'TOTAL'
        if sexo:
            return "total_sexo", sexo
        return "total", None
    return None, sexo


def main() -> int:
    bruto = json.load(open(ENTRADA, encoding="utf-8"))
    salida, problemas = {}, []
    for r in sorted(bruto, key=lambda x: x["anio"]):
        anio = str(r["anio"])
        salida[anio] = {"_archivo": Path(r["archivo"]).name,
                        "_seccion": r.get("seccion", ""), "codigos": {}}
        for c in r["codigos"]:
            entry = {"descripcion": c["descripcion"], "edad_cols": {},
                     "total_cols": [], "po_cols": [], "mig_cols": [],
                     "diada_cols": {}, "sexo_cols": {"H": {}, "M": {}},
                     "sin_clasificar": []}
            for col in c["columnas"]:
                clave, sexo = clasificar(col["significado"])
                nombre = col["col"]
                if clave in ("1m", "3m", "6m", "12m", "24m"):
                    entry["edad_cols"].setdefault(clave, []).append(nombre)
                    if sexo:
                        entry["sexo_cols"][sexo].setdefault(clave, []).append(nombre)
                elif clave == "total":
                    entry["total_cols"].append(nombre)
                elif clave == "total_sexo":
                    pass  # total por sexo: redundante con suma de edades; no se usa
                elif clave == "po":
                    entry["po_cols"].append(nombre)
                elif clave == "mig":
                    entry["mig_cols"].append(nombre)
                elif clave and clave.startswith("diada"):
                    entry["diada_cols"].setdefault(clave, []).append(nombre)
                else:
                    entry["sin_clasificar"].append(
                        {"col": nombre, "significado": col["significado"]})
            salida[anio]["codigos"][c["codigo"]] = entry

        # validación: edades núcleo presentes para denominador y LME
        for cod in ("A0200001", "A0200002"):
            e = salida[anio]["codigos"].get(cod)
            if e is None:
                problemas.append(f"{anio}: falta código {cod}")
                continue
            faltan = [k for k in ("1m", "3m", "6m", "12m") if not e["edad_cols"].get(k)]
            if faltan:
                problemas.append(f"{anio}/{cod}: faltan edades {faltan}")
            if e["sin_clasificar"]:
                problemas.append(
                    f"{anio}/{cod}: sin clasificar {e['sin_clasificar']}")

    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text(json.dumps(salida, indent=1, ensure_ascii=False))
    print(f"mapeo -> {SALIDA} ({len(salida)} años)")
    if problemas:
        print("\nPROBLEMAS DE VALIDACIÓN:")
        for p in problemas:
            print(" -", p)
        return 1
    print("validación OK: 1m/3m/6m/12m presentes para A0200001 y A0200002 en todos los años")
    return 0


if __name__ == "__main__":
    sys.exit(main())
