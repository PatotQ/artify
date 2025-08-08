# Artify — MetaBuscador de Convocatorias (lite, sin pandas)
# - Usa DDG HTML como metabuscador (evita Google bloqueos)
# - Visita resultados y extrae: título limpio, URL, inicio/cierre, días, locación, tipo, dificultad 1–100, resumen
# - Filtros por texto, tipo y ámbito (AR / Fuera de AR). Orden por fecha.
# - Mantiene fuentes iniciales y Bandadas (via st.secrets) como extras.

import re, io, csv, time, urllib.parse
from datetime import date, timedelta
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
import streamlit as st

st.set_page_config(page_title="Artify — Convocatorias", layout="wide")
YEAR = date.today().year
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
REQ_TIMEOUT = 8
TOTAL_HARD_LIMIT = 35  # seg por búsqueda

# ---------- helpers fecha ----------
MONTHS = {'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,'julio':7,'agosto':8,'septiembre':9,'setiembre':9,'octubre':10,'noviembre':11,'diciembre':12}
def parse_spanish_date(txt: str):
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
def extract_deadline(text: str):
    if not text: return None
    for pat in DATE_PATTERNS:
        m = re.search(pat, text, re.I)
        if m:
            d = parse_spanish_date(m.group(1))
            if d: return d
    return parse_spanish_date(text)
def extract_date_range(text: str):
    if not text: return None, None
    for pat in RANGE_PATTERNS:
        m = re.search(pat, text, re.I)
        if m:
            return parse_spanish_date(m.group(1)), parse_spanish_date(m.group(2))
    end = extract_deadline(text)
    return None, end
def days_left(d):
    if not d: return None
    return (d - date.today()).days

# ---------- texto/heurísticas ----------
def safe_text(el): return re.sub(r"\s+", " ", (el.get_text(" ").strip() if el else "")).strip()
def type_guess(text: str):
    s = (text or "").lower()
    if "residenc" in s: return "residency"
    if "beca" in s: return "grant"
    if "premio" in s or "salón" in s or "salon" in s or "concurso" in s: return "prize"
    if "open call" in s or "convocatoria" in s: return "open_call"
    return "other"
CITIES_AR = ["caba","buenos aires","rosario","cordoba","córdoba","la plata","mendoza","tucumán","salta","neuquén","bahía blanca","bahia blanca"]
COUNTRIES = ["argentina","uruguay","chile","mexico","méxico","españa","colombia","peru","perú","brasil","paraguay","bolivia","ecuador","costa rica","guatemala","panamá","panama","estados unidos","usa","reino unido","italia","francia","alemania","grecia","portugal"]
def guess_location(text: str):
    s = (text or "").lower()
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
def scope_from_location(loc: str):
    if not loc or loc == "—": return "UNK"
    return "AR" if loc.lower()=="argentina" else "EX"
def extract_key_data(text: str):
    s = (text or "")
    m_amt = re.search(r"(USD|US\$|€|\$)\s?([\d\.\,]+)", s, re.I)
    prize = f"{m_amt.group(1).upper()} {m_amt.group(2)}" if m_amt else "—"
    m_slots = re.search(r"(\d+)\s+(cupos|ganadores|becas|finalistas)", s, re.I)
    slots = m_slots.group(1) if m_slots else "—"
    m_fee = re.search(r"(?:fee|arancel|inscripci[oó]n)\s*(?:de)?\s*(USD|US\$|€|\$)?\s*([\d\.\,]+)", s, re.I)
    fee = (m_fee.group(1) or "$") + " " + m_fee.group(2) if m_fee else "0"
    return prize, slots, fee
def difficulty_1_100(kind: str, text: str):
    # menor chance => mayor dificultad
    base = 0.18
    t = (kind or "open_call").lower()
    s = (text or "").lower()
    if t == "prize": base -= 0.06
    if t == "grant": base += 0.04
    if t == "residency": base -= 0.02
    if "usd" in s or "$" in s or "€" in s: base -= 0.03
    m = re.search(r"(\d+)\s+(cupos|ganadores|becas|finalistas)", s)
    if m:
        slots = int(m.group(1)); base += min(0.10, slots * 0.01)
    if any(k in s for k in ["internacional","global","worldwide"]): base -= 0.05
    if any(k in s for k in ["argentina","caba","latinoamérica","latinoamerica"]): base += 0.02
    chance = max(0.02, min(0.45, base))
    return max(1, min(100, 100 - round(chance * 100)))  # 100 = muy difícil
def short_summary(text: str, max_len=260):
    s = re.sub(r"\s+", " ", (text or "")).strip()
    if len(s) <= max_len: return s
    return s[:max_len-1] + "…"

# ---------- HTTP ----------
@st.cache_data(ttl=3600)
def ddg_search(query: str, max_results=20):
    """Usa el HTML simple de DuckDuckGo (sin JS). Devuelve lista de URLs limpias."""
    url = "https://html.duckduckgo.com/html/?kl=es-es&q=" + urllib.parse.quote(query)
    html = requests.get(url, headers=HEADERS, timeout=REQ_TIMEOUT).text
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for a in soup.select("a"):
        href = a.get("href") or ""
        if not href: continue
        # DDG envuelve con /l/?uddg=URL
        if href.startswith("/l/?"):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            if "uddg" in qs:
                href = qs["uddg"][0]
        if href.startswith("http") and "duckduckgo" not in href:
            urls.append(href)
        if len(urls) >= max_results: break
    return urls

def fetch(url: str):
    r = requests.get(url, headers=HEADERS, timeout=REQ_TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    return r.text

# ---------- Parse de páginas ----------
def extract_title_and_desc(soup: BeautifulSoup):
    title = ""
    for sel in ["meta[property='og:title']", "meta[name='twitter:title']"]:
        m = soup.select_one(sel)
        if m and m.get("content"): title = m["content"]; break
    if not title:
        h1 = soup.select_one("h1")
        if h1: title = safe_text(h1)
    if not title:
        t = soup.select_one("title")
        if t: title = safe_text(t)
    desc = ""
    for sel in ["meta[name='description']", "meta[property='og:description']"]:
        m = soup.select_one(sel)
        if m and m.get("content"): desc = m["content"]; break
    if not desc:
        p = soup.select_one("p")
        if p: desc = safe_text(p)
    return (title or "Convocatoria"), desc

def parse_page(url: str):
    try:
        html = fetch(url)
    except Exception:
        return None
    soup = BeautifulSoup(html, "html.parser")
    title, meta_desc = extract_title_and_desc(soup)
    full_txt = safe_text(soup)
    s, e = extract_date_range(full_txt)
    end = e or extract_deadline(full_txt)
    kind = type_guess(title + " " + meta_desc + " " + full_txt)
    loc = guess_location(title + " " + full_txt)
    scope = scope_from_location(loc)
    prize, slots, fee = extract_key_data(full_txt)
    diff = difficulty_1_100(kind, full_txt)
    # armar resumen corto prioritizando meta_desc
    snippet = short_summary(meta_desc if meta_desc else full_txt)
    return {
        "source": urlparse(url).netloc,
        "title": title.strip(),
        "url": url,
        "open_at": s,
        "deadline": end,
        "type": kind,
        "location": loc,
        "scope": scope,
        "difficulty": diff,
        "prize": prize, "slots": slots, "fee": fee,
        "summary": snippet,
    }

# ---------- Búsqueda agregada ----------
SEARCH_QUERIES_AR = [
    "convocatoria artes visuales argentina 2025",
    "premio artes visuales argentina 2025",
    "salón nacional artes visuales convocatoria 2025",
    "residencia artistas argentina convocatoria 2025",
    "open call arte argentina 2025",
    "beca arte argentina 2025",
    "premio klemm 2025 inscripción",
    "salón tucumán artes visuales 2025 inscripción",
    "concurso fotografía argentina convocatoria 2025",
]
SEARCH_QUERIES_EX = [
    "open call visual arts 2025 residency",
    "visual arts prize 2025 call",
    "residency artist open call 2025",
    "photography award 2025 open call",
]

@st.cache_data(ttl=1800, show_spinner=False)
def metasearch(only_ar: bool, max_pages: int = 40):
    start = time.time()
    urls = []
    queries = (SEARCH_QUERIES_AR if only_ar else SEARCH_QUERIES_AR + SEARCH_QUERIES_EX)
    for q in queries:
        try:
            urls += ddg_search(q, max_results=15 if only_ar else 10)
        except Exception:
            continue
        if time.time() - start > TOTAL_HARD_LIMIT: break
    # dedupe por URL base
    seen = set(); unique = []
    for u in urls:
        base = u.split("#")[0]
        if base in seen: continue
        seen.add(base); unique.append(base)
    # parsear páginas (capado)
    items = []
    for u in unique[:max_pages]:
        if time.time() - start > TOTAL_HARD_LIMIT: break
        rec = parse_page(u)
        if rec:
            items.append(rec)
    return items

# ---------- UI ----------
st.title("🎨 Artify — Convocatorias (metabúsqueda web)")
with st.sidebar:
    st.header("Opciones")
    ámbito = st.radio("Ámbito", ["Todas", "AR solo", "Fuera de AR"], horizontal=True, index=0)
    solo_futuras = st.checkbox("Solo futuras", True)
    year_to_show = st.number_input("Año hasta", value=YEAR, step=1)
    q = st.text_input("Buscar texto", "")
    type_filter = st.multiselect("Tipo", ["open_call","grant","prize","residency","other"],
                                 default=["open_call","grant","prize","residency"])
    top_n = st.slider("Páginas a inspeccionar", 20, 120, 50, 10)
    st.caption("Tip: subí el número para encontrar más. El tope balancea velocidad vs. cobertura.")

if st.button("🔎 Buscar convocatorias", type="primary"):
    only_ar = (ámbito == "AR solo")
    items = metasearch(only_ar, max_pages=top_n)

    # filtros finos
    def keep(x):
        d = x.get("deadline")
        if ámbito == "Fuera de AR" and x.get("scope") == "AR": return False
        if ámbito == "AR solo" and x.get("scope") != "AR": return False
        if solo_futuras and d and d < date.today(): return False
        if d and d > date(year_to_show,12,31): return False
        if type_filter and x.get("type") not in type_filter: return False
        if q:
            s = (x.get("title","")+" "+x.get("summary","")).lower()
            if q.lower() not in s: return False
        return True
    results = [x for x in items if keep(x)]

    # ordenar: fecha (None al final)
    results.sort(key=lambda r: (r.get("deadline") is None, r.get("deadline") or date(year_to_show,12,31)))

    # resumen superior
    c1,c2,c3 = st.columns(3)
    c1.metric("Resultados", len(results))
    first = next((it["deadline"] for it in results if it.get("deadline")), None)
    last  = next((it["deadline"] for it in reversed(results) if it.get("deadline")), None)
    c2.metric("Primera fecha", first.strftime("%d/%m/%Y") if first else "—")
    c3.metric("Última fecha",  last.strftime("%d/%m/%Y")  if last  else "—")
    st.markdown("---")

    # tarjetas
    if not results:
        st.warning("No encontré resultados con esos filtros. Probá aumentar 'Páginas a inspeccionar' o quitar 'Solo futuras'.")
    for r in results:
        open_txt = r["open_at"].strftime("%d/%m/%Y") if r.get("open_at") else "—"
        dl = r.get("deadline")
        dl_txt = dl.strftime("%d/%m/%Y") if dl else "Sin dato"
        left = days_left(dl)
        urgency = "🟢" if left is None else ("🟡" if left and left <= 21 else "🟢")
        if left is not None and left <= 7: urgency = "🔴"

        with st.container(border=True):
            a,b = st.columns([3,1])
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

    # export
    if results:
        buf = io.StringIO(); w = csv.writer(buf)
        w.writerow(["title","url","source","type","location","scope","open_at","deadline","difficulty","prize","slots","fee","summary"])
        for c in results:
            w.writerow([
                c["title"], c["url"], c["source"], c["type"], c["location"], c["scope"],
                c["open_at"].strftime("%Y-%m-%d") if c.get("open_at") else "",
                c["deadline"].strftime("%Y-%m-%d") if c.get("deadline") else "",
                c["difficulty"], c["prize"], c["slots"], c["fee"], c["summary"]
            ])
        st.download_button("⬇️ Exportar CSV", buf.getvalue(), "artify_websearch.csv", "text/csv")
else:
    st.info("Elegí ‘Ámbito’, ajustá ‘Páginas a inspeccionar’ y tocá **‘🔎 Buscar convocatorias’**. "
            "La app rastrea la web (DDG) y ordena por fecha. Subí el tope para conseguir 20+ resultados.")
