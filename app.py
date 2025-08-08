# Artify — Google + Fuentes + Manual URL (rápido y prolijo)
# - Si hay SERPAPI_KEY en st.secrets -> busca en Google (serio y estable)
# - Si no hay key -> usa las fuentes conocidas (Arte Al Día, Catálogos; Bandadas opcional)
# - Todo en paralelo, con UI clara: Título, URL, Inicio/Cierre, Locación, Dificultad 1–100, Resumen
# - Export CSV + ICS y "Agregar URL" manual

import re, io, csv, time, urllib.parse, json
from datetime import date, timedelta
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
import streamlit as st

st.set_page_config(page_title="Artify — Convocatorias (Google + fuentes)", layout="wide")
YEAR = date.today().year

# ---------------- Config ----------------
MAX_WORKERS = 12
REQ_TIMEOUT = 7
TOTAL_HARD_LIMIT = 28  # s tope por búsqueda
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
BLOCKED = ("instagram.com","facebook.com","x.com","twitter.com","tiktok.com","youtube.com","linkedin.com","pinterest.","flickr.","tumblr.","vimeo.com")

SOURCES = {
    "artealdia_main": "https://es.artealdia.com/Convocatorias",
    "artealdia_tag_convocatorias": "https://es.artealdia.com/Tags/%28tag%29/Convocatorias",
    "artealdia_tag_convocatoria": "https://es.artealdia.com/Tags/%28tag%29/Convocatoria",
    "catalogos_convocatorias": "https://www.catalogosparaartistas.com/convocatorias",
    "bandadas_login": "https://www.bandadas.com/login",
    "bandadas_convoc": "https://www.bandadas.com/convocation",
}

SERP_KEY = st.secrets.get("SERPAPI_KEY")

# --------------- Fecha helpers ---------------
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

DATE_PATS = [
    r"(?:fecha(?:\s+l[ií]mite)?(?:\s+de)?\s*(?:aplicaci[oó]n|postulaci[oó]n|cierre|presentaci[oó]n)?:?\s*)(\d{1,2}\s+de\s+\w+\s+\d{4})",
    r"(?:cierran?\s+el\s+|cierra\s+el\s+|hasta el\s+)(\d{1,2}\s+de\s+\w+\s+\d{4})",
    r"(\d{1,2}/\d{1,2}/\d{2,4})",
]
RANGE_PATS = [
    r"del\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})\s+al\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})",
    r"del\s+(\d{1,2}/\d{1,2}/\d{2,4})\s+al\s+(\d{1,2}/\d{1,2}/\d{2,4})",
]
def extract_deadline(text:str):
    if not text: return None
    for pat in DATE_PATS:
        m = re.search(pat, text, re.I)
        if m:
            d = parse_spanish_date(m.group(1))
            if d: return d
    return parse_spanish_date(text)
def extract_range(text:str):
    if not text: return (None, None)
    for pat in RANGE_PATS:
        m = re.search(pat, text, re.I)
        if m:
            return parse_spanish_date(m.group(1)), parse_spanish_date(m.group(2))
    return (None, extract_deadline(text))
def days_left(d): return None if not d else (d - date.today()).days

# --------------- Heurísticas texto ---------------
def safe_text(el): return re.sub(r"\s+", " ", (el.get_text(' ').strip() if el else "")).strip()
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
    t=(kind or "open_call").lower(); s=(text or "").lower()
    if t=="prize": base-=0.06
    if t=="grant": base+=0.04
    if t=="residency": base-=0.02
    if "usd" in s or "$" in s or "€" in s: base-=0.03
    m=re.search(r"(\d+)\s+(cupos|ganadores|becas|finalistas)", s)
    if m: base += min(0.10, int(m.group(1))*0.01)
    if any(k in s for k in ["internacional","global","worldwide"]): base-=0.05
    if any(k in s for k in ["argentina","caba","latinoamérica","latinoamerica"]): base+=0.02
    chance=max(0.02, min(0.45, base))
    return max(1, min(100, 100 - round(chance*100)))
def short(text:str, n=300):
    s=re.sub(r"\s+"," ",(text or "")).strip()
    return s if len(s)<=n else s[:n-1]+"…"

# --------------- HTTP + parse ---------------
@st.cache_data(ttl=21600)
def fetch(url:str):
    r=requests.get(url, headers=HEADERS, timeout=REQ_TIMEOUT, allow_redirects=True)
    r.raise_for_status(); return r.text

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
    s,e=extract_range(full)
    kind=type_guess(title+" "+meta_desc+" "+full)
    loc=guess_location(title+" "+full)
    scope=scope_from_location(loc)
    prize,slots,fee=extract_key_data(full)
    diff=difficulty_1_100(kind, full)
    return {
        "source":urlparse(url).netloc,
        "title":title.strip(),
        "url":url, "open_at":s, "deadline":e,
        "type":kind, "location":loc, "scope":scope,
        "difficulty":diff, "prize":prize, "slots":slots, "fee":fee,
        "summary": short(meta_desc if meta_desc else full),
    }

