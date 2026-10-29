"""
Сбор JSON по участкам («Проверка новостроек») с наш.дом.рф без браузера —
замена Land_seleniun.py. Работает так же, как cards_curl.py, и использует
его функции (разбор curl.txt, чтение Excel, сессия, паузы, логи),
поэтому оба файла должны лежать в одной папке.

Подготовка: тот же curl.txt, что и для cards_curl.py (домен один и тот же).
Если cookies протухли — откройте любую страницу наш.дом.рф в Chrome и заново
скопируйте «Copy as cURL (bash)» в curl.txt.

Логи: logs/land_<дата-время>/ — run.log, report.csv, failed_ids.txt, html/.

Запуск:
  python land_curl.py --limit 3        # пробный прогон
  python land_curl.py                  # все ID из domrf.xlsx -> json_land/<ID>.txt
  python land_curl.py --force          # перекачать уже сохранённые
  python land_curl.py --ids-file logs/land_…/failed_ids.txt   # только упавшие
"""

from __future__ import annotations

import json
import sys

import cards_curl as cc

OUTPUT_FOLDER = cc.HERE / "json_land"
BASE_URL = "https://xn--80az8a.xn--d1aqf.xn--p1ai/сервисы/проверка_новостроек/"


def extract_land_json(html: str) -> str | None:
    """Как в Land_seleniun.py: первый <script ... json"> … </script>.
    Плюс проверка, что это валидный JSON; запасной вариант — __NEXT_DATA__."""
    start = html.find('json">')
    if start != -1:
        end = html.find("</script>", start)
        if end != -1:
            text = html[start + len('json">'):end].strip()
            try:
                json.loads(text)
                return text
            except ValueError:
                pass
    return cc.extract_json(html)


def classify(html: str) -> str:
    if extract_land_json(html):
        return "ok"
    return cc.classify_page(html)


def main(argv=None):
    return cc.run(argv, name="land", doc=__doc__, out=OUTPUT_FOLDER, base_url=BASE_URL,
                  extractor=extract_land_json, classifier=classify)


if __name__ == "__main__":
    sys.exit(main())
