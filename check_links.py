#!/usr/bin/env python3
"""Check exported URLs anonymously; never use Miro credentials or browser cookies."""
import argparse
import csv
import ipaddress
import json
import re
import socket
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests


def public_url(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            return False
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(a[4][0]).is_global for a in addresses)
    except (ValueError, OSError):
        return False


def classify(code, final_url, sample):
    parsed = urlsplit(final_url)
    if code in (401, 407):
        return "auth_required", f"Нужна авторизация (HTTP {code})"
    if code in (404, 410):
        return "unavailable", f"Недоступна при проверке (HTTP {code})"
    if code in (403, 429, 451):
        return "restricted", f"Доступ не подтверждён: ограничение/блокировка (HTTP {code})"
    if code >= 400:
        return "http_error", f"Ошибка сервера при проверке (HTTP {code})"
    if re.search(r"/(?:login|signin|sign-in|auth)(?:/|$)", parsed.path, re.I) or parsed.hostname in ("accounts.google.com", "login.microsoftonline.com"):
        return "auth_required", "Нужна авторизация: перенаправление на вход"
    if re.search(r"<title[^>]*>[^<]*(?:sign in|log in|вход|авторизац|just a moment|access denied|captcha)", sample, re.I):
        return "restricted", "Доступ к материалу не подтверждён: страница входа/защиты"
    if "youtube.com" in (parsed.hostname or "") or parsed.hostname == "youtu.be":
        return "page_only", f"Страница отвечает (HTTP {code}); воспроизведение видео и ограничения входа не проверены"
    return "responds", f"URL отвечает (HTTP {code}); доступ к платному содержимому не проверялся"


def check(url):
    result = {"url": url, "checkedAt": datetime.now(timezone.utc).isoformat(), "httpStatus": None, "finalUrl": url}
    try:
        host = urlsplit(url).hostname or ""
        malformed = not host or "." not in host or "_" in host or "%" in host
    except ValueError:
        malformed = True
    if malformed:
        result.update(status="invalid_url", label="Некорректный адрес в оригинале; сохранён без исправлений")
        return result
    try:
        with requests.Session() as session:
            session.headers.update({"User-Agent": "miro-to-md-link-audit/1.0", "Accept": "text/html,*/*;q=0.5"})
            session.trust_env = False
            for _ in range(10):
                if not public_url(result["finalUrl"]):
                    result.update(status="unchecked", label="Не проверена: DNS недоступен или адрес не публичный")
                    return result
                with session.get(result["finalUrl"], timeout=(8, 15), stream=True, allow_redirects=False) as response:
                    code = result["httpStatus"] = response.status_code
                    if code in (301, 302, 303, 307, 308) and response.headers.get("Location"):
                        result["finalUrl"] = urljoin(result["finalUrl"], response.headers["Location"])
                        continue
                    sample = b""
                    if "html" in response.headers.get("Content-Type", ""):
                        for chunk in response.iter_content(16384):
                            sample += chunk
                            if len(sample) >= 131072:
                                break
                    result["status"], result["label"] = classify(code, result["finalUrl"], sample.decode("utf-8", errors="replace"))
                    return result
            result.update(status="unchecked", label="Не проверена: слишком много перенаправлений")
    except requests.Timeout:
        result.update(status="timeout", label="Доступ не подтверждён: истекло время ожидания")
    except requests.RequestException:
        result.update(status="network_error", label="Доступ не подтверждён: ошибка сети/TLS")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=Path, default=Path("out/items.json"))
    parser.add_argument("--out", type=Path, default=Path("out"))
    args = parser.parse_args()
    nodes = json.loads(args.items.read_text(encoding="utf-8"))
    urls = sorted({l["url"] for n in nodes for l in n["links"]})
    results = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(check, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            results[url] = future.result()
            if len(results) % 40 == 0:
                print(f"Проверено {len(results)}/{len(urls)} URL", flush=True)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out/"link-checks.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    with (args.out/"link-checks.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["url", "checkedAt", "httpStatus", "finalUrl", "status", "label"])
        writer.writeheader()
        writer.writerows(results[url] for url in urls)
    print(json.dumps(dict(Counter(r["status"] for r in results.values())), ensure_ascii=False))


if __name__ == "__main__":
    main()
