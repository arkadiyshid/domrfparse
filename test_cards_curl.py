"""Тесты для cards_curl.py (реальный сайт не трогают).
Запуск:  python -m pytest -v test_cards_curl.py"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cards_curl as cc  # noqa: E402

OBJ_PATH = "/сервисы/каталог-новостроек/объект/"

# так выглядит «Copy as cURL (bash)» из Chrome (сокращено)
CURL_SAMPLE = r"""curl 'https://xn--80az8a.xn--d1aqf.xn--p1ai/%D1%81%D0%B5%D1%80%D0%B2%D0%B8%D1%81%D1%8B/24248' \
  -H 'accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8' \
  -H 'accept-language: ru-RU,ru;q=0.9,en;q=0.8' \
  -H 'cache-control: no-cache' \
  -b 'antibot=passed; spid=1730198412_abc=; _ym_uid=17301984; empty=' \
  -H 'priority: u=0, i' \
  -H 'sec-ch-ua: "Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"' \
  -H 'sec-ch-ua-platform: "macOS"' \
  -H 'upgrade-insecure-requests: 1' \
  -H 'user-agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36'"""


def card_html(obj_id):
    data = {"props": {"pageProps": {"object": {"id": int(obj_id), "name": f"ЖК {obj_id}"}}},
            "page": "/x", "isFallback": False, "gssp": True}
    return (f'<html><body><script id="__NEXT_DATA__" type="application/json">'
            f'{json.dumps(data, ensure_ascii=False)}</script></body></html>')


BLOCK = "<h1>Доступ заблокирован</h1><p>К сожалению, доступ к запрашиваемому ресурсу заблокирован для Вас.</p>"


class Handler(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        Handler.seen.append(dict(self.headers))
        obj_id = unquote(self.path)[len(OBJ_PATH):]
        ok = "antibot=passed" in (self.headers.get("Cookie") or "")
        body = card_html(obj_id) if ok else BLOCK
        b = body.encode()
        self.send_response(200 if ok else 403)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}{OBJ_PATH}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def no_proxy(monkeypatch):
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")


def test_parse_curl_bash():
    headers, cookies = cc.parse_curl(CURL_SAMPLE)
    assert cookies == {"antibot": "passed", "spid": "1730198412_abc=", "_ym_uid": "17301984", "empty": ""}
    assert headers["user-agent"].startswith("Mozilla/5.0 (Macintosh")
    assert headers["sec-ch-ua"].startswith('"Google Chrome"')
    assert "priority" not in headers and "accept-encoding" not in {h.lower() for h in headers}


def test_parse_curl_cookie_as_header_and_crlf():
    text = "curl 'https://x/' \\\r\n  -H 'Cookie: a=1; b=2' \\\r\n  -H 'User-Agent: UA'"
    headers, cookies = cc.parse_curl(text)
    assert cookies == {"a": "1", "b": "2"} and headers == {"User-Agent": "UA"}


def test_parse_curl_cmd_format_gives_clear_error():
    with pytest.raises(ValueError, match="bash"):
        cc.parse_curl('curl ^"https://x/^" ^\n  -H ^"a: \'b^"')


def test_extract_and_classify():
    assert json.loads(cc.extract_json(card_html("5")))["props"]["pageProps"]["object"]["id"] == 5
    assert cc.classify_page(BLOCK) == "blocked"
    assert cc.classify_page(card_html("5")) == "ok"


@pytest.mark.parametrize("raw,exp", [(58524, "58524"), (58524.0, "58524"), ("ЖК", None), (None, None)])
def test_normalize_id(raw, exp):
    assert cc.normalize_id(raw) == exp


def test_end_to_end_with_cookies(server, tmp_path):
    (tmp_path / "curl.txt").write_text(CURL_SAMPLE, encoding="utf-8")
    out = tmp_path / "out"
    Handler.seen.clear()
    cc.DELAY_RANGE = (0, 0)
    code = cc.main(["--log-dir", str(tmp_path / "logs"), "--curl-file", str(tmp_path / "curl.txt"), "--out", str(out),
                    "--base-url", server, "--ids", "24248", "26466"])
    assert code == 0
    assert sorted(p.name for p in out.iterdir()) == ["24248.txt", "26466.txt"]
    assert json.loads((out / "24248.txt").read_text(encoding="utf-8"))["props"]["pageProps"]["object"]["name"] == "ЖК 24248"
    h = {k.lower(): v for k, v in Handler.seen[0].items()}
    assert "Chrome/141" in h["user-agent"]  # UA взят из curl.txt, а не дефолтный
    h2 = {k.lower(): v for k, v in Handler.seen[1].items()}
    assert h2.get("referer", "").endswith("%D1%82/24248")  # второй запрос идёт «с» первой карточки


def test_end_to_end_without_cookies_stops(server, tmp_path):
    (tmp_path / "curl.txt").write_text("curl 'https://x/' -H 'user-agent: UA'", encoding="utf-8")
    out = tmp_path / "out"
    code = cc.main(["--log-dir", str(tmp_path / "logs"), "--curl-file", str(tmp_path / "curl.txt"), "--out", str(out),
                    "--base-url", server, "--ids", "1", "2", "3"])
    assert code == 2 and not any(out.iterdir())


def test_missing_curl_file(tmp_path):
    with pytest.raises(SystemExit, match="curl.txt"):
        cc.main(["--log-dir", str(tmp_path / "logs"), "--curl-file", str(tmp_path / "curl.txt"), "--ids", "1"])


# ---------- логирование, отчёты, повторы ----------

import csv  # noqa: E402


class FakeResp:
    def __init__(self, status, text):
        self.status_code, self.text, self.content = status, text, text.encode()


class FakeSession:
    """Отдаёт заранее заданные ответы/исключения по ID (последний элемент URL)."""
    def __init__(self, plan):
        self.plan, self.calls = {k: list(v) for k, v in plan.items()}, []

    def get(self, url, headers=None):
        obj_id = url.rsplit("/", 1)[-1]
        self.calls.append(obj_id)
        item = self.plan[obj_id].pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def read_report(run_dir):
    with open(run_dir / "report.csv", encoding="utf-8-sig") as f:
        return {r["id"]: r for r in csv.DictReader(f, delimiter=";")}


def test_logs_report_failed_and_html(tmp_path):
    run_dir = cc.setup_logging("cards", tmp_path / "logs")
    out = tmp_path / "out"
    out.mkdir()
    (out / "1.txt").write_text("old")
    sess = FakeSession({
        "2": [FakeResp(200, card_html("2"))],
        "3": [FakeResp(200, "<html><title>Объект не найден</title></html>")],
        "4": [RuntimeError("что-то сломалось")],
        "5": [FakeResp(200, card_html("5"))],
    })
    stats = cc.scrape(sess, ["1", "2", "3", "4", "5"], out, base_url="https://x/",
                      delay_range=(0, 0), sleep=lambda s: None, run_dir=run_dir, retries=0)
    assert (stats["saved"], stats["skipped"], stats["no_json"], stats["errors"]) == (2, 1, 1, 1)

    rep = read_report(run_dir)
    assert {k: v["status"] for k, v in rep.items()} == \
        {"1": "skipped", "2": "saved", "3": "no_json", "4": "error", "5": "saved"}
    assert rep["2"]["http"] == "200" and int(rep["2"]["bytes"]) > 0
    assert "Объект не найден" in rep["3"]["error"]
    assert "RuntimeError: что-то сломалось" in rep["4"]["error"]

    assert (run_dir / "failed_ids.txt").read_text().split() == ["3", "4"]
    assert "Объект не найден" in (run_dir / "html" / "3.html").read_text(encoding="utf-8")
    text = (run_dir / "run.log").read_text(encoding="utf-8")
    assert "Traceback" in text and "что-то сломалось" in text  # traceback попадает в файл


def test_retry_on_network_error_and_5xx(tmp_path):
    run_dir = cc.setup_logging("cards", tmp_path / "logs")
    sess = FakeSession({
        "7": [ConnectionError("reset"), FakeResp(502, "bad gw"), FakeResp(200, card_html("7"))],
        "8": [ConnectionError("1"), ConnectionError("2"), ConnectionError("3")],
    })
    waits = []
    stats = cc.scrape(sess, ["7", "8"], tmp_path / "out", base_url="https://x/", delay_range=(0, 0),
                      sleep=waits.append, run_dir=run_dir, retries=2)
    assert stats["saved"] == 1 and stats["errors"] == 1
    rep = read_report(run_dir)
    assert rep["7"]["attempts"] == "3" and rep["7"]["status"] == "saved"
    assert rep["8"]["status"] == "error"
    assert sess.calls == ["7", "7", "7", "8", "8", "8"]
    assert cc.RETRY_BACKOFF in waits and 2 * cc.RETRY_BACKOFF in waits


def test_block_marks_rest_as_failed(tmp_path):
    run_dir = cc.setup_logging("cards", tmp_path / "logs")
    sess = FakeSession({"1": [FakeResp(200, card_html("1"))], "2": [FakeResp(403, BLOCK)]})
    stats = cc.scrape(sess, ["1", "2", "3", "4"], tmp_path / "out", base_url="https://x/",
                      delay_range=(0, 0), sleep=lambda s: None, run_dir=run_dir)
    assert stats["blocked"]
    # заблокированный + не дошедшие до очереди — всё в failed_ids для перезапуска
    assert (run_dir / "failed_ids.txt").read_text().split() == ["2", "3", "4"]
    assert (run_dir / "html" / "2.html").exists()


def test_ctrl_c_still_writes_report(tmp_path):
    run_dir = cc.setup_logging("cards", tmp_path / "logs")
    sess = FakeSession({"1": [FakeResp(200, card_html("1"))], "2": [KeyboardInterrupt()]})
    stats = cc.scrape(sess, ["1", "2", "3"], tmp_path / "out", base_url="https://x/",
                      delay_range=(0, 0), sleep=lambda s: None, run_dir=run_dir)
    assert stats.get("interrupted")
    assert read_report(run_dir)["1"]["status"] == "saved"
    assert (run_dir / "failed_ids.txt").read_text().split() == ["2", "3"]


def test_cookie_values_not_logged_and_ids_file(server, tmp_path):
    (tmp_path / "curl.txt").write_text(CURL_SAMPLE, encoding="utf-8")
    (tmp_path / "ids.txt").write_text("24248\nмусор\n26466;коммент\n", encoding="utf-8")
    logs = tmp_path / "logs"
    code = cc.main(["--log-dir", str(logs), "--curl-file", str(tmp_path / "curl.txt"),
                    "--out", str(tmp_path / "out"), "--base-url", server,
                    "--ids-file", str(tmp_path / "ids.txt")])
    assert code == 0
    run_dir = next(logs.iterdir())
    text = (run_dir / "run.log").read_text(encoding="utf-8")
    assert "spid" in text and "1730198412_abc" not in text  # имя cookie есть, значения нет
    assert set(read_report(run_dir)) == {"24248", "26466"}
    assert (run_dir / "failed_ids.txt").read_text() == ""


# ---------- корпоративный прокси со своим сертификатом ----------

import shutil  # noqa: E402
import ssl  # noqa: E402
import subprocess  # noqa: E402


@pytest.fixture(scope="module")
def corp_https(tmp_path_factory):
    """HTTPS-сервер с сертификатом от «корпоративного» корневого центра,
    которого нет в стандартном наборе — как на рабочем ноутбуке."""
    if not shutil.which("openssl"):
        pytest.skip("нет openssl")
    d = tmp_path_factory.mktemp("pki")
    run = lambda *a: subprocess.run(a, cwd=d, check=True, capture_output=True)  # noqa: E731
    run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
        "-keyout", "ca.key", "-out", "ca.pem", "-subj", "/CN=Corp Proxy Root CA")
    run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "srv.key",
        "-out", "srv.csr", "-subj", "/CN=127.0.0.1")
    (d / "ext.cnf").write_text("subjectAltName=IP:127.0.0.1\n")
    run("openssl", "x509", "-req", "-in", "srv.csr", "-CA", "ca.pem", "-CAkey", "ca.key",
        "-CAcreateserial", "-out", "srv.pem", "-days", "2", "-extfile", "ext.cnf")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(d / "srv.pem", d / "srv.key")
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"https://127.0.0.1:{srv.server_address[1]}{OBJ_PATH}", d / "ca.pem"
    srv.shutdown()


def corp_args(tmp_path, url, *extra):
    (tmp_path / "curl.txt").write_text(CURL_SAMPLE, encoding="utf-8")
    return ["--log-dir", str(tmp_path / "logs"), "--curl-file", str(tmp_path / "curl.txt"),
            "--out", str(tmp_path / "out"), "--base-url", url, "--ids", "1", "2", "3", *extra]


def test_cert_error_stops_at_once_with_hint(corp_https, tmp_path):
    url, _ = corp_https
    Handler.seen.clear()
    code = cc.main(corp_args(tmp_path, url, "--system-certs", "off"))
    assert code == 3
    run_dir = next((tmp_path / "logs").iterdir())
    text = (run_dir / "run.log").read_text(encoding="utf-8")
    assert "повтор" not in text  # без бессмысленных повторов
    assert "--ca" in text  # подсказка, что делать
    assert (run_dir / "failed_ids.txt").read_text().split() == ["1", "2", "3"]


def test_explicit_ca_works(corp_https, tmp_path):
    url, ca = corp_https
    assert cc.main(corp_args(tmp_path, url, "--ca", str(ca))) == 0
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["1.txt", "2.txt", "3.txt"]


def test_windows_store_used_automatically(corp_https, tmp_path, monkeypatch):
    """Имитация Windows: корневой сертификат компании лежит в хранилище ROOT."""
    url, ca = corp_https
    der = ssl.PEM_cert_to_DER_cert(ca.read_text())
    calls = []

    def fake_enum(store):
        calls.append(store)
        return [(der, "x509_asn", True)] if store == "ROOT" else []

    monkeypatch.setattr(ssl, "enum_certificates", fake_enum, raising=False)
    monkeypatch.setattr(cc.sys, "platform", "win32")
    assert cc.main(corp_args(tmp_path, url)) == 0  # без --ca, всё само
    assert calls == ["ROOT", "CA"]
    run_dir = next((tmp_path / "logs").iterdir())
    bundle = (run_dir / "ca_bundle.pem").read_text()
    assert ca.read_text().strip().splitlines()[1] in bundle  # наш корневой внутри
    assert bundle.count("BEGIN CERTIFICATE") > 50  # и стандартные certifi на месте
