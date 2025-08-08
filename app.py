# ✨ Artify — buscador de convocatorias de arte para Fla ❤️
# Buscador en español, UI prolija, scraping robusto, título/resumen "IA" on-device.

import re, io, csv, time
from datetime import date, timedelta
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
import streamlit as st

# ────────────────────────────────────────────────────────────────────────────────
# Config de página / Tema
# ────────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Artify — buscador de convocatorias de arte para Fla ❤️",
    layout="wide"
)
st.title("✨ Artify — buscador de convocatorias de arte para Fla ❤️")
st.caption("Resultados en español, ordenados por fecha. Incluye título y reseña generada automáticamente, dificultad (1–100) y export a CSV/ICS.")

# ────────────────────────────────────────────────────────────────────────────────
# Constantes y helpers
# ────────────────────────────────────────────────────────────────────────────────
YEAR = date.today().year
MAX_WORKERS = 12
REQ_TIMEOUT = 7
TOTAL_HARD_LIMIT = 28  # tope en segundos
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
BLOCKED = (
    "instagram.com","facebook.com","x.com","twitter.com","tiktok.com",
    "youtube.com","linkedin.com","pinterest.","flickr.","tumblr.","vimeo.com"
)

# dominios AR "curados" que solemos querer
WHITELIST_AR = [
    "klemm.org.ar",                # Premio Klemm
    "fnartes.gob.ar",              # Fondo Nacional de las Artes
    "cultura.gob.ar",              # Ministerio de Cultura / Palais
    "palaisdeglace.cultura.gob.ar",
    "enteculturaltucuman.gob.ar",  # Salón Tucumán
    "fundacionosde.com.ar",        # Premio OSDE
    "museorosagalisteo.gob.ar",    # Santa Fe
    "castagninomacro.org",         # Rosario
    "museosiivori.buenosaires.gob.ar", # Sívori
    "museomalba.org.ar",
    "mendoza.gov.ar", "cba.gov.ar", "santafe.gob.ar",
    "una.edu.ar", "uba.ar", "unlp.edu.ar",
    # generalistas locales
    "www.catalogosparaartistas.com", "es.artealdia.com", "recursosculturales.com",
]

SOURCES = {
    "artealdia_main": "https://es.artealdia.com/Convocatorias",
    "artealdia_tag_convocatorias": "https://es.artealdia.com/Tags/%28tag%29/Convocatorias",
    "catalogos_convocatorias": "https://www.catalogosparaartistas.com/convocatorias",
}

SERP_KEY = st.secrets.get("SERPAPI_KEY")

# ────────────────────────────────────────────────────────────────────────────────
# Manejo de fechas (robusto y en español)
# ────────────────────────────────────────────────────────────────────────────────
MONTHS = {'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
          'julio':7,'agosto':8,'septiembre':9,'setiembre':9,'octubre':10,'noviembre':11,'diciembre':12}

def _mk_date(y, m, d):
    try: return date(y, m, d)
    except Exception: return None

def parse_spanish_date(txt: str):
    if not txt: return None
    s = str(txt).lower().replace("º","").replace("°","")
    s = re.sub(r"(\d)(?:st|nd|rd|th)", r"\1", s)

    m = re.search(r"(\d{1,2})\s+de\s+([a-zá]+)\s+de\s+(\d{4})", s)
    if m:
        d = int(m.group(1)); mon = m.group(2).replace("á","a"); y = int(m.group(3))
        if mon in MONTHS: return _mk_date(y, MONTHS[mon], d)

    m = re.search(r"(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})", s)
    if m:
        a = int(m.group(1)); b = int(m.group(2)); y = int(m.group(3))
        if y < 100: y += 2000
        return _mk_date(y, b, a) or _mk_date(y, a, b)

    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if m:
        d, mm, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _mk_date(y, mm, d)

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
    s = str(text)
    for pat in DATE_PATS:
        m = re.search(pat, s, re.I)
        if m:
            d = parse_spanish_date(m.group(1))
            if d: return d
    m = re.search(r"\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}", s)
    if m:
        d = parse_spanish_date(m.group(0))
        if d: return d
    return None

