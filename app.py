
import re
from datetime import datetime, timedelta, date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Artify — Convocatorias para Fla", layout="wide")

SOURCES = {
    "artealdia_main": "https://es.artealdia.com/Convocatorias",
    "artealdia_tag_convocatorias": "https://es.artealdia.com/Tags/%28tag%29/Convocatorias",
    "artealdia_tag_convocatoria": "https://es.artealdia.com/Tags/%28tag%29/Convocatoria",
    "catalogos_convocatorias": "https://www.catalogosparaartistas.com/convocatorias",
    "bandadas_home": "https://www.bandadas.com/",
}

# Toggle por fuente para evitar que una sola rompa todo
ENABLED_SOURCES = {
    "artealdia": True,
    "catalogos": True,
    "bandadas": True,  # solo público
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

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

def difficulty_estimate(row):
    base = 0.18
    t = (row.get("type") or "open_call").lower()
    title_text = (row.get("title","") + " " + row.get("summary","")).lower()
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
    return _re.sub(r"\s+", " ", (el.get_text(" ").strip() if el else "")).strip()

def fetch(url):
    # Timeout (connect=5s, read=15s) y manejo de errores no-bloqueante
    try:
        r = requests.get(url, headers=HEADERS, timeout=(5, 15))
        r.raise_for_status()
        return r.text
    except Exception as e:
        return None

def scrape_artealdia_list(url):
    html = fetch(url)
    if not html:
        return []
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
    # Dedup
    seen = set(); uniq = []
    for c in cards:
        key = (c["title"], c["url"])
        if key in seen: continue
        seen.add(key); uniq.append(c)
    return uniq

def scrape_catalogos_convocatorias(url):
    html = fetch(url)
    if not html:
        return []
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
    seen=set(); uniq=[]
    for c in items:
        key=(c["title"], c["url"])
        if key in seen: continue
        seen.add(key); uniq.append(c)
    return uniq

def scrape_bandadas_public(url):
    html = fetch(url)
    if not html:
        return []
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
    return items

def gather_all():
    records = []
    if ENABLED_SOURCES.get("artealdia"):
        for key in ["artealdia_main", "artealdia_tag_convocatorias", "artealdia_tag_convocatoria"]:
            try:
                records += scrape_artealdia_list(SOURCES[key])
            except Exception as e:
                st.warning(f"Arte Al Día off: {e}")
    if ENABLED_SOURCES.get("catalogos"):
        try:
            records += scrape_catalogos_convocatorias(SOURCES["catalogos_convocatorias"])
        except Exception as e:
            st.warning(f"Catálogos off: {e}")
    if ENABLED_SOURCES.get("bandadas"):
        try:
            records += scrape_bandadas_public(SOURCES["bandadas_home"])
        except Exception as e:
            st.info("Bandadas requiere login; usando lo público.")
    return records

def normalize_df(records):
    if not records:
        return pd.DataFrame(columns=[
            "title","source","type","deadline","open_at","url","summary","difficulty_pct","fit_tip"
        ])
    df = pd.DataFrame(records)
    df["difficulty_pct"] = df.apply(difficulty_estimate, axis=1)
    df["fit_tip"] = df["summary"].apply(lambda s: " • ".join(simple_recommendation(s)))
    today = date.today()
    df["open_at"] = df["open_at"].fillna(today)
    def coerce_deadline(x):
        if isinstance(x, (datetime, date)):
            return x
        d = extract_deadline(str(x) if x else "")
        return d
    df["deadline"] = df["deadline"].apply(coerce_deadline)
    return df

st.title("🎨 Artify — Convocatorias para Fla")
st.caption("Cargá las convocatorias cuando quieras. Si alguna fuente falla, desactivala en el panel de la izquierda.")

# Sidebar: controles y fuentes
with st.sidebar:
    st.header("Fuentes")
    arte = st.checkbox("Arte Al Día", value=ENABLED_SOURCES["artealdia"])
    cata = st.checkbox("Catálogos para Artistas", value=ENABLED_SOURCES["catalogos"])
    band = st.checkbox("Bandadas (público)", value=ENABLED_SOURCES["bandadas"])
    ENABLED_SOURCES.update({"artealdia": arte, "catalogos": cata, "bandadas": band})
    st.divider()
    st.caption("Tip: si tarda o hay error de red, desactivá la fuente problemática y volvés a cargar.")

# Lazy-load: solo busca cuando el usuario lo pide
col1, col2 = st.columns([1,3])
with col1:
    load = st.button("🔎 Cargar convocatorias")

if load:
    with st.spinner("Buscando convocatorias… (tiempo máx ~20–30s)"):
        recs = gather_all()
        st.session_state["df"] = normalize_df(recs)

df = st.session_state.get("df")

if df is None or df.empty:
    st.info("Apretá **Cargar convocatorias** para comenzar. Si ya cargaste y no ves resultados, probá desactivar alguna fuente y volver a cargar.")
    st.stop()

types = ["all"] + sorted(df["type"].dropna().unique().tolist())
t_sel = st.selectbox("Tipo", types, index=0)

today = date.today()
max_deadline = df["deadline"].dropna().max() if not df["deadline"].dropna().empty else (today + timedelta(days=120))
deadline_range = st.slider("Fecha de cierre (rango)", min_value=today, max_value=max_deadline, value=(today, max_deadline))

if t_sel != "all":
    df = df[df["type"] == t_sel]

df = df[(df["deadline"].isna()) | ((df["deadline"] >= deadline_range[0]) & (df["deadline"] <= deadline_range[1]))]

def compute_overlaps(df):
    overlaps = {}
    rows = df.reset_index()
    for i, a in rows.iterrows():
        a_start, a_end = a["open_at"], a["deadline"] or (today + timedelta(days=30))
        for j, b in rows.iterrows():
            if j <= i: continue
            b_start, b_end = b["open_at"], b["deadline"] or (today + timedelta(days=30))
            if overlap(a_start, a_end, b_start, b_end):
                overlaps.setdefault(a["index"], []).append(b["index"])
                overlaps.setdefault(b["index"], []).append(a["index"])
    return overlaps

overlaps_map = compute_overlaps(df)
df = df.sort_values(by=["deadline"], ascending=[True])
csv = df.to_csv(index=False)
st.download_button("⬇️ Exportar CSV", data=csv, file_name="convocatorias_artify.csv", mime="text/csv")

st.markdown("---")

for idx, row in df.iterrows():
    with st.container(border=True):
        c1, c2 = st.columns([3,1])
        with c1:
            st.subheader(row["title"])
            st.markdown(f"**Fuente:** {row['source']}  •  **Tipo:** `{row['type']}`")
            dl_txt = row["deadline"].strftime("%d/%m/%Y") if isinstance(row["deadline"], (datetime, date)) else "Sin dato"
            st.markdown(f"**Cierra:** {dl_txt}")
            st.markdown(f"[Abrir convocatoria]({row['url']})")
            if row.get("summary"):
                st.write(row["summary"][:500] + ("…" if len(row["summary"])>500 else ""))
        with c2:
            st.metric("Dificultad estimada", human_pct(row["difficulty_pct"]))
            peers = overlaps_map.get(idx, [])
            if peers:
                st.warning(f"Se solapa con {len(peers)} otra(s).")
            st.caption("Tip de obra:")
            st.write("• " + row["fit_tip"])

st.markdown("---")
st.caption("Heurística experimental. Si ves algo raro, ajustamos scrapers y reglas.")
