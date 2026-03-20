import json
import time
import logging
from pathlib import Path

import httpx

# ==================== Настройки ====================

BASE_URL = "https://api.investmoscow.ru"

MAP_ENDPOINT = (
    f"{BASE_URL}/investmoscow/investment-map/v1/investmentplatform/getall"
)
DETAIL_ENDPOINT = (
    f"{BASE_URL}/common/industry-registry/v1/technoparkexternal/get"
)

MAP_REQUEST_BODY = {
    "districtCodes": [],
    "types": ["Technopark"],
}

MAP_FILE = Path("промышленные_кластеры\map.json")
DATA_DIR = Path("промышленные_кластеры\data")

REQUEST_TIMEOUT = 30          # секунды
DELAY_BETWEEN_REQUESTS = 0.5  # секунды (чтобы не перегружать сервер)

# ==================== Логирование ====================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ==================== Функции ====================


def fetch_map(client: httpx.Client) -> dict:
    """Получает общую карту объектов через POST-запрос."""
    logger.info("Запрашиваем карту объектов...")

    response = client.post(
        MAP_ENDPOINT,
        json=MAP_REQUEST_BODY,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    data = response.json()
    logger.info("Карта получена. Записей: %d", len(data) if isinstance(data, list) else 1)
    return data


def save_json(path: Path, data: dict | list) -> None:
    """Сохраняет данные в JSON-файл."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    logger.debug("Файл сохранён: %s", path)


def extract_ids(data: dict | list) -> list[str | int]:
    """
    Рекурсивно обходит структуру данных и собирает все значения ключа 'id'.
    Возвращает список уникальных идентификаторов.
    """
    ids: list[str | int] = []

    if isinstance(data, dict):
        if "id" in data and data["id"] is not None:
            ids.append(data["id"])
        for value in data.values():
            ids.extend(extract_ids(value))

    elif isinstance(data, list):
        for item in data:
            ids.extend(extract_ids(item))

    # Убираем дубликаты, сохраняя порядок
    seen: set = set()
    unique_ids = []
    for item_id in ids:
        if item_id not in seen:
            seen.add(item_id)
            unique_ids.append(item_id)

    return unique_ids


def fetch_detail(client: httpx.Client, object_id: str | int) -> dict | None:
    """Получает детальную информацию об объекте по его ID."""
    try:
        response = client.get(
            DETAIL_ENDPOINT,
            params={"id": object_id},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    except httpx.HTTPStatusError as error:
        logger.warning(
            "HTTP-ошибка для ID %s: %s %s",
            object_id,
            error.response.status_code,
            error.response.text[:100],
        )
    except httpx.RequestError as error:
        logger.warning("Ошибка запроса для ID %s: %s", object_id, error)

    return None


def fetch_all_details(
    client: httpx.Client,
    ids: list[str | int],
) -> dict[str, dict]:
    """
    Получает детальные данные для каждого ID и сохраняет их в папку data/.
    Возвращает словарь {id: данные}.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    results: dict[str, dict] = {}
    total = len(ids)

    for index, object_id in enumerate(ids, start=1):
        logger.info("Обрабатываем ID %s (%d / %d)...", object_id, index, total)

        detail = fetch_detail(client, object_id)

        if detail is not None:
            file_path = DATA_DIR / f"{object_id}.json"
            save_json(file_path, detail)
            results[str(object_id)] = detail
            logger.info("  ✓ Сохранено: %s", file_path)
        else:
            logger.warning("  ✗ Данные для ID %s не получены, пропускаем.", object_id)

        # Пауза между запросами
        if index < total:
            time.sleep(DELAY_BETWEEN_REQUESTS)

    return results


# ==================== Точка входа ====================


def main() -> None:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; InvestMoscow-Parser/1.0)",
    }

    with httpx.Client(headers=headers, verify =r"C:\Users\shidlovskiyaf\combined_ca.pem") as client:

        # Шаг 1: Получаем карту объектов
        map_data = fetch_map(client)

        # Шаг 2: Сохраняем карту в map.json
        save_json(MAP_FILE, map_data)
        logger.info("Карта сохранена: %s", MAP_FILE)

        # Шаг 3: Извлекаем все ID
        ids = extract_ids(map_data)
        logger.info("Найдено уникальных ID: %d", len(ids))

        if not ids:
            logger.warning("ID не найдены. Проверьте структуру ответа в map.json.")
            return

        # Шаг 4: Получаем детальные данные и сохраняем в data/
        results = fetch_all_details(client, ids)

        # Итог
        logger.info(
            "Готово! Успешно обработано: %d / %d объектов.",
            len(results),
            len(ids),
        )


if __name__ == "__main__":
    main()