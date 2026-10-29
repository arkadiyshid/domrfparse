"""
Сбор JSON-карточек с наш.дом.рф БЕЗ браузера: curl_cffi + cookies из вашего
обычного Chrome. Работает одинаково на macOS и Windows.

Идея: данные уже лежат в HTML страницы (<script id="__NEXT_DATA__">), браузер
нужен только чтобы пройти анти-бот. Проходите его вы сами в обычном Chrome,
а скрипт берёт оттуда cookies и заголовки и делает запросы через curl_cffi,
который на сетевом уровне (TLS/HTTP2) выглядит как настоящий Chrome.

Установка:
  python -m pip install curl_cffi pandas openpyxl

Подготовка перед каждым запуском (1 минута):
  1. Откройте в обычном Chrome любую карточку, например
     https://наш.дом.рф/сервисы/каталог-новостроек/объект/24248
  2. F12 (Mac: Cmd+Option+I) → вкладка Network (Сеть) → обновите страницу (F5).
  3. Первая строка в списке (сама страница, тип document) → правый клик →
     Copy → Copy as cURL (bash). На Windows — именно «(bash)», не «(cmd)».
  4. Вставьте в файл curl.txt рядом со скриптом и сохраните.

Логи: каждый запуск пишет папку logs/cards_<дата-время>/:
  run.log         подробный журнал (ошибки с traceback)
  report.csv      по строке на каждый ID: статус, HTTP-код, размер, время, ошибка
  failed_ids.txt  что не скачалось — перезапуск: --ids-file <путь>
  html/<ID>.html  сохранённая страница для каждого проблемного ID

Запуск:
  python cards_curl.py --limit 3        # пробный прогон
  python cards_curl.py                  # все ID из domrf.xlsx
  python cards_curl.py --ids 24248 26466
  python cards_curl.py --force          # перекачать уже сохранённые

Если «Доступ заблокирован» — cookies протухли или привязаны к чему-то ещё:
обновите страницу в Chrome, заново скопируйте cURL в curl.txt, повторите.

Корпоративная сеть с подменой сертификатов (CERTIFICATE_VERIFY_FAILED, curl: (60)):
  на Windows скрипт сам добавляет сертификаты из хранилища Windows (там лежит
  корневой сертификат компании). Если не помогло — укажите его явно:
  python cards_curl.py --ca путь\\к\\корпоративному_сертификату.cer
"""

from __future__ import annotations

import argparse
import csv
import logging
import json
import random
import re
import shlex
import sys
import time
from urllib.parse import quote
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXCEL_PATH = HERE / "domrf.xlsx"
SHEET_NAME = "sheet1"
ID_COLUMN = "ID"
OUTPUT_FOLDER = HERE / "json_card"
CURL_FILE = HERE / "curl.txt"
BASE_URL = "https://xn--80az8a.xn--d1aqf.xn--p1ai/сервисы/каталог-новостроек/объект/"
IMPERSONATE = "chrome"          # последний поддерживаемый профиль Chrome в curl_cffi

DELAY_RANGE = (0.5, 2.1)        # пауза между объектами, сек
LONG_PAUSE_EVERY = 20
LONG_PAUSE_RANGE = (3.0, 7.0)
TIMEOUT = 30

BLOCK_MARKERS = ["доступ заблокирован", "access denied", "доступ к запрашиваемому ресурсу"]
CAPTCHA_MARKERS = ["captcha", "smartcaptcha", "g-recaptcha", "hcaptcha",
                   "проверка, что вы не робот", "я не робот"]

# заголовки, которые curl_cffi выставляет сам (или которые нельзя переносить)
_SKIP_HEADERS = {"host", "content-length", "accept-encoding", "connection", "priority"}


# === разбор «Copy as cURL» ===

