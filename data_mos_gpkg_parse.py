import requests
import geopandas as gpd
from shapely.geometry import shape
import time

API_KEY = "f24ed422-b3ba-4a49-9192-428311678fc3"
dataset_id = 2263
base_url = f"https://apidata.mos.ru/v1/datasets/{dataset_id}/features"
step = 10  # Можешь регулировать

# Запрашиваем количество записей
count_url = f"https://apidata.mos.ru/v1/datasets/{dataset_id}/count?api_key={API_KEY}"
total_records = requests.get(count_url).json()
print("Всего записей:", total_records)

features = []

for skip in range(0, total_records, step):
    params = {
        '$top': step,
        '$skip': skip,
        'api_key': API_KEY
    }

    try:
        response = requests.get(base_url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        for feature in data['features']:
            geometry = shape(feature['geometry'])
            properties = feature.get('properties', {})
            attributes = properties.get('Attributes', properties)  # Фикс тут!
            features.append({"geometry": geometry, **attributes})

        print(f"Загружено записей: {len(features)} из {total_records}")

    except requests.exceptions.RequestException as e:
        print(f"Ошибка: {e}. Повтор через 10 секунд.")
        time.sleep(10)
        continue

    time.sleep(2)

# Создаем GeoDataFrame
gdf = gpd.GeoDataFrame(features, crs="EPSG:4326")

# Сохраняем в GeoPackage
gdf.to_file("educational_institutions.gpkg", driver="GPKG")

print("GeoPackage успешно создан!")
