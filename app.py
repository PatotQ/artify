# Artify — versión liviana para Streamlit Cloud (sin pandas)
# Fuentes: Arte Al Día, Catálogos para Artistas, Bandadas (público)
# Carga manual (no scrapea al iniciar), timeouts cortos y toggles por fuente.

import re, csv, io, time
from datetime import date, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
import streamlit as st

st.set_page_config(page_title="Artify — Convocatorias para Fla", layout="wide")

# ----------------------------- Config -----------------------------
SOURCES = {
    "artealdia_main": "https://es.artealdia.com/Convocatorias",
    "artealdia_tag_convocatorias": "https://es.artealdia.com/Tags/%28tag%29/Convocatorias",
    "artealdia_tag_convocatoria": "https://es.artealdia.com/Tags/%28tag%29/Convocatoria",
    "catalogos_convocatorias": "https://www.catalogosparaartistas.com/convocatorias",
    "bandadas_home": "https://www.bandadas.com/",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}
REQUEST_TIMEOUT = 8       # segundos por request
TOTAL_HARD_LIMIT = 25     # máximo total de scrapeo por clic

# ----------------------- Helpers de fechas ------------------------
MONTHS = {
    'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
    'julio':7,'agosto':8,'septiembre':9,'setiembre':9,'octubre':10,'noviembre':11,'diciembre':12
}

def parse_spanish_date(txt: str):
    if not txt:
        return None
    s = txt.lower()
    # 12 de agosto de 2025
    m = re.search(r"(\d{1,2})\s+de\s+([a-zá]+)\s+de\s+(\d{4})", s)
    if m:
        d, mon, y = int(m.group(1)), m.group(2).replace("á", "a"), int(m.group(3))
        if mon in MONTHS:
            return date(y, MONTHS[mon], d)
    # 12/08/2025
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
    if m:
        d, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if yy < 100:
            yy += 2000
        return date(yy, mm, d)
    return None

DATE_PATTERNS = [
    r"(?:fecha(?:\s+l[ií]mite)?(?:\s+de)?\s*(?:aplicaci[oó]n|postulaci[oó]n|cierre|presentaci[oó]n)?:?\s*)(\d{1,2}\s+de\s+\w+\s+\d{4})",
    r"(?:cierran?\s+el\s+|cierra\s+el\s+)(\d{1,2}\s+de\s+\w+\s+\d{4})",
    r"(?:hasta el\s*)(\d{1,2}\s+de\s+\w+\s+\d{4})",
    r"(?:deadline:?|fecha l[ií]mite:?|cierre:?|cierra:?|cierran:?)[^\d]*(\d{1,2}\s+de\s+\w+\s+\d{4})",
    r"(\d{1,2}/\d{1,2}/\d{2,4})",
]

def extract_deadline(text: str):
    if not text:
        return None
    for pat in DATE_PATTERNS:
        m = re.search(pat, text, flags=re.I)
        if m:
            d = parse_spanish_date(m.group(1))
            if d:
                return d
    return parse_spanish_date(text)

def safe_text(el):
    return re.sub(r"\s+", " ", (el.get_text(" ").strip() if el else "")).strip()

# ------------------------ Heurísticas UI --------------------------
def type_guess(text: str):
    s = (text or "").lower()
    if "residenc" in s:
        return "residency"
    if "beca" in s:
        return "grant"
    if "premio" in s or "salón" in s or "salon" in s:
        return "prize"
    if "open call" in s or "convocatoria" in s:
        return "open_call"
    return "other"

def difficulty_estimate(kind: str, text: str):
    # 2%..45% según pistas muy simples
    base = 0.18
    t = (kind or "open_call").lower()
    s = (text or "").lower()
    if t == "prize":
        base -= 0.06
    if t == "grant":
        base += 0.04
    if t == "residency":
        base -= 0.02
    if "usd" in s or "$" in s:
        base -= 0.03
    m = re.search(r"(\d+)\s+(cupos|ganadores|becas|finalistas)", s)
    if m:
        slots = int(m.group(1))
        base += min(0.10, slots * 0.01)
    if any(k in s for k in ["internacional", "global", "worldwide"]):
        base -= 0.05
    if any(k in s for k in ["argentina", "caba", "latinoamérica", "latinoamerica"]):
        base += 0.02
    return max(0.02, min(0.45, base))

def rec_tip(text: str):
    s = (text or "").lower()
    tips = []
    if any(k in s for k in ["site-specific", "arquitect", "edificio", "mural"]):
        tips.append("Site-specific / pintura expandida.")
    if any(k in s for k in ["pintur", "acrílico", "óleo", "temple"]):
        tips.append("Serie pictórica (6–10 obras).")
    if any(k in s for k in ["digital", "video", "new media", "web"]):
        tips.append("Obra digital / videoarte documentada.")
    if any(k in s for k in ["instalación", "instalacion", "escultura", "3d"]):
        tips.append("Instalación con plan de montaje.")
    if not tips:
        tips.append("Alineá con el texto curatorial; documentá proceso.")
    return " • ".join(tips[:2])

# --------------------------- Scrapers -----------------------------
def fetch(url: str):
    r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.text

def scrape_artealdia(url: str):
    soup = BeautifulSoup(fetch(url), "html.parser")
    out = []
    for art in soup.select("article, .views-row, .node-teaser, .grid__item"):
        a = art.select_one("h2 a, h3 a, a")
        title = safe_text(a)
        link = a["href"] if a and a.has_attr("href") else url
        if link.startswith("/"):
            link = urljoin(url, link)
        summary = safe_text(art)
        deadline = extract_deadline(summary)
        if not (title or summary):
            continue
        out.append({
            "source": "Arte Al Día",
            "title": title or "Convocatoria",
            "url": link,
            "deadline": deadline,
            "type": type_guess(title + " " + summary),
            "summary": summary[:600],
        })
    # dedupe
    seen, uniq = set(), []
    for r in out:
        k = (r["title"], r["url"])
        if k in seen:
            continue
        seen.add(k); uniq.append(r)
    return uniq[:25]

