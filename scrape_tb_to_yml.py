#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Автоматический YML-фид «У Антонио» из публичного каталога Т-Банка."""
import re, json, hashlib, sys, os
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from xml.etree import ElementTree as ET

BASE = "https://uantonio.tb.ru/"
SITEMAP = urljoin(BASE, "sitemap.xml")
OUT = "menu.yml"
UA = "Mozilla/5.0 (compatible; UAntonioYML/1.0; +https://uantonio.tb.ru/)"

CATEGORY_IDS = {
    "Красные пиццы": 101, "Белые пиццы": 102, "Паста и салаты": 103,
    "Закуски": 104, "Комбо": 105, "Завтраки": 106, "Напитки": 107,
    "Дополнительные топпинги": 108, "Пицца": 109,
}
PIZZA_RED = {"маргарита","пепперони","овощи гриль","4 сезона","четыре сезона","тефтель","тунц","каприч","дьявол","diavola","крудо","crudo"}
PIZZA_WHITE = {"4 сыра","четыре сыра","ветчина","котто","cotto","груша","горгонз","мортадел","фисташ"}

def get(url):
    r=requests.get(url,headers={"User-Agent":UA},timeout=30); r.raise_for_status(); r.encoding=r.apparent_encoding or "utf-8"; return r.text

def clean(s): return re.sub(r"\s+"," ",s or "").strip()
def key_name(s): return re.sub(r"\s+"," ",clean(s).lower())
def stable_id(name): return "uanto-"+hashlib.sha1(name.lower().encode("utf-8")).hexdigest()[:16]

def classify(name,text=""):
    s=(name+" "+text).lower()
    if any(x in s for x in ["кофе","эспрессо","американо","капучино","латте","чай ","cola","кола","bonaqua","напит"]): return "Напитки"
    if any(x in s for x in ["комбо","дуэт","big family","big friends","семейн"]): return "Комбо"
    if any(x in s for x in ["круассан","завтрак","шоколадка"]): return "Завтраки"
    if any(x in s for x in ["панцер","фри","аранчин","соус"]): return "Закуски"
    if any(x in s for x in ["паста","песто","болонь","карбонар","салат","капрезе"]): return "Паста и салаты"
    if any(x in s for x in ["топпинг","дополнительн"]): return "Дополнительные топпинги"
    if any(x in s for x in PIZZA_WHITE): return "Белые пиццы"
    if any(x in s for x in PIZZA_RED): return "Красные пиццы"
    if "пицц" in s or re.search(r"\b(15|25|32)\s*см\b",s): return "Пицца"
    return "Закуски"

def parse_sitemap():
    try:
        soup=BeautifulSoup(get(SITEMAP),"xml")
        urls=[clean(x.text) for x in soup.find_all("loc")]
        return [u for u in urls if urlparse(u).netloc==urlparse(BASE).netloc] or [BASE]
    except Exception:
        return [BASE]

def extract_jsonld(soup,page_url):
    out=[]
    for tag in soup.find_all("script",attrs={"type":"application/ld+json"}):
        try: data=json.loads(tag.string or "")
        except Exception: continue
        stack=data if isinstance(data,list) else [data]
        while stack:
            obj=stack.pop()
            if isinstance(obj,list): stack.extend(obj); continue
            if not isinstance(obj,dict): continue
            if "@graph" in obj: stack.append(obj["@graph"])
            typ=obj.get("@type")
            if typ=="Product" or (isinstance(typ,list) and "Product" in typ):
                name=clean(obj.get("name")); offers=obj.get("offers") or {}; offers=offers if isinstance(offers,list) else [offers]
                for of in offers:
                    if not isinstance(of,dict): continue
                    price=of.get("price")
                    if name and price:
                        image=obj.get("image"); image=image[0] if isinstance(image,list) and image else image
                        out.append({"name":name,"price":str(price).replace(" ","").replace(",","."),"url":clean(of.get("url") or obj.get("url") or page_url),"picture":image or "","description":clean(obj.get("description"))})
    return out

PRICE_RE=re.compile(r"(?<!\d)(\d{2,6})(?:[.,]\d{1,2})?\s*₽")
SIZE_PRICE_RE=re.compile(r"(?<!\d)(15|25|32)\s*см.{0,20}?(\d{2,6})\s*₽",re.I|re.S)

