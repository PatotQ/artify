# ✨ Artify — buscador de convocatorias de arte para Fla ❤️
# Motor AR curado + parsers dedicados. SIN "dificultad".
# Fix: detección PDF/binario + sanitización de texto + títulos inteligentes.
# Cobertura ampliada (más páginas por sitio y tope global mayor).

import re, io, csv, time, socket
from datetime import date, timedelta
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
import streamlit as st

# ────────────────────────────────────────────────────────────────────────────────
# Config / Estilo
# ────────────────────────────────────────────────────────────────────────────────
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
st.caption("Motor curado para Argentina con parsers dedicados. Maneja PDFs, limpia texto y genera títulos/resúmenes en español.")

# ────────────────────────────────────────────────────────────────────────────────
# Constantes / Seeds
# ────────────────────────────────────────────────────────────────────────────────
MAX_WORKERS   = 14
REQ_TIMEOUT   = 9
HARD_TIME_LIMIT = 45       # más margen para recorrer
TOTAL_LIMIT   = 120        # más URLs en total
PER_SITE_PAGES= 24         # más páginas por dominio

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
# Utilidades de red / binarios / links
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

def fetch_bytes(url:str):
    r=requests.get(url, headers=HEADERS, timeout=REQ_TIMEOUT, allow_redirects=True, stream=True)
    r.raise_for_status()
    ct=r.headers.get("Content-Type","").lower()
    data=r.content
    return data, ct, r.url

def is_pdf(data:bytes, ct:str):
    if "application/pdf" in ct: return True
    return data[:4] == b"%PDF"

def bytes_to_html(data:bytes, ct:str):
    if is_pdf(data, ct): return "", None
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        try:
            return data.decode("latin-1"), "latin-1"
        except UnicodeDecodeError:
            return "", None

def cleanup_text(s:str):
    if not s: return ""
    s = s.replace("\x00"," ")
    s = re.sub(r"[\uFFFD]{2,}", " ", s)  # � repetidos
    s = re.sub(r"[^\x09\x0A\x0D\x20-\x7E\u00A0-\uFFFF]", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s

def looks_gibberish(s:str):
    if not s: return False
    if s.count("�") >= 5: return True
    junk = len(re.findall(r"[^0-9A-Za-zÁÉÍÓÚáéíóúÑñÜü¿?¡!;:.,()\\[\\]{}/\\-–—%€$\\s]", s))
    return junk > 0.35*max(1,len(s))

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
    for k in ["inscripcion","bases","principal"]:
        if links[k] and not head_ok(links[k], timeout=5): links[k]=None
    if not links["bases"]:
        for p in links["pdfs"]:
            if head_ok(p, timeout=5): links["bases"]=p; break
    return links

def link_button(label: str, url: str):
    if not url: return
    try: st.link_button(label, url)
    except Exception: st.markdown(f"[{label}]({url})")

# ────────────────────────────────────────────────────────────────────────────────
# Fechas ES
# ────────────────────────────────────────────────────────────────────────────────
MONTHS={'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,'julio':7,'agosto':8,'septiembre':9,'setiembre':9,'octubre':10,'noviembre':11,'diciembre':12}
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
# IA liviana: título + resumen
# ────────────────────────────────────────────────────────────────────────────────
KEYWORDS=["convocatoria","premio","salón","salon","residenc","beca","open call","inscripción","cierre","bases"]

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

def sentences(text:str): return [s.strip() for s in re.split(r"(?<=[\.\!\?])\s+", text) if s.strip()]

def resumen_ia(text:str,n=3,max_chars=360):
    txt=cleanup_text(text or "")
    if not txt or looks_gibberish(txt): return "Documento o publicación sin descripción legible. Abrí “Bases / Reglamento” para ver requisitos."
    sents=sentences(txt)
    scored=[]
    for s in sents:
        score=sum(int(k in s.lower()) for k in KEYWORDS)+len(re.findall(r"\d{4}", s))
        scored.append((score,s))
    scored.sort(reverse=True)
    chosen=[s for _,s in scored[:n]] or sents[:n]
    res=" ".join(chosen)
    return (res[:max_chars]+"…") if len(res)>max_chars else res

# ────────────────────────────────────────────────────────────────────────────────
# Parse genérico (maneja PDF/binario)
# ────────────────────────────────────────────────────────────────────────────────
def extract_title_desc(soup:BeautifulSoup):
    title=""
    for sel in ["meta[property='og:title']","meta[name='twitter:title']"]:
        m=soup.select_one(sel)
        if m and m.get("content"): title=m["content"]; break
    if not title:
        h1=soup.select_one("h1")
        if h1: title=cleanup_text(h1.get_text(" "))
    if not title:
        t=soup.select_one("title")
        if t: title=cleanup_text(t.get_text())
    desc=""
    for sel in ["meta[name='description']","meta[property='og:description']"]:
        m=soup.select_one(sel)
        if m and m.get("content"): desc=m["content"]; break
    if not desc:
        p=soup.select_one("p")
        if p: desc=cleanup_text(p.get_text(" "))
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
    m_fee=re.search(r"(?:fee|arancel|inscripci[oó]n)\s*(?:de)?\s*(USD|US\$|€)?\s*([\d\.\,]+)", s, re.I)
    fee=(m_fee.group(1) or "$")+" "+m_fee.group(2) if m_fee else "0"
    return premio, cupos, fee

def parse_generic(url:str):
    try:
        data, ct, final = fetch_bytes(url)
        html, enc = bytes_to_html(data, ct)

        if not html:
            domain=urlparse(final).netloc.replace("www.","")
            fname = final.split("/")[-1]
            title = smart_title_guess(fname, "", domain)
            return {
                "source": domain, "title": title or f"Documento PDF — {domain}", "url": final,
                "open_at": None, "deadline": None, "type": "Convocatorias",
                "location":"—","scope":"UNK", "prize":"—","slots":"—","fee":"0","
