import requests
import json
import time

API_KEY = "f24ed422-b3ba-4a49-9192-428311678fc3"
dataset_id = 2263
base_url = f"https://apidata.mos.ru/v1/datasets/{dataset_id}/features"
step = 10
features = []

# Узнаём общее количество записей
count_url = f"https://apidata.mos.ru/v1/datasets/{dataset_id}/count?api_key={API_KEY}"
total_records = requests.get(count_url).json()
print("Всего записей:", total_records)

# Загружаем данные частями по 1000 записей
for skip in range(0, total_records, step):
    params = {
        '$top': step,
        '$skip': skip,
        'api_key': API_KEY
    }
    response = requests.get(base_url, params=params)
    if response.status_code == 200:
        data = response.json()
        features.extend(data['features'])
        print(f"Загружено записей: {len(features)}")
    else:
        print("Ошибка при загрузке данных:", response.status_code)
        break
    time.sleep(1)  # чтобы не превышать лимиты запросов

# Формируем итоговый GeoJSON
geojson = {
    "type": "FeatureCollection",
    "features": features
}

# Сохраняем в файл
with open("educational_institutions.geojson", "w", encoding='utf-8') as f:
    json.dump(geojson, f, ensure_ascii=False, indent=4)

print("GeoJSON успешно создан!")