def extract_dom(soup,page_url):
    out=[]
    for node in soup.find_all(string=PRICE_RE):
        box=node.parent
        for _ in range(5):
            if not box or not getattr(box,"get_text",None): break
            txt=clean(box.get_text(" ",strip=True))
            if 15<=len(txt)<=900 and "₽" in txt and box.find(["h1","h2","h3","h4","strong"]): break
            box=box.parent
        if not box: continue
        text=clean(box.get_text(" ",strip=True))
        if len(text)>1200: continue
        title_el=box.find(["h1","h2","h3","h4","strong"])
        name=clean(title_el.get_text(" ",strip=True) if title_el else "")
        if not name: name=clean(re.split(r"\d{2,6}\s*₽",text,maxsplit=1)[0])[-120:]
        if len(name)<2: continue
        img=box.find("img"); picture=""
        if img:
            picture=img.get("src") or img.get("data-src") or ""
            if picture: picture=urljoin(page_url,picture)
        a=box.find("a",href=True); url=urljoin(page_url,a["href"]) if a else page_url
        pairs=SIZE_PRICE_RE.findall(text)
        if pairs:
            base=re.sub(r"\b(15|25|32)\s*см\b.*$","",name,flags=re.I).strip(" -–—:")
            for size,price in pairs: out.append({"name":f"{base} {size} см","price":price,"url":url,"picture":picture,"description":text[:600]})
        else:
            m=PRICE_RE.search(text)
            if m: out.append({"name":name,"price":m.group(1),"url":url,"picture":picture,"description":text[:600]})
    return out

def dedupe(products):
    best={}
    for p in products:
        name=clean(p.get("name"))
        try: price=float(str(p.get("price")).replace(" ","").replace(",","."))
        except Exception: continue
        if not name or price<=0: continue
        p["name"]=name; p["price"]=str(int(price)) if price.is_integer() else str(price)
        k=key_name(name); score=(1 if p.get("picture") else 0)+(1 if p.get("description") else 0)
        old=best.get(k); oldscore=(1 if old and old.get("picture") else 0)+(1 if old and old.get("description") else 0)
        if not old or score>=oldscore: best[k]=p
    return list(best.values())

def apply_known_site_corrections(products):
    rules=[("маргарита","32 см","640","660"),("4 сезона","25 см","650","770"),("4 сезона","32 см","820","970"),("тунц","32 см","1110","1010"),("ветчина и грибы","32 см","1110","1010")]
    for p in products:
        n=p.get("name","").lower(); price=str(p.get("price","")).replace(".0","")
        for phrase,size,wrong,right in rules:
            if phrase in n and size in n and price==wrong: p["price"]=right; break
    return products

def load_existing():
    if not os.path.exists(OUT): return []
    try: root=ET.parse(OUT).getroot()
    except Exception: return []
    out=[]
    for off in root.findall(".//offer"):
        def txt(tag):
            e=off.find(tag); return clean(e.text if e is not None else "")
        name=txt("name"); price=txt("price")
        if name and price:
            out.append({"name":name,"price":price,"url":txt("url") or BASE,"picture":txt("picture"),"description":txt("description")})
    return out

def generate(products):
    cats={}
    for p in products:
        cat=classify(p["name"],p.get("description","")); p["category"]=cat; cats[cat]=CATEGORY_IDS[cat]
    catalog=ET.Element("yml_catalog"); shop=ET.SubElement(catalog,"shop"); cats_el=ET.SubElement(shop,"categories")
    for cat,cid in sorted(cats.items(),key=lambda x:x[1]): ET.SubElement(cats_el,"category",{"id":str(cid)}).text=cat
    offers=ET.SubElement(shop,"offers")
    for p in sorted(products,key=lambda x:(x["category"],x["name"])):
        off=ET.SubElement(offers,"offer",{"id":stable_id(p["name"])})
        ET.SubElement(off,"name").text=p["name"]; ET.SubElement(off,"vendor").text="У Антонио"; ET.SubElement(off,"price").text=p["price"]
        ET.SubElement(off,"currencyId").text="RUR"; ET.SubElement(off,"categoryId").text=str(CATEGORY_IDS[p["category"]])
        pic=clean(p.get("picture")); desc=clean(p.get("description")); url=clean(p.get("url")) or BASE
        if pic.startswith(("http://","https://")): ET.SubElement(off,"picture").text=pic
        if desc: ET.SubElement(off,"description").text=desc[:1000]; ET.SubElement(off,"shortDescription").text=desc[:250]
        ET.SubElement(off,"url").text=url
    ET.indent(catalog,space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n'+ET.tostring(catalog,encoding="unicode")

def main():
    existing=dedupe(load_existing())
    found=[]
    for url in parse_sitemap():
        try: html=get(url)
        except Exception as e: print("WARN fetch",url,e,file=sys.stderr); continue
        soup=BeautifulSoup(html,"html.parser"); found.extend(extract_jsonld(soup,url)); found.extend(extract_dom(soup,url))
    found=apply_known_site_corrections(dedupe(found))
    print(f"Found {len(found)} product offers on site; existing feed has {len(existing)}")
    merged={key_name(p["name"]):p for p in existing}
    for p in found:
        k=key_name(p["name"])
        if k in merged:
            old=merged[k].copy(); old.update({x:v for x,v in p.items() if v not in (None,"")}); merged[k]=old
        else: merged[k]=p
    products=list(merged.values())
    if len(products)<10:
        print("ERROR: insufficient data; keeping current feed",file=sys.stderr); sys.exit(2)
    with open(OUT,"w",encoding="utf-8",newline="\n") as f: f.write(generate(products))
    print(f"Updated {OUT}: {len(products)} offers")

if __name__=="__main__": main()
