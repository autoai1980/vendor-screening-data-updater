#!/usr/bin/env python3
import json
import time
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

UA = "VendorScreeningUpdater/1.0 (+https://github.com/autoai1980/vendor-screening-data-updater)"

def inspect_idb():
    url = "https://data.iadb.org/files/download/f5022f04-a3f1-4604-a099-5d562e3a0aa5"
    session = requests.Session()
    response = session.get(url, headers={"User-Agent": UA}, allow_redirects=False, timeout=90)
    print(json.dumps({"idb_status": response.status_code, "headers": {
        key: value for key, value in response.headers.items()
        if key.lower() in {"location", "retry-after", "content-type", "content-disposition"}
    }}))
    location = response.headers.get("Location")
    if location:
        for attempt in range(6):
            time.sleep(min(2 ** attempt, 15))
            polled = session.get(requests.compat.urljoin(url, location), headers={"User-Agent": UA}, timeout=90)
            print(json.dumps({"idb_poll": attempt + 1, "status": polled.status_code,
                              "content_type": polled.headers.get("Content-Type"),
                              "bytes": len(polled.content), "final_url": polled.url}))
            if polled.status_code == 200 and polled.content:
                break

def inspect_html(url, label):
    response = requests.get(url, headers={"User-Agent": UA}, timeout=90)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    headings = [node.get_text(" ", strip=True)[:180] for node in soup.select("h1,h2,h3,h4")]
    list_items = [node.get_text(" ", strip=True)[:220] for node in soup.select("li") if node.get_text(" ", strip=True)]
    print(json.dumps({"label": label, "headings": headings[:120], "list_sample": list_items[:160]}, ensure_ascii=False))

def inspect_json_endpoints():
    endpoints = {
        "world_bank_json": "https://apigwext.worldbank.org/dvsvc/v1.0/json/APPLICATION/ADOBE_EXPRNCE_MGR/FIRM/SANCTIONED_FIRM",
        "idb_package": "https://data.iadb.org/api/3/action/package_show?id=3a873ab8-20cb-4e12-9826-9abcdac3f51a",
        "idb_datastore": "https://data.iadb.org/api/action/datastore_search?resource_id=cd0bd9ac-18c6-44bc-8592-9be468c2efd9&limit=5",
    }
    for label, url in endpoints.items():
        try:
            response = requests.get(url, headers={"User-Agent": UA}, timeout=120)
            response.raise_for_status()
            payload = response.json()
            print(json.dumps({"label": label, "top_type": type(payload).__name__,
                              "top_keys": list(payload)[:30] if isinstance(payload, dict) else None,
                              "sample": payload if len(response.content) < 15000 else str(payload)[:12000]},
                             ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({"label": label, "error": str(exc)}))

def inspect_world_bank():
    url = "https://www.worldbank.org/en/projects-operations/procurement/debarred-firms"
    interesting = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=UA)
        def capture(response):
            low = response.url.lower()
            ctype = response.headers.get("content-type", "")
            if any(term in low for term in ("debar", "sanction", "api", "json")) or "json" in ctype:
                interesting.append({"status": response.status, "url": response.url, "content_type": ctype})
        page.on("response", capture)
        page.goto(url, wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(20000)
        frames = []
        for frame in page.frames:
            rows = frame.locator("table tr").all_inner_texts()
            frames.append({"url": frame.url, "row_count": len(rows), "rows": rows[:8]})
        print(json.dumps({"world_bank_responses": interesting[-100:], "frames": frames}, ensure_ascii=False))
        browser.close()

if __name__ == "__main__":
    inspect_idb()
    inspect_html("https://www.publicsafety.gc.ca/cnt/ntnl-scrt/cntr-trrrsm/lstd-ntts/crrnt-lstd-ntts-en.aspx", "terrorist_entities")
    inspect_html("https://laws-lois.justice.gc.ca/eng/regulations/SOR-2001-360/FullText.html", "riunrst")
    inspect_json_endpoints()
    inspect_world_bank()