def extract_range(text: str):
    if not text: return (None, None)
    s = str(text)
    for pat in RANGE_PATS:
        m = re.search(pat, s, re.I)
        if m:
            return parse_spanish_date(m.group(1)), parse_spanish_date(m.group(2))
    m = re.search(r"hasta(?:\s+el)?\s+([^\.;,\n]+)", s, re.I)
    if m:
        return None, parse_spanish_date(m.group(1))
    return (None, extract_deadline(s))

def days_left(d): return None if not d else (d - date.today()).days

# ────────────────────────────────────────────────────────────────────────────────
# “IA” liviana (en local): título y resumen en español
# ────────────────────────────────────────────────────────────────────────────────
KEYWORDS = ["convocatoria","premio","salón","salon","residenc","beca","open call","inscripción","cierre","bases"]

def sentences(text: str):
    # separador simple de oraciones
    return [s.strip() for s in re.split(r"(?<=[\.\!\?])\s+", text) if s.strip()]

def resumen_ia(text: str, n_frases=3, max_chars=360):
    txt = re.sub(r"\s+"," ", (text or "")).strip()
    if not txt: return "Convocatoria sin descripción."
    sents = sentences(txt)
    # preferí frases con palabras clave
    scored = []
    for s in sents:
        score = sum(int(k in s.lower()) for k in KEYWORDS) + len(re.findall(r"\d{4}", s))
        scored.append((score, s))
    scored.sort(reverse=True)
    chosen = [s for _,s in scored[:n_frases]]
    if not chosen:
        chosen = sents[:n_frases]
    resumen = " ".join(chosen)
    return (resumen[:max_chars] + "…") if len(resumen) > max_chars else resumen

def titulo_ia(title: str, text: str, domain: str):
    t = (title or "").strip()
    if t and not re.fullmatch(r"(convocatoria|home|inicio|noticias?)", t, re.I):
        return t[:140]
    # buscar algo tipo "Premio Klemm 2025", "Salón Nacional ..."
    m = re.search(r"(premio|sal[oó]n|residenc\w+|beca|open call)[^\.]{0,80}?(20\d{2})?", text, re.I)
    if m:
        pro = m.group(0)
        pro = re.sub(r"\s{2,}"," ", pro).strip().strip(" .:;-")
        return pro[:140].title()
    # por defecto, usa el host bonito
    host = domain.replace("www.","")
    return f"Convocatoria ({host})"

