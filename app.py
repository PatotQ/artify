# Artify — Web Search ULTRAFAST (paralelo, sin pandas)

import re, io, csv, time, urllib.parse
from datetime import date, timedelta
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
import streamlit as st

st.set_page_config(page_title="Artify — Convocatorias (rápido)", layout="wide")
YEAR = date.today().year

# ======== Config de performance ========
MAX_WORKERS = 12
REQ_TIMEOUT = 6
TOTAL_HARD_LIMIT = 28   # segundos tope por búsqueda
BLOCKED_DOMAINS = (
    "instagram.com","facebook.com","fb.com","x.com","twitter.com",
    "tiktok.com","youtube.com","linkedin.com","pinterest.","flickr.","tumblr.","vimeo.com"
)
HEADERS = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# ======== Fechas / heurísticas ========
MONTHS = {'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,'julio':7,'agosto':8,'septiembre':9,'setiembre':9,'octubre':10,'noviembre':11,'diciembre':12}

def parse_spanish_date(txt:str):
    if not txt: return None
    s = txt.lower()
    m = re.search(r"(\d{1,2})\s+de\s+([a-zá]+)\s+de\s+(\d{4})", s)
    if m:
        d, mon, y = int(m.group(1)), m.group(2).replace("á","a"), int(m.group(3))
        if mon in MONTHS: return date(y, MONTHS[mon], d)
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
    if m:
        d, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if yy < 100: yy += 2000
        return date(yy, mm, d)
    return None

DATE_PATTERNS = [
    r"(?:fecha(?:\s+l[ií]mite)?(?:\s+de)?\s*(?:aplicaci[oó]n|postulaci[oó]n|cierre|presentaci[oó]n)?:?\s*)(\d{1,2}\s+de\s+\w+\s+\d{4})",
    r"(?:cierran?\s+el\s+|cierra\s+el\s+|hasta el\s+)(\d{1,2}\s+de\s+\w+\s+\d{4})",
    r"(\d{1,2}/\d{1,2}/\d{2,4})",
]
RANGE_PATTERNS = [
    r"del\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})\s+al\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})",
    r"del\s+(\d{1,2}/\d{1,2}/\d{2,4})\s+al\s+(\d{1,2}/\d{1,2}/\d{2,4})",
]
def extract_deadline(text:str):
    if not text: return None
    for pat in DATE_PATTERNS:
        m = re.search(pat, text, re.I)
        if m:
            d = parse_spanish_date(m.group(1))
            if d: return d
    return parse_spanish_date(text)

def extract_date_range(text:str):
    if not text: return (None, None)
    for pat in RANGE_PATTERNS:
        m = re.search(pat, text, re.I)
        if m:
            return parse_spanish_date(m.group(1)), parse_spanish_date(m.group(2))
    return (None, extract_deadline(text))

def days_left(d): return None if not d else (d - date.today()).days
def safe_text(el): return re.sub(r"\s+"," ",(el.get_text(" ").strip() if el else "")).strip()

def type_guess(text:str):
    s=(text or "").lower()
    if "residenc" in s: return "residency"
    if "beca" in s: return "grant"
    if "premio" in s or "salón" in s or "salon" in s or "concurso" in s: return "prize"
    if "open call" in s or "convocatoria" in s: return "open_call"
    return "other"

CITIES_AR=["caba","buenos aires","rosario","cordoba","córdoba","la plata","mendoza","tucumán","salta","neuquén","bahía blanca","bahia blanca"]
COUNTRIES=["argentina","uruguay","chile","mexico","méxico","españa","colombia","peru","perú","brasil","paraguay","bolivia","ecuador","costa rica","guatemala","panamá","panama","estados unidos","usa","reino unido","italia","francia","alemania","grecia","portugal"]

def guess_location(text:str):
    s=(text or "").lower()
    for k in CITIES_AR:
        if k in s: return "Argentina"
    for c in COUNTRIES:
        if c in s:
            if c in ["usa","estados unidos"]: return "Estados Unidos"
            if c in ["méxico"]: return "Mexico"
            if c in ["panamá"]: return "Panamá"
            return c.title()
    if "internacional" in s: return "Internacional"
    return "—"

def scope_from_location(loc:str): 
    if not loc or loc=="—": return "UNK"
    return "AR" if loc.lower()=="argentina" else "EX"

def extract_key_data(text:str):
    s=(text or "")
    m_amt=re.search(r"(USD|US\$|€|\$)\s?([\d\.\,]+)", s, re.I)
    prize=f"{m_amt.group(1).upper()} {m_amt.group(2)}" if m_amt else "—"
    m_slots=re.search(r"(\d+)\s+(cupos|ganadores|becas|finalistas)", s, re.I)
    slots=m_slots.group(1) if m_slots else "—"
    m_fee=re.search(r"(?:fee|arancel|inscripci[oó]n)\s*(?:de)?\s*(USD|US\$|€|\$)?\s*([\d\.\,]+)", s, re.I)
    fee=(m_fee.group(1) or "$")+" "+m_fee.group(2) if m_fee else "0"
    return prize, slots, fee

def difficulty_1_100(kind:str, text:str):
    base=0.18
    t=(kind or "open_call").lower()
    s=(text or "").lower()
    if t=="prize": base-=0.06
    if t=="grant": base+=0.04
    if t=="residency": base-=0.02
    if "usd" in s or "$" in s or "€" in s: base-=0.03
    m=re.search(r"(\d+)\s+(cupos|ganadores|becas|finalistas)", s)
    if m:
        slots=int(m.group(1)); base+=min(0.10, slots*0.01)
    if any(k in s for k in ["internacional","global","worldwide"]): base-=0.05
    if any(k in s for k in ["argentina","caba","latinoamérica","latinoamerica"]): base+=0.02
    chance=max(0.02, min(0.45, base))
    return max(1, min(100, 100 - round(chance*100)))

def short_summary(text:str, n=260):
    s=re.sub(r"\s+"," ",(text or "")).strip()
    return s if len(s)<=n else s[:n-1]+"…"

# ======== Búsqueda (DDG HTML) ========
@st.cache_data(ttl=3600)
def ddg_search(query:str, max_results=20):
    url="https://html.duckduckgo.com/html/?kl=es-es&q="+urllib.parse.quote(query)
    html=requests.get(url, headers=HEADERS, timeout=REQ_TIMEOUT).text
    soup=BeautifulSoup(html,"html.parser")
    urls=[]
    for a in soup.select("a.result__a, a"):
        href=a.get("href") or ""
        if not href: continue
        if href.startswith("/l/?"):
            qs=urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            if "uddg" in qs: href=qs["uddg"][0]
        if href.startswith("http") and all(b not in href for b in BLOCKED_DOMAINS):
            urls.append(href)
        if len(urls)>=max_results: break
    return urls

@st.cache_data(ttl=21600)
def fetch(url:str):
    r=requests.get(url, headers=HEADERS, timeout=REQ_TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    return r.text

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
    return (title or "Convocatoria"), desc

@st.cache_data(ttl=21600)
def parse_page(url:str):
    try:
        html=fetch(url)
    except Exception:
        return None
    soup=BeautifulSoup(html,"html.parser")
    title, meta_desc=extract_title_desc(soup)
    full=safe_text(soup)
    s,e=extract_date_range(full)
    end=e or extract_deadline(full)
    kind=type_guess(title+" "+meta_desc+" "+full)
    loc=guess_location(title+" "+full)
    scope=scope_from_location(loc)
    prize,slots,fee=extract_key_data(full)
    diff=difficulty_1_100(kind, full)
    return {
        "source":urlparse(url).netloc,
        "title":title.strip(),
        "url":url,
        "open_at":s,
        "deadline":end,
        "type":kind,
        "location":loc,
        "scope":scope,
        "difficulty":diff,
        "prize":prize,"slots":slots,"fee":fee,
        "summary": short_summary(meta_desc if meta_desc else full),
    }

# ======== Queries base ========
SEARCH_QUERIES_AR=[
    "site:.gob.ar convocatoria artes visuales 2025",
    "site:.gov.ar convocatoria artes visuales 2025",
    "premio artes visuales argentina 2025",
    "salón artes visuales convocatoria 2025 argentina",
    "residencia artistas argentina convocatoria 2025",
    "open call arte argentina 2025",
    "premio klemm 2025 inscripcion",
    "salon tucuman artes visuales 2025 inscripcion",
]
SEARCH_QUERIES_EX=[
    "open call visual arts 2025 residency",
    "visual arts prize 2025 call",
    "residency artist open call 2025 photography",
]

# ======== UI ========
st.title("🎨 Artify — Convocatorias (búsqueda rápida)")
with st.sidebar:
    ámbito=st.radio("Ámbito", ["Todas","AR solo","Fuera de AR"], horizontal=True)
    solo_futuras=st.checkbox("Solo futuras", True)
    year_to_show=st.number_input("Año hasta", value=YEAR, step=1)
    q=st.text_input("Buscar texto", "")
    type_filter=st.multiselect("Tipo", ["open_call","grant","prize","residency","other"], default=["open_call","grant","prize","residency"])
    top_n=st.slider("Máx. páginas a inspeccionar", 20, 120, 40, 10)
    st.caption("⚡ Si querés 20+ resultados, subí el tope a 60–100. Aun así no supera ~30 s.")

if st.button("🔎 Buscar convocatorias", type="primary"):
    t0=time.time()
    only_ar=(ámbito=="AR solo")
    queries = (SEARCH_QUERIES_AR if only_ar else SEARCH_QUERIES_AR+SEARCH_QUERIES_EX)
    # 1) Juntar URLs rápido
    urls=[]
    for qy in queries:
        urls += ddg_search(qy, max_results=top_n//len(queries)+5)
    # Dedupe + bloquear dominios pesados
    seen=set(); clean=[]
    for u in urls:
        base=u.split("#")[0]
        host=urlparse(base).netloc.lower()
        if base in seen: continue
        if any(b in host for b in BLOCKED_DOMAINS): continue
        seen.add(base); clean.append(base)
        if len(clean)>=top_n: break

    # 2) Parse paralelo con progreso
    results=[]; prog=st.progress(0); done=0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures=[ex.submit(parse_page, u) for u in clean]
        for f in as_completed(futures):
            rec=f.result()
            if rec: results.append(rec)
            done+=1
            prog.progress(min(1.0, done/max(1,len(futures))))
            if time.time()-t0>TOTAL_HARD_LIMIT: break
    prog.empty()

    # 3) Filtros finos
    def keep(x):
        d=x.get("deadline")
        if ámbito=="Fuera de AR" and x.get("scope")=="AR": return False
        if ámbito=="AR solo" and x.get("scope")!="AR": return False
        if solo_futuras and d and d<date.today(): return False
        if d and d>date(year_to_show,12,31): return False
        if type_filter and x.get("type") not in type_filter: return False
        if q:
            s=(x.get("title","")+" "+x.get("summary","")).lower()
            if q.lower() not in s: return False
        return True
    results=[x for x in results if keep(x)]

    # Ordenar por fecha
    results.sort(key=lambda r:(r.get("deadline") is None, r.get("deadline") or date(year_to_show,12,31)))

    # Resumen
    c1,c2,c3 = st.columns(3)
    c1.metric("Resultados", len(results))
    first=next((it["deadline"] for it in results if it.get("deadline")), None)
    last =next((it["deadline"] for it in reversed(results) if it.get("deadline")), None)
    c2.metric("Primera fecha", first.strftime("%d/%m/%Y") if first else "—")
    c3.metric("Última fecha",  last.strftime("%d/%m/%Y")  if last  else "—")
    st.caption(f"⏱ {round(time.time()-t0,1)} s")

    # Tarjetas
    if not results:
        st.warning("Sin resultados con esos filtros. Probá subir 'Máx. páginas', quitar 'Solo futuras' o usar otra palabra clave.")
    for r in results:
        open_txt = r["open_at"].strftime("%d/%m/%Y") if r.get("open_at") else "—"
        dl=r.get("deadline")
        dl_txt = dl.strftime("%d/%m/%Y") if dl else "Sin dato"
        left=days_left(dl)
        urgency="🟢" if left is None else ("🟡" if left and left<=21 else "🟢")
        if left is not None and left<=7: urgency="🔴"

        with st.container(border=True):
            a,b=st.columns([3,1])
            with a:
                st.subheader(r["title"])
                st.markdown(f"[Abrir convocatoria]({r['url']})")
                st.markdown(f"**Fuente:** {r['source']}  •  **Tipo:** `{r['type']}`  •  **Lugar:** {r['location']}")
                st.markdown(f"**Abre:** {open_txt}  •  **Cierra:** {dl_txt} {('(' + str(left) + ' días)') if left is not None else ''}  {urgency}")
                st.write(r["summary"])
            with b:
                st.metric("Dificultad (1–100)", r["difficulty"])
                st.caption("Datos clave")
                st.write(f"• **Premio:** {r['prize']}")
                st.write(f"• **Cupos:** {r['slots']}")
                st.write(f"• **Fee:** {r['fee']}")

    # Export
    if results:
        buf=io.StringIO(); w=csv.writer(buf)
        w.writerow(["title","url","source","type","location","scope","open_at","deadline","difficulty","prize","slots","fee","summary"])
        for c in results:
            w.writerow([
                c["title"], c["url"], c["source"], c["type"], c["location"], c["scope"],
                c["open_at"].strftime("%Y-%m-%d") if c.get("open_at") else "",
                c["deadline"].strftime("%Y-%m-%d") if c.get("deadline") else "",
                c["difficulty"], c["prize"], c["slots"], c["fee"], c["summary"]
            ])
        st.download_button("⬇️ Exportar CSV", buf.getvalue(), "artify_websearch_fast.csv", "text/csv")

else:
    st.info("Elegí el ámbito, ajustá **Máx. páginas** (40–80 rinde bien) y dale a **Buscar**. "
            "La app descarga y analiza páginas **en paralelo** con un tope de ~30 s.")