def parse_curl(text: str) -> tuple[dict, dict]:
    """Из команды 'Copy as cURL (bash)' достаёт (headers, cookies)."""
    if '^"' in text:
        raise ValueError("curl.txt в формате «Copy as cURL (cmd)». Скопируйте «Copy as cURL (bash)».")
    text = text.replace("\\\r\n", " ").replace("\\\n", " ")  # переносы строк с '\'
    try:
        tokens = shlex.split(text)
    except ValueError as e:
        raise ValueError(f"Не удалось разобрать curl.txt ({e}). Скопируйте именно "
                         "«Copy as cURL (bash)», а не «(cmd)».") from e

    headers, cookie_str = {}, ""
    it = iter(range(len(tokens)))
    for i in it:
        t = tokens[i]
        if t in ("-H", "--header") and i + 1 < len(tokens):
            name, _, value = tokens[i + 1].partition(":")
            name, value = name.strip(), value.strip()
            if name.lower() == "cookie":
                cookie_str = value
            elif name and name.lower() not in _SKIP_HEADERS:
                headers[name] = value
            next(it, None)
        elif t in ("-b", "--cookie") and i + 1 < len(tokens):
            cookie_str = tokens[i + 1]
            next(it, None)
        elif t in ("-A", "--user-agent") and i + 1 < len(tokens):
            headers["user-agent"] = tokens[i + 1]
            next(it, None)

    cookies = {}
    for part in cookie_str.split(";"):
        k, sep, v = part.strip().partition("=")
        if sep and k:
            cookies[k] = v
    return headers, cookies


# === разбор страницы ===

_NEXT_DATA_RE = re.compile(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S | re.I)


def extract_json(html: str) -> str | None:
    """JSON из <script id="__NEXT_DATA__">; fallback — маркеры из cards_selenium_1.py."""
    candidates = []
    m = _NEXT_DATA_RE.search(html)
    if m:
        candidates.append(m.group(1).strip())
    pos = html.find('json">{"p')
    if pos != -1:
        end = html.find("true}</script>", pos)
        if end != -1:
            candidates.append(html[pos + 6:end + 5].strip())
    for text in candidates:
        try:
            json.loads(text)
            return text
        except ValueError:
            pass
    return None


def classify_page(html: str) -> str:
    """'ok' | 'blocked' | 'captcha'"""
    if extract_json(html):
        return "ok"
    low = html.lower()
    if any(m in low for m in BLOCK_MARKERS):
        return "blocked"
    if any(m in low for m in CAPTCHA_MARKERS):
        return "captcha"
    return "ok"


def normalize_id(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, float):
        if value != value or not value.is_integer():
            return None
        value = int(value)
    s = str(value).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s if s.isdigit() else None


def load_ids(excel_path=EXCEL_PATH, sheet=SHEET_NAME, column=ID_COLUMN) -> list[str]:
    import pandas as pd
    seen, ids = set(), []
    for v in pd.read_excel(excel_path, sheet_name=sheet)[column].tolist():
        nid = normalize_id(v)
        if nid and nid not in seen:
            seen.add(nid)
            ids.append(nid)
    return ids


# === логирование ===

log = logging.getLogger("domrf")
LOG_DIR = HERE / "logs"
RETRIES = 2                     # повторов при сетевой ошибке / HTTP 5xx
RETRY_BACKOFF = 5.0             # сек * номер попытки