def scrape_catalogos(url: str):
    soup = BeautifulSoup(fetch(url), "html.parser")
    items = []
    for block in soup.select("main, .blog, .entry, article"):
        text = safe_text(block)
        if re.search(r"Inscripci[oó]n|Fecha l[ií]mite|cierra", text, re.I):
            title_el = soup.select_one("h1, h2, .post-title")
            title = safe_text(title_el) or "Convocatorias"
            items.append({
                "source": "Catálogos para Artistas",
                "title": title,
                "url": url,
                "deadline": extract_deadline(text),
                "type": type_guess(text),
                "summary": text[:600],
            })
    if not items:
        for a in soup.select("a"):
            t = safe_text(a)
            if re.search(r"convocatoria|sal[oó]n|premio|beca|residenc", t, re.I):
                items.append({
                    "source": "Catálogos para Artistas",
                    "title": t,
                    "url": urljoin(url, a.get("href", "")),
                    "deadline": None,
                    "type": type_guess(t),
                    "summary": t,
                })
    seen, uniq = set(), []
    for r in items:
        k = (r["title"], r["url"])
        if k in seen:
            continue
        seen.add(k); uniq.append(r)
    return uniq[:25]

def scrape_bandadas(url: str):
    soup = BeautifulSoup(fetch(url), "html.parser")
    items = []
    for h in soup.select("h2, h3"):
        t = safe_text(h)
        if re.search(r"convocatoria|residencia|beca|premio", t, re.I):
            txt = safe_text(h.find_parent() or h)
            items.append({
                "source": "Bandadas",
                "title": t,
                "url": url,
                "deadline": extract_deadline(txt),
                "type": type_guess(t),
                "summary": txt,
            })
    return items[:15]

def gather(enabled: dict):
    start = time.time()
    out = []

    if enabled.get("artealdia"):
        try:
            for k in ["artealdia_main", "artealdia_tag_convocatorias", "artealdia_tag_convocatoria"]:
                out += scrape_artealdia(SOURCES[k])
        except Exception as e:
            st.warning(f"Arte Al Día off: {e}")
    if time.time() - start > TOTAL_HARD_LIMIT:
        return out

    if enabled.get("catalogos"):
        try:
            out += scrape_catalogos(SOURCES["catalogos_convocatorias"])
        except Exception as e:
            st.warning(f"Catálogos off: {e}")
    if time.time() - start > TOTAL_HARD_LIMIT:
        return out

    if enabled.get("bandadas"):
        try:
            out += scrape_bandadas(SOURCES["bandadas_home"])
        except Exception as e:
            st.info("Bandadas requiere login o cambió el HTML.")
    return out

# ----------------------------- UI ------------------------------
st.title("🎨 Artify — Convocatorias para Fla (Lite)")
st.caption("Carga manual, timeouts cortos y fuentes conmutables. Exporta CSV. ")

with st.sidebar:
    st.header("Fuentes")
    enabled = {
        "artealdia": st.checkbox("Arte Al Día", True),
        "catalogos": st.checkbox("Catálogos para Artistas", True),
        "bandadas": st.checkbox("Bandadas (público)", False),
    }
    st.caption("Sugerencia: probá primero con Arte Al Día + Catálogos.")

if st.button("🔎 Cargar convocatorias"):
    calls = gather(enabled)

    if not calls:
        st.info("No encontramos nada ahora. Te mostramos un ejemplo para ver el formato.")
        calls = [{
            "source": "Demo",
            "title": "Ejemplo — Premio X 2025",
            "url": "https://ejemplo.org/convocatoria",
            "deadline": date.today() + timedelta(days=20),
            "type": "prize",
            "summary": "Convocatoria de ejemplo con premio en efectivo. Fecha límite en 20 días."
        }]

    # Render + CSV export
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["title", "source", "type", "deadline", "url", "summary"])

    for c in calls:
        diff = difficulty_estimate(c.get("type", "open_call"), (c.get("title", "") + " " + c.get("summary", "")))
        tip = rec_tip(c.get("summary", ""))

        with st.container(border=True):
            col1, col2 = st.columns([3, 1])
            with col1:
                st.subheader(c["title"])
                st.markdown(f"**Fuente:** {c['source']}  •  **Tipo:** `{c['type']}`")
                dl = c["deadline"].strftime("%d/%m/%Y") if c.get("deadline") else "Sin dato"
                st.markdown(f"**Cierra:** {dl}")
                st.markdown(f"[Abrir convocatoria]({c['url']})")
                if c.get("summary"):
                    st.write(c["summary"][:500] + ("…" if len(c["summary"]) > 500 else ""))
            with col2:
                st.metric("Dificultad estimada", f"{round(diff*100)}%")
                st.caption("Tip de obra:")
                st.write("• " + tip)

        writer.writerow([
            c["title"], c["source"], c["type"],
            c["deadline"].strftime("%Y-%m-%d") if c.get("deadline") else "",
            c["url"], c.get("summary", "")
        ])

    st.download_button("⬇️ Exportar CSV", buf.getvalue(), "artify_calls.csv", "text/csv")

else:
    st.info("Listo para usar. Elegí fuentes en la izquierda y tocá **“🔎 Cargar convocatorias”**.")

