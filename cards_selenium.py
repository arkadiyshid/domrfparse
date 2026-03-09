import os
import time
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By

# === ПАРАМЕТРЫ ===

# EXCEL_PATH = os.path.expanduser("~/Desktop/domrf.xlsx")  # Путь к Excel-файлу
EXCEL_PATH = "domrf.xlsx"  # Путь к Excel-файлу
SHEET_NAME = "sheet1"                                  # Имя листа
ID_COLUMN = "ID"                                       # Название столбца с ID объектов
# OUTPUT_FOLDER = os.path.expanduser("~/Desktop/json_card")  # Куда сохранять JSON-файлы
OUTPUT_FOLDER = "json_card"  # Куда сохранять JSON-файлы
BASE_URL = "https://xn--80az8a.xn--d1aqf.xn--p1ai/сервисы/каталог-новостроек/объект/"

# === ПОДГОТОВКА ===

# Создаём папку, если не существует
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Читаем Excel
df = pd.read_excel(EXCEL_PATH, sheet_name=SHEET_NAME)
ids = df[ID_COLUMN].dropna().astype(str).tolist()

# Настройка Selenium
driver = webdriver.Chrome()

# === ОСНОВНОЙ ЦИКЛ ===

for obj_id in ids:
    url = BASE_URL + obj_id
    print(f"🔗 Обрабатываем: {url}")
    
    try:
        driver.get(url)
        time.sleep(1.5)  # Подождать загрузку

        html = driver.page_source

        # Извлекаем JSON из HTML
        start_marker = 'json">{"p'
        end_marker = 'true}</script>'
        start_index = html.find(start_marker)-3
        end_index = html.find(end_marker, start_index)+5

        if start_index != -1 and end_index != -1:
            json_text = html[start_index + len(start_marker):end_index].strip()

            # Сохраняем JSON в файл
            filename = f"{obj_id}.txt"
            file_path = os.path.join(OUTPUT_FOLDER, filename)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(json_text)

            print(f"✅ JSON сохранён: {file_path}")
        else:
            print(f"⚠️ JSON не найден на странице для ID {obj_id}")

    except Exception as e:
        print(f"❌ Ошибка при обработке ID {obj_id}: {e}")

# Завершаем
driver.quit()
print("🎉 Обработка завершена.")
