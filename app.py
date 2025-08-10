# ✨ Artify — buscador de convocatorias de arte para Fla ❤️
# UI estilo “Hércules” + motor curado para Argentina (sin Google) + enlaces inteligentes.

import re, io, csv, time, socket
from datetime import date, datetime, timedelta
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
import streamlit as st

# ────────────────────────────────────────────────────────────────────────────────
# Config / Título
# ────────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Artify — buscador de convocatorias de arte para Fla ❤️", layout="wide")

# CSS de estilo (chips, tarjetas, topbar)
st.markdown("""
<style>
:root{--card-bg:#fff;--muted:#6b7280;--chip:#eef2ff;--chip-text:#3730a3;--ok:#10b981;--warn:#f59e0b;--bad:#ef4444;}
.block-container{padding-top:2rem;padding-bottom:2rem;}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem}
.topbar h1{font-weight:800;margin:0}
.toolbar .stButton>button{margin-left:.5rem}
.filterbox{border:1px solid #e5e7eb;border-radius:14px;padding:18px;margin-bottom:12px;background:#fafafa}
.row{display:flex;gap:12px;flex-wrap:wrap}
.col{flex:1 1 220px}
.badge{display:inline-block;background:#f3f4f6;color:#111827;border-radius:999px;padding:.2rem .6rem;font-size:.80rem;margin-right:.35rem}
.card{border:1px solid #e5e7eb;border-radius:16px;padding:18px;background:var(--card-bg);margin:10px 0}
.card h3{margin:0 0 6px 0}
.meta{color:var(--muted);font-size:.88rem;margin:.25rem 0}
.kpis{display:flex;gap:8px;flex-wrap:wrap;margin:.5rem 0}
.kpis .pill{background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:.25rem .5rem;font-size:.85rem}
.chips .stMultiSelect{min-width:260px}
button[kind="secondary"]{border:1px solid #e5e7eb}
.btn-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:.5rem}
hr.sep{border:none;border-top:1px solid #eee;margin:8px 0 16px}
.small{font-size:.9rem;color:var(--muted)}
</style>
""", unsafe_allow_html=True)

# Topbar
c1, c2 = st.columns([0.7,0.3])
with c1:
    st.markdown('<div class="topbar"><h1>✨ Artify — buscador de convocatorias de arte para Fla ❤️</h1></div>', unsafe_allow_html=True)
    st.caption("Resultados en español con título y reseña automática, dificultad (1–100) y export a CSV/ICS. Filtros curados para Argentina. No dependemos de Google.")
with c2:
    pass  # export va abajo cuando hay resultados

# ────────────────────────────────────────────────────────────────────────────────
# Constantes / seeds AR
# ────────────────────────────────────────────────────────────────────────────────
YEAR = date.today().year
MAX_WORKERS = 12
REQ_TIMEOUT = 8
HARD_TIME_LIMIT = 28  # tope total de crawl/parse para no colgar
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

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
    "https://www.catalogosparaartistas.com/convocatorias",
    "https://es.artealdia.com/Convocatorias",
    "https://www.recursosculturales.com/",
]

SKIP_HOSTS = (
    "instagram.com","facebook.com","x.com","twitter.com","tiktok.com",
    "youtube.com","linkedin.com","pinterest.","flickr.","tumblr.","vimeo.com"
)
SKIP_EXTS = (".jpg",".jpeg",".png",".gif",".webp",".doc",".docx",".xls",".xlsx",".zip",".rar")

# ────────────────────────────────────────────────────────────────────────────────
# Links inteligentes
# ────────────────────────────────────────────────────────────────────────────────
def normalize_url(u: str, base: str):
    if not u: return None
    u=u.strip()
    if u.startswith("//"): u="https:"+u
    if u.startswith("/"):  u=urljoin(base,u)
    if not re.match(r"^https?://", u): return None
    return u.split("#")[0]

