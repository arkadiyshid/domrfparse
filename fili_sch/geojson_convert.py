import json
import sys
import os

# ============================================================
# Настройка
# ============================================================
INPUT_FILE  = "fili_sch/ТЦ.json"
OUTPUT_FILE = "fili_sch/projects_converted.geojson"
# ============================================================


# ────────────────────────────────────────────────────────────
# Чтение файла
# ────────────────────────────────────────────────────────────

def load_json(filepath: str):
    """Читает JSON-файл и возвращает сырые данные."""

    if not os.path.exists(filepath):
        print(f"[Ошибка] Файл не найден: '{filepath}'")
        sys.exit(1)

    if not os.path.isfile(filepath):
        print(f"[Ошибка] '{filepath}' не является файлом.")
        sys.exit(1)

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"[Ошибка] Невалидный JSON:")
        print(f"         Строка {e.lineno}, позиция {e.colno}: {e.msg}")
        sys.exit(1)
    except OSError as e:
        print(f"[Ошибка] Не удалось прочитать файл: {e}")
        sys.exit(1)


# ────────────────────────────────────────────────────────────
# Извлечение массива features из структуры
# ────────────────────────────────────────────────────────────

def extract_features(raw) -> list:
    """
    Поддерживаемые структуры (от частного к общему):

    1. { "data": { "type": "FeatureCollection",       ← ваш случай
                   "features": [...] },
         "status": 200, "error": null }

    2. { "type": "FeatureCollection",                 ← чистый GeoJSON
         "features": [...] }

    3. { "data": [...] }                              ← объект с массивом

    4. [...]                                          ← корневой массив

    5. { "type": "Feature", ... }                     ← одиночный Feature
    """

    # ── Случай 4: корневой массив ─────────────────────────────
    if isinstance(raw, list):
        print(f"[Инфо] Структура: корневой массив [{len(raw)} записей]")
        return raw

    if not isinstance(raw, dict):
        print(f"[Ошибка] Неожиданный тип данных: {type(raw).__name__}")
        sys.exit(1)

    # ── Случай 1: { "data": { "features": [...] } } ───────────
    data_field = raw.get("data")
    if isinstance(data_field, dict):
        features = data_field.get("features")
        if isinstance(features, list):
            # Печатаем мета-информацию верхнего уровня
            meta_top = {k: v for k, v in raw.items() if k != "data"}
            if meta_top:
                print(f"[Инфо] Метаданные ответа: {meta_top}")

            # Печатаем мета-информацию из data
            meta_data = {
                k: v for k, v in data_field.items() if k != "features"
            }
            if meta_data:
                print(f"[Инфо] Метаданные data:   {meta_data}")

            print(
                f"[Инфо] Структура: data → FeatureCollection "
                f"[{len(features)} features]"
            )
            return features

        # data есть, но features внутри нет — ищем любой список
        for key, value in data_field.items():
            if isinstance(value, list):
                print(
                    f"[Инфо] Структура: data → '{key}' [{len(value)} записей]"
                )
                return value

    # ── Случай 2: { "type": "FeatureCollection", "features": [...] } ──
    if raw.get("type") == "FeatureCollection":
        features = raw.get("features", [])
        print(
            f"[Инфо] Структура: FeatureCollection [{len(features)} features]"
        )
        return features

    # ── Случай 3: { "data": [...] } или другой ключ с массивом ──
    ARRAY_KEYS = ["data", "features", "items", "results", "records", "rows"]
    for key in ARRAY_KEYS:
        if key in raw and isinstance(raw[key], list):
            print(
                f"[Инфо] Структура: объект → ключ '{key}' "
                f"[{len(raw[key])} записей]"
            )
            return raw[key]

    # Ищем любой вложенный список
    for key, value in raw.items():
        if isinstance(value, list):
            print(
                f"[Инфо] Структура: объект → автоключ '{key}' "
                f"[{len(value)} записей]"
            )
            return value

    # ── Случай 5: одиночный Feature ──────────────────────────
    if raw.get("type") == "Feature":
        print("[Инфо] Структура: одиночный Feature")
        return [raw]

    print("[Ошибка] Не удалось найти массив записей в JSON.")
    print(f"         Ключи верхнего уровня: {list(raw.keys())}")
    sys.exit(1)


# ────────────────────────────────────────────────────────────
# Валидация геометрии
# ────────────────────────────────────────────────────────────

def is_valid_point(coords) -> bool:
    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        return False
    return all(isinstance(v, (int, float)) for v in coords[:2])


def is_valid_ring(ring) -> bool:
    """Кольцо полигона: ≥4 точек, первая == последняя."""
    if not isinstance(ring, list) or len(ring) < 4:
        return False
    if not all(is_valid_point(pt) for pt in ring):
        return False
    if ring[0][:2] != ring[-1][:2]:
        return False
    return True


def is_valid_polygon(coords) -> bool:
    """Polygon: список колец."""
    if not isinstance(coords, list) or len(coords) == 0:
        return False
    return all(is_valid_ring(ring) for ring in coords)


def is_valid_multipolygon(coords) -> bool:
    """
    MultiPolygon: список полигонов.
    coords[i]       — полигон (список колец)
    coords[i][j]    — кольцо  (список точек)
    coords[i][j][k] — точка   [lon, lat]
    """
    if not isinstance(coords, list) or len(coords) == 0:
        return False
    return all(is_valid_polygon(polygon) for polygon in coords)