# ────────────────────────────────────────────────────────────────────────────────
# HTTP + parse
# ────────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=21600, show_spinner=False)
def fetch(url: str):
    r = requests.get(url, headers=HEADERS, timeout=REQ_TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    return r.text

def extract_title_desc(soup: BeautifulSoup):
    title=""
    for sel in ["meta[property='og:title']","meta[name='twitter:title']"]:
        m=soup.select_one(sel)
        if m and m.get("content"): title=m["content"]; break
    if not title:
        h1=soup.select_one("h1")
        if h1: title=h1.get_text(" ").strip()
    if not title:
        t=soup.select_one("title")
        if t: title=t.get_text(" ").strip()
    desc=""
    for sel in ["meta[name='description']","meta[property='og:description']"]:
        m=soup.select_one(sel)
        if m and m.get("content"): desc=m["content"]; break
    if not desc:
        p=soup.select_one("p")
        if p: desc=p.get_text(" ").strip()
    return (title or "Convocatoria"), (desc or "")

def type_guess(text: str):
    s=(text or "").lower()
    if "residenc" in s: return "residencia"
    if "beca" in s: return "beca"
    if "premio" in s or "salón" in s or "salon" in s or "concurso" in s: return "premio"
    if "open call" in s or "convocatoria" in s: return "convocatoria"
    return "otro"

def guess_location(text: str):
    s=(text or "").lower()
    if "argentina" in s or "buenos aires" in s or "caba" in s: return "Argentina"
    if "internacional" in s: return "Internacional"
    # heurística mínima
    return "—"

def scope_from_location(loc: str):
    if loc == "Argentina": return "AR"
    if loc == "—": return "UNK"
    return "EX"

def extract_key_data(text: str):
    s=(text or "")
    m_amt=re.search(r"(USD|US\$|€|\$)\s?([\d\.\,]+)", s, re.I)
    premio=f"{m_amt.group(1).upper()} {m_amt.group(2)}" if m_amt else "—"
    m_slots=re.search(r"(\d+)\s+(cupos|ganadores|becas|finalistas)", s, re.I)
    cupos=m_slots.group(1) if m_slots else "—"
    m_fee=re.search(r"(?:fee|arancel|inscripci[oó]n)\s*(?:de)?\s*(USD|US\$|€|\$)?\s*([\d\.\,]+)", s, re.I)
    fee=(m_fee.group(1) or "$")+" "+m_fee.group(2) if m_fee else "0"
    return premio, cupos, fee

def dificultad_1_100(tipo: str, text: str):
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
def parse_page(url: str):
    """Nunca levanta excepción: si algo falla, vuelve None."""
    try:
        html = fetch(url)
        soup = BeautifulSoup(html, "html.parser")
        raw_title, meta_desc = extract_title_desc(soup)
        full = re.sub(r"\s+"," ", soup.get_text(" ").strip())
        abre, cierra = extract_range(full)
        tipo = type_guess(raw_title + " " + meta_desc + " " + full)
        loc  = guess_location(raw_title + " " + full)
        scope = scope_from_location(loc)
        premio, cupos, fee = extract_key_data(full)
        titulo = titulo_ia(raw_title, full, urlparse(url).netloc)
        resumen = resumen_ia(meta_desc if meta_desc else full)
        diff = dificultad_1_100(tipo, full)
        return {
            "source": urlparse(url).netloc.replace("www.",""),
            "title": titulo,
            "url": url,
            "open_at": abre, "deadline": cierra,
            "type": tipo, "location": loc, "scope": scope,
            "difficulty": diff, "prize": premio, "slots": cupos, "fee": fee,
            "summary": resumen,
        }
    except Exception:
        return None

# ────────────────────────────────────────────────────────────────────────────────
# Buscador (SerpAPI) + Fallback de listados
# ────────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def google_search_serpapi(queries, per_q=10):
    if not SERP_KEY: return []
    urls=[]
    for q in queries:
        try:
            params={"engine":"google","q":q,"hl":"es","num":per_q,"api_key":SERP_KEY}
            data=requests.get("https://serpapi.com/search.json", params=params, timeout=REQ_TIMEOUT).json()
            for r in data.get("organic_results", []):
                u=r.get("link","")
                if u and u.startswith("http") and not any(b in u for b in BLOCKED):
                    urls.append(u)
        except Exception:
            continue
    # dedupe
    seen=set(); uniq=[]
    for u in urls:
        base=u.split("#")[0]
        if base in seen: continue
        seen.add(base); uniq.append(base)
    return uniq

def make_queries(only_ar: bool, top_n: int):
    base_ar = [
        "convocatoria artes visuales argentina 2025",
        "premio artes visuales argentina 2025",
        "salón artes visuales 2025 argentina",
        "beca arte argentina 2025",
        "open call arte argentina 2025",
    ]
    # empujar dominios AR explícitamente
    domain_q = [f"site:{d} convocatoria 2025" for d in WHITELIST_AR]
    if only_ar:
        return base_ar + domain_q
    else:
        base_world = [
            "open call visual arts 2025", "artist residency 2025",
            "visual arts prize 2025", "photography award 2025"
        ]
        return base_ar + domain_q + base_world

@st.cache_data(ttl=7200, show_spinner=False)
def scrape_list_page(url: str):
    """Plan B: de una página de listados, extraer links que parezcan convocatorias."""
    out=[]
    try:
        soup=BeautifulSoup(fetch(url), "html.parser")
        for a in soup.select("a[href]"):
            href=a["href"].strip()
            if not href or href.startswith("#"): continue
            text=a.get_text(" ").strip()
            if not text: continue
            blob=(text+" "+href).lower()
            if not any(k in blob for k in ["convocatoria","premio","salon","salón","residenc","beca","open-call","open_call","opencall"]):
                continue
            link=href if href.startswith("http") else urljoin(url, href)
            out.append(link)
    except Exception:
        pass
    # dedupe por host+path
    seen=set(); uniq=[]
    for u in out:
        base=u.split("#")[0]
        if base in seen: continue
        seen.add(base); uniq.append(base)
    return uniq[:50]

def gather_fallback_from_whitelist():
    urls=[]
    seeds = [
        "https://www.klemm.org.ar/",
        "https://www.fnartes.gob.ar/",
        "https://www.cultura.gob.ar/",
        "https://palaisdeglace.cultura.gob.ar/",
        "https://enteculturaltucuman.gob.ar/",
        "https://www.fundacionosde.com.ar/",
        "https://museorosagalisteo.gob.ar/",
        "https://www.castagninomacro.org/",
        "https://museosiivori.buenosaires.gob.ar/",
        "https://www.catalogosparaartistas.com/convocatorias",
        "https://es.artealdia.com/Convocatorias",
        "https://www.recursosculturales.com/",
    ]
    for s in seeds:
        urls += scrape_list_page(s)
    # dedupe
    seen=set(); clean=[]
    for u in urls:
        host=urlparse(u).netloc.lower()
        if any(b in host for b in BLOCKED): continue
        base=u.split("#")[0]
        if base in seen: continue
        seen.add(base); clean.append(base)
    return clean

def gather_sources(top_n: int, only_ar: bool):
    urls=[]
    # 1) Google si hay clave
    queries = make_queries(only_ar, top_n)
    if SERP_KEY:
        urls += google_search_serpapi(queries, per_q=max(5, top_n//len(queries)))
    # 2) Fallback: listados de dominios AR + fuentes locales
    urls += gather_fallback_from_whitelist()
    urls += [SOURCES["artealdia_main"], SOURCES["catalogos_convocatorias"]]
    # expandir listados en artealdia/catalogos
    urls += scrape_list_page(SOURCES["artealdia_main"])
    urls += scrape_list_page(SOURCES["catalogos_convocatorias"])

    # limpiar/limitar
    seen=set(); clean=[]
    for u in urls:
        host=urlparse(u).netloc.lower()
        if any(b in host for b in BLOCKED): continue
        base=u.split("#")[0]
        if base in seen: continue
        seen.add(base); clean.append(base)
        if len(clean)>=top_n: break
    return clean

# ────────────────────────────────────────────────────────────────────────────────
# UI — Filtros
# ────────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Filtros")
    ámbito = st.radio("Ámbito", ["Todas","AR solo","Fuera de AR"], horizontal=True, index=0)
    solo_futuras = st.checkbox("Solo futuras", True)
    year_to_show = st.number_input("Año hasta", value=YEAR, step=1)
    q = st.text_input("Buscar texto", "")
    tipo_sel = st.multiselect("Tipo", ["convocatoria","premio","beca","residencia","otro"],
                              default=["convocatoria","premio","beca","residencia"])
    top_n = st.slider("Páginas a inspeccionar", 30, 120, 70, 10)
    st.caption("Sugerencia: 60–90 páginas suele traer 20–60 resultados en menos de 25 s.")

# ────────────────────────────────────────────────────────────────────────────────
# Búsqueda principal
# ────────────────────────────────────────────────────────────────────────────────
if st.button("🔎 Buscar convocatorias", type="primary"):
    t0=time.time()
    items=[]
    only_ar = (ámbito=="AR solo")

    urls = gather_sources(top_n, only_ar)

    results=[]; done=0; prog=st.progress(0)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures=[ex.submit(parse_page, u) for u in urls]
        for f in as_completed(futures):
            rec = None
            try: rec = f.result()
            except Exception: rec = None
            if rec: results.append(rec)
            done+=1; prog.progress(min(1.0, done/max(1,len(futures))))
            if time.time()-t0 > TOTAL_HARD_LIMIT: break
    prog.empty()

    # filtros finos
    def keep(r):
        d = r.get("deadline")
        if ámbito=="Fuera de AR" and r.get("scope")=="AR": return False
        if ámbito=="AR solo" and r.get("scope")!="AR": return False
        if solo_futuras and d and d < date.today(): return False
        if d and d > date(year_to_show,12,31): return False
        if tipo_sel and r.get("type") not in tipo_sel: return False
        if q:
            blob=(r.get("title","")+" "+r.get("summary","")).lower()
            if q.lower() not in blob: return False
        return True

    items=[x for x in results if keep(x)]
    items.sort(key=lambda r:(r.get("deadline") is None, r.get("deadline") or date(year_to_show,12,31)))

    # métricas
    c1,c2,c3 = st.columns(3)
    c1.metric("Convocatorias", len(items))
    first = next((it["deadline"] for it in items if it.get("deadline")), None)
    last  = next((it["deadline"] for it in reversed(items) if it.get("deadline")), None)
    c2.metric("Primera fecha", first.strftime("%d/%m/%Y") if first else "—")
    c3.metric("Última fecha",  last.strftime("%d/%m/%Y")  if last  else "—")
    st.caption(f"⏱ {round(time.time()-t0,1)} s")
    st.markdown("---")

    if not items:
        st.warning("No hay resultados con estos filtros. Subí el tope, quitá 'Solo futuras' o cambiá palabras clave.")
    # tarjetas
    for r in items:
        open_txt = r["open_at"].strftime("%d/%m/%Y") if r.get("open_at") else "—"
        dl = r.get("deadline")
        dl_txt = dl.strftime("%d/%m/%Y") if dl else "Sin dato"
        left = days_left(dl)
        urgency = "🟢" if left is None else ("🟡" if left and left<=21 else "🟢")
        if left is not None and left <= 7: urgency = "🔴"

        with st.container(border=True):
            a,b = st.columns([3,1])
            with a:
                st.subheader(r["title"])
                st.markdown(f"[Abrir convocatoria]({r['url']})")
                chips = f"`{r['type']}`  ·  {r['location']}  ·  {r['source']}"
                st.markdown(chips)
                st.markdown(f"**Abre:** {open_txt}  •  **Cierra:** {dl_txt} {f'({left} días)' if left is not None else ''}  {urgency}")
                st.write(r["summary"])
            with b:
                st.metric("Dificultad (1–100)", r["difficulty"])
                st.caption("Datos clave")
                st.write(f"• **Premio:** {r['prize']}")
                st.write(f"• **Cupos:** {r['slots']}")
                st.write(f"• **Fee:** {r['fee']}")

    # export
    if items:
        # CSV
        buf=io.StringIO(); import csv as _csv; w=_csv.writer(buf)
        w.writerow(["titulo","url","fuente","tipo","lugar","ambito","abre","cierra","dificultad","premio","cupos","fee","resumen"])
        for c in items:
            w.writerow([
                c["title"], c["url"], c["source"], c["type"], c["location"], c["scope"],
                c["open_at"].strftime("%Y-%m-%d") if c.get("open_at") else "",
                c["deadline"].strftime("%Y-%m-%d") if c.get("deadline") else "",
                c["difficulty"], c["prize"], c["slots"], c["fee"], c["summary"]
            ])
        st.download_button("⬇️ Exportar CSV", buf.getvalue(), "artify_convocatorias.csv", "text/csv")

        # ICS (eventos en la fecha de cierre)
        def make_ics(items):
            def dtfmt(d): return d.strftime("%Y%m%d")
            ics = ["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//Artify//Convocatorias//ES"]
            for c in items:
                if not c.get("deadline"): continue
                desc = (c.get("summary","")[:200]).replace("\n"," ")
                ics += [
                    "BEGIN:VEVENT",
                    f"SUMMARY:{c['title']} (cierre)",
                    f"DTSTART;VALUE=DATE:{dtfmt(c['deadline'])}",
                    f"DTEND;VALUE=DATE:{dtfmt(c['deadline'] + timedelta(days=1))}",
                    f"DESCRIPTION:{desc}  URL: {c.get('url','')}",
                    "END:VEVENT"
                ]
            ics.append("END:VCALENDAR")
            return "\n".join(ics)
        st.download_button("📅 Exportar calendario (ICS)", make_ics(items), "artify_convocatorias.ics", "text/calendar")

else:
    st.info("Elegí filtros y apretá **Buscar convocatorias**. Con 60–90 páginas y ‘AR solo’ deberías ver 20+ resultados.")
