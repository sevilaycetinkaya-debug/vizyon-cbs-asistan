from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.sax.saxutils import escape
import base64
import io
import json
import math
import csv
import os
import time
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / "outputs"
DATA = ROOT / "work" / "vizyon-data"
DATA.mkdir(parents=True, exist_ok=True)
ROOT_HTML = ROOT / "vizyon-cbs-prototip.html"
ROOT_PGA_GRID = ROOT / "afad_pga_akbank_grid.csv"
ADMIN_NEIGHBORHOODS_CSV = ROOT / "admin_neighborhoods.csv"
TKGM_API_BASE = "https://cbsapi.tkgm.gov.tr/megsiswebapi.v3.1/api"
TKGM_PARSEL_BASE = "https://parselsorgu.tkgm.gov.tr/app/modules/administrativeQuery/data"
QUICK_API_BASE = "https://quicksigorta.com/api"
_TKGM_CACHE = {}
_QUICK_CACHE = {}
_ADMIN_NEIGHBORHOOD_CACHE = None


def tbdy_zemin_sinifi(vs30):
    if vs30 is None:
        return None
    if vs30 > 1500:
        return {"code": "ZA", "description": "Saglam, sert kaya"}
    if vs30 > 760:
        return {"code": "ZB", "description": "Az ayrismis, orta saglam kaya"}
    if vs30 > 360:
        return {"code": "ZC", "description": "Cok siki kum/cakil veya sert kil"}
    if vs30 > 180:
        return {"code": "ZD", "description": "Orta siki-siki kum/cakil veya cok kati kil"}
    return {"code": "ZE", "description": "Gevsek kum/cakil veya yumusak-kati kil"}


def get_json(url, timeout=12):
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Vizyon-CBS-Asistan/0.1",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw)


def quick_location(path, params=None):
    query = urllib.parse.urlencode(params or {})
    url = f"{QUICK_API_BASE}/location/{path}"
    if query:
        url = f"{url}?{query}"
    key = f"{path}:{query}"
    if key not in _QUICK_CACHE:
        try:
            _QUICK_CACHE[key] = get_json(url)
        except Exception as exc:
            _QUICK_CACHE[key] = {"success": False, "_error": str(exc), "result": {}}
    payload = _QUICK_CACHE[key]
    return payload.get("result", payload) if isinstance(payload, dict) else payload


def quick_rows(payload, plural_key):
    def find_list(value):
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            preferred = (
                plural_key,
                "items",
                "data",
                "results",
                "result",
                "list",
                "rows",
                "values",
            )
            for key in preferred:
                rows = value.get(key)
                if isinstance(rows, list):
                    return rows
                nested = find_list(rows)
                if nested:
                    return nested
            for rows in value.values():
                nested = find_list(rows)
                if nested:
                    return nested
        return []

    if isinstance(payload, dict):
        rows = find_list(payload)
        if rows:
            return rows
    return payload if isinstance(payload, list) else []


def norm_text(value):
    raw = (
        str(value or "")
        .replace("İ", "I")
        .replace("ı", "i")
        .replace("Ç", "C")
        .replace("ç", "c")
        .replace("Ğ", "G")
        .replace("ğ", "g")
        .replace("Ö", "O")
        .replace("ö", "o")
        .replace("Ş", "S")
        .replace("ş", "s")
        .replace("Ü", "U")
        .replace("ü", "u")
        .replace("Ý", "I")
        .replace("ý", "i")
        .replace("Þ", "S")
        .replace("þ", "s")
        .replace("Ð", "G")
        .replace("ð", "g")
    )
    text = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def location_match(row_value, query_value):
    row_norm = norm_text(row_value)
    query_norm = norm_text(query_value)
    if not query_norm:
        return True
    if row_norm == query_norm:
        return True
    row_flat = row_norm.replace(" ", "")
    query_flat = query_norm.replace(" ", "")
    if row_flat and query_flat and row_flat == query_flat:
        return True
    # Tapu/rapor metinlerinde "Darıca Ş.", "İlçe merkezi" gibi ekler gelebiliyor.
    return bool(row_flat and query_flat and (row_flat in query_flat or query_flat in row_flat))


def load_admin_neighborhoods():
    global _ADMIN_NEIGHBORHOOD_CACHE
    if _ADMIN_NEIGHBORHOOD_CACHE is not None:
        return _ADMIN_NEIGHBORHOOD_CACHE

    rows = []
    if ADMIN_NEIGHBORHOODS_CSV.exists():
        with ADMIN_NEIGHBORHOODS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                rows.append(
                    {
                        "il": (row.get("il") or "").strip(),
                        "ilce": (row.get("ilce") or "").strip(),
                        "mahalle": (row.get("mahalle") or "").strip(),
                        "tip": (row.get("tip") or "").strip(),
                        "baglilik": (row.get("baglilik") or "").strip(),
                    }
                )
    _ADMIN_NEIGHBORHOOD_CACHE = rows
    return rows


def admin_neighborhood_options(city, district):
    options = []
    seen = set()
    for row in load_admin_neighborhoods():
        if city and not location_match(row.get("il"), city):
            continue
        if district and not location_match(row.get("ilce"), district):
            continue
        name = (row.get("mahalle") or "").strip()
        if not name:
            continue
        key = norm_text(name)
        if key in seen:
            continue
        seen.add(key)
        options.append(
            {
                "name": name,
                "type": row.get("tip") or "MAHALLE",
                "label": f"{name} {row.get('tip') or 'MAHALLE'}".strip(),
                "city": row.get("il") or city,
                "district": row.get("ilce") or district,
                "source": row.get("baglilik") or "İçişleri mülki idari bölümleri",
            }
        )
    return sorted(options, key=lambda item: norm_text(item.get("name")))


def item_label(item):
    if not isinstance(item, dict):
        return str(item or "")
    for key in (
        "name",
        "label",
        "text",
        "value",
        "title",
        "description",
        "adi",
        "ad",
        "mahalleAdi",
        "caddeSokakAdi",
        "sokakAdi",
        "kapiNo",
        "disKapiNo",
        "icKapiNo",
        "doorNo",
        "outerDoorNo",
        "innerDoorNo",
        "spaceNo",
        "binaNo",
        "binaKodu",
        "buildingCode",
    ):
        if item.get(key):
            return str(item.get(key))
    return str(item)


