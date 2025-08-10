# ✨ Artify — buscador de convocatorias de arte para Fla ❤️
# Modo Profundo (parsea sitios) + Modo Rápido (agregadores) para asegurar 10–40 resultados.
# Sin "dificultad". Deduplicación fuerte. Resumen automático en español.

import re, io, csv, time, socket
from datetime import date, timedelta
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
import streamlit as st

# ───────────── Config UI
st.set_page_config(page_title="Artify — buscador de convocatorias de arte para Fla ❤️", layout="wide")
st.markdown("""
<style>
:root{--card-bg:#fff;--muted:#6b7280;}
.block-container{padding-top:2rem;padding-bottom:2rem;}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem}
.topbar h1{font-weight:800;margin:0}
.filterbox{border:1px solid #e5e7eb;border-radius:14px;padding:18px;margin-bottom:12px;background:#fafafa}
.badge{display:inline-block;background:#f3f4f6;color:#111827;border-radius:999px;padding:.2rem .6rem;font-size:.80rem;margin-right:.35rem}
.card{border:1px solid #e5e7eb;border-radius:16px;padding:18px;background:var(--card-bg);margin:10px 0}
.card h3{margin:0 0 6px 0}
.meta{color:var(--muted);font-size:.88rem;margin:.25rem 0}
.kpis{display:flex;gap:8px;flex-wrap:wrap;margin:.5rem 0}
.kpis .pill{background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:.25rem .5rem;font-size:.85rem}
.btn-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:.5rem}
hr.sep{border:none;border-top:1px solid #eee;margin:8px 0 16px}
.small{font-size:.9rem;color:var(--muted)}
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="topbar"><h1>✨ Artify — buscador de convocatorias de arte para Fla ❤️</h1></div>', unsafe_allow_html=True)
st.caption("Motor argentino curado. Si el rastreo profundo encuentra poco, activo un modo rápido que cosecha links de agregadores (siempre en español).")

# ───────────── Constantes
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
REQ_TIMEOUT = 9
MAX_WORKERS = 16

CURATED_AR_SEEDS = [
    "https://www.klemm.org.ar/",
    "https://premioklemm.klemm.org.ar/",
    "https://www.fnartes.gob.ar/",
    "https://palaisdeglace.cultura.gob.ar/",
    "https://www.cultura.gob.ar/",
    "https://enteculturaltucuman.gob.ar/",
    "https://www.fundacionosde.com.ar/",
    "https://museorosagalisteo.gob.ar/",
    "https://www.castagninomacro.org/",
    "https://museosiivori.buenosaires.gob.ar/",
]

AGGREGATORS = [
    "https://www.catalogosparaartistas.com/convocatorias",
    "https://es.artealdia.com/Convocatorias",
    "https://www.recursosculturales.com/",
]

KEYWORDS = ["convocatoria","premio","salón","salon","residenc","beca","open call","inscripción","cierre","bases","concurso"]

SKIP_HOSTS = (
    "instagram.com","facebook.com","x.com","twitter.com","tiktok.com",
    "youtube.com","linkedin.com","pinterest.","flickr.","tumblr.","vimeo.com"
)
SKIP_EXTS = (".jpg",".jpeg",".png",".gif",".webp",".doc",".docx",".xls",".xlsx",".zip",".rar")

BAD_TITLE = re.compile(r"(cookies|privacidad|privacy|prensa|press|t[eé]rminos|terms?|acerca|about|colecci[oó]n|historia|contacto|mapa del sitio)", re.I)

# ───────────── Utils red/texto
def normalize_url(u: str, base: str):
    if not u: return None
    u=u.strip()
    if u.startswith("//"): u="https:"+u
    if u.startswith("/"):  u=urljoin(base,u)
    if not re.match(r"^https?://", u): return None
    return u.split("#")[0]

def fetch(url:str):
    r=requests.get(url, headers=HEADERS, timeout=REQ_TIMEOUT, allow_redirects=True, stream=True)
    r.raise_for_status()
    return r.content, r.headers.get("Content-Type","").lower(), r.url

def is_pdf(data:bytes, ct:str):
    return "application/pdf" in ct or data[:4]==b"%PDF"

def to_html(data:bytes, ct:str):
    if is_pdf(data, ct): return "", None
    try: return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        try: return data.decode("latin-1"), "latin-1"
        except UnicodeDecodeError: return "", None

def cleanup(s:str):
    if not s: return ""
    s=s.replace("\x00"," ")
    s=re.sub(r"[\uFFFD]{2,}", " ", s)
    s=re.sub(r"[^\x09\x0A\x0D\x20-\x7E\u00A0-\uFFFF]", " ", s)
    s=re.sub(r"\s{2,}", " ", s).strip()
    return s

def sentences(text:str): return [t.strip() for t in re.split(r"(?<=[\.\!\?])\s+", text) if t.strip()]

# ───────────── Fechas (ES)
MONTHS={'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,'julio':7,'agosto':8,'septiembre':9,'setiembre':9,'octubre':10,'noviembre':11,'diciembre':12}
def _mk(y,m,d):
    try: return date(y,m,d)
    except: return None
def parse_spanish_date(txt:str):
    if not txt: return None
    s=txt.lower().replace("º","").replace("°","")
    s=re.sub(r"(\d)(?:st|nd|rd|th)", r"\1", s)
    m=re.search(r"(\d{1,2})\s+de\s+([a-zá]+)\s+de\s+(\d{4})", s)
    if m:
        d=int(m.group(1)); mon=m.group(2).replace("á","a"); y=int(m.group(3))
        if mon in MONTHS: return _mk(y, MONTHS[mon], d)
    m=re.search(r"(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})", s)
    if m:
        a=int(m.group(1)); b=int(m.group(2)); y=int(m.group(3))
        if y<100: y+=2000
        return _mk(y,b,a) or _mk(y,a,b)
    m=re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if m: return _mk(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None
DATE_PATS=[
    r"(?:fecha(?:\s+l[ií]mite)?(?:\s+de)?\s*(?:aplicaci[oó]n|postulaci[oó]n|cierre|presentaci[oó]n)?:?\s*)(\d{1,2}\s+de\s+\w+\s+\d{4})",
    r"(?:cierran?\s+el\s+|cierra\s+el\s+|hasta el\s+)(\d{1,2}\s+de\s+\w+\s+\d{4})",
    r"(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})",
]
RANGE_PATS=[
    r"del\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})\s+al\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})",
    r"del\s+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})\s+al\s+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})",
]
def extract_deadline(text:str):
    if not text: return None
    s=str(text)
    for pat in DATE_PATS:
        m=re.search(pat,s,re.I)
        if m:
            d=parse_spanish_date(m.group(1))
            if d: return d
    m=re.search(r"\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}", s)
    if m:
        d=parse_spanish_date(m.group(0))
        if d: return d
    return None
def extract_range(text:str):
    if not text: return (None,None)
    s=str(text)
    for pat in RANGE_PATS:
        m=re.search(pat,s,re.I)
        if m: return parse_spanish_date(m.group(1)), parse_spanish_date(m.group(2))
    m=re.search(r"hasta(?:\s+el)?\s+([^\.;,\n]+)", s, re.I)
    if m: return None, parse_spanish_date(m.group(1))
    return (None, extract_deadline(s))
def days_left(d): return None if not d else (d - date.today()).days

# ───────────── Clasificación + resumen
def extract_title_desc(soup:BeautifulSoup):
    title=""
    for sel in ["meta[property='og:title']","meta[name='twitter:title']"]:
        m=soup.select_one(sel)
        if m and m.get("content"): title=m["content"]; break
    if not title:
        h1=soup.select_one("h1")
        if h1: title=cleanup(h1.get_text(" "))
    if not title:
        t=soup.select_one("title")
        if t: title=cleanup(t.get_text())
    desc=""
    for sel in ["meta[name='description']","meta[property='og:description']"]:
        m=soup.select_one(sel)
        if m and m.get("content"): desc=m["content"]; break
    if not desc:
        p=soup.select_one("p")
        if p: desc=cleanup(p.get_text(" "))
    return (title or "Convocatoria"), (desc or "")

def type_guess(text:str):
    s=(text or "").lower()
    if "residenc" in s: return "Residencias"
    if "beca" in s: return "Becas"
    if "premio" in s or "salón" in s or "salon" in s or "concurso" in s: return "Concursos"
    if "exposición" in s or "exposicion" in s: return "Exposiciones"
    return "Convocatorias"

def guess_location(text:str):
    s=(text or "").lower()
    if "argentina" in s or "buenos aires" in s or "caba" in s: return "Argentina"
    if "internacional" in s: return "Internacional"
    return "—"
def scope_from_location(loc:str):
    if loc=="Argentina": return "AR"
    if loc=="—": return "UNK"
    return "EX"

def extract_key_data(text:str):
    s=(text or "")
    m_amt=re.search(r"(USD|US\$|€|\$)\s?([\d\.\,]+)", s, re.I)
    premio=f"{m_amt.group(1).upper()} {m_amt.group(2)}" if m_amt else "—"
    m_slots=re.search(r"(\d+)\s+(cupos|ganadores|becas|finalistas)", s, re.I)
    cupos=m_slots.group(1) if m_slots else "—"
    m_fee=re.search(r"(?:fee|arancel|inscripci[oó]n)\s*(?:de)?\s*(USD|US\$|€|\$)?\s*([\d\.\,]+)", s, re.I)
    fee=(m_fee.group(1) or "$")+" "+m_fee.group(2) if m_fee else "0"
    return premio, cupos, fee

def smart_title_guess(title:str, text:str, domain:str):
    raw = (title or "").strip()
    if raw and not re.fullmatch(r"(convocatoria|home|inicio|noticias?)", raw, re.I):
        base = raw
    else:
        base = ""
    parts = re.split(r"\s*[|\-—–·]\s*", base) if base else []
    if not parts:
        m=re.search(r"(premio|sal[oó]n|residenc\w+|beca|open call|convocatoria)[^\.]{0,80}", (text or ""), re.I)
        if m: parts=[m.group(0)]
    if not parts:
        return f"Convocatoria ({domain.replace('www.','')})"
    best=None; score_best=-1
    for p in parts:
        s=0
        s+=sum(k in p.lower() for k in ["premio","salón","salon","residenc","beca","convocatoria","open call"])
        s+=len(re.findall(r"20\d{2}", p))
        if s>score_best: score_best=s; best=p
    def friendly_tc(x):
        words=x.split()
        out=[]
        for w in words:
            if len(w)<=3 and w.isupper(): out.append(w)
            elif w.lower() in {"de","del","la","las","los","y","en","a","al","para"}: out.append(w.lower())
            else: out.append(w[:1].upper()+w[1:].lower())
        return " ".join(out)
    return friendly_tc(best.strip(" .:;-"))[:140]

def resumen_ia(text:str,n=3,max_chars=360):
    txt=cleanup(text or "")
    if not txt: return "Publicación con poca descripción. Abrí “Bases / Reglamento” para ver requisitos."
    sents=sentences(txt)
    scored=[]
    for s in sents:
        score=sum(int(k in s.lower()) for k in KEYWORDS)+len(re.findall(r"\d{4}", s))
        scored.append((score,s))
    scored.sort(reverse=True)
    chosen=[s for _,s in scored[:n]] or sents[:n]
    res=" ".join(chosen)
    return (res[:max_chars]+"…") if len(res)>max_chars else res

def best_links(soup: BeautifulSoup, base_url: str):
    links={"principal":None,"bases":None,"inscripcion":None,"pdfs":[]}
    og=soup.select_one("meta[property='og:url']")
    if og and og.get("content"): links["principal"]=normalize_url(og["content"],base_url)
    if not links["principal"]:
        can=soup.select_one("link[rel='canonical']")
        if can and can.get("href"): links["principal"]=normalize_url(can["href"],base_url)
    candidates=[]
    for a in soup.select("a[href]"):
        href=normalize_url(a["href"], base_url)
        if not href: continue
        text=(a.get_text(" ") or "").strip().lower()
        candidates.append((text, href))
        if href.lower().endswith(".pdf"): links["pdfs"].append(href)
    def pick(patterns, avoid_pdf=True):
        for txt, href in candidates:
            if avoid_pdf and href.lower().endswith(".pdf"): continue
            if any(p in txt for p in patterns): return href
        return None
    links["inscripcion"]=pick(["inscrip","postul","apply","registro","formulario","aplicar"])
    links["bases"]=pick(["base","reglamento","condicion","término","terms","rules"], avoid_pdf=False)
    if not links["principal"]:
        for txt, href in candidates:
            if href and not href.endswith("#"): links["principal"]=href; break
    if not links["bases"]:
        for p in links["pdfs"]:
            links["bases"]=p; break
    return links

# ───────────── Parser genérico (relajado)
def parse_generic(url:str):
    try:
        data, ct, final = fetch(url)
        html, _ = to_html(data, ct)
        if not html:  # PDF/binario
            host=urlparse(final).netloc.replace("www.","")
            fname=final.split("/")[-1]
            title=smart_title_guess(fname,"",host)
            return {
                "source": host, "title": title, "url": final,
                "open_at": None, "deadline": None, "type":"Convocatorias",
                "location":"—","scope":"UNK","prize":"—","slots":"—","fee":"0","free":True,
                "summary":"Documento PDF de bases / reglamento.",
                "links":{"principal":final,"bases":final,"inscripcion":None}
            }
        soup=BeautifulSoup(html,"html.parser")
        raw_title, meta_desc = extract_title_desc(soup)
        if BAD_TITLE.search((raw_title or "")+" "+final): return None
        full=cleanup(soup.get_text(" "))

        abre, cierra = extract_range(full)
        tipo = type_guess(raw_title+" "+meta_desc+" "+full)
        loc  = guess_location(raw_title+" "+full)
        premio, cupos, fee = extract_key_data(full)
        titulo = smart_title_guess(raw_title, full, urlparse(final).netloc)
        resumen = resumen_ia(meta_desc or full)
        links = best_links(soup, final)
        free = bool(re.search(r"(sin costo|sin cargo|gratuit[oa]|gratis)", full.lower()))
        return {
            "source": urlparse(final).netloc.replace("www.",""),
            "title": titulo, "url": final,
            "open_at": abre, "deadline": cierra, "type": tipo,
            "location": loc, "scope": scope_from_location(loc),
            "prize": premio, "slots": cupos, "fee": fee, "free": free,
            "summary": resumen, "links": links
        }
    except Exception:
        return None

# ───────────── Parsers dedicados (retoques)
def parse_klemm(u): return parse_generic(u)
def parse_fna(u):   return parse_generic(u)
def parse_palais(u):return parse_generic(u)
def parse_tucu(u):  return parse_generic(u)
def parse_osde(u):  return parse_generic(u)

DOMAIN_PARSERS={
    "klemm.org.ar": parse_klemm, "premioklemm.klemm.org.ar": parse_klemm,
    "fnartes.gob.ar": parse_fna, "palaisdeglace.cultura.gob.ar": parse_palais,
    "cultura.gob.ar": parse_palais, "enteculturaltucuman.gob.ar": parse_tucu,
    "fundacionosde.com.ar": parse_osde,
}

def parse_page(url:str):
    host=urlparse(url).netloc.lower().replace("www.","")
    return DOMAIN_PARSERS.get(host, parse_generic)(url)

# ───────────── Rastrear sitios curados (pocas páginas por sitio)
def seems_call(url:str, text:str):
    u=url.lower()
    if any(k in u for k in ("/convocatoria","/convocatorias","/premio","/residenc","/salon","/salón","/beca")): return True
    s=(text or "").lower()
    return any(k in s for k in ("convocatoria","premio","salón","salon","residenc","beca","open call","exposición","exposicion"))

@st.cache_data(ttl=3600, show_spinner=False)
def crawl_site(seed:str, per_site:int=12):
    host=urlparse(seed).netloc
    visited=set(); queue=[seed]; found=[]
    while queue and len(visited)<per_site:
        url=queue.pop(0)
        if url in visited: continue
        visited.add(url)
        try:
            data, ct, final = fetch(url)
            html,_=to_html(data, ct)
        except Exception:
            continue
        soup=BeautifulSoup(html or "<html></html>", "html.parser")
        text=cleanup(soup.get_text(" ")) if html else ""
        if seems_call(url, text) and not BAD_TITLE.search((soup.title.get_text() if soup.title else "")+" "+final):
            found.append(url)
        for a in soup.select("a[href]"):
            href=a["href"].strip()
            if not href or href.startswith("#") or href.startswith("mailto:"): continue
            u=urljoin(url, href); p=urlparse(u)
            if p.netloc!=host: continue
            if any(p.path.lower().endswith(ext) for ext in SKIP_EXTS): continue
            if any(b in p.netloc for b in SKIP_HOSTS): continue
            if u not in visited and u not in queue and len(queue)<per_site*2:
                queue.append(u)
    uniq=[]; seen=set()
    for u in found:
        base=u.split("#")[0]
        if base in seen: continue
        seen.add(base); uniq.append(base)
    return uniq[:per_site]

def gather_curated(limit:int=80):
    per=max(8, limit//max(1,len(CURATED_AR_SEEDS)))
    urls=[]
    for s in CURATED_AR_SEEDS: urls+=crawl_site(s, per_site=per)
    clean=[]; seen=set()
    for u in urls:
        host=urlparse(u).netloc.lower()
        if any(h in host for h in SKIP_HOSTS): continue
        base=u.split("#")[0]
        if base in seen: continue
        seen.add(base); clean.append(base)
        if len(clean)>=limit: break
    return clean

# ───────────── HARVESTER (modo rápido) — solo saca links de agregadores
def harvest_aggregators(max_links:int=60):
    out=[]
    for base in AGGREGATORS:
        try:
            data, ct, final = fetch(base)
            html,_ = to_html(data, ct)
            soup=BeautifulSoup(html or "<html></html>","html.parser")
            for a in soup.select("a[href]"):
                href=normalize_url(a.get("href"), final)
                if not href: continue
                host=urlparse(href).netloc.lower()
                txt=(a.get_text(" ") or "").strip()
                if any(h in host for h in SKIP_HOSTS): continue
                if BAD_TITLE.search(txt+" "+href): continue
                if not any(k in (txt+" "+href).lower() for k in KEYWORDS): continue
                out.append({
                    "source": host.replace("www.",""),
                    "title": cleanup(txt) or f"Convocatoria ({host.replace('www.','')})",
                    "url": href,
                    "open_at": None, "deadline": None,
                    "type": type_guess(txt), "location":"—","scope":"UNK",
                    "prize":"—","slots":"—","fee":"0","free": True,
                    "summary": "Link de agregador. Abrí la publicación y buscá “Bases / Reglamento”.",
                    "links": {"principal": href, "bases": None, "inscripcion": None}
                })
                if len(out)>=max_links: break
        except Exception:
            continue
    # dedupe por (host,título,url)
    ded={}
    for r in out:
        k=(r["source"], re.sub(r"\W+"," ", r["title"].lower()), r["url"])
        ded[k]=r
    return list(ded.values())

# ───────────── UI filtros
st.markdown('<div class="filterbox">', unsafe_allow_html=True)
c1, c2 = st.columns([0.7,0.3])
with c1: q = st.text_input("Búsqueda", placeholder="residencia, FNA, performance")
with c2: ordenar = st.selectbox("Ordenar por", ["Fecha límite", "Título"])

c3, c4, c5 = st.columns([0.6,0.25,0.15])
with c3:
    cats = st.multiselect("Categorías", ["Becas","Residencias","Concursos","Exposiciones","Convocatorias"],
                          default=["Becas","Residencias","Concursos","Convocatorias"])
with c4: ubic = st.selectbox("Ubicación", ["Todas","Argentina","Internacional"])
with c5: sin_costo = st.checkbox("Solo sin arancel", value=False)

c6,c7,c8 = st.columns([0.25,0.25,0.5])
with c6: d_from = st.date_input("Desde", value=None)
with c7: d_to   = st.date_input("Hasta", value=None)
with c8: inten  = st.slider("Intensidad (páginas aprox.)", 24, 96, 60)
st.markdown('</div>', unsafe_allow_html=True)

bot = st.button("Buscar convocatorias", type="primary")

# ───────────── RUN
if bot:
    t0=time.time()
    # 1) Modo profundo
    urls = gather_curated(limit=int(inten*1.2))
    results=[]; done=0; prog=st.progress(0)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs=[ex.submit(parse_page,u) for u in urls]
        for f in as_completed(futs):
            rec=None
            try: rec=f.result()
            except: rec=None
            if rec: results.append(rec)
            done+=1; prog.progress(min(1.0, done/max(1,len(futs))))
            if time.time()-t0 > 40: break
    prog.empty()

    # Si el profundo trajo poco, 2) HARVEST rápido de agregadores (garantiza volumen)
    if len(results) < 8:
        results += harvest_aggregators(max_links=72)

    # Deduplicado fuerte
    def norm(t): return re.sub(r"\W+"," ", (t or "").strip().lower())
    ded={}
    for r in results:
        key=(r["source"], norm(r["title"]), r["url"])
        if key not in ded: ded[key]=r
    results=list(ded.values())

    # Filtros
    map_c={"Becas":"Becas","Residencias":"Residencias","Concursos":"Concursos",
           "Exposiciones":"Exposiciones","Convocatorias":"Convocatorias"}
    def keep(r):
        if sin_costo and not r.get("free"): return False
        if ubic=="Argentina" and r.get("scope")!="AR": return False
        if ubic=="Internacional" and r.get("scope")=="AR": return False
        if cats and r.get("type") not in [map_c[c] for c in cats]: return False
        d=r.get("deadline")
        if d_from and d and d < d_from: return False
        if d_to   and d and d > d_to:   return False
        if q:
            blob=(r.get("title","")+" "+r.get("summary","")).lower()
            if q.lower() not in blob: return False
        return True

    items=[x for x in results if keep(x)]
    if ordenar=="Fecha límite":
        items.sort(key=lambda r:(r.get("deadline") is None, r.get("deadline") or date(2100,1,1)))
    else:
        items.sort(key=lambda r:r.get("title",""))

    # Header + export
    left, right = st.columns([0.5,0.5])
    with left: st.caption(f"**{len(items)} resultados**  ·  ⏱ {round(time.time()-t0,1)} s")
    with right:
        if items:
            buf=io.StringIO(); w=csv.writer(buf)
            w.writerow(["titulo","url","fuente","categoria","ubicacion","ambito","abre","cierra","premio","cupos","fee","resumen"])
            for c in items:
                w.writerow([
                    c["title"], c["url"], c["source"], c["type"], c["location"], c["scope"],
                    c["open_at"].strftime("%Y-%m-%d") if c.get("open_at") else "",
                    c["deadline"].strftime("%Y-%m-%d") if c.get("deadline") else "",
                    c["prize"], c["slots"], c["fee"], c["summary"]
                ])
            st.download_button("📄 Exportar CSV", buf.getvalue(), "artify_convocatorias.csv", "text/csv")

            def make_ics(items):
                def dtfmt(d): return d.strftime("%Y%m%d")
                ics=["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//Artify//Convocatorias//ES"]
                for c in items:
                    if not c.get("deadline"): continue
                    desc=(c.get("summary","")[:200]).replace("\n"," ")
                    ics+=["BEGIN:VEVENT",
                          f"SUMMARY:{c['title']} (cierre)",
                          f"DTSTART;VALUE=DATE:{dtfmt(c['deadline'])}",
                          f"DTEND;VALUE=DATE:{dtfmt(c['deadline']+timedelta(days=1))}",
                          f"DESCRIPTION:{desc}  URL: {c.get('url','')}",
                          "END:VEVENT"]
                ics.append("END:VCALENDAR")
                return "\n".join(ics)
            st.download_button("📅 Exportar ICS", make_ics(items), "artify_convocatorias.ics", "text/calendar")

    st.markdown("<hr class='sep'/>", unsafe_allow_html=True)

    if not items:
        st.info("No hubo resultados. Subí la **intensidad** o quitá filtros. El modo rápido se activa solo cuando el profundo trae poco.")

    for r in items:
        open_txt = r["open_at"].strftime("%d/%m/%Y") if r.get("open_at") else "—"
        dl=r.get("deadline"); dl_txt = dl.strftime("%d/%m/%Y") if dl else "Sin dato"
        left = days_left(dl)
        urgency = "🟢" if left is None else ("🟡" if left and left<=21 else "🟢")
        if left is not None and left <= 7: urgency="🔴"

        st.markdown('<div class="card">', unsafe_allow_html=True)
        hA, hB = st.columns([0.72,0.28])
        with hA:
            st.markdown(f"### {r['title']}")
            chips = " ".join([
                f"<span class='badge'>{r['type']}</span>",
                f"<span class='badge'>{r['location']}</span>",
                f"<span class='badge'>{r['source']}</span>",
                f"<span class='badge'>{'Sin costo' if r.get('free') else 'Con arancel'}</span>",
            ])
            st.markdown(chips, unsafe_allow_html=True)
            st.markdown(f"<div class='meta'><b>Abre:</b> {open_txt} &nbsp;•&nbsp; <b>Cierra:</b> {dl_txt} {f'({left} días)' if left is not None else ''} &nbsp;{urgency}</div>", unsafe_allow_html=True)
            st.write(r["summary"])
        with hB:
            st.markdown("<div class='kpis'>", unsafe_allow_html=True)
            st.markdown(f"<span class='pill'>Premio: {r['prize']}</span>", unsafe_allow_html=True)
            st.markdown(f"<span class='pill'>Cupos: {r['slots']}</span>", unsafe_allow_html=True)
            st.markdown(f"<span class='pill'>Fee: {r['fee']}</span>", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)
            st.markdown("<div class='btn-row'>", unsafe_allow_html=True)
            links=r.get("links",{})
            if links.get("inscripcion"): st.link_button("📝 Postular / Inscripción", links["inscripcion"])
            if links.get("bases"):       st.link_button("📄 Bases / Reglamento",   links["bases"])
            st.link_button("🌐 Abrir publicación", links.get("principal") or r["url"])
            st.markdown("</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)
else:
    st.info("Elegí filtros y dale a **Buscar convocatorias**. Si el rastreo profundo falla, se activa un modo rápido de agregadores para garantizar volumen.")