# --------- Google (SerpAPI) ----------
@st.cache_data(ttl=3600)
def google_search_serpapi(queries, max_results_per_q=10):
    urls=[]
    for q in queries:
        try:
            params={"engine":"google","q":q,"hl":"es","num":max_results_per_q,"api_key":SERP_KEY}
            data=requests.get("https://serpapi.com/search.json", params=params, timeout=REQ_TIMEOUT).json()
            for r in data.get("organic_results", []):
                u=r.get("link","")
                if u and u.startswith("http") and not any(b in u for b in BLOCKED): urls.append(u)
        except Exception:
            continue
    # dedupe
    seen=set(); uniq=[]
    for u in urls:
        base=u.split("#")[0]
        if base in seen: continue
        seen.add(base); uniq.append(base)
    return uniq

SEARCH_QUERIES_AR=[
    "site:.gob.ar convocatoria artes visuales 2025",
    "site:.gov.ar convocatoria artes visuales 2025",
    "convocatoria artes visuales argentina 2025",
    "premio artes visuales argentina 2025",
    "salón artes visuales 2025 convocatoria argentina",
    "open call arte argentina 2025",
    "beca arte argentina 2025",
    "Premio Klemm 2025 inscripción",
    "Salón Nacional Tucumán 2025 inscripción",
]

SEARCH_QUERIES_EX=[
    "open call visual arts 2025",
    "visual arts prize 2025",
    "artist residency 2025 open call",
    "photography award 2025 open call",
]

# --------- Fuentes base (fallback) ----------
def scrape_artealdia(url: str):
    soup = BeautifulSoup(fetch(url), "html.parser")
    out=[]
    for art in soup.select("article, .views-row, .node-teaser, .grid__item"):
        a=art.select_one("h2 a, h3 a, a")
        link=a.get("href") if a and a.has_attr("href") else url
        if link.startswith("/"): link=urljoin(url, link)
        title=safe_text(a) or "Convocatoria"
        summary=safe_text(art)
        s,e=extract_range(summary)
        out.append({"source":"Arte Al Día","title":title,"url":link,"open_at":s,"deadline":e,
                    "type":type_guess(title+" "+summary),"location":guess_location(summary),
                    "scope":scope_from_location(guess_location(summary)),
                    "difficulty":difficulty_1_100("open_call", summary),
                    "prize":extract_key_data(summary)[0],"slots":extract_key_data(summary)[1],"fee":extract_key_data(summary)[2],
                    "summary":short(summary)})
    # dedupe
    uniq, seen=[], set()
    for r in out:
        k=(r["title"], r["url"])
        if k in seen: continue
        seen.add(k); uniq.append(r)
    return uniq[:80]

def parse_catalogos_blocks(text:str):
    paras=[p.strip() for p in text.split("\n\n") if p.strip()]
    items=[]
    for i,p in enumerate(paras):
        if not re.search(r"inscripci[oó]n\s*:", p, re.I): continue
        title="Convocatoria"
        for j in range(i-1, max(-1, i-4), -1):
            cand=re.sub(r"\s+"," ", paras[j].strip())
            if len(cand)<4: continue
            if re.search(r"^(suscribite|convocatorias|premios|los que ya pasaron)", cand, re.I): continue
            if re.match(r"^https?://", cand, re.I): continue
            title=cand.split("\n")[0][:120]; break
        s,e=extract_range(p)
        m_bases=re.search(r"bases\s*:\s*(https?://\S+)", p, re.I)
        m_form=re.search(r"inscripci[oó]n\s*:\s*(https?://\S+)", p, re.I)
        items.append({"title":title,"open_at":s,"deadline":e or extract_deadline(p),
                      "url": (m_form.group(1) if m_form else (m_bases.group(1) if m_bases else SOURCES["catalogos_convocatorias"])),
                      "summary": (title+". "+re.sub(r'\\s+',' ',p))[:900]})
    return items

def scrape_catalogos(url:str):
    soup=BeautifulSoup(fetch(url),"html.parser")
    text=safe_text(soup)
    blocks=parse_catalogos_blocks(text)
    out=[]
    for b in blocks:
        kind=type_guess(b["title"]+" "+b["summary"]); loc=guess_location(b["summary"])
        prize,slots,fee=extract_key_data(b["summary"])
        out.append({"source":"Catálogos para Artistas","title":b["title"],"url":b["url"],
                    "open_at":b["open_at"],"deadline":b["deadline"],"type":kind,
                    "location":loc,"scope":scope_from_location(loc),"difficulty":difficulty_1_100(kind,b["summary"]),
                    "prize":prize,"slots":slots,"fee":fee,"summary":short(b["summary"])})
    return out[:80]