def pick_by_name(rows, wanted):
    wanted_norm = norm_text(wanted)
    wanted_compact = wanted_norm.replace(" ", "")
    if not wanted_norm:
        return rows[0] if rows else None
    scored = []
    for row in rows:
        label_norm = norm_text(item_label(row))
        label_compact = label_norm.replace(" ", "")
        if label_norm == wanted_norm or label_compact == wanted_compact:
            return row
        if (
            wanted_norm in label_norm
            or label_norm in wanted_norm
            or (wanted_compact and wanted_compact in label_compact)
            or (label_compact and label_compact in wanted_compact)
        ):
            scored.append((abs(len(label_norm) - len(wanted_norm)), row))
    if scored:
        return sorted(scored, key=lambda item: item[0])[0][1]
    tokens = set(wanted_norm.split())
    for row in rows:
        label_tokens = set(norm_text(item_label(row)).split())
        if tokens and tokens.issubset(label_tokens):
            return row
    return None


def row_id(row):
    if not isinstance(row, dict):
        return ""
    for key in (
        "id",
        "value",
        "code",
        "addressCode",
        "adresKodu",
        "uavt",
        "uavtCode",
        "buildingId",
        "buildingCode",
        "buildingIdentityNo",
        "binaKodu",
        "binaNo",
        "neighbourhoodId",
        "neighborhoodId",
        "mahalleId",
        "streetId",
        "caddeSokakId",
        "doorId",
        "outerDoorId",
        "innerDoorId",
    ):
        if row.get(key) not in (None, ""):
            return row.get(key)
    return ""


def split_city_district(value):
    parts = [part.strip() for part in re.split(r"/|-", str(value or ""), maxsplit=1)]
    city = parts[0] if parts else ""
    district = parts[1] if len(parts) > 1 else ""
    return city, district


def outer_door_candidates(outer_door, block_no):
    outer = str(outer_door or "").strip()
    block = str(block_no or "").strip().upper()
    compact_outer = re.sub(r"\s+", "", outer).upper()
    spaced_outer = re.sub(r"(\d+)([A-ZÇĞİÖŞÜ]+)", r"\1 \2", compact_outer)
    candidates = [outer, compact_outer, spaced_outer]
    if block:
        candidates.extend([
            f"{outer} {block}-BLOK",
            f"{compact_outer} {block}-BLOK",
            f"{spaced_outer} {block}-BLOK",
            f"{outer} {block} BLOK",
            f"{spaced_outer} {block} BLOK",
        ])
    seen = []
    for candidate in candidates:
        clean = re.sub(r"\s+", " ", str(candidate or "").strip())
        if clean and clean not in seen:
            seen.append(clean)
    return seen


def try_quick_address_lookup(body):
    city, district = split_city_district(body.get("city_district") or body.get("title"))
    city = body.get("city") or city
    district = body.get("district") or district
    admin_neighborhood = body.get("administrative_neighborhood") or body.get("neighborhood") or ""
    street_name = body.get("street_name") or ""
    outer_door = body.get("outer_door_no") or body.get("door_no") or ""
    inner_door = body.get("inner_door_no") or body.get("bb_no") or ""
    block_no = body.get("block_no") or ""
    site_name = body.get("site_name") or ""
    formatted_address = body.get("formatted_address") or ""

    if not (city and district and admin_neighborhood and street_name and outer_door):
        missing = [
            label for label, value in (
                ("il", city),
                ("ilçe", district),
                ("idari mahalle", admin_neighborhood),
                ("cadde/sokak", street_name),
                ("dış kapı", outer_door),
            )
            if not value
        ]
        return {"ok": False, "message": "Quick otomatik sorgu için eksik alan: " + ", ".join(missing)}

    city_row = pick_by_name(quick_rows(quick_location("city"), "cities"), city)
    if not city_row:
        return {"ok": False, "message": f"Quick il listesinde bulunamadı: {city}"}
    county_row = pick_by_name(quick_rows(quick_location("county", {"cityId": row_id(city_row)}), "counties"), district)
    if not county_row:
        return {"ok": False, "message": f"Quick ilçe listesinde bulunamadı: {district}"}

    town_payload = quick_location("town", {"countyId": row_id(county_row)})
    town_row = pick_by_name(quick_rows(town_payload, "towns"), "MERKEZ")
    if not town_row:
        return {"ok": False, "message": "Quick bucak listesi alınamadı; reCAPTCHA nedeniyle manuel Quick ekranı gerekebilir."}

    village_payload = quick_location("village", {"townId": row_id(town_row)})
    village_row = pick_by_name(quick_rows(village_payload, "villages"), "MERKEZ")
    if not village_row:
        return {"ok": False, "message": "Quick köy listesi alınamadı; reCAPTCHA nedeniyle manuel Quick ekranı gerekebilir."}

    neighborhood_row = pick_by_name(
        quick_rows(quick_location("neighbourhood", {"villageId": row_id(village_row)}), "neighbourhoods"),
        admin_neighborhood,
    )
    if not neighborhood_row:
        return {"ok": False, "message": f"Quick mahalle listesinde bulunamadı: {admin_neighborhood}"}

    street_row = pick_by_name(
        quick_rows(quick_location("street", {"neighbourhoodId": row_id(neighborhood_row)}), "streets"),
        street_name,
    )
    if not street_row:
        return {"ok": False, "message": f"Quick sokak listesinde bulunamadı: {street_name}"}

    buildings = quick_rows(quick_location("building", {"streetId": row_id(street_row)}), "buildings")
    building_row = None
    for candidate in outer_door_candidates(outer_door, block_no):
        building_row = pick_by_name(buildings, candidate)
        if building_row:
            break
    if not building_row:
        return {"ok": False, "message": f"Quick dış kapı listesinde bulunamadı: {outer_door}"}

    spaces = quick_rows(quick_location("space", {"buildingId": row_id(building_row)}), "spaces")
    space_row = pick_by_name(spaces, inner_door) if inner_door else (spaces[0] if spaces else None)
    if not space_row:
        return {"ok": False, "message": f"Quick iç kapı listesinde bulunamadı: {inner_door or '-'}"}

    address_code = (
        space_row.get("uavtCode")
        or space_row.get("uavt")
        or space_row.get("addressCode")
        or space_row.get("code")
        or row_id(space_row)
    )
    building_code = (
        building_row.get("buildingCode")
        or building_row.get("buildingIdentityNo")
        or building_row.get("code")
        or row_id(building_row)
    )
    return {
        "ok": bool(address_code or building_code),
        "address_code": str(address_code or ""),
        "building_code": str(building_code or ""),
        "address": formatted_address or ", ".join(
            part
            for part in [
                str(item_label(neighborhood_row)).title(),
                str(item_label(street_row)).title(),
                site_name,
                str(item_label(building_row)),
                str(item_label(space_row)),
                f"{district}/{city}".upper(),
            ]
            if part
        ),
        "message": "Quick Sigorta adres kodu ve bina kodu otomatik okundu.",
    }


