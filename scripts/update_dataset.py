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

def normalized_value(value):
    if isinstance(value,list):
        return tuple(sorted(normalized_value(x) for x in value))
    return re.sub(r"\s+"," ",unicodedata.normalize("NFKC",str(value or "")).casefold()).strip()

def record_key(row):
    # IDs are assigned after deduplication. Every other material field, including
    # source, country, reference and listing dates, must match before a row is removed.
    return tuple(normalized_value(value) for value in row[1:])

def deduplicate_records(rows):
    kept=[]; seen=set(); removed=[]
    for row in rows:
        key=record_key(row)
        if key in seen: removed.append(row)
        else: seen.add(key); kept.append(row)
    return kept,removed

def dataset_delta(previous,current):
    old={}; new={}
    for row in previous:
        old.setdefault(record_key(row),[]).append(row)
    for row in current:
        new.setdefault(record_key(row),[]).append(row)
    added=[]; removed=[]
    for key in set(old)|set(new):
        before=old.get(key,[]); after=new.get(key,[]); matched=min(len(before),len(after))
        removed.extend(before[matched:]); added.extend(after[matched:])
    return added,removed
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
    from collections import Counter
    xml_tags=Counter(node.tag.rsplit("}",1)[-1] for node in root.iter())
    print(json.dumps({"diagnostic":"Canadian XML structure","bytes":len(r.content),
                      "rootTag":root.tag.rsplit("}",1)[-1],
                      "recordElements":xml_tags.get("record",0),
                      "topLevelTags":dict(Counter(node.tag.rsplit("}",1)[-1] for node in root))}),flush=True)
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
    r=get(src["url"],"text/html"); soup=BeautifulSoup(r.content,"html.parser"); main=soup
    names=[]; active=False
    for h in main.select("h1,h2,h3,h4"):
        text=clean(h.get_text(" ",strip=True))
        if text=="Currently listed entities": active=True; continue
        if active and text=="About this site": break
        if active and text and text not in {"Notice of amendments"}: names.append(text)
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
    link.wait_for(timeout=TIMEOUT*1000); href=link.get_attribute("href")
    downloads=[]; page.on("download",lambda item: downloads.append(item))
    link.click(force=True)
    body=None
    for _ in range(24):
        if downloads:
            path=downloads[0].path(); body=Path(path).read_bytes(); break
        response=page.context.request.get(href,headers={"Referer":page.url},timeout=30000)
        candidate=response.body()
        if response.ok and len(candidate)>100 and b"Title" in candidate[:500] and b"Entity" in candidate[:500]:
            body=candidate; break
        page.wait_for_timeout(5000)
    page.close()
    if not body: raise RuntimeError("IDB dataset download did not become available after retry window")
    text=body.decode("utf-8-sig"); reader=csv.DictReader(io.StringIO(text))
    headers=reader.fieldnames or []
    required={"Title","Entity","Country","From","To","Prohibited Practice","IDB Sanction Source"}
    missing=sorted(required-set(headers))
    if missing: raise RuntimeError(f"IDB required columns missing: {missing}; headers={headers}")
    out=[]
    for row in reader:
        name=clean(row.get("Title"))
        if not name or name.upper()=="NULL": continue
        country=clean(row.get("Country")) or clean(row.get("Nationality"))
        reference=" / ".join(x for x in (clean(row.get("IDB Sanction Source")),clean(row.get("Tipo de sancion del BID"))) if x)
        out.append(record(src["name"],name,country=country,ref=reference,
                          listed=row.get("From",""),until=row.get("To",""),
                          grounds=row.get("Prohibited Practice",""),
                          kind=clean(row.get("Entity")) or "Firm / individual"))
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
    raw_count=len(all_records)
    all_records,duplicates_removed=deduplicate_records(all_records)
    for item in metadata:
        item["recordCount"]=sum(1 for row in all_records if row[1]==item["name"])
    for i,row in enumerate(all_records,1): row[0]=i
    # Emit source counts before the safety check so a blocked run can be diagnosed.
    previous_manifest_path=ROOT/"data/manifest.json"
    previous_manifest=json.loads(previous_manifest_path.read_text()) if previous_manifest_path.exists() else {}
    previous_counts={item["id"]:item["recordCount"] for item in previous_manifest.get("sources",[])}
    print(json.dumps({"diagnostic":"pre-publication source counts",
                      "previousTotal":previous_manifest.get("recordCount"),
                      "currentTotal":len(all_records),
                      "sources":{item["id"]:{"previous":previous_counts.get(item["id"]),
                                              "current":item["recordCount"]} for item in metadata}}),flush=True)
    if len(all_records)<7500: raise RuntimeError(f"Combined dataset unexpectedly small: {len(all_records)}")
    stamp=datetime.now(timezone.utc).strftime("%Y-%m-%d")
    package={"schemaVersion":1,"datasetVersion":f"official-{stamp}","generatedAt":now(),
             "recordCount":len(all_records),"sources":metadata,"records":all_records}
    schema=json.loads((ROOT/"schema/update-package.schema.json").read_text()); validate(package,schema)
    out=ROOT/"data"; out.mkdir(exist_ok=True)
    current_path=out/"vendor-screening-data.json"
    previous=json.loads(current_path.read_text()) if current_path.exists() else None
    payload=json.dumps(package,ensure_ascii=False,separators=(",",":")).encode()
    current_path.write_bytes(payload)

    # The first deduplicated package becomes the fixed audit baseline. Later
    # removals remain in dated delta files but do not stay active for screening.
    baseline_path=out/"retention-baseline.json"
    flawed_baseline_sha="61d1e254c69decc05690c644fa80a688df4da0d47be1999ced0e93bba3dbb198"
    reset_baseline=not baseline_path.exists() or sha(baseline_path.read_bytes())==flawed_baseline_sha
    if reset_baseline:
        baseline_path.write_bytes(payload)
        previous=None
    added,removed=dataset_delta(previous.get("records",[]) if previous else [],all_records)
    if previous and (added or removed):
        history=out/"history"; history.mkdir(exist_ok=True)
        delta={"schemaVersion":1,"fromDatasetVersion":previous.get("datasetVersion"),"toDatasetVersion":package["datasetVersion"],
               "generatedAt":package["generatedAt"],"addedCount":len(added),"removedCount":len(removed),
               "addedRecords":added,"removedRecords":removed}
        (history/f"{stamp}.json").write_text(json.dumps(delta,ensure_ascii=False,indent=2)+"\n")

    manifest={"schemaVersion":1,"datasetVersion":package["datasetVersion"],"generatedAt":package["generatedAt"],
              "recordCount":package["recordCount"],"sha256":sha(payload),"dataUrl":"vendor-screening-data.json",
              "sources":metadata}
    (out/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"rawRecordCount":raw_count,"duplicatesRemoved":len(duplicates_removed),
                      "recordCount":len(all_records),"sources":{m["id"]:m["recordCount"] for m in metadata},
                      "addedSincePrevious":len(added),"removedSincePrevious":len(removed),"sha256":sha(payload)}))

if __name__=="__main__":
    try: main()
    except Exception as exc:
        print(f"UPDATE BLOCKED: {exc}",file=sys.stderr); raise