def scrape_bandadas(session: requests.Session):
    out=[]
    email=st.secrets.get("BANDADAS_EMAIL"); password=st.secrets.get("BANDADAS_PASSWORD")
    try:
        if email and password:
            login_html=session.get(SOURCES["bandadas_login"], headers=HEADERS, timeout=REQ_TIMEOUT).text
            soup=BeautifulSoup(login_html,"html.parser")
            token=None
            for name in ["authenticity_token","csrfmiddlewaretoken","_token","__RequestVerificationToken"]:
                el=soup.find("input", {"name":name})
                if el and el.get("value"): token=(name,el["value"]); break
            payload={"email":email,"password":password}
            if token: payload[token[0]]=token[1]
            session.post(SOURCES["bandadas_login"], data=payload, headers=HEADERS, timeout=REQ_TIMEOUT)
        html = session.get(SOURCES["bandadas_convoc"], headers=HEADERS, timeout=REQ_TIMEOUT).text
        soup=BeautifulSoup(html,"html.parser")
        for card in soup.select("article, .card, .convocation, .convocatoria, li"):
            txt=safe_text(card)
            if not re.search(r"(convocatoria|residenc|premio|sal[oó]n|beca|open call|concurso)", txt, re.I): continue
            a=card.select_one("a"); link=(a.get("href") if a and a.has_attr("href") else SOURCES["bandadas_convoc"])
            if link.startswith("/"): link=urljoin(SOURCES["bandadas_convoc"], link)
            title=safe_text(a) or txt.split(".")[0][:120]
            s,e=extract_range(txt)
            kind=type_guess(txt); loc=guess_location(txt); prize,slots,fee=extract_key_data(txt)
            out.append({"source":"Bandadas","title":title,"url":link,"open_at":s,"deadline":e or extract_deadline(txt),
                        "type":kind,"location":loc,"scope":scope_from_location(loc),
                        "difficulty":difficulty_1_100(kind, txt),"prize":prize,"slots":slots,"fee":fee,
                        "summary":short(txt)})
    except Exception:
        st.info("Bandadas: no pude leer listados (posible cambio de login/HTML).")
    # dedupe
    uniq, seen=[], set()
    for r in out:
        k=(r["title"], r["url"])
        if k in seen: continue
        seen.add(k); uniq.append(r)
    return uniq[:60]

def gather_fallback(enabled: dict):
    out=[]
    if enabled.get("artealdia"):
        for k in ["artealdia_main","artealdia_tag_convocatorias","artealdia_tag_convocatoria"]:
            try: out += scrape_artealdia(SOURCES[k])
            except Exception as e: st.warning(f"Arte Al Día off: {e}")
    if enabled.get("catalogos"):
        try: out += scrape_catalogos(SOURCES["catalogos_convocatorias"])
        except Exception as e: st.warning(f"Catálogos off: {e}")
    if enabled.get("bandadas"):
        try:
            with requests.Session() as s:
                out += scrape_bandadas(s)
        except Exception:
            st.info("Bandadas requiere login/HTML estable.")
    return out

# ---------------- UI ----------------
st.title("🎨 Artify — Convocatorias (Google + fuentes)")
tab1, tab2 = st.tabs(["Buscar automático", "Agregar URL manual"])

with st.sidebar:
    st.header("Filtros")
    ámbito = st.radio("Ámbito", ["Todas","AR solo","Fuera de AR"], horizontal=True)
    solo_futuras = st.checkbox("Solo futuras", True)
    year_to_show = st.number_input("Año hasta", value=YEAR, step=1)
    q = st.text_input("Buscar texto", "")
    type_filter = st.multiselect("Tipo", ["open_call","grant","prize","residency","other"],
                                 default=["open_call","grant","prize","residency"])