def parse_feature_list(data, key_name):
    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        rows = []
        for feature in data.get("features") or []:
            props = feature.get("properties") or {}
            rows.append({"id": props.get("id"), "text": props.get("text") or props.get(key_name) or ""})
        return rows
    return data if isinstance(data, list) else []


def cached_json(key, url):
    if key not in _TKGM_CACHE:
        _TKGM_CACHE[key] = get_json(url)
    return _TKGM_CACHE[key]


def get_tkgm_iller():
    data = cached_json("iller", f"{TKGM_PARSEL_BASE}/ilListe.json")
    return parse_feature_list(data, "il")


def get_tkgm_ilceler(il_id):
    data = cached_json(f"ilce:{il_id}", f"{TKGM_API_BASE}/idariYapi/ilceListe/{il_id}")
    return parse_feature_list(data, "ilce")


def get_tkgm_mahalleler(ilce_id):
    data = cached_json(f"mahalle:{ilce_id}", f"{TKGM_API_BASE}/idariYapi/mahalleListe/{ilce_id}")
    return parse_feature_list(data, "mahalle")


def parse_tkgm_text_query(text):
    raw = str(text or "")
    ada_parsel = (
        re.search(r"(\d+)\s*/\s*(\d+)", raw)
        or re.search(r"(\d+)\s*(?:ada|ada\s+no)\D{0,30}(\d+)\s*(?:parsel|parsel\s+no)", raw, re.I)
    )
    if not ada_parsel:
        raise ValueError("Ada/parsel bulunamadi. Ornek: kocaeli darica 1147 ada 7 parsel")
    ada, parsel = ada_parsel.group(1), ada_parsel.group(2)
    cleaned = re.sub(r"\d+\s*/\s*\d+", " ", raw, flags=re.I)
    cleaned = re.sub(r"\d+\s*(?:ada|ada\s+no)\D{0,30}\d+\s*(?:parsel|parsel\s+no)", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\b(ada|parsel|no|numara|il|ilce|ilÃ§e|mahalle|mahallesi)\b", " ", cleaned, flags=re.I)
    tokens = norm_text(cleaned).split()
    return {"ada": ada, "parsel": parsel, "tokens": tokens, "raw": raw}


def center_from_geometry(geometry):
    coords = ((geometry or {}).get("coordinates") or [[]])[0]
    points = [(float(item[1]), float(item[0])) for item in coords if len(item) >= 2]
    if not points:
        return None
    return {
        "lat": sum(point[0] for point in points) / len(points),
        "lng": sum(point[1] for point in points) / len(points),
    }


def read_tkgm_parcel_by_admin(mahalle_id, ada, parsel):
    data = get_json(f"{TKGM_API_BASE}/parsel/{mahalle_id}/{ada}/{parsel}")
    return normalize_tkgm_feature(data)


def search_tkgm_parcel_text(text):
    parsed = parse_tkgm_text_query(text)
    token_text = " ".join(parsed["tokens"])
    if not token_text:
        raise ValueError("Il/ilce bilgisi bulunamadi.")

    iller = get_tkgm_iller()
    il_candidates = [row for row in iller if norm_text(row.get("text")) in token_text]
    if not il_candidates:
        raise ValueError("Il eslesmedi. Ornek: Kocaeli Darica 1147 ada 7 parsel")

    errors = []
    for il in il_candidates[:3]:
        ilceler = get_tkgm_ilceler(il["id"])
        matched_ilceler = [row for row in ilceler if norm_text(row.get("text")) in token_text]
        ilce_candidates = matched_ilceler + [row for row in ilceler if row not in matched_ilceler]
        for ilce in ilce_candidates[:40]:
            mahalleler = get_tkgm_mahalleler(ilce["id"])
            matched_mahalleler = [row for row in mahalleler if norm_text(row.get("text")) in token_text]
            mahalle_candidates = matched_mahalleler + [row for row in mahalleler if row not in matched_mahalleler]
            for mahalle in mahalle_candidates[:80]:
                try:
                    parcel = read_tkgm_parcel_by_admin(mahalle["id"], parsed["ada"], parsed["parsel"])
                    parcel["admin_search"] = {
                        "input": text,
                        "matched_il": il.get("text"),
                        "matched_ilce": ilce.get("text"),
                        "matched_mahalle": mahalle.get("text"),
                    }
                    center = center_from_geometry(parcel.get("geometry"))
                    if center:
                        parcel["center"] = center
                    return parcel
                except Exception as exc:
                    errors.append(str(exc))
    raise ValueError("TKGM il/ilce/mahalle icinde parsel bulunamadi.")


def extract_afad_pga(text):
    import re

    raw = (text or "").replace(",", ".")
    patterns = [
        r"PGA\s*475[\s\S]{0,160}?([0-9]+(?:\.[0-9]+)?)\s*g?",
        r"gridcell[^>]*>\s*PGA\s*475[\s\S]{0,160}?gridcell[^>]*>\s*([0-9]+(?:\.[0-9]+)?)",
        r"<td[^>]*>\s*PGA\s*475\s*</td>\s*<td[^>]*>\s*([0-9]+(?:\.[0-9]+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            value = float(match.group(1))
            if 0 < value < 5:
                return round(value, 3)

    # Fallback: AFAD response contains only the selected value near the copied text.
    for match in re.finditer(r"(?<!\d)(0?\.[0-9]{2,4})(?!\d)", raw):
        value = float(match.group(1))
        if 0 < value < 2:
            return round(value, 3)
    return None


def normalize_tkgm_feature(feature):
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        raise ValueError("TKGM parsel cevabi Feature degil")
    props = feature.get("properties") or {}
    return {
        "status": "ok",
        "source": "TKGM MEGSIS Parsel Sorgu",
        "properties": props,
        "geometry": feature.get("geometry"),
        "summary": {
            "il": props.get("ilAd") or "",
            "ilce": props.get("ilceAd") or "",
            "mahalle": props.get("mahalleAd") or "",
            "mahalleId": props.get("mahalleId"),
            "ada": str(props.get("adaNo") or ""),
            "parsel": str(props.get("parselNo") or ""),
            "alan": props.get("alan") or "",
            "pafta": props.get("pafta") or "",
            "mulkiyet": props.get("zeminKmdurum") or "",
            "durum": "Aktif" if str(props.get("durum") or "") == "1" else str(props.get("durum") or ""),
            "nitelik": props.get("nitelik") or "",
            "mevkii": props.get("mevkii") or "",
        },
    }


def read_tkgm_parcel_by_coords(lat, lng):
    data = get_json(f"{TKGM_API_BASE}/parsel/{lat}/{lng}/")
    return normalize_tkgm_feature(data)


def read_tkgm_blocks(mahalle_id, ada, parsel):
    if not mahalle_id or not ada or not parsel:
        return {"status": "missing_parcel_key", "rows": [], "total_bb": None}

    data = get_json(f"{TKGM_API_BASE}/parsel/blok/{mahalle_id}/{ada}/{parsel}")
    if data.get("type") != "FeatureCollection":
        raise ValueError("TKGM blok/BB cevabi FeatureCollection degil")

    rows = []
    total = 0
    for feature in data.get("features") or []:
        props = feature.get("properties") or {}
        try:
            count = int(props.get("bagimsizBolumSayisi") or 0)
        except (TypeError, ValueError):
            count = 0
        total += count
        rows.append(
            {
                "blok": props.get("blok") or "Ana",
                "nitelik": props.get("zeminKmdurum") or "",
                "adet": count,
                "mahalleId": props.get("mahalleId") or mahalle_id,
                "adaNo": str(props.get("adaNo") or ada),
                "parselNo": str(props.get("parselNo") or parsel),
            }
        )

    return {"status": "ok", "rows": rows, "total_bb": total}


def parcel_to_kml(parcel, lat=None, lng=None):
    summary = parcel.get("summary") or {}
    ada_parsel = f"{summary.get('ada')}/{summary.get('parsel')}" if summary.get("ada") and summary.get("parsel") else ""
    title = " - ".join(
        item
        for item in [summary.get("il"), summary.get("ilce"), summary.get("mahalle"), ada_parsel]
        if item
    ) or "Vizyon secili parsel"
    description = (
        f"Alan: {summary.get('alan') or ''}; "
        f"Mulkiyet: {summary.get('mulkiyet') or ''}; "
        f"Durum: {summary.get('durum') or ''}; "
        f"Nitelik: {summary.get('nitelik') or ''}"
    )
    coords = (((parcel.get("geometry") or {}).get("coordinates") or [[]])[0] or [])
    coord_text = " ".join(f"{float(item[0]):.8f},{float(item[1]):.8f},0" for item in coords if len(item) >= 2)
    point = ""
    if lat is not None and lng is not None:
        point = f"""
    <Placemark>
      <name>{escape(title)} merkez</name>
      <Point><coordinates>{float(lng):.8f},{float(lat):.8f},0</coordinates></Point>
    </Placemark>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{escape(title)}</name>
    <Style id="vizyonParcel">
      <LineStyle><color>ff0099cc</color><width>3</width></LineStyle>
      <PolyStyle><color>5533ccff</color></PolyStyle>
    </Style>
    <Placemark>
      <name>{escape(title)}</name>
      <description>{escape(description)}</description>
      <styleUrl>#vizyonParcel</styleUrl>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>{coord_text}</coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>{point}
  </Document>
</kml>
"""


def read_usgs_vs30(lat, lng):
    endpoint = "https://earthquake.usgs.gov/arcgis/rest/services/eq/vs30_slope/MapServer/identify"
    pad = 0.01
    params = urllib.parse.urlencode(
        {
            "f": "json",
            "geometry": f"{lng},{lat}",
            "geometryType": "esriGeometryPoint",
            "sr": "4326",
            "layers": "all",
            "tolerance": "3",
            "mapExtent": f"{lng - pad},{lat - pad},{lng + pad},{lat + pad}",
            "imageDisplay": "800,600,96",
            "returnGeometry": "false",
        }
    )
    data = get_json(f"{endpoint}?{params}")
    attrs = (data.get("results") or [{}])[0].get("attributes") or {}
    raw_value = attrs.get("Classify.Pixel Value")
    try:
        vs30 = float(raw_value)
    except (TypeError, ValueError):
        raise ValueError("USGS Vs30 degeri okunamadi")
    return round(vs30, 1)


AFAD_REFERENCE_POINTS = [
    # Kullanici tarafindan AFAD TDTH ekranindan dogrulanan test noktasi.
    {"lat": 40.216032, "lng": 28.979374, "pga_g": 0.388, "source": "AFAD TDTH manuel dogrulama"},
]

AFAD_GRID_PATHS = (
    DATA / "afad_pga_akbank_grid.csv",
    ROOT / "afad_pga_akbank_grid.csv",
)
_AFAD_GRID = None


def load_afad_grid():
    global _AFAD_GRID
    if _AFAD_GRID is not None:
        return _AFAD_GRID

    points = {}
    lngs = set()
    lats = set()
    source = ""
    for grid_path in AFAD_GRID_PATHS:
        if not grid_path.exists():
            continue
        source = str(grid_path)
        with grid_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                try:
                    lng = round(float(row["lng"]), 6)
                    lat = round(float(row["lat"]), 6)
                    pga = float(row["pga_g"])
                except (KeyError, TypeError, ValueError):
                    continue
                points[(lng, lat)] = pga
                lngs.add(lng)
                lats.add(lat)
        if points:
            break

    _AFAD_GRID = {"points": points, "lngs": sorted(lngs), "lats": sorted(lats), "source": source}
    return _AFAD_GRID


def bracket(values, value):
    if not values or value < values[0] or value > values[-1]:
        return None
    return (
        max(item for item in values if item <= value),
        min(item for item in values if item >= value),
    )


def interpolate_afad_grid(lat, lng):
    grid = load_afad_grid()
    points = grid["points"]
    if not points:
        return None

    x_pair = bracket(grid["lngs"], lng)
    y_pair = bracket(grid["lats"], lat)
    if not x_pair or not y_pair:
        return None

    x1, x2 = x_pair
    y1, y2 = y_pair
    keys = [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]
    if any(key not in points for key in keys):
        nearest_key, nearest_value = min(
            points.items(),
            key=lambda item: math.hypot(lng - item[0][0], lat - item[0][1]),
        )
        if math.hypot(lng - nearest_key[0], lat - nearest_key[1]) > 0.075:
            return None
        return {
            "status": "grid_nearest",
            "pga_g": round(nearest_value, 3),
            "method": "nearest grid point",
            "grid_points": [{"lng": nearest_key[0], "lat": nearest_key[1], "pga_g": nearest_value}],
        }

    if x1 == x2 and y1 == y2:
        value = points[(x1, y1)]
    elif x1 == x2:
        q1 = points[(x1, y1)]
        q2 = points[(x1, y2)]
        value = q1 + (q2 - q1) * ((lat - y1) / (y2 - y1))
    elif y1 == y2:
        q1 = points[(x1, y1)]
        q2 = points[(x2, y1)]
        value = q1 + (q2 - q1) * ((lng - x1) / (x2 - x1))
    else:
        q11 = points[(x1, y1)]
        q21 = points[(x2, y1)]
        q12 = points[(x1, y2)]
        q22 = points[(x2, y2)]
        value = (
            q11 * (x2 - lng) * (y2 - lat)
            + q21 * (lng - x1) * (y2 - lat)
            + q12 * (x2 - lng) * (lat - y1)
            + q22 * (lng - x1) * (lat - y1)
        ) / ((x2 - x1) * (y2 - y1))

    return {
        "status": "grid_interpolated",
        "pga_g": round(value, 3),
        "method": "bilinear interpolation",
        "grid_points": [
            {"lng": x1, "lat": y1, "pga_g": points[(x1, y1)]},
            {"lng": x2, "lat": y1, "pga_g": points[(x2, y1)]},
            {"lng": x1, "lat": y2, "pga_g": points[(x1, y2)]},
            {"lng": x2, "lat": y2, "pga_g": points[(x2, y2)]},
        ],
    }


def nearest_afad_reference(lat, lng):
    best = None
    for point in AFAD_REFERENCE_POINTS:
        distance = math.hypot(lat - point["lat"], lng - point["lng"])
        if best is None or distance < best["distance"]:
            best = {**point, "distance": distance}
    if best and best["distance"] <= 0.005:
        return best
    return None


def append_jsonl(name, payload):
    target = DATA / name
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_uploaded_text(filename, mime, content_base64):
    raw = base64.b64decode(content_base64 or "")
    lower = str(filename or "").lower()
    mime = str(mime or "").lower()

    if lower.endswith(".pdf") or "pdf" in mime:
        try:
            from pypdf import PdfReader
        except Exception as exc:
            raise ValueError(f"PDF metni için pypdf gerekli: {exc}") from exc
        reader = PdfReader(io.BytesIO(raw))
        return "\n".join((page.extract_text() or "") for page in reader.pages)

    for encoding in ("utf-8-sig", "utf-8", "cp1254", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def parse_takbis_upload_text(text):
    normalized = re.sub(r"\s+", " ", str(text or " "))
    ascii_norm = norm_text(normalized)

    def first_match(patterns, source=normalized, flags=re.I):
        for pattern in patterns:
            match = re.search(pattern, source, flags)
            if match:
                return match.group(1).strip()
        return ""

    city = first_match([
        r"\bil\s*(?:adı|adi)?\s*[:=-]?\s*([A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜ\s]{1,40}?)(?=\s+(?:ilçe|ilce|mahalle|köy|koy|ada|parsel|pafta|mevkii|mevki)\b|$)",
    ])
    district = first_match([
        r"\bilçe\s*(?:adı)?\s*[:=-]?\s*([A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜ\s]{1,40}?)(?=\s+(?:mahalle|köy|koy|ada|parsel|pafta|mevkii|mevki)\b|$)",
        r"\bilce\s*(?:adi)?\s*[:=-]?\s*([A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜ\s]{1,40}?)(?=\s+(?:mahalle|koy|ada|parsel|pafta|mevkii|mevki)\b|$)",
    ])
    neighborhood = first_match([
        r"\bmahalle\s*(?:adı|adi)?\s*[:=-]?\s*([A-ZÇĞİÖŞÜ0-9][A-ZÇĞİÖŞÜ0-9\s\.\-]{1,60}?)(?=\s+(?:ada|parsel|pafta|mevkii|mevki|alan|yüz|yuz|nitelik|mülkiyet|mulkiyet)\b|$)",
        r"\bköy\s*(?:adı)?\s*[:=-]?\s*([A-ZÇĞİÖŞÜ0-9][A-ZÇĞİÖŞÜ0-9\s\.\-]{1,60}?)(?=\s+(?:ada|parsel|pafta|mevkii|mevki|alan|yüz|yuz|nitelik|mülkiyet|mulkiyet)\b|$)",
        r"\bkoy\s*(?:adi)?\s*[:=-]?\s*([A-ZÇĞİÖŞÜ0-9][A-ZÇĞİÖŞÜ0-9\s\.\-]{1,60}?)(?=\s+(?:ada|parsel|pafta|mevkii|mevki|alan|yuz|nitelik|mulkiyet)\b|$)",
    ])

    ada = first_match([
        r"\bada\s*(?:no|numarası|numarasi)?\s*[:=-]?\s*(\d+)",
        r"(\d+)\s*ada\b",
    ])
    parsel = first_match([
        r"\bparsel\s*(?:no|numarası|numarasi)?\s*[:=-]?\s*(\d+)",
        r"(\d+)\s*parsel\b",
    ])

    if re.search(r"kat\s+irtifak", ascii_norm, re.I):
        asset_type = "Kat İrtifak"
    elif re.search(r"kat\s+mulkiyet", ascii_norm, re.I):
        asset_type = "Kat Mülkiyet"
    elif re.search(r"ana\s+tasinmaz", ascii_norm, re.I):
        asset_type = "Ana taşınmaz"
    else:
        asset_type = ""

    block = first_match([
        r"Blok/Kat/Giri(?:ş|s|þ)/BBNo\s*[:=-]?\s*([A-ZÇÐÝÖÞÜ0-9]+)\s*/",
        r"Blok/Kat/Giriş/BBNo\s*[:=-]?\s*([A-ZÇĞİÖŞÜ0-9]+)\s*/",
        r"(?:blok|block)\s*(?:no|adı|adi)?\s*[:=-]?\s*([A-ZÇĞİÖŞÜ0-9]+)",
        r"\b([A-ZÇĞİÖŞÜ])\s*(?:blok|block)\b",
    ])
    bb = first_match([
        r"Blok/Kat/Giri(?:ş|s|þ)/BBNo\s*[:=-]?\s*[A-ZÇÐÝÖÞÜ0-9]+\s*/[^/]*//\s*(\d+)",
        r"Blok/Kat/Giriş/BBNo\s*[:=-]?\s*[A-ZÇĞİÖŞÜ0-9]+\s*/[^/]*//\s*([A-ZÇĞİÖŞÜ]?\s*\d+\s*[A-ZÇĞİÖŞÜ]?)",
        r"(?:bb|b\.b\.|bağımsız bölüm|bagimsiz bolum|bağ\.?\s*böl\.?|bag\.?\s*bol\.?)\s*(?:no|numarası|numarasi|nu|nolu)?\s*[:=-]?\s*([A-ZÇĞİÖŞÜ]?\s*\d+\s*[A-ZÇĞİÖŞÜ]?|\d+\s*/\s*[A-ZÇĞİÖŞÜ0-9]+|[A-ZÇĞİÖŞÜ]\s*/\s*\d+)",
        r"(?:iç kapı|ic kapi|içkapı|ickapi)\s*(?:no|numarası|numarasi)?\s*[:=-]?\s*([A-ZÇĞİÖŞÜ]?\s*\d+\s*[A-ZÇĞİÖŞÜ]?|\d+\s*/\s*[A-ZÇĞİÖŞÜ0-9]+|[A-ZÇĞİÖŞÜ]\s*/\s*\d+)",
        r"(?:daire|mesken)\s*(?:no|numarası|numarasi)?\s*[:=-]?\s*([A-ZÇĞİÖŞÜ]?\s*\d+\s*[A-ZÇĞİÖŞÜ]?|\d+\s*/\s*[A-ZÇĞİÖŞÜ0-9]+|[A-ZÇĞİÖŞÜ]\s*/\s*\d+)",
    ])
    bb = re.sub(r"\s+", "", bb).upper() if bb else ""
    block = re.sub(r"\s+", "", block).upper() if block else ""

    return {
        "city": city.title() if city else "",
        "district": district.title() if district else "",
        "neighborhood": neighborhood.strip(" .-") if neighborhood else "",
        "ada": ada,
        "parsel": parsel,
        "block": block,
        "bb": bb,
        "type": asset_type,
    }


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        parsed = urlparse(path)
        clean_path = parsed.path
        if clean_path == "/":
            if ROOT_HTML.exists():
                return str(ROOT_HTML)
            return str(OUTPUTS / "vizyon-cbs-prototip.html")
        root_candidate = ROOT / clean_path.lstrip("/")
        if root_candidate.exists():
            return str(root_candidate)
        output_candidate = OUTPUTS / clean_path.lstrip("/")
        if output_candidate.exists():
            return str(output_candidate)
        return str(root_candidate)

    def send_json(self, payload, status=200):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(raw)

    def send_kml(self, content, filename="vizyon-secili-parsel.kml"):
        raw = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.google-earth.kml+xml; charset=utf-8")
        self.send_header("Content-Disposition", f'inline; filename="{filename}"')
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()
        self.wfile.write(raw)

    def read_body_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        return json.loads(raw or "{}")

    def do_OPTIONS(self):
        self.send_json({"ok": True})

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self.send_json({"ok": True, "name": "Vizyon Asistan CBS API", "version": "address-options-2026-07-10"})
            return
        if parsed.path == "/api/soil/usgs-vs30":
            query = parse_qs(parsed.query)
            try:
                lat = float(query.get("lat", [""])[0])
                lng = float(query.get("lng", [""])[0])
                vs30 = read_usgs_vs30(lat, lng)
                site_class = tbdy_zemin_sinifi(vs30)
                self.send_json(
                    {
                        "status": "ok",
                        "source": "USGS Global Vs30",
                        "vs30": vs30,
                        "site_class": site_class["code"],
                        "description": site_class["description"],
                    }
                )
            except Exception as exc:
                self.send_json({"status": "error", "message": str(exc)}, 502)
            return
        if parsed.path == "/api/tkgm/parcel-by-coords":
            query = parse_qs(parsed.query)
            try:
                lat = float(query.get("lat", [""])[0])
                lng = float(query.get("lng", [""])[0])
                parcel = read_tkgm_parcel_by_coords(lat, lng)
                summary = parcel["summary"]
                try:
                    parcel["blocks"] = read_tkgm_blocks(
                        summary.get("mahalleId"),
                        summary.get("ada"),
                        summary.get("parsel"),
                    )
                except Exception as exc:
                    parcel["blocks"] = {"status": "error", "message": str(exc), "rows": []}
                self.send_json(parcel)
            except Exception as exc:
                self.send_json({"status": "error", "message": str(exc)}, 502)
            return
        if parsed.path == "/api/parcel.kml":
            query = parse_qs(parsed.query)
            try:
                lat = float(query.get("lat", [""])[0])
                lng = float(query.get("lng", [""])[0])
                parcel = read_tkgm_parcel_by_coords(lat, lng)
                summary = parcel["summary"]
                filename = f"vizyon-{summary.get('ada') or 'ada'}-{summary.get('parsel') or 'parsel'}.kml"
                self.send_kml(parcel_to_kml(parcel, lat, lng), filename)
            except Exception as exc:
                self.send_json({"status": "error", "message": str(exc)}, 502)
            return
        if parsed.path == "/api/address/options":
            params = parse_qs(parsed.query)
            kind = (params.get("kind") or ["city"])[0]
            allowed = {
                "city": ("city", {}),
                "county": ("county", {"cityId": (params.get("cityId") or [""])[0]}),
                "town": ("town", {"countyId": (params.get("countyId") or [""])[0]}),
                "village": ("village", {"townId": (params.get("townId") or [""])[0]}),
                "neighbourhood": ("neighbourhood", {"villageId": (params.get("villageId") or [""])[0]}),
                "street": ("street", {"neighbourhoodId": (params.get("neighbourhoodId") or [""])[0]}),
                "building": ("building", {"streetId": (params.get("streetId") or [""])[0]}),
                "space": ("space", {"buildingId": (params.get("buildingId") or [""])[0]}),
            }
            if kind not in allowed:
                self.send_json({"status": "error", "message": "Desteklenmeyen adres liste tipi"}, 400)
                return
            path, quick_params = allowed[kind]
            try:
                payload = quick_location(path, {k: v for k, v in quick_params.items() if v})
                self.send_json({"status": "ok", "source": "Quick Sigorta location API", "kind": kind, "result": payload})
            except Exception as exc:
                self.send_json(
                    {
                        "status": "error",
                        "source": "Quick Sigorta location API",
                        "kind": kind,
                        "message": str(exc),
                        "fallback": "İdari mahalle manuel girilebilir; İçişleri mülki idari bölümleri resmi referans olarak kontrol edilebilir.",
                    },
                    422,
                )
            return
        if parsed.path == "/api/admin-neighborhoods":
            params = parse_qs(parsed.query)
            city = (params.get("city") or [""])[0]
            district = (params.get("district") or [""])[0]
            options = admin_neighborhood_options(city, district)
            self.send_json(
                {
                    "status": "ok",
                    "source": "İçişleri Mülki İdari Bölümleri PDF/CSV",
                    "city": city,
                    "district": district,
                    "count": len(options),
                    "neighborhoods": options,
                }
            )
            return
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/lookup":
            started = time.perf_counter()
            try:
                body = self.read_body_json()
                lat = float(body["lat"])
                lng = float(body["lng"])
            except Exception:
                self.send_json({"status": "error", "message": "lat/lng zorunlu"}, 400)
                return

            result = {
                "status": "ok",
                "query": {"lat": lat, "lng": lng},
                "parcel": {
                    "status": "pending",
                    "source": "TKGM",
                    "note": "TKGM parsel ters sorgu connector/server modulu eklenecek.",
                    "metadata": body.get("metadata") or {},
                },
                "pga": {
                    "status": "pending",
                    "source": "AFAD TDTH PGA grid",
                    "note": "PGA grid dosyasi okunursa bilinear interpolation ile otomatik hesaplanacak.",
                },
                "soil": {
                    "status": "pending",
                    "source": "USGS Global Vs30",
                },
                "uavt": {
                    "status": "pending",
                    "source": "NVI Adres Sorgu",
                    "note": "Kapi no/adres ipucu ile UAVT modulu e-Devlet oturumu sonrasinda baglanacak.",
                },
                "ekb": {
                    "status": "pending",
                    "source": "BEP-TR EKB",
                    "note": "Bina kimlik no bulununca EKB sorgu modulune aktarilacak.",
                },
                "meta": {"sources_called": [], "warnings": []},
            }

            try:
                parcel = read_tkgm_parcel_by_coords(lat, lng)
                summary = parcel["summary"]
                try:
                    parcel["blocks"] = read_tkgm_blocks(
                        summary.get("mahalleId"),
                        summary.get("ada"),
                        summary.get("parsel"),
                    )
                except Exception as exc:
                    parcel["blocks"] = {"status": "error", "message": str(exc), "rows": []}
                    result["meta"]["warnings"].append(f"TKGM BB okunamadi: {exc}")
                result["parcel"] = parcel
                result["meta"]["sources_called"].append("TKGM")
            except Exception as exc:
                result["parcel"]["status"] = "error"
                result["parcel"]["message"] = str(exc)
                result["meta"]["warnings"].append(f"TKGM okunamadi: {exc}")

            try:
                vs30 = read_usgs_vs30(lat, lng)
                site_class = tbdy_zemin_sinifi(vs30)
                result["soil"] = {
                    "status": "ok",
                    "source": "USGS Global Vs30",
                    "vs30": vs30,
                    "site_class": site_class["code"],
                    "description": site_class["description"],
                    "note": "USGS Vs30 tahmini zemin sinifidir; kesin sinif icin zemin etudu gerekir.",
                }
                result["meta"]["sources_called"].append("USGS")
            except Exception as exc:
                result["soil"]["status"] = "error"
                result["soil"]["message"] = str(exc)
                result["meta"]["warnings"].append(f"USGS okunamadi: {exc}")

            afad_grid = interpolate_afad_grid(lat, lng)
            if afad_grid:
                result["pga"] = {
                    "status": afad_grid["status"],
                    "source": "PGA AKBANK.xlsx / AFAD TDTH PGA-475 grid kontrol dosyasi",
                    "return_period": 475,
                    "percentile": 50,
                    "pga_g": afad_grid["pga_g"],
                    "method": afad_grid["method"],
                    "grid_points": afad_grid["grid_points"],
                    "note": "Banka tarafindan paylasilan koordinatli PGA tablosundan hesaplandi; Resmi Gazete/AFAD kaynak dosyasi ile nihai teyit edilecek.",
                }
                result["meta"]["sources_called"].append("AFAD-grid")

            afad_ref = nearest_afad_reference(lat, lng)
            if afad_ref:
                result["pga"] = {
                    "status": "verified_reference",
                    "source": afad_ref["source"],
                    "return_period": 475,
                    "percentile": 50,
                    "pga_g": afad_ref["pga_g"],
                    "note": "Bu nokta Vizyon test kaydindaki AFAD manuel dogrulamasindan geldi.",
                }
                result["meta"]["sources_called"].append("AFAD-manuel")

            result["meta"]["lookup_latency_ms"] = round((time.perf_counter() - started) * 1000)
            append_jsonl("parcel-lookups.jsonl", result)
            self.send_json(result)
            return

        if parsed.path == "/api/afad/parse":
            body = self.read_body_json()
            value = extract_afad_pga(body.get("text") or "")
            if value is None:
                self.send_json(
                    {
                        "status": "error",
                        "message": "AFAD metninde PGA 475 degeri bulunamadi.",
                    },
                    422,
                )
                return
            payload = {
                "status": "ok",
                "source": "AFAD TDTH response parser",
                "return_period": 475,
                "pga_g": value,
            }
            append_jsonl("afad-pga-parses.jsonl", {**payload, "saved_at": time.time()})
            self.send_json(payload)
            return

        if parsed.path == "/api/tkgm/search":
            body = self.read_body_json()
            try:
                parcel = search_tkgm_parcel_text(body.get("query") or "")
                summary = parcel["summary"]
                try:
                    parcel["blocks"] = read_tkgm_blocks(
                        summary.get("mahalleId"),
                        summary.get("ada"),
                        summary.get("parsel"),
                    )
                except Exception as exc:
                    parcel["blocks"] = {"status": "error", "message": str(exc), "rows": []}
                payload = {
                    "status": "ok",
                    "query": body.get("query") or "",
                    "parcel": parcel,
                    "center": parcel.get("center"),
                }
                append_jsonl("tkgm-text-searches.jsonl", payload)
                self.send_json(payload)
            except Exception as exc:
                self.send_json({"status": "error", "message": str(exc)}, 422)
            return

        if parsed.path == "/api/address/options":
            params = parse_qs(parsed.query)
            kind = (params.get("kind") or ["city"])[0]
            allowed = {
                "city": ("city", {}),
                "county": ("county", {"cityId": (params.get("cityId") or [""])[0]}),
                "town": ("town", {"countyId": (params.get("countyId") or [""])[0]}),
                "village": ("village", {"townId": (params.get("townId") or [""])[0]}),
                "neighbourhood": ("neighbourhood", {"villageId": (params.get("villageId") or [""])[0]}),
                "street": ("street", {"neighbourhoodId": (params.get("neighbourhoodId") or [""])[0]}),
                "building": ("building", {"streetId": (params.get("streetId") or [""])[0]}),
                "space": ("space", {"buildingId": (params.get("buildingId") or [""])[0]}),
            }
            if kind not in allowed:
                self.send_json({"status": "error", "message": "Desteklenmeyen adres liste tipi"}, 400)
                return
            path, quick_params = allowed[kind]
            try:
                payload = quick_location(path, {k: v for k, v in quick_params.items() if v})
                self.send_json({"status": "ok", "source": "Quick Sigorta location API", "kind": kind, "result": payload})
            except Exception as exc:
                self.send_json(
                    {
                        "status": "error",
                        "source": "Quick Sigorta location API",
                        "kind": kind,
                        "message": str(exc),
                        "fallback": "İdari mahalle manuel girilebilir; İçişleri mülki idari bölümleri resmi referans olarak kontrol edilebilir.",
                    },
                    422,
                )
            return

        if parsed.path == "/api/soil-observations":
            body = self.read_body_json()
            body["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            append_jsonl("soil-observations.jsonl", body)
            self.send_json({"status": "ok", "saved": body})
            return

        if parsed.path == "/api/takbis/parse":
            body = self.read_body_json()
            try:
                text = read_uploaded_text(
                    body.get("filename") or "",
                    body.get("mime") or "",
                    body.get("content_base64") or "",
                )
                parsed_takbis = parse_takbis_upload_text(text)
                if not text.strip():
                    self.send_json(
                        {
                            "status": "needs_ocr",
                            "parsed": parsed_takbis,
                            "message": "TAKBİS dosyasında seçilebilir metin bulunamadı; taranmış belge için OCR/QR gerekir.",
                        },
                        422,
                    )
                    return
                message = "TAKBİS belgesinden metin okundu."
                if parsed_takbis.get("bb"):
                    message = "TAKBİS belgesinden BB/iç kapı bilgisi okundu."
                elif parsed_takbis.get("type"):
                    message = "TAKBİS belgesinden taşınmaz tipi okundu; BB no bulunamadı."
                payload = {
                    "status": "ok",
                    "source": "Vizyon TAKBİS belge okuyucu",
                    "parsed": parsed_takbis,
                    "message": message,
                    "text_sample": text[:1200],
                }
                append_jsonl("takbis-parses.jsonl", {**payload, "saved_at": time.time(), "filename": body.get("filename")})
                self.send_json(payload)
            except Exception as exc:
                self.send_json(
                    {
                        "status": "error",
                        "message": f"TAKBİS belgesi okunamadı: {exc}",
                        "parsed": {},
                    },
                    422,
                )
            return

        if parsed.path == "/api/uavt/lookup":
            body = self.read_body_json()
            quick_result = {}
            try:
                if not (body.get("address_code") and (body.get("building_code") or body.get("building_identity_no"))):
                    quick_result = try_quick_address_lookup(body)
            except Exception as exc:
                quick_result = {"ok": False, "message": f"Quick otomatik sorgu tamamlanamadı: {exc}"}

            address_code = body.get("address_code") or quick_result.get("address_code") or ""
            building_code = (
                body.get("building_code")
                or body.get("building_identity_no")
                or quick_result.get("building_code")
                or ""
            )
            response = {
                "status": "ok" if (address_code or building_code) else "needs_auth",
                "source": "Quick Sigorta Adres Kodu / NVI Adres Sorgu",
                "input": body,
                "label": "Adres kodu okundu" if address_code else "Adres kodu entegrasyonu gerekli",
                "uavt": address_code,
                "address_code": address_code,
                "building_identity_no": building_code,
                "building_code": building_code,
                "address": quick_result.get("address") or body.get("address") or "",
                "message": "Adres kodu ve bina kodu kaydedildi; bina kodu BEP-TR/EKB için bina kimlik no olarak kullanılacak." if building_code else "Adres kodu paketi kaydedildi; Quick/NVI sonucu gelince bina kodu, EKB için bina kimlik no olarak kullanılacak.",
                "quick_message": quick_result.get("message", ""),
                "quick_address_url": "https://quicksigorta.com/adres-kodu-sorgulama",
                "nvi_url": "https://adres.nvi.gov.tr/VatandasIslemleri/AdresSorgu",
                "next_step": "Adres kodu sorgusundan bina kodu gelirse BEP-TR/EKB sorgusuna otomatik geçilecek.",
            }
            append_jsonl("uavt-requests.jsonl", response)
            self.send_json(response)
            return

        self.send_json({"status": "error", "message": "Endpoint bulunamadi"}, 404)


def main():
    host = "0.0.0.0"
    port = int(os.environ.get("PORT", "8787"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Vizyon CBS server: http://127.0.0.1:{port} veya ayni agda http://<bilgisayar-ip>:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
