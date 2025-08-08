# ✨ Artify — buscador de convocatorias de arte para Fla ❤️
# Modo AR curado: sin buscadores, crawl dirigido en sitios argentinos + “IA” liviana en español.

import re, io, csv, time
from datetime import date, timedelta
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
import streamlit as st

# ────────────────── Config / UI ──────────────────
st.set_page_config(
    page_title="Artify — buscador de convocatorias de arte para Fla ❤️",
    layout="wide"
)
st.title("✨ Artify — buscador de convocatorias de arte para Fla ❤️")
st.caption("Motor argentino curado. Resultados en español con título y reseña automática, dificultad (1–100) y export a CSV/ICS.")

YEAR = date.today().year
MAX_WORKERS = 12
REQ_TIMEOUT = 8
HARD_TIME_LIMIT = 28  # segundos para toda la búsqueda
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# Sitios argentinos priorizados (homepage o sección de convocatorias)
CURATED_AR_SEEDS = [
    "https://www.klemm.org.ar/",
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
SKIP_EXTS = (".pdf",".jpg",".jpeg",".png",".gif",".webp",".doc",".docx",".xls",".xlsx",".zip",".rar")

# ────────────────── Fecha (robusta) ──────────────────
MONTHS = {'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
          'julio':7,'agosto':8,'septiembre':9,'setiembre':9,'octubre':10,'noviembre':11,'diciembre':12}
def _mk_date(y, m, d):
    try: return date(y, m, d)
    except: return None

def parse_spanish_date(txt: str):
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
    if m:
        d,mm,y=int(m.group(1)),int(m.group(2)),int(m.group(3))
        return _mk_date(y,mm,d)
    return None

DATE_PATS = [
    r"(?:fecha(?:\s+l[ií]mite)?(?:\s+de)?\s*(?:aplicaci[oó]n|postulaci[oó]n|cierre|presentaci[oó]n)?:?\s*)(\d{1,2}\s+de\s+\w+\s+\d{4})",
    r"(?:cierran?\s+el\s+|cierra\s+el\s+|hasta el\s+)(\d{1,2}\s+de\s+\w+\s+\d{4})",
    r"(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})",
]
RANGE_PATS = [
    r"del\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})\s+al\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})",
    r"del\s+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})\s+al\s+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})",
]
def extract_deadline(text: str):
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
def extract_range(text: str):
    if not text: return (None,None)
    s=str(text)
    for pat in RANGE_PATS:
        m=re.search(pat,s,re.I)
        if m: return parse_spanish_date(m.group(1)), parse_spanish_date(m.group(2))
    m=re.search(r"hasta(?:\s+el)?\s+([^\.;,\n]+)", s, re.I)
    if m: return None, parse_spanish_date(m.group(1))
    return (None, extract_deadline(s))
def days_left(d): return None if not d else (d - date.today()).days

# ────────────────── IA liviana (título+resumen en ES) ──────────────────
KEYWORDS = ["convocatoria","premio","salón","salon","residenc","beca","open call","inscripción","cierre","bases"]
def sentences(text:str): return [s.strip() for s in re.split(r"(?<=[\.\!\?])\s+", text) if s.strip()]
def resumen_ia(text:str, n=3, max_chars=360):
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

# ────────────────── HTTP / Parse ──────────────────
@st.cache_data(ttl=21600, show_spinner=False)
def fetch(url:str):
    r=requests.get(url, headers=HEADERS, timeout=REQ_TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    return r.text

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
    if "residenc" in s: return "residencia"
    if "beca" in s: return "beca"
    if "premio" in s or "salón" in s or "salon" in s or "concurso" in s: return "premio"
    if "open call" in s or "convocatoria" in s: return "convocatoria"
    return "otro"

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
    if t=="premio": base-=0.06
    if t=="beca": base+=0.04
    if t=="residencia": base-=0.02
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
        return {
            "source": urlparse(url).netloc.replace("www.",""),
            "title": titulo, "url": url,
            "open_at": abre, "deadline": cierra,
            "type": tipo, "location": loc, "scope": scope_from_location(loc),
            "difficulty": diff, "prize": premio, "slots": cupos, "fee": fee,
            "summary": resumen,
        }
    except Exception:
        return None

# ────────────────── Crawl AR curado ──────────────────
def seems_call(url:str, text:str):
    u=url.lower()
    if any(k in u for k in ("/convocatoria","/convocatorias","/premio","/residenc","/salon","/salón","/beca")):
        return True
    s=(text or "").lower()
    return any(k in s for k in ("convocatoria","premio","salón","salon","residenc","beca","open call"))

@st.cache_data(ttl=7200, show_spinner=False)
def crawl_site_for_calls(seed:str, per_site_limit:int=12):
    """BFS cortito dentro del dominio: junta URLs que parecen convocatoria."""
    host = urlparse(seed).netloc
    visited=set(); queue=[seed]; found=[]
    while queue and len(visited) < per_site_limit:
        url = queue.pop(0)
        if url in visited: continue
        visited.add(url)
        try:
            html=fetch(url)
        except Exception:
            continue
        soup=BeautifulSoup(html,"html.parser")
        text=safe_text(soup)

        if seems_call(url, text):
            found.append(url)

        # expandir dentro del dominio
        for a in soup.select("a[href]"):
            href=a["href"].strip()
            if not href or href.startswith("#") or href.startswith("mailto:"): continue
            u = urljoin(url, href)
            p = urlparse(u)
            if p.netloc != host: continue
            if any(p.path.lower().endswith(ext) for ext in SKIP_EXTS): continue
            if any(b in p.netloc for b in SKIP_HOSTS): continue
            if u not in visited and u not in queue and len(queue) < per_site_limit*2:
                queue.append(u)

    # dedupe
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
    # limpieza final
    clean=[]; seen=set()
    for u in urls:
        host=urlparse(u).netloc.lower()
        if any(h in host for h in SKIP_HOSTS): continue
        base=u.split("#")[0]
        if base in seen: continue
        seen.add(base); clean.append(base)
        if len(clean) >= total_limit: break
    return clean

# ────────────────── Filtros UI ──────────────────
with st.sidebar:
    st.header("Filtros")
    ámbito = st.radio("Ámbito", ["AR (curado)","Todas (curado)"], horizontal=True, index=0)
    solo_futuras = st.checkbox("Solo futuras", True)
    año_hasta = st.number_input("Año hasta", value=YEAR, step=1)
    q = st.text_input("Buscar texto", "")
    tipo_sel = st.multiselect("Tipo", ["convocatoria","premio","beca","residencia","otro"],
                              default=["convocatoria","premio","beca","residencia"])
    total_pages = st.slider("Intensidad de búsqueda (páginas aprox.)", 24, 96, 60, 12)
    st.caption("El motor recorre sitios argentinos clave. 48–72 suele traer 10–40 convocatorias reales.")

# ────────────────── Run ──────────────────
if st.button("🔎 Buscar convocatorias", type="primary"):
    t0=time.time()
    items=[]

    urls = gather_curated_ar(total_pages if ámbito=="AR (curado)" else total_pages)
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

    # filtros finos
    def keep(r):
        d=r.get("deadline")
        if ámbito=="AR (curado)" and r.get("scope") not in ("AR","UNK"): return False
        if solo_futuras and d and d < date.today(): return False
        if d and d > date(año_hasta,12,31): return False
        if tipo_sel and r.get("type") not in tipo_sel: return False
        if q:
            blob=(r.get("title","")+" "+r.get("summary","")).lower()
            if q.lower() not in blob: return False
        return True

    items=[x for x in results if keep(x)]
    items.sort(key=lambda r:(r.get("deadline") is None, r.get("deadline") or date(año_hasta,12,31)))

    # métricas
    c1,c2,c3 = st.columns(3)
    c1.metric("Convocatorias", len(items))
    first=next((it["deadline"] for it in items if it.get("deadline")), None)
    last =next((it["deadline"] for it in reversed(items) if it.get("deadline")), None)
    c2.metric("Primera fecha", first.strftime("%d/%m/%Y") if first else "—")
    c3.metric("Última fecha",  last.strftime("%d/%m/%Y")  if last  else "—")
    st.caption(f"⏱ {round(time.time()-t0,1)} s")
    st.markdown("---")

    if not items:
        st.warning("No hay resultados con estos filtros. Probá ampliar la intensidad o quitar 'Solo futuras'.")

    # tarjetas
    for r in items:
        open_txt = r["open_at"].strftime("%d/%m/%Y") if r.get("open_at") else "—"
        dl=r.get("deadline")
        dl_txt = dl.strftime("%d/%m/%Y") if dl else "Sin dato"
        left = days_left(dl)
        urgency = "🟢" if left is None else ("🟡" if left and left<=21 else "🟢")
        if left is not None and left <= 7: urgency="🔴"

        with st.container(border=True):
            a,b=st.columns([3,1])
            with a:
                st.subheader(r["title"])
                st.markdown(f"[Abrir convocatoria]({r['url']})")
                st.markdown(f"`{r['type']}` · {r['location']} · {r['source']}")
                st.markdown(f"**Abre:** {open_txt} • **Cierra:** {dl_txt} {f'({left} días)' if left is not None else ''} {urgency}")
                st.write(r["summary"])
            with b:
                st.metric("Dificultad (1–100)", r["difficulty"])
                st.caption("Datos clave")
                st.write(f"• **Premio:** {r['prize']}")
                st.write(f"• **Cupos:** {r['slots']}")
                st.write(f"• **Fee:** {r['fee']}")

    # export
    if items:
        buf=io.StringIO(); w=csv.writer(buf)
        w.writerow(["titulo","url","fuente","tipo","lugar","ambito","abre","cierra","dificultad","premio","cupos","fee","resumen"])
        for c in items:
            w.writerow([
                c["title"], c["url"], c["source"], c["type"], c["location"], c["scope"],
                c["open_at"].strftime("%Y-%m-%d") if c.get("open_at") else "",
                c["deadline"].strftime("%Y-%m-%d") if c.get("deadline") else "",
                c["difficulty"], c["prize"], c["slots"], c["fee"], c["summary"]
            ])
        st.download_button("⬇️ Exportar CSV", buf.getvalue(), "artify_convocatorias.csv", "text/csv")

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
        st.download_button("📅 Exportar calendario (ICS)", make_ics(items), "artify_convocatorias.ics", "text/calendar")

else:
    st.info("Elegí filtros y apretá **Buscar convocatorias**. El motor recorre sitios argentinos clave; no depende de Google.")