VALIDATORS = {
    "Point"        : is_valid_point,
    "Polygon"      : is_valid_polygon,
    "MultiPolygon" : is_valid_multipolygon,
}


def validate_geometry(geometry, index: int) -> bool:
    if not isinstance(geometry, dict):
        print(
            f"[Предупреждение] #{index}: "
            f"geometry не является объектом — пропущено."
        )
        return False

    geo_type = geometry.get("type", "")
    coords   = geometry.get("coordinates")

    if geo_type not in VALIDATORS:
        print(
            f"[Предупреждение] #{index}: "
            f"неподдерживаемый тип геометрии '{geo_type}' — пропущено."
        )
        return False

    if not VALIDATORS[geo_type](coords):
        print(
            f"[Предупреждение] #{index}: "
            f"невалидные координаты для {geo_type} — пропущено."
        )
        return False

    return True


# ────────────────────────────────────────────────────────────
# Построение Feature
# ────────────────────────────────────────────────────────────

# Служебные поля верхнего уровня Feature, не нужные в properties
FEATURE_SERVICE_KEYS = {"type", "id", "bbox", "geometry", "properties"}


def build_feature(record: dict, index: int) -> dict | None:
    """
    Строит чистый GeoJSON Feature из записи вашего API.

    Структура входной записи:
    {
      "type"      : "Feature",
      "id"        : 78483,
      "bbox"      : [37.567..., 55.839..., 37.570..., 55.841...],
      "geometry"  : {
          "type"        : "MultiPolygon",
          "coordinates" : [[[[lon, lat], ...]]]
      },
      "properties": {
          "projectId"   : 78483,
          "name"        : "...",
          "lat"         : 55.84...,
          "lng"         : 37.56...,
          ...
      }
    }
    """

    # ── Геометрия ─────────────────────────────────────────────
    geometry = record.get("geometry")

    # Если geometry пустой/None — пробуем собрать Point из lat/lng
    if not geometry:
        geometry = _point_from_latlon(record, index)

    if geometry is None:
        return None

    if not validate_geometry(geometry, index):
        return None

    # ── Properties ────────────────────────────────────────────
    # Берём из поля properties; если его нет — из самой записи
    raw_props = record.get("properties")

    if isinstance(raw_props, dict):
        properties = dict(raw_props)          # копируем все поля как есть
    else:
        properties = {
            k: v for k, v in record.items()
            if k not in FEATURE_SERVICE_KEYS
        }

    return {
        "type"      : "Feature",
        "geometry"  : geometry,
        "properties": properties,
    }


def _point_from_latlon(record: dict, index: int) -> dict | None:
    """
    Запасной вариант: ищем lat/lng в properties или в самой записи.
    Пробуем разные названия полей долготы: lng, lon, longitude.
    """
    props = record.get("properties") or {}

    lat = props.get("lat") or record.get("lat")
    lon = (
        props.get("lng") or props.get("lon") or props.get("longitude")
        or record.get("lng") or record.get("lon")
    )

    if lat is None or lon is None:
        print(
            f"[Предупреждение] #{index}: "
            f"нет геометрии и нет lat/lng — пропущено."
        )
        return None

    try:
        return {"type": "Point", "coordinates": [float(lon), float(lat)]}
    except (TypeError, ValueError):
        print(
            f"[Предупреждение] #{index}: "
            f"невалидные координаты lat={lat!r}, lng={lon!r} — пропущено."
        )
        return None


# ────────────────────────────────────────────────────────────
# Построение FeatureCollection
# ────────────────────────────────────────────────────────────

def convert(records: list) -> dict:
    features  = []
    skipped   = 0
    type_stat = {}

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            print(
                f"[Предупреждение] #{index}: "
                f"ожидался dict, получено {type(record).__name__} — пропущено."
            )
            skipped += 1
            continue

        feature = build_feature(record, index)

        if feature is not None:
            geo_type = feature["geometry"]["type"]
            type_stat[geo_type] = type_stat.get(geo_type, 0) + 1
            features.append(feature)
        else:
            skipped += 1

    # Статистика
    print()
    print("─" * 48)
    print(f"  Всего записей      : {len(records)}")
    print(f"  Успешно            : {len(features)}")
    print(f"  Пропущено          : {skipped}")
    if type_stat:
        print("  Типы геометрии:")
        for geo_type, count in sorted(type_stat.items()):
            print(f"    {geo_type:<16} : {count}")
    print("─" * 48)

    return {"type": "FeatureCollection", "features": features}


# ────────────────────────────────────────────────────────────
# Сохранение
# ────────────────────────────────────────────────────────────

def save(geojson: dict, filepath: str) -> None:
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(geojson, f, ensure_ascii=False, indent=2)
        print(f'\n[Успех] Файл записан: "{filepath}"')
    except OSError as e:
        print(f"[Ошибка] Не удалось записать файл: {e}")
        sys.exit(1)


# ────────────────────────────────────────────────────────────
# Точка входа
# ────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 48)
    print("  JSON → GeoJSON  (MultiPolygon / API формат)")
    print("=" * 48)
    print(f'  Входной файл  : "{INPUT_FILE}"')
    print(f'  Выходной файл : "{OUTPUT_FILE}"')
    print("=" * 48)
    print()

    raw      = load_json(INPUT_FILE)
    records  = extract_features(raw)
    geojson  = convert(records)
    save(geojson, OUTPUT_FILE)


if __name__ == "__main__":
    main()