with tab1:
    st.subheader("Automático")
    st.caption("Usa Google (SerpAPI) si hay API key en *Secrets*. Si no, usa las fuentes configuradas.")
    colA, colB = st.columns(2)
    with colA:
        only_ar = st.checkbox("Priorizar Argentina (Google)", True)
    with colB:
        top_n = st.slider("Máx. páginas a inspeccionar", 20, 120, 60, 10)

    go = st.button("🔎 Buscar convocatorias", type="primary")
    if go:
        t0 = time.time()
        items = []
        # --- Camino Google ---
        if SERP_KEY:
            queries = SEARCH_QUERIES_AR if only_ar else (SEARCH_QUERIES_AR + SEARCH_QUERIES_EX)
            urls = google_search_serpapi(queries, max_results_per_q=max(5, top_n // len(queries)))
            # filtrar dominios bloqueados
            clean = []
            seen=set()
            for u in urls:
                base = u.split("#")[0]
                host = urlparse(base).netloc.lower()
                if any(b in host for b in BLOCKED): continue
                if base in seen: continue
                seen.add(base); clean.append(base)
                if len(clean)>=top_n: break
            # parse paralelo
            results = []
            prog = st.progress(0); done=0
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                futs = [ex.submit(parse_page, u) for u in clean]
                for f in as_completed(futs):
                    rec = f.result()
                    if rec: results.append(rec)
                    done+=1; prog.progress(min(1.0, done/max(1,len(futs))))
                    if time.time()-t0 > TOTAL_HARD_LIMIT: break
            prog.empty()
            items = results

        # --- Fallback a fuentes conocidas ---
        if not SERP_KEY or len(items) < 10:
            st.info("Usando fuentes locales (Arte Al Día, Catálogos y Bandadas si está activa).")
            enabled = {
                "artealdia": True,
                "catalogos": True,
                "bandadas": bool(st.secrets.get("BANDADAS_EMAIL") and st.secrets.get("BANDADAS_PASSWORD")),
            }
            items += gather_fallback(enabled)

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
        items = [x for x in items if keep(x)]

        # ordenar por fecha
        items.sort(key=lambda r: (r.get("deadline") is None, r.get("deadline") or date(year_to_show,12,31)))

        # resumen
        c1,c2,c3 = st.columns(3)
        c1.metric("Resultados", len(items))
        first = next((it["deadline"] for it in items if it.get("deadline")), None)
        last  = next((it["deadline"] for it in reversed(items) if it.get("deadline")), None)
        c2.metric("Primera fecha", first.strftime("%d/%m/%Y") if first else "—")
        c3.metric("Última fecha",  last.strftime("%d/%m/%Y")  if last  else "—")
        st.caption(f"⏱ {round(time.time()-t0,1)} s")
        st.markdown("---")

        if not items:
            st.warning("No encontré resultados con estos filtros. Probá ampliar a 'Todas', subir el tope o quitar 'Solo futuras'.")

        # tarjetas
        for r in items:
            open_txt = r["open_at"].strftime("%d/%m/%Y") if r.get("open_at") else "—"
            dl=r.get("deadline")
            dl_txt = dl.strftime("%d/%m/%Y") if dl else "Sin dato"
            left = days_left(dl)
            urgency = "🟢" if left is None else ("🟡" if left and left<=21 else "🟢")
            if left is not None and left <= 7: urgency="🔴"

            with st.container(border=True):
                a,b = st.columns([3,1])
                with a:
                    st.subheader(r["title"])
                    st.markdown(f"[Abrir convocatoria]({r['url']})")
                    st.markdown(f"**Fuente:** {r['source']} • **Tipo:** `{r['type']}` • **Lugar:** {r['location']}")
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
            w.writerow(["title","url","source","type","location","scope","open_at","deadline","difficulty","prize","slots","fee","summary"])
            for c in items:
                w.writerow([
                    c["title"], c["url"], c["source"], c["type"], c["location"], c["scope"],
                    c["open_at"].strftime("%Y-%m-%d") if c.get("open_at") else "",
                    c["deadline"].strftime("%Y-%m-%d") if c.get("deadline") else "",
                    c["difficulty"], c["prize"], c["slots"], c["fee"], c["summary"]
                ])
            st.download_button("⬇️ Exportar CSV", buf.getvalue(), "artify_convocatorias.csv", "text/csv")

with tab2:
    st.subheader("Agregar URL manual")
    manual_url = st.text_input("Pegá cualquier link de convocatoria")
    if st.button("➕ Agregar"):
        if not manual_url:
            st.warning("Pegá una URL primero.")
        else:
            rec = parse_page(manual_url)
            if not rec:
                st.error("No pude leer esa página. Pasame otra o probá más tarde.")
            else:
                open_txt = rec["open_at"].strftime("%d/%m/%Y") if rec.get("open_at") else "—"
                dl = rec.get("deadline")
                dl_txt = dl.strftime("%d/%m/%Y") if dl else "Sin dato"
                left = days_left(dl)
                with st.container(border=True):
                    a,b = st.columns([3,1])
                    with a:
                        st.subheader(rec["title"])
                        st.markdown(f"[Abrir convocatoria]({rec['url']})")
                        st.markdown(f"**Fuente:** {rec['source']} • **Tipo:** `{rec['type']}` • **Lugar:** {rec['location']}")
                        st.markdown(f"**Abre:** {open_txt} • **Cierra:** {dl_txt} {f'({left} días)' if left is not None else ''}")
                        st.write(rec["summary"])
                    with b:
                        st.metric("Dificultad (1–100)", rec["difficulty"])
                        st.caption("Datos clave")
                        st.write(f"• **Premio:** {rec['prize']}")
                        st.write(f"• **Cupos:** {rec['slots']}")
                        st.write(f"• **Fee:** {rec['fee']}")
