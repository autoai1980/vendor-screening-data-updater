#!/usr/bin/env python3
from __future__ import annotations
import csv, hashlib, io, json, re, sys, unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from jsonschema import validate
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
CONFIG=json.loads((ROOT/"config/sources.json").read_text())
UA="VendorScreeningUpdater/1.0 (+https://github.com/autoai1980/vendor-screening-data-updater)"
TIMEOUT=120
now=lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")

def sha(b): return hashlib.sha256(b).hexdigest()
def clean(v): return re.sub(r"\s+"," ",str(v or "")).strip()
def split_aliases(v):
    return list(dict.fromkeys(x for x in (clean(x) for x in re.split(r";|\n|\s+\|\s+",clean(v))) if x))
def record(source,name,aliases=None,country="",ref="",listed="",until="",grounds="",kind="Entity",weak=None):
    return [0,source,clean(name),"","; ".join(aliases or []),clean(country),clean(ref),clean(listed),
            clean(until),clean(grounds),"",aliases or [],weak or [],kind,[source]]
def get(url,accept="*/*"):
    r=requests.get(url,headers={"User-Agent":UA,"Accept":accept},timeout=TIMEOUT)
    r.raise_for_status()
    if not r.content: raise RuntimeError(f"Empty response from {url}")
    return r
def tag(el,name):
    for node in el.iter():
        if node.tag.rsplit("}",1)[-1]==name:
            return clean(node.text)
    return ""
def children(el,name):
    return [x for x in el.iter() if x.tag.rsplit("}",1)[-1]==name]

def canada(src):
    import xml.etree.ElementTree as ET
    r=get(src["url"],"application/xml,text/xml")
    root=ET.fromstring(r.content); out=[]
    for row in children(root,"record"):
        vals={x.tag.rsplit("}",1)[-1]:clean(x.text) for x in row}
        entity=vals.get("EntityOrShip-EntiteOuNavire","")
        given=vals.get("GivenName-Prenom",""); last=vals.get("LastName-NomDeFamille","")
        name=entity or clean(f"{given} {last}")
        if not name: raise RuntimeError("Canada record without a name")
        aliases=split_aliases(vals.get("Aliases-Alias",""))
        schedule=vals.get("Schedule-Annexe",""); item=vals.get("Item-NumeroDarticle","")
        title=vals.get("TitleOrShipType-TitreOuTypeDeNavire","")
        kind="Ship" if vals.get("ShipIMONumber-NumeroOMIDuNavire") or "ship" in title.lower() else ("Individual" if given or last else "Entity")
        out.append(record(src["name"],name,aliases,vals.get("Country-Pays","")," / ".join(x for x in (schedule,item) if x),
                          vals.get("DateOfListing-DateDinscription",""),kind=kind))
    return out,r

def un_list(src):
    import xml.etree.ElementTree as ET
    r=get(src["url"],"application/xml,text/xml"); root=ET.fromstring(r.content); out=[]
    for kind,node_name in (("Individual","INDIVIDUAL"),("Entity","ENTITY")):
        for row in children(root,node_name):
            if kind=="Individual":
                name=clean(" ".join(tag(row,x) for x in ("FIRST_NAME","SECOND_NAME","THIRD_NAME","FOURTH_NAME")))
                alias_nodes=children(row,"INDIVIDUAL_ALIAS")
            else:
                name=tag(row,"FIRST_NAME"); alias_nodes=children(row,"ENTITY_ALIAS")
            good=[]; weak=[]
            for a in alias_nodes:
                n=tag(a,"ALIAS_NAME"); quality=tag(a,"QUALITY").lower()
                if n: (weak if "low" in quality else good).append(n)
            countries=[tag(n,"VALUE") for n in children(row,"NATIONALITY")]
            countries=[x for x in countries if x and x.lower()!="na"]
            out.append(record(src["name"],name,list(dict.fromkeys(good)),"; ".join(countries),
                              tag(row,"REFERENCE_NUMBER"),tag(row,"LISTED_ON"),kind=kind,weak=list(dict.fromkeys(weak))))
    return out,r

def terrorist_entities(src):
    r=get(src["url"],"text/html"); soup=BeautifulSoup(r.content,"html.parser"); main=soup.find("main") or soup
    names=[]; active=False
    for h in main.select("h1,h2,h3,h4"):
        text=clean(h.get_text(" ",strip=True))
        if text=="Currently listed entities": active=True; continue
        if active and text=="Notice of amendments": break
        if active and text and text not in {"About this site"}: names.append(text)
    names=list(dict.fromkeys(names))
    if not 70<=len(names)<=150: raise RuntimeError(f"Unexpected terrorist-entity count: {len(names)}")
    out=[]
    for name in names:
        aliases=[]
        for p in re.findall(r"\(([^()]{2,100})\)",name):
            aliases.extend(split_aliases(re.sub(r"^(also known as|formerly known as)\s+","",p,flags=re.I)))
        out.append(record(src["name"],name,list(dict.fromkeys(aliases)),ref="Criminal Code listed entity",kind="Entity"))
    return out,r

def riunrst(src):
    r=get(src["url"],"text/html"); soup=BeautifulSoup(r.content,"html.parser"); main=soup.find("main") or soup
    names=[]
    for li in main.select("li"):
        text=clean(li.get_text(" ",strip=True))
        m=re.match(r"^(\d+)\s+(.*)$",text)
        if m and 1<=int(m.group(1))<=100 and len(m.group(2))>2: names.append((m.group(1),m.group(2)))
    unique=[]; seen=set()
    for num,name in names:
        if num not in seen: seen.add(num); unique.append((num,name))
    if not 20<=len(unique)<=60: raise RuntimeError(f"Unexpected RIUNRST schedule count: {len(unique)}")
    return [record(src["name"],n,ref=f"Schedule / {i}",kind="Individual" if re.search(r"\b(born|Mr\.?|Mohammed|Ahmed|Ali|Hassan|Khalid|Usama|Jose)\b",n,re.I) else "Entity") for i,n in unique],r

