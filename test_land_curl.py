"""Тесты для land_curl.py (реальный сайт не трогают).
Запуск:  python -m pytest -v test_land_curl.py"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import land_curl as lc  # noqa: E402

LAND_PATH = "/сервисы/проверка_новостроек/"
CURL = "curl 'https://x/' -b 'antibot=passed; a=1' -H 'user-agent: Mozilla/5.0 Chrome/141'"


def land_html(obj_id):
    data = {"props": {"pageProps": {"land": {"objId": int(obj_id), "cadNum": "77:01:0001001:1"}}},
            "page": "/x", "isFallback": False}
    return (f'<html><head></head><body><script id="__NEXT_DATA__" type="application/json">'
            f'{json.dumps(data, ensure_ascii=False)}</script></body></html>')


BLOCK = "<h1>Доступ заблокирован</h1>"


class Handler(BaseHTTPRequestHandler):
    paths = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = unquote(self.path)
        Handler.paths.append(path)
        ok = "antibot=passed" in (self.headers.get("Cookie") or "") and path.startswith(LAND_PATH)
        body = land_html(path[len(LAND_PATH):]) if ok else BLOCK
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
    yield f"http://127.0.0.1:{srv.server_address[1]}{LAND_PATH}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(lc.cc, "DELAY_RANGE", (0, 0))
    monkeypatch.setattr(lc.cc, "LONG_PAUSE_RANGE", (0, 0))


def test_extract_matches_old_land_parser():
    html = land_html("123")
    # старый парсер Land_seleniun.py
    s = html.find('json">')
    old = html[s + len('json">'):html.find("</script>", s)].strip()
    assert lc.extract_land_json(html) == old
    assert json.loads(old)["props"]["pageProps"]["land"]["objId"] == 123


def test_extract_skips_broken_first_script():
    html = '<script type="x-json">не json</script>' + land_html("5")
    assert json.loads(lc.extract_land_json(html))["props"]["pageProps"]["land"]["objId"] == 5


def test_extract_none_and_block():
    assert lc.extract_land_json(BLOCK) is None
    assert lc.classify(BLOCK) == "blocked"


def test_default_url_is_land_page():
    assert "проверка_новостроек/" in lc.BASE_URL and lc.OUTPUT_FOLDER.name == "json_land"


def test_end_to_end(server, tmp_path):
    (tmp_path / "curl.txt").write_text(CURL, encoding="utf-8")
    out = tmp_path / "json_land"
    (out).mkdir()
    (out / "1.txt").write_text("old")
    Handler.paths.clear()
    code = lc.main(["--log-dir", str(tmp_path / "logs"), "--curl-file", str(tmp_path / "curl.txt"), "--out", str(out),
                    "--base-url", server, "--ids", "1", "24248", "26466"])
    assert code == 0
    assert sorted(p.name for p in out.iterdir()) == ["1.txt", "24248.txt", "26466.txt"]
    assert (out / "1.txt").read_text() == "old"  # уже скачанное не трогаем
    assert Handler.paths == [LAND_PATH + "24248", LAND_PATH + "26466"]
    d = json.loads((out / "26466.txt").read_text(encoding="utf-8"))
    assert d["props"]["pageProps"]["land"]["objId"] == 26466


def test_end_to_end_blocked(server, tmp_path):
    (tmp_path / "curl.txt").write_text("curl 'https://x/' -H 'user-agent: UA'", encoding="utf-8")
    out = tmp_path / "o"
    assert lc.main(["--log-dir", str(tmp_path / "logs"), "--curl-file", str(tmp_path / "curl.txt"), "--out", str(out),
                    "--base-url", server, "--ids", "1", "2"]) == 2
    assert not any(out.iterdir())
