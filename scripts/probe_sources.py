#!/usr/bin/env python3
import csv
import hashlib
import io
import json
import sys
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

UA = "VendorScreeningUpdater/1.0 (+https://github.com/autoai1980/vendor-screening-data-updater)"

class TableCounter(HTMLParser):
    def __init__(self):
        super().__init__(); self.tables = self.rows = 0
    def handle_starttag(self, tag, attrs):
        if tag.lower() == "table": self.tables += 1
        if tag.lower() == "tr": self.rows += 1

def local(tag):
    return tag.rsplit("}", 1)[-1]

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=90) as response:
        return {
            "status": response.status,
            "content_type": response.headers.get("Content-Type", ""),
            "last_modified": response.headers.get("Last-Modified"),
            "content_disposition": response.headers.get("Content-Disposition"),
            "final_url": response.geturl(),
            "body": response.read(),
        }

def main():
    cfg = json.loads(Path("config/sources.json").read_text())
    failed = []
    for src in cfg["sources"]:
        try:
            response = fetch(src["url"])
            status, ctype, final_url, body = response["status"], response["content_type"], response["final_url"], response["body"]
            info = {
                "id": src["id"], "status": status, "contentType": ctype,
                "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                "redirected": final_url != src["url"],
                "lastModified": response["last_modified"],
                "contentDisposition": response["content_disposition"]
            }
            if src["format"] == "xml":
                root = ET.fromstring(body)
                tags = {}
                for element in root.iter():
                    key = local(element.tag)
                    tags[key] = tags.get(key, 0) + 1
                info["root"] = local(root.tag)
                info["topTags"] = sorted(tags.items(), key=lambda item: -item[1])[:30]
                leaves = []
                for element in root.iter():
                    if len(element) == 0 and (element.text or "").strip():
                        leaves.append([local(element.tag), (element.text or "").strip()[:120]])
                    if len(leaves) == 20:
                        break
                info["sampleLeaves"] = leaves
            elif src["format"] == "csv":
                text = body.decode("utf-8-sig")
                reader = csv.reader(io.StringIO(text))
                info["csvHeader"] = next(reader, [])
                info["csvRows"] = sum(1 for _ in reader)
            else:
                parser = TableCounter()
                parser.feed(body.decode("utf-8", "replace"))
                info["htmlTables"] = parser.tables
                info["htmlRows"] = parser.rows
            print(json.dumps(info, ensure_ascii=False))
        except Exception as exc:
            failed.append(src["id"])
            print(json.dumps({"id": src["id"], "error": str(exc)}))
    if failed:
        print("SOURCE PROBE FAILED: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
