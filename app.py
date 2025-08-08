
import re
from datetime import datetime, timedelta, date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
import streamlit as st

st.set_page_config(page_title="Artify — Convocatorias para Fla", layout="wide")

SOURCES = {
    "artealdia_main": "https://es.artealdia.com/Convocatorias",
    "artealdia_tag_convocatorias": "https://es.artealdia.com/Tags/%28tag%29/Convocatorias",
    "artealdia_tag_convocatoria": "https://es.artealdia.com/Tags/%28tag%29/Convocatoria",
    "catalogos_convocatorias": "https://www.catalogosparaartistas.com/convocatorias",
    "bandadas_home": "https://www.bandadas.com/",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

REQUEST_TIMEOUT = 8
TOTAL_TIMEOUT = 25

DATE_PATTERNS = [
    r"(?:fecha(?:\s+l[ií]mite)?(?:\s+de)?\s*(?:aplicaci[oó]n|postulaci[oó]n|cierre|presentaci[oó]n)?:?\s*)(\d{1,2}\s+de\s+\w+\s+\d{4})",
    r"(?:cierran?\s+el\s+|cierra\s+el\s+)(\d{1,2}\s+de\s+\w+\s+\d{4})",
    r"(?:hasta el\s*)(\d{1,2}\s+de\s+\w+\s+\d{4})",
    r"(?:deadline:?|fecha l[ií]mite:?|cierre:?|cierra:?|cierran:?)[^\d]*(\d{1,2}\s+de\s+\w+\s+\d{4})",
    r"(\d{1,2}/\d{1,2}/\d{2,4})",
]

MONTHS = {
    'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
    'julio':7,'agosto':8,'septiembre':9,'setiembre':9,'octubre':10,'noviembre':11,'diciembre':12
}

def parse_spanish_date(text):
    text = (text or "").lower()
    m = re.search(r"(\d{1,2})\s+de\s+([a-zá]+)\s+de\s+(\d{4})", text)
    if m:
        d, mon, y = int(m.group(1)), m.group(2), int(m.group(3))
        mon = MONTHS.get(mon.replace("á","a"), None)
        if mon:
            return date(y, mon, d)
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", text)
    if m:
        d, mon, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100: y += 2000
        return date(y, mon, d)
    return None

def extract_deadline(text):
    if not text: return None
    for pat in DATE_PATTERNS:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            dt_txt = m.group(1)
            d = parse_spanish_date(dt_txt)
            if d: return d
    d = parse_spanish_date(text)
    return d

def guess_type(title_or_text):
    s = (title_or_text or "").lower()
    if "residenc" in s: return "residency"
    if "beca" in s: return "grant"
    if "premio" in s or "salón" in s or "salon" in s: return "prize"
    if "open call" in s or "convocatoria" in s: return "open_call"
    return "other"

def simple_recommendation(text):
    s = (text or "").lower()
    tips = []
    if any(k in s for k in ["site-specific","arquitect","edificio","mural"]):
        tips.append("Obra site-specific o pintura expandida con integración arquitectónica.")
    if any(k in s for k in ["pintur","acrílico","óleo","temple"]):
        tips.append("Serie pictórica (6–10 obras) con statement curado.")
    if any(k in s for k in ["fotograf", "lens", "cámara"]):
        tips.append("Ensayo fotográfico con eje conceptual y edición cuidada.")
    if any(k in s for k in ["digital","video","new media","web"]):
        tips.append("Obra digital / videoarte con documentación técnica clara.")
    if any(k in s for k in ["instalación","instalacion","escultura","3d"]):
        tips.append("Instalación con plan de montaje y mantenimiento detallado.")
    if not tips:
        tips.append("Alineá la propuesta al texto curatorial; enfatizá proceso + documentación.")
    return tips[:3]

def difficulty_estimate(item):
    base = 0.18
    t = (item.get("type") or "open_call").lower()
    title_text = (item.get("title","") + " " + item.get("summary","")).lower()
    if t == "prize": base -= 0.06
    if t == "grant": base += 0.04
    if t == "residency": base -= 0.02
    if "usd" in title_text or "$" in title_text: base -= 0.03
    m_slots = re.search(r"(\d+)\s+(?:cupos|ganadores|becas|finalistas)", title_text)
    if m_slots:
        slots = int(m_slots.group(1))
        base += min(0.10, slots * 0.01)
    if any(k in title_text for k in ["internacional","global","worldwide"]):
        base -= 0.05
    if any(k in title_text for k in ["argentina","caba","latinoamérica","latinoamerica"]):
        base += 0.02
    return float(max(0.02, min(0.45, base)))

def human_pct(p):
    return f"{round(p*100)}%"

def overlap(a_start, a_end, b_start, b_end):
    return max(a_start, b_start) <= min(a_end, b_end)

def safe_get_text(el):
    import re as _re
    return _re.sub(r"\\s+", " ", (el.get_text(" ").strip() if el else "")).strip()

def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.text

def scrape_artealdia_list(url):
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for art in soup.select("article, .views-row, .node-teaser, .grid__item"):
        title_el = art.select_one("h2 a, h3 a, a")
        title = safe_get_text(title_el)
        link = title_el["href"] if title_el and title_el.has_attr("href") else None
        if link and link.startswith("/"):
            link = urljoin(url, link)
        summary = safe_get_text(art)
        deadline = extract_deadline(summary)
        if title or summary:
            cards.append({
                "source": "Arte Al Día",
                "url": link or url,
                "title": title or "Convocatoria",
                "summary": summary[:500],
                "deadline": deadline,
                "type": guess_type((title or "") + " " + summary),
                "open_at": None,
            })
    # dedup
    uniq=[]; seen=set()
    for c in cards:
        key=(c["title"], c["url"])
        if key in seen: continue
        seen.add(key); uniq.append(c)
    return uniq[:25]

def scrape_catalogos_convocatorias(url):
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for block in soup.select("main, .blog, .entry, article"):
        text = safe_get_text(block)
        if re.search(r"Inscripci[oó]n|Fecha l[ií]mite|cierra", text, re.I):
            deadline = extract_deadline(text)
            title_el = soup.select_one("h1, h2, .post-title")
            title = safe_get_text(title_el) or "Convocatorias"
            items.append({
                "source": "Catálogos para Artistas",
                "url": url,
                "title": title,
                "summary": text[:600],
                "deadline": deadline,
                "type": guess_type(text),
                "open_at": None,
            })
    if not items:
        for a in soup.select("a"):
            t = safe_get_text(a)
            if re.search(r"convocatoria|sal[oó]n|premio|beca|residenc", t, re.I):
                items.append({
                    "source": "Catálogos para Artistas",
                    "url": urljoin(url, a.get("href","")),
                    "title": t,
                    "summary": t,
                    "deadline": None,
                    "type": guess_type(t),
                    "open_at": None,
                })
    uniq=[]; seen=set()
    for c in items:
        key=(c["title"], c["url"])
        if key in seen: continue
        seen.add(key); uniq.append(c)
    return uniq[:25]

def scrape_bandadas_public(url):
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for h in soup.select("h2, h3"):
        t = safe_get_text(h)
        if re.search(r"convocatoria|residencia|beca|premio", t, re.I):
            items.append({
                "source": "Bandadas",
                "url": url,
                "title": t,
                "summary": safe_get_text(h.find_parent() or h),
                "deadline": extract_deadline(safe_get_text(h.find_parent() or h)),
                "type": guess_type(t),
                "open_at": None,
            })
    return items[:15]

def gather_all(enabled):
    import time
    records = []
    start = time.time()
    if enabled.get("artealdia"):
        try:
            for key in ["artealdia_main", "artealdia_tag_convocatorias", "artealdia_tag_convocatoria"]:
                records += scrape_artealdia_list(SOURCES[key])
        except Exception as e:
            st.warning(f"Arte Al Día off: {e}")
    if time.time() - start > TOTAL_TIMEOUT:
        return records
    if enabled.get("catalogos"):
        try:
            records += scrape_catalogos_convocatorias(SOURCES["catalogos_convocatorias"])
        except Exception as e:
            st.warning(f"Catálogos off: {e}")
    if time.time() - start > TOTAL_TIMEOUT:
        return records
    if enabled.get("bandadas"):
        try:
            records += scrape_bandadas_public(SOURCES["bandadas_home"])
        except Exception as e:
            st.warning("Bandadas requiere login; usando lo público (o desactivar si tarda).")
    return records

def normalize_records(records):
    today = date.today()
    norm = []
    for r in records:
        item = dict(r)
        item["difficulty_pct"] = difficulty_estimate(item)
        item["fit_tip"] = " • ".join(simple_recommendation(item.get("summary","")))
        item["open_at"] = item.get("open_at") or today
        d = item.get("deadline")
        if not isinstance(d, (datetime, date)):
            d = extract_deadline(str(d) if d else "")
        item["deadline"] = d
        norm.append(item)
    return norm

# UI
st.title("🎨 Artify — Convocatorias para Fla (light)")
st.caption("Build ultraliviano (sin pandas). Carga manual con timeouts cortos.")

with st.sidebar:
    st.header("Fuentes")
    enabled = {
        "artealdia": st.checkbox("Arte Al Día", value=True),
        "catalogos": st.checkbox("Catálogos para Artistas", value=True),
        "bandadas": st.checkbox("Bandadas (público)", value=False),
    }
    st.caption("Si tarda, apagá Bandadas y reintentá.")

if st.button("🔎 Cargar convocatorias"):
    with st.spinner("Buscando convocatorias…"):
        recs = gather_all(enabled)
        data = normalize_records(recs)

    if not data:
        st.info("No encontramos nada o las fuentes cambiaron. Te mostramos un ejemplo.")
        data = [{
            "title": "Ejemplo — Premio X 2025",
            "source": "Demo",
            "type": "prize",
            "deadline": date.today() + timedelta(days=20),
            "open_at": date.today() - timedelta(days=5),
            "url": "https://ejemplo.org/convocatoria",
            "summary": "Convocatoria de ejemplo con premio en efectivo. Fecha límite en 20 días.",
            "difficulty_pct": 0.14,
            "fit_tip": "Serie pictórica (6–10 obras) con statement curado."
        }]

    # Filtros
    types = ["all"] + sorted({d.get("type","other") for d in data})
    t_sel = st.selectbox("Tipo", types, index=0)

    today = date.today()
    deadlines = [d.get("deadline") for d in data if isinstance(d.get("deadline"), (datetime, date))]
    max_deadline = max(deadlines) if deadlines else (today + timedelta(days=120))
    dr = st.slider("Fecha de cierre (rango)", min_value=today, max_value=max_deadline, value=(today, max_deadline))
    
    def in_range(d):
        if not isinstance(d, (datetime, date)): return True
        return dr[0] <= d <= dr[1]

    filtered = [d for d in data if (t_sel=="all" or d.get("type")==t_sel) and in_range(d.get("deadline"))]

    # Overlaps
    def compute_overlaps(items):
        overlaps = {}
        for i, a in enumerate(items):
            a_start, a_end = a.get("open_at"), a.get("deadline") or (today + timedelta(days=30))
            for j, b in enumerate(items):
                if j <= i: continue
                b_start, b_end = b.get("open_at"), b.get("deadline") or (today + timedelta(days=30))
                if a_start and b_start and overlap(a_start, a_end, b_start, b_end):
                    overlaps.setdefault(i, []).append(j)
                    overlaps.setdefault(j, []).append(i)
        return overlaps
    overlaps_map = compute_overlaps(filtered)

    st.markdown("---")
    for i, row in enumerate(sorted(filtered, key=lambda x: (x.get('deadline') or (today + timedelta(days=365))))):
        with st.container(border=True):
            c1, c2 = st.columns([3,1])
            with c1:
                st.subheader(row["title"])
                st.markdown(f"**Fuente:** {row['source']}  •  **Tipo:** `{row.get('type','other')}`")
                dl = row.get("deadline")
                dl_txt = dl.strftime("%d/%m/%Y") if isinstance(dl, (datetime, date)) else "Sin dato"
                st.markdown(f"**Cierra:** {dl_txt}")
                st.markdown(f"[Abrir convocatoria]({row['url']})")
                if row.get("summary"):
                    s = row["summary"]
                    st.write(s[:500] + ("…" if len(s)>500 else ""))
            with c2:
                st.metric("Dificultad", human_pct(row["difficulty_pct"]))
                peers = overlaps_map.get(i, [])
                if peers:
                    st.warning(f"Se solapa con {len(peers)} otra(s).")
                st.caption("Tip de obra:")
                st.write("• " + row["fit_tip"])

    st.markdown("---")
    st.caption("Build light. Si alguna fuente tarda, apagá y reintentá.")
else:
    st.info("Listo para usar. Elegí las fuentes y tocá **“🔎 Cargar convocatorias”**.")
