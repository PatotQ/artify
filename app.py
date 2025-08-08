# Artify — versión PRO (lite, sin pandas) para Streamlit Cloud
# Fuentes: Arte Al Día, Catálogos para Artistas, Bandadas (opcional con login por st.secrets)
# - No scrapea al iniciar. Botón "Cargar" con timeouts y toggles por fuente.
# - Orden por fecha hasta fin de año.
# - Fichas con: lugar (heurístico), tipo, dificultad (% + etiqueta), días restantes, premio/cupos/fee y tip de obra.
# - Exporta CSV e ICS. Búsqueda, filtros por tipo y por ámbito (AR / Fuera de AR).

import re, io, csv, time
from datetime import date, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
import streamlit as st

# ---------- Config ----------
st.set_page_config(page_title="Artify — Convocatorias", layout="wide")
YEAR = date.today().year

SOURCES = {
    "artealdia_main": "https://es.artealdia.com/Convocatorias",
    "artealdia_tag_convocatorias": "https://es.artealdia.com/Tags/%28tag%29/Convocatorias",
    "artealdia_tag_convocatoria": "https://es.artealdia.com/Tags/%28tag%29/Convocatoria",
    "catalogos_convocatorias": "https://www.catalogosparaartistas.com/convocatorias",
    "bandadas_login": "https://www.bandadas.com/login",
    "bandadas_convoc": "https://www.bandadas.com/convocation",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}
REQUEST_TIMEOUT = 8       # seg por request
TOTAL_HARD_LIMIT = 28     # máximo total de scrapeo por clic

# ---------- Helpers de fechas ----------
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
        if mon in MONTHS: return date(y, MONTHS[mon], d)
    # 12/08/2025
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
    if m:
        d, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if yy < 100: yy += 2000
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
            if d: return d
    return parse_spanish_date(text)

def days_left(d):
    if not d: return None
    return (d - date.today()).days

# ---------- Limpieza/heurísticas ----------
def safe_text(el):
    return re.sub(r"\s+", " ", (el.get_text(" ").strip() if el else "")).strip()

def type_guess(text: str):
    s = (text or "").lower()
    if "residenc" in s: return "residency"
    if "beca" in s: return "grant"
    if "premio" in s or "salón" in s or "salon" in s: return "prize"
    if "open call" in s or "convocatoria" in s: return "open_call"
    return "other"

COUNTRIES = [
    "argentina","uruguay","chile","mexico","méxico","españa","colombia","peru","perú",
    "brasil","paraguay","bolivia","ecuador","costa rica","guatemala","panamá","panama",
    "estados unidos","usa","reino unido","italia","francia","alemania","grecia"
]
CITIES_AR = ["caba","buenos aires","rosario","cordoba","córdoba","la plata","mendoza","tucumán","salta","neuquén","bahía blanca"]

def guess_location(text: str):
    s = (text or "").lower()
    for k in CITIES_AR:
        if k in s: return "Argentina"
    for c in COUNTRIES:
        if c in s:
            # normalizamos
            if c in ["usa","estados unidos"]:
                return "Estados Unidos"
            if c in ["méxico"]:
                return "Mexico"
            if c in ["panamá"]:
                return "Panamá"
            return c.title()
    if "internacional" in s:
        return "Internacional"
    return "—"

def scope_from_location(loc: str):
    if not loc or loc == "—": return "UNK"
    return "AR" if loc.lower() in ["argentina"] else "EX"

def extract_key_data(text: str):
    s = (text or "")
    # premio/monto
    m_amt = re.search(r"(USD|US\$|€|\$)\s?([\d\.\,]+)", s, re.I)
    prize = f"{m_amt.group(1).upper()} {m_amt.group(2)}" if m_amt else "—"
    # cupos
    m_slots = re.search(r"(\d+)\s+(cupos|ganadores|becas|finalistas)", s, re.I)
    slots = m_slots.group(1) if m_slots else "—"
    # fee
    m_fee = re.search(r"(?:fee|arancel|inscripci[oó]n)\s*(?:de)?\s*(USD|US\$|€|\$)?\s*([\d\.\,]+)", s, re.I)
    fee = (m_fee.group(1) or "$") + " " + m_fee.group(2) if m_fee else "0"
    return prize, slots, fee

def rec_tip(text: str):
    s = (text or "").lower()
    tips = []
    if any(k in s for k in ["site-specific","arquitect","edificio","mural"]): tips.append("Site-specific / pintura expandida.")
    if any(k in s for k in ["pintur","acrílico","óleo","temple"]): tips.append("Serie pictórica (6–10 obras).")
    if any(k in s for k in ["digital","video","new media","web"]): tips.append("Obra digital / videoarte documentada.")
    if any(k in s for k in ["instalación","instalacion","escultura","3d"]): tips.append("Instalación con plan de montaje.")
    if any(k in s for k in ["foto","fotograf","lens"]): tips.append("Ensayo fotográfico con edición cuidada.")
    if not tips: tips.append("Alineá con el texto curatorial; documentá proceso.")
    return " • ".join(tips[:2])

def difficulty_estimate(kind: str, text: str):
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
    pct = max(0.02, min(0.45, base))
    label = "Baja" if pct >= 0.30 else "Media" if pct >= 0.15 else "Alta"
    return pct, label

# ---------- Request helpers ----------
def fetch(url: str, session: requests.Session = None):
    s = session or requests
    r = s.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.text

# ---------- Scrapers ----------
def scrape_artealdia(url: str):
    soup = BeautifulSoup(fetch(url), "html.parser")
    out = []
    for art in soup.select("article, .views-row, .node-teaser, .grid__item"):
        a = art.select_one("h2 a, h3 a, a")
        title = safe_text(a)
        link = a["href]()