def head_ok(u: str, timeout=7):
    if not u: return False
    try:
        r=requests.head(u, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code in (405,403):
            r=requests.get(u, headers=HEADERS, timeout=timeout, allow_redirects=True, stream=True)
        return 200<=r.status_code<400
    except (requests.RequestException, socket.error):
        return False

def best_links(soup: BeautifulSoup, base_url: str):
    links={"principal":None,"bases":None,"inscripcion":None,"pdfs":[]}
    og=soup.select_one("meta[property='og:url']")
    if og and og.get("content"):
        links["principal"]=normalize_url(og["content"],base_url)
    if not links["principal"]:
        can=soup.select_one("link[rel='canonical']")
        if can and can.get("href"):
            links["principal"]=normalize_url(can["href"],base_url)

    candidates=[]
    for a in soup.select("a[href]"):
        href=normalize_url(a["href"], base_url)
        if not href: continue
        text=(a.get_text(" ") or "").strip().lower()
        candidates.append((text, href))
        if href.lower().endswith(".pdf"):
            links["pdfs"].append(href)

    def pick(patterns, avoid_pdf=True):
        for txt, href in candidates:
            if avoid_pdf and href.lower().endswith(".pdf"): continue
            if any(p in txt for p in patterns): return href
        return None

    links["inscripcion"]=pick(["inscrip","postul","apply","registro","formulario","aplicar"])
    links["bases"]=pick(["base","reglamento","condicion","término","terms","rules"], avoid_pdf=False)

    if not links["principal"]:
        for txt, href in candidates:
            if href and not href.endswith("#"):
                links["principal"]=href; break

    for k in ["inscripcion","bases","principal"]:
        if links[k] and not head_ok(links[k], timeout=5):
            links[k]=None

    if not links["bases"]:
        for p in links["pdfs"]:
            if head_ok(p, timeout=5):
                links["bases"]=p; break
    return links

def link_button(label: str, url: str):
    if not url: return
    try: st.link_button(label, url)
    except Exception: st.markdown(f"[{label}]({url})")

# ────────────────────────────────────────────────────────────────────────────────
# Fechas ES robustas
# ────────────────────────────────────────────────────────────────────────────────
MONTHS={'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
        'julio':7,'agosto':8,'septiembre':9,'setiembre':9,'octubre':10,'noviembre':11,'diciembre':12}
def _mk_date(y,m,d):
    try: return date(y,m,d)
    except: return None
def parse_spanish_date(txt:str):
    if not txt: return None
    s=str(txt).lower().replace("º","").replace("°","")
    s=re.sub(r"(\d)(?:st|nd|rd|th)", r"\1", s)
    m=re.search(r"(\d{1,2})\s+de\s+([a-zá]+)\s+de\s+(\d{4})", s)
    if m:
        d=int(m.group(1)); mon=m.group(2).replace("á","a"); y=int(m.group(3))
        if mon in MONTHS: return _mk_date(y, MONTHS[mon], d)
    m=re.search(r"(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})", s)
    if m:
        a=int(m.group(1)); b=int(m.group(2)); y=int(m.group(3))
        if y<100: y+=2000
        return _mk_date(y,b,a) or _mk_date(y,a,b)
    m=re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if m: return _mk_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
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

# ────────────────────────────────────────────────────────────────────────────────
# IA liviana ES (título+resumen)
# ────────────────────────────────────────────────────────────────────────────────
KEYWORDS=["convocatoria","premio","salón","salon","residenc","beca","open call","inscripción","cierre","bases"]
def sentences(text:str): return [s.strip() for s in re.split(r"(?<=[\.\!\?])\s+", text) if s.strip()]
def resumen_ia(text:str,n=3,max_chars=360):
    txt=re.sub(r"\s+"," ",(text or "")).strip()
    if not txt: return "Convocatoria sin descripción."
    sents=sentences(txt)
    scored=[]
    for s in sents:
        score=sum(int(k in s.lower()) for k in KEYWORDS)+len(re.findall(r"\d{4}", s))
        scored.append((score,s))
    scored.sort(reverse=True)
    chosen=[s for _,s in scored[:n]] or sents[:n]
    res=" ".join(chosen)
    return (res[:max_chars]+"…") if len(res)>max_chars else res
def titulo_ia(title:str, text:str, domain:str):
    t=(title or "").strip()
    if t and not re.fullmatch(r"(convocatoria|home|inicio|noticias?)", t, re.I):
        return t[:140]
    m=re.search(r"(premio|sal[oó]n|residenc\w+|beca|open call)[^\.]{0,80}?(20\d{2})?", text, re.I)
    if m:
        pro=re.sub(r"\s{2,}"," ", m.group(0)).strip(" .:;-")
        return pro[:140].title()
    return f"Convocatoria ({domain.replace('www.','')})"

# ────────────────────────────────────────────────────────────────────────────────
# HTTP / Parse / Detección
# ────────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=21600, show_spinner=False)
def fetch(url:str):
    r=requests.get(url, headers=HEADERS, timeout=REQ_TIMEOUT, allow_redirects=True)
    r.raise_for_status(); return r.text

def safe_text(el): 
    try: return re.sub(r"\s+"," ", el.get_text(" ").strip())
    except: return ""

def extract_title_desc(soup:BeautifulSoup):
    title=""
    for sel in ["meta[property='og:title']","meta[name='twitter:title']"]:
        m=soup.select_one(sel)
        if m and m.get("content"): title=m["content"]; break
    if not title:
        h1=soup.select_one("h1")
        if h1: title=safe_text(h1)
    if not title:
        t=soup.select_one("title")
        if t: title=safe_text(t)
    desc=""
    for sel in ["meta[name='description']","meta[property='og:description']"]:
        m=soup.select_one(sel)
        if m and m.get("content"): desc=m["content"]; break
    if not desc:
        p=soup.select_one("p")
        if p: desc=safe_text(p)
    return (title or "Convocatoria"), (desc or "")

def type_guess(text:str):
    s=(text or "").lower()
    if "residenc" in s: return "residencias"
    if "beca" in s: return "becas"
    if "premio" in s or "salón" in s or "salon" in s or "concurso" in s: return "concursos"
    if "open call" in s or "convocatoria" in s: return "convocatorias"
    if "exposición" in s or "exposicion" in s: return "exposiciones"
    return "convocatorias"

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

def dificultad_1_100(tipo:str, text:str):
    base=0.18
    t=(tipo or "").lower(); s=(text or "").lower()
    if t=="concursos": base-=0.06
    if t=="becas": base+=0.04
    if t=="residencias": base-=0.02
    if "usd" in s or "$" in s or "€" in s: base-=0.03
    m=re.search(r"(\d+)\s+(cupos|ganadores|becas|finalistas)", s)
    if m: base += min(0.10, int(m.group(1))*0.01)
    if any(k in s for k in ["internacional","global"]): base-=0.05
    if "argentina" in s: base+=0.02
    chance=max(0.02, min(0.45, base))
    return max(1, min(100, 100 - round(chance*100)))

@st.cache_data(ttl=21600, show_spinner=False)
def parse_page(url:str):
    try:
        html=fetch(url)
        soup=BeautifulSoup(html,"html.parser")
        raw_title, meta_desc = extract_title_desc(soup)
        full = safe_text(soup)
        abre, cierra = extract_range(full)
        tipo = type_guess(raw_title+" "+meta_desc+" "+full)
        loc  = guess_location(raw_title+" "+full)
        premio, cupos, fee = extract_key_data(full)
        titulo = titulo_ia(raw_title, full, urlparse(url).netloc)
        resumen = resumen_ia(meta_desc if meta_desc else full)
        diff = dificultad_1_100(tipo, full)
        links = best_links(soup, url)
        fee_num = 0 if re.search(r"(sin costo|sin cargo|gratuito|gratis)", full.lower()) else None
        return {
            "source": urlparse(url).netloc.replace("www.",""),
            "title": titulo, "url": url,
            "open_at": abre, "deadline": cierra,
            "type": tipo, "location": loc, "scope": scope_from_location(loc),
            "difficulty": diff, "prize": premio, "slots": cupos, "fee": fee,
            "free": (fee=="0" or fee_num==0),
            "summary": resumen, "links": links,
        }
    except Exception:
        return None

# ────────────────────────────────────────────────────────────────────────────────
# Crawl AR curado (BFS corto por dominio)
# ────────────────────────────────────────────────────────────────────────────────
def seems_call(url:str, text:str):
    u=url.lower()
    if any(k in u for k in ("/convocatoria","/convocatorias","/premio","/residenc","/salon","/salón","/beca")):
        return True
    s=(text or "").lower()
    return any(k in s for k in ("convocatoria","premio","salón","salon","residenc","beca","open call","exposición","exposicion"))

@st.cache_data(ttl=7200, show_spinner=False)
def crawl_site_for_calls(seed:str, per_site_limit:int=12):
    host=urlparse(seed).netloc
    visited=set(); queue=[seed]; found=[]
    while queue and len(visited)<per_site_limit:
        url=queue.pop(0)
        if url in visited: continue
        visited.add(url)
        try: html=fetch(url)
        except Exception: continue
        soup=BeautifulSoup(html,"html.parser")
        text=safe_text(soup)

        if seems_call(url, text): found.append(url)

        for a in soup.select("a[href]"):
            href=a["href"].strip()
            if not href or href.startswith("#") or href.startswith("mailto:"): continue
            u=urljoin(url, href)
            p=urlparse(u)
            if p.netloc!=host: continue
            if any(p.path.lower().endswith(ext) for ext in SKIP_EXTS): continue
            if any(b in p.netloc for b in SKIP_HOSTS): continue
            if u not in visited and u not in queue and len(queue) < per_site_limit*2:
                queue.append(u)
    uniq=[]; seen=set()
    for u in found:
        base=u.split("#")[0]
        if base in seen: continue
        seen.add(base); uniq.append(base)
    return uniq[:per_site_limit]

def gather_curated_ar(total_limit:int):
    per=max(3, total_limit // max(1,len(CURATED_AR_SEEDS)))
    urls=[]
    for s in CURATED_AR_SEEDS:
        urls += crawl_site_for_calls(s, per_site_limit=per)
    clean=[]; seen=set()
    for u in urls:
        host=urlparse(u).netloc.lower()
        if any(h in host for h in SKIP_HOSTS): continue
        base=u.split("#")[0]
        if base in seen: continue
        seen.add(base); clean.append(base)
        if len(clean)>=total_limit: break
    return clean

# ────────────────────────────────────────────────────────────────────────────────
# FRONTEND — Filtros estilo “Hércules”
# ────────────────────────────────────────────────────────────────────────────────
st.markdown('<div class="filterbox">', unsafe_allow_html=True)

col_wide = st.columns([1,1])[0]  # ancho total dentro de la caja

# Línea 1: búsqueda y ordenar
c1, c2 = st.columns([0.7,0.3])
with c1:
    query = st.text_input("Búsqueda", placeholder="residencia, FNA, performance")
with c2:
    ordenar = st.selectbox("Ordenar por", ["Fecha límite", "Dificultad", "Premio estimado"])

# Línea 2: Categorías (chips), Ubicación y “sin costo”
c3, c4, c5 = st.columns([0.6,0.25,0.15])
with c3:
    categorias = st.multiselect("Categorias", ["Becas","Residencias","Concursos","Exposiciones","Convocatorias"],
                                default=["Becas","Residencias","Concursos","Convocatorias"])
with c4:
    ubicacion = st.selectbox("Ubicación", ["Todas","Argentina","Internacional"])
with c5:
    sin_costo = st.checkbox("Solo convocatorias sin arancel", value=False)

# Línea 3: Rango de fechas y dificultad
c6, c7, c8 = st.columns([0.25,0.25,0.5])
with c6:
    desde = st.date_input("Desde", value=None)
with c7:
    hasta = st.date_input("Hasta", value=None)
with c8:
    dificultad_max = st.slider("Dificultad máx. (100)", 10, 100, 100)

# Acciones
c9, c10 = st.columns([0.3,0.7])
with c9:
    do_search = st.button("Buscar", type="primary", use_container_width=True)
with c10:
    if st.button("Limpiar", use_container_width=True):
        st.experimental_rerun()

st.markdown('</div>', unsafe_allow_html=True)

# ────────────────────────────────────────────────────────────────────────────────
# RUN
# ────────────────────────────────────────────────────────────────────────────────
if do_search:
    t0=time.time()
    urls = gather_curated_ar(total_limit=72)  # suficiente sin ser lento

    results=[]; done=0; prog=st.progress(0)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs=[ex.submit(parse_page,u) for u in urls]
        for f in as_completed(futs):
            rec=None
            try: rec=f.result()
            except: rec=None
            if rec: results.append(rec)
            done+=1; prog.progress(min(1.0, done/max(1,len(futs))))
            if time.time()-t0 > HARD_TIME_LIMIT: break
    prog.empty()

    # Mapeo categorías → tipos detectados
    map_cats = {
        "Becas":"becas",
        "Residencias":"residencias",
        "Concursos":"concursos",
        "Exposiciones":"exposiciones",
        "Convocatorias":"convocatorias",
    }

    # Filtros
    def keep(r):
        if r.get("difficulty",100) > dificultad_max: return False
        if sin_costo and not r.get("free"): return False
        if ubicacion=="Argentina" and r.get("scope")!="AR": return False
        if ubicacion=="Internacional" and r.get("scope")=="AR": return False
        if categorias:
            if r.get("type") not in [map_cats[c] for c in categorias]: return False
        d=r.get("deadline")
        if desde and d and d < desde: return False
        if hasta and d and d > hasta: return False
        if query:
            blob=(r.get("title","")+" "+r.get("summary","")).lower()
            if query.lower() not in blob: return False
        return True

    items=[x for x in results if keep(x)]

    # Orden
    if ordenar=="Fecha límite":
        items.sort(key=lambda r:(r.get("deadline") is None, r.get("deadline") or date(2100,1,1)))
    elif ordenar=="Dificultad":
        items.sort(key=lambda r:r.get("difficulty",100))
    else:  # Premio estimado: heurística por presencia de monto
        def amt(r):
            m=re.search(r"(\d[\d\.\,]+)", r.get("prize","") or "")
            try:
                return -float(m.group(1).replace(".","").replace(",","."))
            except: return 0
        items.sort(key=amt)

    # Top toolbar (export)
    left, right = st.columns([0.5,0.5])
    with left:
        st.caption(f"**{len(items)} resultados**  ·  ⏱ {round(time.time()-t0,1)} s")
    with right:
        if items:
            # CSV
            buf=io.StringIO(); w=csv.writer(buf)
            w.writerow(["titulo","url","fuente","categoria","ubicacion","ambito","abre","cierra","dificultad","premio","cupos","fee","resumen"])
            for c in items:
                w.writerow([
                    c["title"], c["url"], c["source"], c["type"], c["location"], c["scope"],
                    c["open_at"].strftime("%Y-%m-%d") if c.get("open_at") else "",
                    c["deadline"].strftime("%Y-%m-%d") if c.get("deadline") else "",
                    c["difficulty"], c["prize"], c["slots"], c["fee"], c["summary"]
                ])
            st.download_button("📄 Exportar CSV", buf.getvalue(), "artify_convocatorias.csv", "text/csv")
            # ICS
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

    # Tarjetas
    if not items:
        st.info("No hubo resultados con estos filtros. Probá quitar 'sin costo', ampliar fecha o dificultad.")
    for r in items:
        open_txt = r["open_at"].strftime("%d/%m/%Y") if r.get("open_at") else "—"
        dl=r.get("deadline")
        dl_txt = dl.strftime("%d/%m/%Y") if dl else "Sin dato"
        left = days_left(dl)
        urgency = "🟢" if left is None else ("🟡" if left and left<=21 else "🟢")
        if left is not None and left <= 7: urgency="🔴"

        # header
        st.markdown('<div class="card">', unsafe_allow_html=True)
        hA, hB = st.columns([0.72,0.28])
        with hA:
            st.markdown(f"### {r['title']}")
            chips = " ".join([
                f"<span class='badge'>{r['type'].capitalize()}</span>",
                f"<span class='badge'>{r['location']}</span>",
                f"<span class='badge'>{r['source']}</span>",
                f"<span class='badge'>{'Sin costo' if r.get('free') else 'Con arancel'}</span>",
            ])
            st.markdown(chips, unsafe_allow_html=True)
            st.markdown(f"<div class='meta'><b>Abre:</b> {open_txt} &nbsp;•&nbsp; <b>Cierra:</b> {dl_txt} {f'({left} días)' if left is not None else ''} &nbsp;{urgency}</div>", unsafe_allow_html=True)
            st.write(r["summary"])
        with hB:
            st.metric("Dificultad (1–100)", r["difficulty"])
            st.markdown("<div class='kpis'>", unsafe_allow_html=True)
            st.markdown(f"<span class='pill'>Premio: {r['prize']}</span>", unsafe_allow_html=True)
            st.markdown(f"<span class='pill'>Cupos: {r['slots']}</span>", unsafe_allow_html=True)
            st.markdown(f"<span class='pill'>Fee: {r['fee']}</span>", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)
            st.markdown("<div class='btn-row'>", unsafe_allow_html=True)
            links=r.get("links",{})
            if links.get("inscripcion"): link_button("📝 Postular / Inscripción", links["inscripcion"])
            if links.get("bases"):       link_button("📄 Bases / Reglamento",   links["bases"])
            link_button("🌐 Abrir publicación", links.get("principal") or r["url"])
            st.markdown("</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

else:
    st.info("Elegí filtros y dale a **Buscar**. El motor recorre sitios argentinos clave; no usa Google. Tip: probá 'Residencias' + 'Sin costo'.")
