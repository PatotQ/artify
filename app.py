# # Artify — versión prolija (lite, sin pandas) para Streamlit Cloud
# Fuentes: Arte Al Día, Catálogos para Artistas, Bandadas (público)
# - No scrapea al iniciar. Botón "Cargar" con timeouts y toggles por fuente.
# - Orden por fecha hasta fin de año.
# - Fichas con: lugar (heurístico), tipo, dificultad (% + etiqueta), días restantes, premio/cupos/fee y tip de obra.
# - Exporta CSV e ICS (para calendario). Búsqueda y filtros.

import re, io, csv, time
from datetime import date, datetime, timedelta
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
    "bandadas_home": "https://www.bandadas.com/",
}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
REQ_TIMEOUT = 8         # seg por request
TOTAL_HARD_LIMIT = 25   # seg máx por clic

# ---------- Helpers de fechas ----------
MONTHS = {
    'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
    'julio':7,'agosto':8,'septiembre':9,'setiembre':9,'octubre':10,'noviembre':11,'diciembre':12
}
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
    r"(?:fecha


