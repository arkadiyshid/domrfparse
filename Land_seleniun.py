import os
import time
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options


options = Options()
# options.add_argument("--headless=new")  # Активирует Headless-режим
# options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)")

# === ПАРАМЕТРЫ ===

EXCEL_PATH = os.path.expanduser("~/Desktop/domrf.xlsx")  # Путь к Excel-файлу
SHEET_NAME = "sheet1"                                  # Имя листа
ID_COLUMN = "ID"                                       # Название столбца с ID объектов
OUTPUT_FOLDER = os.path.expanduser("~/Desktop/json_land")  # Куда сохранять JSON-файлы
BASE_URL = "https://xn--80az8a.xn--d1aqf.xn--p1ai/%D1%81%D0%B5%D1%80%D0%B2%D0%B8%D1%81%D1%8B/%D0%BF%D1%80%D0%BE%D0%B2%D0%B5%D1%80%D0%BA%D0%B0_%D0%BD%D0%BE%D0%B2%D0%BE%D1%81%D1%82%D1%80%D0%BE%D0%B5%D0%BA/"

# === ПОДГОТОВКА ===

# Создаём папку, если не существует
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Читаем Excel
df = pd.read_excel(EXCEL_PATH, sheet_name=SHEET_NAME)
ids = df[ID_COLUMN].dropna().astype(str).tolist()

# Настройка Selenium
driver = webdriver.Chrome(options=options)

# === ОСНОВНОЙ ЦИКЛ ===

for obj_id in ids:
    url = BASE_URL + obj_id
    print(f"🔗 Обрабатываем: {url}")
    
    try:
        driver.get(url)
        time.sleep(1)  # Подождать загрузку

        html = driver.page_source

        # Извлекаем JSON из HTML
        start_marker = 'json">'
        end_marker = '</script>'
        start_index = html.find(start_marker)
        end_index = html.find(end_marker, start_index)

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
