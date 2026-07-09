#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Descarga automatizada de los datos abiertos REM (Resúmenes Estadísticos
Mensuales) del DEIS-MINSAL Chile (https://deis.minsal.cl/#datosabiertos).

Los datos están publicados como un ZIP por año en:
    https://repositoriodeis.minsal.cl/DatosAbiertos/REM/SERIE_REM_{año}.zip
(años disponibles: 2009 en adelante; el año en curso se actualiza
periódicamente con datos preliminares).

Cada ZIP contiene todas las series (A, BM, BS, D, P) y pesa 50–240 MB.
Por defecto este script extrae SOLO la serie solicitada (Serie A) usando
peticiones HTTP con rango de bytes, sin descargar el ZIP completo, y
guarda el archivo con el nombre estándar del proyecto:
    data/SerieA_2017.txt, data/SerieA_2024.csv, etc.

Tras la descarga, normaliza los archivos a un formato canónico uniforme
entre años (los originales del DEIS varían):
    - extensión .csv (los .txt de 2015-2023 se convierten y eliminan)
    - UTF-8 sin BOM, fin de línea LF, separador ';'
    - encabezado con 'Col##' uniforme (2009 traía col42..col50 en minúscula)
    - IdComuna rellenado a 5 dígitos (2024+ venía sin cero inicial),
      necesario para cruzar con códigos territoriales/shapefiles
    - el resto de los campos queda intacto (CodigoPrestacion conserva sus
      ceros iniciales; IdEstablecimiento 2009-2013 conserva el formato XX-XXX)
Un manifiesto en <dest>/.rem_manifest.json evita re-normalizar en cada corrida.

Uso típico:
    python3 download_rem.py --list                 # ver catálogo disponible
    python3 download_rem.py                        # Serie A, todos los años + normalizar
    python3 download_rem.py --years 2015-2026      # rango de años
    python3 download_rem.py --years 2026 --force   # re-descargar año en curso
    python3 download_rem.py --series A BS          # más de una serie
    python3 download_rem.py --normalize-only       # solo normalizar lo ya descargado
    python3 download_rem.py --no-normalize         # descargar sin normalizar
    python3 download_rem.py --full-zip --keep-zip  # ZIP completo por año

Solo usa la librería estándar de Python (no requiere pip install).
"""

import argparse
import http.client
import json
import re
import ssl
import struct
import sys
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from pathlib import Path

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
CATALOG_URL = (
    "https://deis.minsal.cl/wp-admin/admin-ajax.php"
    "?action=wp_ajax_ninja_tables_public_action"
    "&table_id=2889&target_action=get-all-data&default_sorting=old_first"
)
CATALOG_REM_LABEL = "Resumenes Estadisticos Mensuales (REM)"
URL_PATTERN = "https://repositoriodeis.minsal.cl/DatosAbiertos/REM/SERIE_REM_{year}.zip"
FIRST_YEAR = 2009
SERIES_VALIDAS = ("A", "BM", "BS", "D", "P")
CHUNK = 1024 * 256
RETRIES = 4

_ssl_context = ssl.create_default_context()


def _request(url, headers=None, method="GET"):
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    return urllib.request.Request(url, headers=hdrs, method=method)


def _urlopen(req, timeout=120):
    """urlopen con reintentos y tolerancia a certificados SSL defectuosos."""
    global _ssl_context
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ConnectionError, OSError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code in (403, 404):
                raise
            last_err = e
            # urllib envuelve los fallos de certificado en URLError(reason=SSLCertVerificationError)
            if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                print("  [aviso] certificado SSL no verificable; continuando sin verificación", file=sys.stderr)
                _ssl_context = ssl._create_unverified_context()
                continue
            wait = 5 * attempt
            print(f"  [reintento {attempt}/{RETRIES}] {e} — esperando {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise last_err


def head_info(url):
    """Devuelve (tamaño, validador ETag/Last-Modified) del recurso remoto."""
    with _urlopen(_request(url, method="HEAD"), timeout=60) as r:
        validador = r.headers.get("ETag") or r.headers.get("Last-Modified")
        return int(r.headers["Content-Length"]), validador


ERRORES_RED = (TimeoutError, ConnectionError, http.client.HTTPException, OSError)


def fetch_range(url, start, end):
    """Lee un rango de bytes, reintentando también los cortes a mitad de lectura."""
    esperado = end - start + 1
    last_err = None
    for attempt in range(1, RETRIES + 1):
        req = _request(url, headers={"Range": f"bytes={start}-{end}"})
        try:
            with _urlopen(req) as r:
                if r.status not in (200, 206):
                    raise RuntimeError(f"HTTP {r.status} al pedir rango de {url}")
                data = r.read()
            if len(data) == esperado:
                return data
            last_err = ConnectionError(f"respuesta truncada ({len(data)} de {esperado} bytes)")
        except ERRORES_RED as e:
            last_err = e
        wait = 5 * attempt
        print(f"  [reintento {attempt}/{RETRIES}] {last_err} — esperando {wait}s", file=sys.stderr)
        time.sleep(wait)
    raise last_err


# ---------------------------------------------------------------------------
# Catálogo de datos abiertos DEIS
# ---------------------------------------------------------------------------

def obtener_catalogo():
    """Devuelve {año: url_zip} consultando el catálogo oficial del DEIS.

    Si el sitio no responde, cae al patrón de URL conocido (2009 → presente).
    """
    try:
        with _urlopen(_request(CATALOG_URL), timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
        rows = data if isinstance(data, list) else data.get("data", [])
        catalogo = {}
        for row in rows:
            v = row.get("value", row) if isinstance(row, dict) else {}
            if v.get("filtro_1", "").strip() != CATALOG_REM_LABEL:
                continue
            m = re.search(r"\b(20\d\d)\b", str(v.get("filtro_2", "")) + " " + str(v.get("nombre", "")))
            url = (v.get("ver") or "").strip()
            if m and url.lower().endswith(".zip"):
                catalogo[int(m.group(1))] = url.replace(" ", "%20")
        if catalogo:
            return catalogo
        raise ValueError("el catálogo no contiene entradas REM")
    except Exception as e:
        print(f"[aviso] no se pudo leer el catálogo DEIS ({e}); usando patrón de URL conocido", file=sys.stderr)
        anio_actual = time.localtime().tm_year
        return {y: URL_PATTERN.format(year=y) for y in range(FIRST_YEAR, anio_actual + 1)}


# ---------------------------------------------------------------------------
# Lectura remota del directorio central del ZIP (HTTP Range)
# ---------------------------------------------------------------------------

def leer_directorio_zip(url, zip_size):
    """Lista los miembros del ZIP remoto sin descargarlo completo.

    Devuelve una lista de dicts: name, method, flags, comp_size, uncomp_size,
    crc32, header_offset.
    """
    tail_len = min(zip_size, 65536)
    tail = fetch_range(url, zip_size - tail_len, zip_size - 1)
    i = tail.rfind(b"PK\x05\x06")  # End of Central Directory
    if i < 0:
        raise RuntimeError("no se encontró el directorio central del ZIP")
    cd_size, cd_off = struct.unpack("<II", tail[i + 12:i + 20])
    if cd_off == 0xFFFFFFFF:  # ZIP64
        j = tail.rfind(b"PK\x06\x07", 0, i)
        if j < 0:
            raise RuntimeError("ZIP64 sin localizador EOCD64")
        eocd64_off = struct.unpack("<Q", tail[j + 8:j + 16])[0]
        eocd64 = fetch_range(url, eocd64_off, eocd64_off + 55)
        cd_size, cd_off = struct.unpack("<QQ", eocd64[40:56])

    cd = fetch_range(url, cd_off, cd_off + cd_size - 1)
    miembros, p = [], 0
    while p + 46 <= len(cd) and cd[p:p + 4] == b"PK\x01\x02":
        flags, method = struct.unpack("<HH", cd[p + 8:p + 12])
        crc32, comp_size, uncomp_size = struct.unpack("<III", cd[p + 16:p + 28])
        nlen, elen, clen = struct.unpack("<HHH", cd[p + 28:p + 34])
        header_off = struct.unpack("<I", cd[p + 42:p + 46])[0]
        name = cd[p + 46:p + 46 + nlen]
        name = name.decode("utf-8" if flags & 0x800 else "cp437", errors="replace")
        extra = cd[p + 46 + nlen:p + 46 + nlen + elen]
        # campos ZIP64 en el extra field
        if 0xFFFFFFFF in (comp_size, uncomp_size, header_off):
            q = 0
            while q + 4 <= len(extra):
                tag, size = struct.unpack("<HH", extra[q:q + 4])
                if tag == 0x0001:
                    vals, off = [], q + 4
                    for cur in (uncomp_size, comp_size, header_off):
                        if cur == 0xFFFFFFFF:
                            vals.append(struct.unpack("<Q", extra[off:off + 8])[0])
                            off += 8
                        else:
                            vals.append(cur)
                    uncomp_size, comp_size, header_off = vals
                    break
                q += 4 + size
        miembros.append({
            "name": name, "method": method, "flags": flags, "crc32": crc32,
            "comp_size": comp_size, "uncomp_size": uncomp_size,
            "header_offset": header_off,
        })
        p += 46 + nlen + elen + clen
    return miembros


def extraer_miembro_remoto(url, miembro, destino):
    """Descarga y descomprime un solo miembro del ZIP remoto usando rangos.

    Si la conexión se corta a mitad de la transferencia, reanuda desde el
    último byte recibido conservando el estado del descompresor y del CRC.
    """
    off = miembro["header_offset"]
    header = fetch_range(url, off, off + 29)
    if header[:4] != b"PK\x03\x04":
        raise RuntimeError(f"cabecera local inválida para {miembro['name']}")
    nlen, elen = struct.unpack("<HH", header[26:30])
    data_start = off + 30 + nlen + elen
    data_end = data_start + miembro["comp_size"] - 1

    method = miembro["method"]
    if method == 8:
        decomp = zlib.decompressobj(-15)
    elif method == 0:
        decomp = None
    else:
        raise RuntimeError(f"método de compresión no soportado: {method}")

    crc, escrito, leido = 0, 0, 0
    total = miembro["comp_size"]
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(destino.suffix + ".part")
    with open(tmp, "wb") as f:
        ultimo_pct = -10
        for intento in range(1, RETRIES + 1):
            req = _request(url, headers={"Range": f"bytes={data_start + leido}-{data_end}"})
            try:
                with _urlopen(req) as r:
                    while leido < total:
                        chunk = r.read(min(CHUNK, total - leido))
                        if not chunk:
                            raise ConnectionError("flujo truncado por el servidor")
                        leido += len(chunk)
                        out = decomp.decompress(chunk) if decomp else chunk
                        if out:
                            crc = zlib.crc32(out, crc)
                            escrito += len(out)
                            f.write(out)
                        pct = int(100 * leido / total) if total else 100
                        if pct >= ultimo_pct + 10:
                            print(f"    ... {pct}% ({leido / 1048576:.0f}/{total / 1048576:.0f} MB descargados)")
                            ultimo_pct = pct
                break
            except ERRORES_RED as e:
                if intento == RETRIES:
                    tmp.unlink(missing_ok=True)
                    raise
                print(f"  [reintento {intento}/{RETRIES}] lectura interrumpida ({e}); "
                      f"reanudando desde {leido / 1048576:.0f} MB", file=sys.stderr)
                time.sleep(5 * intento)
        if decomp:
            out = decomp.flush()
            if out:
                crc = zlib.crc32(out, crc)
                escrito += len(out)
                f.write(out)

    if escrito != miembro["uncomp_size"] or (crc & 0xFFFFFFFF) != miembro["crc32"]:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"verificación fallida para {miembro['name']} "
            f"(tamaño {escrito}/{miembro['uncomp_size']}, CRC no coincide)"
        )
    tmp.replace(destino)


# ---------------------------------------------------------------------------
# Selección de miembros y nombres de salida
# ---------------------------------------------------------------------------

RE_SERIE = re.compile(r"(?i)^serie[\s_]*(BM|BS|A|D|P)(?![A-Za-z])")


def serie_de_miembro(nombre_miembro):
    """Devuelve 'A', 'BM', ... si el miembro es un archivo de datos de serie."""
    base = nombre_miembro.rsplit("/", 1)[-1]
    if not base.lower().endswith((".txt", ".csv")):
        return None
    m = RE_SERIE.match(base)
    return m.group(1).upper() if m else None


def nombre_salida(serie, anio, nombre_miembro):
    ext = Path(nombre_miembro).suffix.lower()
    return f"Serie{serie}_{anio}{ext}"


def es_diccionario(nombre_miembro):
    n = nombre_miembro.lower()
    return ("diccionario" in n or "codigos" in n) and n.endswith((".xlsx", ".xlsm", ".xls", ".pdf"))


# ---------------------------------------------------------------------------
# Normalización de formato
# ---------------------------------------------------------------------------

NORM_CHUNK = 16 * 1024 * 1024
RE_COL = re.compile(rb"(?i)^col(\d+)$")
# campos que se rellenan con ceros a la izquierda: {nombre_en_minúscula: ancho}
CAMPOS_PAD = {b"idcomuna": 5}
MANIFEST = ".rem_manifest.json"


def cargar_manifest(dest):
    try:
        with open(dest / MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def guardar_manifest(dest, manifest):
    tmp = dest / (MANIFEST + ".part")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, ensure_ascii=False)
    tmp.replace(dest / MANIFEST)


def _linea_a_utf8(linea, stats):
    """Garantiza UTF-8: los bytes no-UTF8 se reinterpretan como Latin-1."""
    if linea.isascii():
        return linea
    try:
        linea.decode("utf-8")
        return linea
    except UnicodeDecodeError:
        stats["latin1"] += 1
        return linea.decode("latin-1").encode("utf-8")


def normalizar_archivo(src, destino):
    """Reescribe un archivo de serie REM al formato canónico (streaming).

    Devuelve un dict con estadísticas. No modifica valores de datos salvo el
    relleno de ceros de IdComuna; las filas con un número de columnas
    inesperado se copian tal cual y se cuentan.
    """
    stats = {"filas": 0, "pad": 0, "latin1": 0, "cols_malas": 0}
    tmp = destino.with_name(destino.name + ".part")
    with open(src, "rb") as fin, open(tmp, "wb", buffering=NORM_CHUNK) as fout:
        buf = b""
        ncols, idx_pad, ancho_pad, nsep = None, None, None, None

        def procesar(linea):
            nonlocal ncols, idx_pad, ancho_pad, nsep
            if linea.endswith(b"\r"):
                linea = linea[:-1]
            if ncols is None:  # encabezado
                if linea.startswith(b"\xef\xbb\xbf"):
                    linea = linea[3:]
                linea = _linea_a_utf8(linea, stats)
                campos = linea.split(b";")
                campos = [RE_COL.sub(lambda m: b"Col" + m.group(1), c) for c in campos]
                for i, c in enumerate(campos):
                    if c.strip().lower() in CAMPOS_PAD:
                        idx_pad, ancho_pad = i, CAMPOS_PAD[c.strip().lower()]
                ncols = len(campos)
                nsep = ncols - 1
                fout.write(b";".join(campos) + b"\n")
                return
            stats["filas"] += 1
            if linea.count(b";") != nsep:
                stats["cols_malas"] += 1
            elif idx_pad is not None:
                partes = linea.split(b";", idx_pad + 1)
                campo = partes[idx_pad]
                if campo.isdigit() and len(campo) < ancho_pad:
                    partes[idx_pad] = campo.rjust(ancho_pad, b"0")
                    linea = b";".join(partes)
                    stats["pad"] += 1
            fout.write(_linea_a_utf8(linea, stats) + b"\n")

        while True:
            chunk = fin.read(NORM_CHUNK)
            if not chunk:
                break
            buf += chunk
            lineas = buf.split(b"\n")
            buf = lineas.pop()
            for linea in lineas:
                procesar(linea)
        if buf:
            procesar(buf)

    tmp.replace(destino)
    if src != destino:
        src.unlink()
    return stats


def normalizar_seleccion(anios, series, dest):
    """Normaliza los archivos Serie{S}_{año} presentes en dest. Devuelve True si no hubo errores."""
    manifest = cargar_manifest(dest)
    ok = True
    print("\n=== Normalización de formato ===")
    for anio in anios:
        for s in series:
            candidatos = [p for p in dest.glob(f"Serie{s}_{anio}.*")
                          if p.suffix.lower() in (".csv", ".txt")]
            if not candidatos:
                continue
            if len(candidatos) > 1:
                print(f"  [aviso] {', '.join(c.name for c in candidatos)} coexisten; "
                      f"resuelva manualmente cuál conservar", file=sys.stderr)
                ok = False
                continue
            src = candidatos[0]
            destino = src.with_suffix(".csv")
            st = src.stat()
            reg = manifest.get(destino.name)
            if reg and src == destino and reg.get("size") == st.st_size \
                    and abs(reg.get("mtime", 0) - st.st_mtime) < 1:
                print(f"  [ya normalizado] {destino.name}")
                continue
            print(f"  normalizando {src.name} → {destino.name} ({st.st_size / 1048576:.0f} MB)")
            try:
                stats = normalizar_archivo(src, destino)
            except Exception as e:
                print(f"  [error] {src.name}: {e}", file=sys.stderr)
                ok = False
                continue
            st = destino.stat()
            manifest[destino.name] = {
                "size": st.st_size, "mtime": st.st_mtime, "filas": stats["filas"],
                "idcomuna_rellenadas": stats["pad"], "lineas_latin1": stats["latin1"],
                "filas_columnas_inesperadas": stats["cols_malas"],
                "normalizado_en": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            guardar_manifest(dest, manifest)
            extra = ""
            if stats["cols_malas"]:
                extra = f" [AVISO: {stats['cols_malas']} filas con columnas inesperadas]"
            print(f"  [ok] {destino.name}: {stats['filas']:,} filas, "
                  f"{stats['pad']:,} IdComuna rellenadas, {stats['latin1']} líneas Latin-1{extra}")
    return ok


# ---------------------------------------------------------------------------
# Flujo principal por año
# ---------------------------------------------------------------------------

def procesar_anio(anio, url, series, dest, force=False, full_zip=False,
                  keep_zip=False, dicts=False):
    print(f"\n=== REM {anio} ===\n  {url}")
    try:
        zip_size, validador = head_info(url)
    except urllib.error.HTTPError as e:
        print(f"  [error] HTTP {e.code}: no disponible, se omite")
        return False
    print(f"  ZIP: {zip_size / 1048576:.1f} MB")

    if full_zip:
        return _procesar_zip_completo(anio, url, zip_size, validador, series, dest,
                                      force, keep_zip, dicts)

    miembros = leer_directorio_zip(url, zip_size)
    objetivo = []
    for m in miembros:
        s = serie_de_miembro(m["name"])
        if s in series:
            objetivo.append((s, m))
        elif dicts and es_diccionario(m["name"]):
            objetivo.append((None, m))

    if not any(s for s, _ in objetivo):
        print("  [aviso] no se encontró ninguna serie solicitada; contenido del ZIP:")
        for m in miembros:
            print(f"    - {m['name']}")
        return False

    ok = True
    for s, m in objetivo:
        if s is None:
            destino = dest / "diccionarios" / str(anio) / Path(m["name"]).name
        else:
            destino = dest / nombre_salida(s, anio, m["name"])
        if destino.exists() and not force:
            print(f"  [ya existe] {destino.name} (use --force para re-descargar)")
            continue
        print(f"  descargando {m['name']} → {destino.name} "
              f"({m['comp_size'] / 1048576:.0f} MB comprimidos, "
              f"{m['uncomp_size'] / 1048576:.0f} MB finales)")
        try:
            extraer_miembro_remoto(url, m, destino)
            print(f"  [ok] {destino}")
        except Exception as e:
            print(f"  [error] {e}", file=sys.stderr)
            ok = False
    return ok


def _procesar_zip_completo(anio, url, zip_size, validador, series, dest, force, keep_zip, dicts):
    zip_dir = dest / "zips"
    zip_path = zip_dir / f"SERIE_REM_{anio}.zip"

    # si todas las salidas pedidas ya existen, no hace falta bajar el ZIP
    if not force and not zip_path.exists():
        destinos = []
        for m in leer_directorio_zip(url, zip_size):
            s = serie_de_miembro(m["name"])
            if s in series:
                destinos.append(dest / nombre_salida(s, anio, m["name"]))
            elif dicts and es_diccionario(m["name"]):
                destinos.append(dest / "diccionarios" / str(anio) / Path(m["name"]).name)
        if destinos and all(d.exists() for d in destinos):
            for d in destinos:
                print(f"  [ya existe] {d.name} (use --force para re-descargar)")
            return True

    zip_dir.mkdir(parents=True, exist_ok=True)
    tmp = zip_path.with_suffix(".zip.part")
    if force:
        tmp.unlink(missing_ok=True)  # nunca reanudar un parcial posiblemente obsoleto
    if not zip_path.exists() or zip_path.stat().st_size != zip_size or force:
        descargado = tmp.stat().st_size if tmp.exists() else 0
        modo = "ab" if 0 < descargado < zip_size else "wb"
        if modo == "wb":
            descargado = 0
        print(f"  descargando ZIP completo ({descargado / 1048576:.0f} MB ya presentes)")
        ultimo_pct = -10
        for intento in range(1, RETRIES + 1):
            headers = {}
            if descargado:
                headers["Range"] = f"bytes={descargado}-"
                if validador:
                    # si el archivo cambió en el servidor, responde 200 y reiniciamos
                    headers["If-Range"] = validador
            try:
                with _urlopen(_request(url, headers=headers)) as r:
                    if descargado and r.status != 206:
                        print("  [aviso] reanudación no válida; reiniciando desde cero", file=sys.stderr)
                        modo, descargado = "wb", 0
                    with open(tmp, modo) as f:
                        while descargado < zip_size:
                            chunk = r.read(min(CHUNK, zip_size - descargado))
                            if not chunk:
                                raise ConnectionError("flujo truncado por el servidor")
                            f.write(chunk)
                            descargado += len(chunk)
                            pct = int(100 * descargado / zip_size)
                            if pct >= ultimo_pct + 10:
                                print(f"    ... {pct}% ({descargado / 1048576:.0f}/{zip_size / 1048576:.0f} MB)")
                                ultimo_pct = pct
                break
            except ERRORES_RED as e:
                if intento == RETRIES:
                    raise
                modo = "ab"
                print(f"  [reintento {intento}/{RETRIES}] lectura interrumpida ({e}); "
                      f"reanudando desde {descargado / 1048576:.0f} MB", file=sys.stderr)
                time.sleep(5 * intento)
        # integridad antes de promover: tamaño esperado y ZIP legible
        if tmp.stat().st_size != zip_size:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"descarga incompleta del ZIP (se esperaban {zip_size} bytes)")
        try:
            with zipfile.ZipFile(tmp):
                pass
        except zipfile.BadZipFile:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("el ZIP descargado está corrupto; vuelva a intentarlo")
        tmp.replace(zip_path)

    ok = True
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                s = serie_de_miembro(info.filename)
                if s in series:
                    destino = dest / nombre_salida(s, anio, info.filename)
                elif dicts and es_diccionario(info.filename):
                    destino = dest / "diccionarios" / str(anio) / Path(info.filename).name
                else:
                    continue
                if destino.exists() and not force:
                    print(f"  [ya existe] {destino.name} (use --force para re-descargar)")
                    continue
                print(f"  extrayendo {info.filename} → {destino.name}")
                destino.parent.mkdir(parents=True, exist_ok=True)
                tmp_out = destino.with_suffix(destino.suffix + ".part")
                with zf.open(info) as src, open(tmp_out, "wb") as out:
                    while True:
                        chunk = src.read(CHUNK)
                        if not chunk:
                            break
                        out.write(chunk)
                if tmp_out.stat().st_size != info.file_size:
                    tmp_out.unlink(missing_ok=True)
                    raise RuntimeError(f"extracción incompleta de {info.filename}")
                tmp_out.replace(destino)
                print(f"  [ok] {destino}")
    except zipfile.BadZipFile:
        zip_path.unlink(missing_ok=True)  # que la próxima ejecución re-descargue
        raise
    if not keep_zip:
        zip_path.unlink()
    return ok


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_years(tokens, disponibles):
    if tokens is None:
        return sorted(disponibles)
    anios = set()
    for tok in tokens:
        for parte in tok.replace(",", " ").replace("–", "-").replace("—", "-").split():
            if "-" in parte:
                a, b = parte.split("-", 1)
                if not (a.isdigit() and b.isdigit()):
                    raise ValueError(f"rango de años no válido: {parte!r}")
                ia, ib = int(a), int(b)
                if ia > ib:
                    raise ValueError(f"rango de años invertido: {parte!r} (¿quiso decir {ib}-{ia}?)")
                anios.update(range(ia, ib + 1))
            else:
                if not parte.isdigit():
                    raise ValueError(f"año no válido: {parte!r}")
                anios.add(int(parte))
    if not anios:
        raise ValueError("no se indicó ningún año válido")
    return sorted(anios)


def main():
    ap = argparse.ArgumentParser(
        description="Descarga los datos abiertos REM del DEIS-MINSAL (todas las series, todos los años).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Ejemplos:\n"
               "  python3 download_rem.py --list\n"
               "  python3 download_rem.py --years 2015-2026\n"
               "  python3 download_rem.py --years 2026 --force\n"
               "  python3 download_rem.py --series A BS --dest data\n",
    )
    ap.add_argument("--years", nargs="+", default=None,
                    help="años a descargar: '2017 2019', '2015-2024' o combinaciones (defecto: todos)")
    ap.add_argument("--series", nargs="+", default=["A"],
                    help=f"series a extraer: {', '.join(SERIES_VALIDAS)} o 'all' (defecto: A)")
    ap.add_argument("--dest", default="data", help="carpeta de destino (defecto: data)")
    ap.add_argument("--force", action="store_true",
                    help="re-descarga aunque el archivo ya exista (útil para el año en curso)")
    ap.add_argument("--full-zip", action="store_true",
                    help="descarga el ZIP completo del año en vez de extraer solo las series pedidas")
    ap.add_argument("--keep-zip", action="store_true",
                    help="con --full-zip, conserva el ZIP en <dest>/zips/")
    ap.add_argument("--dicts", action="store_true",
                    help="extrae también los diccionarios de códigos a <dest>/diccionarios/<año>/")
    ap.add_argument("--no-normalize", action="store_true",
                    help="no normalizar el formato de los archivos tras la descarga")
    ap.add_argument("--normalize-only", action="store_true",
                    help="solo normaliza los archivos ya presentes en <dest>, sin descargar")
    ap.add_argument("--list", action="store_true",
                    help="muestra el catálogo de años disponibles y termina")
    args = ap.parse_args()

    if args.normalize_only and args.no_normalize:
        ap.error("--normalize-only y --no-normalize son incompatibles")

    series = [s.upper() for s in args.series]
    if "ALL" in series:
        series = list(SERIES_VALIDAS)
    invalidas = [s for s in series if s not in SERIES_VALIDAS]
    if invalidas:
        ap.error(f"series no válidas: {invalidas}; opciones: {SERIES_VALIDAS} o all")

    if args.list:
        catalogo = obtener_catalogo()
        print("Años REM disponibles en el catálogo DEIS:")
        for y in sorted(catalogo):
            print(f"  {y}: {catalogo[y]}")
        return 0

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)

    catalogo = None
    if args.normalize_only and args.years is None:
        # sin --years, normaliza todos los años presentes en disco
        anios = sorted({int(m.group(1)) for s in series
                        for p in dest.glob(f"Serie{s}_*.*")
                        if (m := re.fullmatch(rf"Serie{s}_(\d{{4}})\.(csv|txt)",
                                              p.name, re.IGNORECASE))})
        if not anios:
            print(f"No hay archivos Serie{{{','.join(series)}}}_<año> en {dest}/")
            return 1
    else:
        if args.years is None:
            catalogo = obtener_catalogo()
        try:
            anios = parse_years(args.years, catalogo.keys() if catalogo else None)
        except ValueError as e:
            ap.error(f"--years: {e}; use formatos como '2017 2019' o '2015-2024'")

    print(f"Series: {', '.join(series)} | Años: {anios[0]}–{anios[-1]} "
          f"({len(anios)} en total) | Destino: {dest}/")

    fallos = []
    if not args.normalize_only:
        if catalogo is None:
            catalogo = obtener_catalogo()
        for anio in anios:
            url = catalogo.get(anio, URL_PATTERN.format(year=anio))
            try:
                if not procesar_anio(anio, url, series, dest, force=args.force,
                                     full_zip=args.full_zip, keep_zip=args.keep_zip,
                                     dicts=args.dicts):
                    fallos.append(anio)
            except KeyboardInterrupt:
                print("\nInterrumpido por el usuario.")
                return 130
            except Exception as e:
                print(f"  [error] {anio}: {e}", file=sys.stderr)
                fallos.append(anio)

    norm_ok = True
    if not args.no_normalize:
        try:
            norm_ok = normalizar_seleccion(anios, series, dest)
        except KeyboardInterrupt:
            print("\nInterrumpido por el usuario.")
            return 130

    print("\n=== Resumen ===")
    if fallos:
        print(f"Años con problemas de descarga: {fallos}")
    if not norm_ok:
        print("Hubo problemas en la normalización (ver avisos).")
    if fallos or not norm_ok:
        return 1
    print("Todos los años solicitados se procesaron correctamente.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