def setup_logging(name: str, log_dir: Path = LOG_DIR, verbose: bool = False) -> Path:
    """Папка запуска logs/<name>_<дата-время>/ с run.log; в консоль — кратко,
    в файл — всё, включая traceback-и."""
    run_dir = Path(log_dir) / f"{name}_{datetime.now():%Y-%m-%d_%H-%M-%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    log.setLevel(logging.DEBUG)
    log.propagate = False
    fh = logging.FileHandler(run_dir / "run.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(fh)
    log.addHandler(ch)
    return run_dir


REPORT_FIELDS = ["n", "id", "status", "http", "bytes", "seconds", "attempts", "url", "file", "error"]


class Report:
    """report.csv (по строке на каждый ID), failed_ids.txt, html/ проблемных страниц."""

    def __init__(self, run_dir: Path | None):
        self.run_dir = run_dir
        self.rows: list[dict] = []
        self._f = None
        if run_dir:
            # utf-8-sig — чтобы Excel сразу открыл кириллицу
            self._f = open(run_dir / "report.csv", "w", encoding="utf-8-sig", newline="")
            self._w = csv.DictWriter(self._f, fieldnames=REPORT_FIELDS, delimiter=";")
            self._w.writeheader()

    def add(self, **row):
        self.rows.append(row)
        if self._f:
            self._w.writerow({k: row.get(k, "") for k in REPORT_FIELDS})
            self._f.flush()  # отчёт не теряется, даже если процесс убьют

    def save_html(self, obj_id: str, html: str) -> str:
        if not self.run_dir or not html:
            return ""
        d = self.run_dir / "html"
        d.mkdir(exist_ok=True)
        p = d / f"{obj_id}.html"
        p.write_text(html, encoding="utf-8")
        return str(p)

    def close(self, not_processed: list[str]):
        if self._f:
            self._f.close()
        failed = [r["id"] for r in self.rows if r["status"] in ("no_json", "error", "blocked")]
        failed += not_processed
        if self.run_dir:
            (self.run_dir / "failed_ids.txt").write_text("\n".join(failed) + ("\n" if failed else ""),
                                                         encoding="utf-8")
        return failed


# === сбор ===

def system_ca_bundle(dest_dir: Path) -> str | None:
    """Собирает файл сертификатов: стандартные (certifi) + сертификаты из системного
    хранилища. На корпоративном Windows там лежит корневой сертификат компании,
    которым прокси подписывает HTTPS — Chrome ему доверяет, а curl_cffi без этого нет."""
    import ssl
    pems, n_sys = [], 0
    if sys.platform == "win32":
        for store in ("ROOT", "CA"):
            try:
                for cert, encoding, _trust in ssl.enum_certificates(store):
                    if encoding == "x509_asn":
                        pems.append(ssl.DER_cert_to_PEM_cert(cert))
                        n_sys += 1
            except Exception as e:
                log.debug("Хранилище Windows %s не прочиталось: %s", store, e)
    elif sys.platform == "darwin":
        import subprocess
        for kc in ("/Library/Keychains/System.keychain",
                   "/System/Library/Keychains/SystemRootCertificates.keychain"):
            r = subprocess.run(["security", "find-certificate", "-a", "-p", kc],
                               capture_output=True, text=True)
            if r.returncode == 0 and "BEGIN CERTIFICATE" in r.stdout:
                pems.append(r.stdout)
                n_sys += r.stdout.count("BEGIN CERTIFICATE")
    if not n_sys:
        return None
    try:
        import certifi
        pems.insert(0, Path(certifi.where()).read_text(encoding="utf-8"))
    except Exception:
        pass
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / "ca_bundle.pem"
    path.write_text("\n".join(pems), encoding="utf-8")
    log.info("🔐 Сертификаты: стандартные + %d из системного хранилища (%s)", n_sys, path)
    return str(path)


def is_cert_error(e: Exception) -> bool:
    text = f"{type(e).__name__} {e}"
    return "Certificate" in text or "certificate" in text or "curl: (60)" in text


CERT_HINT = ("Ошибка сертификата: HTTPS проверяет корпоративный прокси со своим сертификатом. "
             "Скрипт пробует взять его из хранилища Windows автоматически; если не вышло — "
             "выгрузите корневой сертификат компании (Chrome → замок у адреса → Сертификат → "
             "Путь сертификации → верхний → Экспорт, Base64 .cer) и запустите с --ca путь\\к\\файлу.cer")


def make_session(headers: dict, cookies: dict, ca: str | None = None, impersonate=IMPERSONATE):
    from curl_cffi import requests as creq

    s = creq.Session(impersonate=impersonate, timeout=TIMEOUT)
    if ca:
        s.verify = ca
    s.headers.update(headers)
    for k, v in cookies.items():
        s.cookies.set(k, v)
    return s


def fetch(session, url: str, headers: dict, obj_id: str, retries: int, sleep):
    """GET с повторами при сетевых ошибках и HTTP 5xx. -> (response, попыток)"""
    for attempt in range(1, retries + 2):
        try:
            r = session.get(url, headers=headers)
        except Exception as e:
            if attempt > retries or is_cert_error(e):  # сертификат повтором не починить
                raise
            wait = RETRY_BACKOFF * attempt
            log.warning("ID %s: сетевая ошибка (попытка %d/%d): %s: %s — повтор через %.0f с",
                        obj_id, attempt, retries + 1, type(e).__name__, e, wait)
            sleep(wait)
            continue
        if r.status_code >= 500 and attempt <= retries:
            wait = RETRY_BACKOFF * attempt
            log.warning("ID %s: HTTP %d (попытка %d/%d) — повтор через %.0f с",
                        obj_id, r.status_code, attempt, retries + 1, wait)
            sleep(wait)
            continue
        return r, attempt


def scrape(session, ids, out_dir: Path, base_url=BASE_URL, force=False,
           delay_range=None, sleep=time.sleep, extractor=None, classifier=None,
           run_dir: Path | None = None, retries: int | None = None) -> dict:
    extractor = extractor or extract_json
    classifier = classifier or classify_page
    delay_range = delay_range if delay_range is not None else DELAY_RANGE
    retries = RETRIES if retries is None else retries
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = Report(run_dir)
    stats = {"saved": 0, "skipped": 0, "no_json": 0, "errors": 0, "blocked": False}
    referer, n, t_start = None, 0, time.monotonic()

    try:
        for n, obj_id in enumerate(ids, 1):
            path = out_dir / f"{obj_id}.txt"
            if path.exists() and not force:
                stats["skipped"] += 1
                log.debug("[%d/%d] ID %s: уже есть %s — пропуск", n, len(ids), obj_id, path.name)
                report.add(n=n, id=obj_id, status="skipped", file=str(path))
                continue

            url = base_url + obj_id
            log.info("🔗 [%d/%d] ID %s", n, len(ids), obj_id)
            log.debug("GET %s", url)
            row = dict(n=n, id=obj_id, url=url)
            t0 = time.monotonic()
            html = ""
            try:
                extra = {"referer": referer, "sec-fetch-site": "same-origin"} if referer else {}
                r, attempts = fetch(session, url, extra, obj_id, retries, sleep)
                html = r.text
                row.update(http=r.status_code, bytes=len(r.content), attempts=attempts)
                status = classifier(html)
                if status in ("blocked", "captcha") or r.status_code in (401, 403, 429):
                    stats["blocked"] = True
                    saved_html = report.save_html(obj_id, html)
                    log.error("⛔ ID %s: сайт не пустил (HTTP %s, %s). Остановка. Обновите cookies: "
                              "откройте страницу в Chrome и заново скопируйте cURL в curl.txt. "
                              "Страница: %s", obj_id, r.status_code, status, saved_html or "-")
                    row.update(status="blocked", error=f"{status}, HTTP {r.status_code}", file=saved_html)
                    break
                json_text = extractor(html)
                if json_text:
                    path.write_text(json_text, encoding="utf-8")
                    stats["saved"] += 1
                    referer = quote(url, safe=":/?=&%#")  # заголовки — только ASCII
                    row.update(status="saved", file=str(path))
                    log.info("✅ %s (%d КБ, HTTP %s, %.1f с%s)", path.name, len(json_text) // 1024,
                             r.status_code, time.monotonic() - t0,
                             f", попыток {attempts}" if attempts > 1 else "")
                else:
                    stats["no_json"] += 1
                    saved_html = report.save_html(obj_id, html)
                    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
                    title = title.group(1).strip()[:80] if title else ""
                    row.update(status="no_json", file=saved_html,
                               error=f"JSON не найден; title: {title}" if title else "JSON не найден")
                    log.warning("⚠️  ID %s: JSON не найден (HTTP %s, %d байт, title «%s»). Страница: %s",
                                obj_id, r.status_code, len(html), title, saved_html or "-")
            except Exception as e:
                stats["errors"] += 1
                saved_html = report.save_html(obj_id, html)
                row.update(status="error", error=f"{type(e).__name__}: {e}", file=saved_html)
                log.error("❌ ID %s: %s: %s", obj_id, type(e).__name__, e)
                log.debug("Traceback для ID %s:", obj_id, exc_info=True)
                if is_cert_error(e):
                    log.error("🔐 %s", CERT_HINT)
                    stats["cert_error"] = True
                    break  # все остальные ID упадут так же
            finally:
                row["seconds"] = round(time.monotonic() - t0, 2)
                if "status" in row:
                    report.add(**row)

            if n < len(ids):
                long_pause = LONG_PAUSE_EVERY and n % LONG_PAUSE_EVERY == 0
                wait = random.uniform(*(LONG_PAUSE_RANGE if long_pause else delay_range))
                log.debug("пауза %.1f с", wait)
                sleep(wait)
    except KeyboardInterrupt:
        stats["interrupted"] = True
        log.warning("⏹  Прервано пользователем (Ctrl+C) на ID %s", ids[n - 1] if n else "-")
    finally:
        done = {r["id"] for r in report.rows}
        not_processed = [i for i in ids if i not in done]
        stats["failed_ids"] = report.close(not_processed)
        stats["elapsed"] = time.monotonic() - t_start
    return stats


# === запуск (общий для cards_curl.py и land_curl.py) ===

def read_ids_file(path) -> list[str]:
    ids = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        nid = normalize_id(line.split(";")[0].split(",")[0])
        if nid:
            ids.append(nid)
    return ids


def run(argv=None, *, name="cards", doc=__doc__, out=None, base_url=BASE_URL,
        extractor=None, classifier=None) -> int:
    ap = argparse.ArgumentParser(description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", nargs="*", help="конкретные ID")
    ap.add_argument("--ids-file", help="файл с ID по одному в строке (например, logs/…/failed_ids.txt)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true", help="перекачать уже сохранённые")
    ap.add_argument("--curl-file", default=str(CURL_FILE))
    ap.add_argument("--excel", default=str(EXCEL_PATH))
    ap.add_argument("--out", default=str(out or OUTPUT_FOLDER))
    ap.add_argument("--base-url", default=base_url)
    ap.add_argument("--ca", help="корпоративный корневой сертификат (.pem/.cer в Base64)")
    ap.add_argument("--system-certs", choices=["auto", "on", "off"], default="auto",
                    help="брать сертификаты из хранилища ОС (auto = только на Windows)")
    ap.add_argument("--impersonate", default=IMPERSONATE)
    ap.add_argument("--log-dir", default=str(LOG_DIR))
    ap.add_argument("--retries", type=int, default=RETRIES)
    ap.add_argument("-v", "--verbose", action="store_true", help="подробный вывод в консоль")
    args = ap.parse_args(argv)

    run_dir = setup_logging(name, Path(args.log_dir), args.verbose)
    log.info("Лог запуска: %s", run_dir)
    log.debug("Аргументы: %s", vars(args))
    try:
        import curl_cffi
        cffi_ver = curl_cffi.__version__
    except Exception:
        cffi_ver = "?"
    log.debug("Python %s, curl_cffi %s, платформа %s", sys.version.split()[0], cffi_ver, sys.platform)

    curl_path = Path(args.curl_file)
    if not curl_path.exists() or not curl_path.read_text(encoding="utf-8").strip():
        log.error("❌ Нет %s. Скопируйте запрос страницы из Chrome как «Copy as cURL (bash)» и "
                  "сохраните в этот файл (инструкция — в начале cards_curl.py).", curl_path)
        sys.exit(f"Нет {curl_path.name}")
    headers, cookies = parse_curl(curl_path.read_text(encoding="utf-8"))
    age_h = (time.time() - curl_path.stat().st_mtime) / 3600
    # значения cookies в лог НЕ пишем — это ваша сессия
    log.info("curl.txt: заголовков %d, cookies %d (%s), файлу %.1f ч", len(headers), len(cookies),
             ", ".join(sorted(cookies)) or "нет", age_h)
    log.debug("User-Agent: %s", next((v for k, v in headers.items() if k.lower() == "user-agent"), "-"))
    if not cookies:
        log.warning("⚠️  В curl.txt нет cookies — анти-бот почти наверняка не пустит.")

    if args.ids:
        ids = [normalize_id(i) for i in args.ids]
    elif args.ids_file:
        ids = read_ids_file(args.ids_file)
    else:
        ids = load_ids(args.excel)
    ids = [i for i in ids if i][: args.limit or None]
    log.info("Всего ID: %d → %s", len(ids), args.out)

    ca = args.ca
    if ca:
        log.info("🔐 Сертификаты из --ca: %s", ca)
    elif args.system_certs == "on" or (args.system_certs == "auto" and sys.platform == "win32"):
        ca = system_ca_bundle(run_dir)
        if not ca:
            log.warning("Не удалось прочитать системные сертификаты — используются стандартные.")
    session = make_session(headers, cookies, ca=ca, impersonate=args.impersonate)
    stats = scrape(session, ids, Path(args.out), base_url=args.base_url, force=args.force,
                   extractor=extractor, classifier=classifier, run_dir=run_dir, retries=args.retries)

    failed = stats["failed_ids"]
    flags = "".join([
        ", ОСТАНОВЛЕНО: сайт не пустил" if stats["blocked"] else "",
        ", ОСТАНОВЛЕНО: ошибка сертификата" if stats.get("cert_error") else "",
        ", ПРЕРВАНО" if stats.get("interrupted") else "",
    ])
    log.info("🎉 Готово за %.0f с: сохранено %d, пропущено (уже было) %d, без JSON %d, ошибок %d%s",
             stats["elapsed"], stats["saved"], stats["skipped"], stats["no_json"], stats["errors"], flags)
    log.info("Отчёт: %s", run_dir / "report.csv")
    if failed:
        log.info("Не скачано %d ID. Перезапустить только их:\n  python %s.py --ids-file \"%s\"",
                 len(failed), Path(sys.argv[0]).stem or name, run_dir / "failed_ids.txt")
    if stats.get("cert_error"):
        return 3
    return 2 if stats["blocked"] else (130 if stats.get("interrupted") else 0)


def main(argv=None):
    return run(argv, name="cards")


if __name__ == "__main__":
    sys.exit(main())