def world_bank(src,browser):
    page=browser.new_page(user_agent=UA); page.goto(src["url"],wait_until="domcontentloaded",timeout=TIMEOUT*1000)
    page.wait_for_selector("table tr:nth-child(100)",timeout=TIMEOUT*1000)
    rows=page.locator("table tr").evaluate_all("""rows=>rows.map(r=>[...r.querySelectorAll('th,td')].map(c=>c.innerText.replace(/\\s+/g,' ').trim()))""")
    out=[]
    for cells in rows:
        if len(cells)>=6 and cells[0] and cells[0].upper() not in {"FIRM NAME","FROM DATE"}:
            out.append(record(src["name"],cells[0],country=cells[2],listed=cells[3],until=cells[4],grounds=cells[5],kind="Firm / individual"))
    page.close()
    if not 1000<=len(out)<=3000: raise RuntimeError(f"Unexpected World Bank count: {len(out)}")
    body=json.dumps(rows,ensure_ascii=False).encode()
    return out,type("Response",(),{"content":body,"headers":{}})()

def idb(src,browser):
    page=browser.new_page(user_agent=UA)
    page.goto("https://data.iadb.org/dataset/dataset-of-sanctioned-firms-and-individuals",wait_until="domcontentloaded",timeout=TIMEOUT*1000)
    link=page.locator('a[href*="/files/download/"]').first
    link.wait_for(timeout=TIMEOUT*1000)
    with page.expect_download(timeout=TIMEOUT*1000) as event: link.click()
    download=event.value; path=download.path(); body=Path(path).read_bytes(); page.close()
    text=body.decode("utf-8-sig"); reader=csv.DictReader(io.StringIO(text))
    headers=reader.fieldnames or []
    def find(*terms):
        for h in headers:
            n=unicodedata.normalize("NFKD",h).encode("ascii","ignore").decode().lower()
            if all(t in n for t in terms): return h
    name_col=find("name") or find("nombre")
    if not name_col: raise RuntimeError(f"IDB name column not found; headers={headers}")
    country_col=find("country") or find("pais"); from_col=find("from") or find("start") or find("desde")
    to_col=find("to") or find("end") or find("hasta"); grounds_col=find("ground") or find("practice") or find("motivo")
    out=[]
    for row in reader:
        name=clean(row.get(name_col))
        if name: out.append(record(src["name"],name,country=row.get(country_col,"") if country_col else "",
                                   listed=row.get(from_col,"") if from_col else "",until=row.get(to_col,"") if to_col else "",
                                   grounds=row.get(grounds_col,"") if grounds_col else "",kind="Firm / individual"))
    if not 500<=len(out)<=3000: raise RuntimeError(f"Unexpected IDB count: {len(out)}; headers={headers}")
    return out,type("Response",(),{"content":body,"headers":{}})()

def main():
    parsers={"ca_autonomous_sanctions":canada,"un_sc_consolidated":un_list,
             "ca_criminal_code_terrorist_entities":terrorist_entities,"ca_riunrst":riunrst}
    all_records=[]; metadata=[]; sources={x["id"]:x for x in CONFIG["sources"]}
    with sync_playwright() as p:
        browser=p.chromium.launch()
        for sid in ("ca_autonomous_sanctions","un_sc_consolidated","ca_criminal_code_terrorist_entities","ca_riunrst"):
            src=sources[sid]; rows,response=parsers[sid](src); all_records.extend(rows)
            metadata.append({"id":sid,"name":src["name"],"authority":src["authority"],"sourceUrl":src["url"],
                             "retrievedAt":now(),"recordCount":len(rows),"contentSha256":sha(response.content)})
        for sid,parser in (("world_bank_debarments",world_bank),("idb_sanctions",idb)):
            src=sources[sid]; rows,response=parser(src,browser); all_records.extend(rows)
            metadata.append({"id":sid,"name":src["name"],"authority":src["authority"],"sourceUrl":src["url"],
                             "retrievedAt":now(),"recordCount":len(rows),"contentSha256":sha(response.content)})
        browser.close()
    for i,row in enumerate(all_records,1): row[0]=i
    if len(all_records)<7500: raise RuntimeError(f"Combined dataset unexpectedly small: {len(all_records)}")
    stamp=datetime.now(timezone.utc).strftime("%Y-%m-%d")
    package={"schemaVersion":1,"datasetVersion":f"official-{stamp}","generatedAt":now(),
             "recordCount":len(all_records),"sources":metadata,"records":all_records}
    schema=json.loads((ROOT/"schema/update-package.schema.json").read_text()); validate(package,schema)
    out=ROOT/"data"; out.mkdir(exist_ok=True)
    payload=json.dumps(package,ensure_ascii=False,separators=(",",":")).encode()
    (out/"vendor-screening-data.json").write_bytes(payload)
    manifest={"schemaVersion":1,"datasetVersion":package["datasetVersion"],"generatedAt":package["generatedAt"],
              "recordCount":package["recordCount"],"sha256":sha(payload),"dataUrl":"vendor-screening-data.json",
              "sources":metadata}
    (out/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"recordCount":len(all_records),"sources":{m["id"]:m["recordCount"] for m in metadata},"sha256":sha(payload)}))

if __name__=="__main__":
    try: main()
    except Exception as exc:
        print(f"UPDATE BLOCKED: {exc}",file=sys.stderr); raise
