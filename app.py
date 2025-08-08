# Artify — versión prolija (lite, sin pandas) para Streamlit Cloud
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
    r"(?:fecha(?:\s+l[ií]mite)?(?:\s+de)?\s*(?:aplicaci[oó]n|postulaci[oó]n|cierre|presentaci[oó]n)?:?\s*)(\d{1,2}\s+de\s+\w+\s+\d{4})",
    r"(?:cierran?\s+el\s+|cierra\s+el\s+)(\d{1,2}\s+de\s+\w+\s+\d{4})",
    r"(?:hasta el\s*)(\d{1,2}\s+de\s+\w+\s+\d{4})",
    r"(?:deadline:?|fecha l[ií]mite:?|cierre:?|cierra:?|cierran:?)[^\d]*(\d{1,2}\s+de\s+\w+\s+\d{4})",
    r"(\d{1,2}/\d{1,2}/\d{2,4})",
]
def extract_deadline(text: str):
    if not text: return None
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

COUNTRIES = ["argentina","uruguay","chile","mexico","méxico","españa","colombia","peru","perú","brasil","paraguay","bolivia","ecuador","costa rica","guatemala","panamá","panama"]
def guess_location(text: str):
    s = (text or "").lower()
    for c in COUNTRIES:
        if c in s: return c.title()
    for k in ["caba","buenos aires","rosario","cordoba","córdoba","montevideo","santiago","cdmx","madrid","barcelona","bogotá","lima","rio","sao paulo","são paulo"]:
        if k in s: return k.title()
    return "—"

def extract_key_data(text: str):
    s = (text or "")
    m_amt = re.search(r"(USD|US\$|€|\$)\s?([\d\.\,]+)", s, re.I)
    prize = f"{m_amt.group(1).upper()} {m_amt.group(2)}" if m_amt else "—"
    m_slots = re.search(r"(\d+)\s+(cupos|ganadores|becas|finalistas)", s, re.I)
    slots = m_slots.group(1) if m_slots else "—"
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

# ---------- Scrapers ----------
def fetch(url: str):
    r = requests.get(url, headers=HEADERS, timeout=REQ_TIMEOUT)
    r.raise_for_status()
    return r.text

def scrape_artealdia(url: str):
    soup = BeautifulSoup(fetch(url), "html.parser")
    out = []
    for art in soup.select("article, .views-row, .node-teaser, .grid__item"):
        a = art.select_one("h2 a, h3 a, a")
        title = safe_text(a)
        link = a["href"] if a and a.has_attr("href") else url
        if link.startswith("/"): link = urljoin(url, link)
        summary = safe_text(art)
        deadline = extract_deadline(summary)
        if not (title or summary): continue
        out.append({
            "source": "Arte Al Día",
            "title": title or "Convocatoria",
            "url": link,
            "deadline": deadline,
            "type": type_guess(title + " " + summary),
            "summary": summary[:1000],
        })
    seen=set(); uniq=[]
    for r in out:
        k=(r["title"], r["url"])
        if k in seen: continue
        seen.add(k); uniq.append(r)
    return uniq[:30]

def scrape_catalogos(url: str):
    soup = BeautifulSoup(fetch(url), "html.parser")
    items=[]
    for block in soup.select("main, .blog, .entry, article"):
        text = safe_text(block)
        if re.search(r"Inscripci[oó]n|Fecha l[ií]mite|cierra|deadline", text, re.I):
            title_el = soup.select_one("h1, h2, .post-title")
            title = safe_text(title_el) or "Convocatorias"
            items.append({
                "source":"Catálogos para Artistas",
                "title":title,"url":url,
                "deadline":extract_deadline(text),
                "type":type_guess(text),"summary":text[:1200]
            })
    if not items:
        for a in soup.select("a"):
            t = safe_text(a)
            if re.search(r"convocatoria|sal[oó]n|premio|beca|residenc", t, re.I):
                items.append({"source":"Catálogos para Artistas","title":t,"url":urljoin(url,a.get("href","")),
                              "deadline":None,"type":type_guess(t),"summary":t})
    seen=set(); uniq=[]
    for r in items:
        k=(r["title"], r["url"])
        if k in seen: continue
        seen.add(k); uniq.append(r)
    return uniq[:30]

def scrape_bandadas(url: str):
    soup = BeautifulSoup(fetch(url), "html.parser")
    items=[]
    for h in soup.select("h2, h3"):
        t = safe_text(h)
        if re.search(r"convocatoria|residencia|beca|premio", t, re.I):
            txt = safe_text(h.find_parent() or h)
            items.append({"source":"Bandadas","title":t,"url":url,"deadline":extract_deadline(txt),
                          "type":type_guess(t),"summary":txt})
    return items[:20]

def gather(enabled: dict):
    start = time.time()
    out=[]
    if enabled.get("artealdia"):
        try:
            for k in ["artealdia_main","artealdia_tag_convocatorias","artealdia_tag_convocatoria"]:
                out += scrape_artealdia(SOURCES[k])
        except Exception as e:
            st.warning(f"Arte Al Día off: {e}")
    if time.time() - start > TOTAL_HARD_LIMIT: return out

    if enabled.get("catalogos"):
        try: out += scrape_catalogos(SOURCES["catalogos_convocatorias"])
        except Exception as e: st.warning(f"Catálogos off: {e}")
    if time.time() - start > TOTAL_HARD_LIMIT: return out

    if enabled.get("bandadas"):
        try: out += scrape_bandadas(SOURCES["bandadas_home"])
        except Exception as e: st.info("Bandadas requiere login o cambió HTML.")
    return out

# ---------- UI ----------
st.title("🎨 Artify — Convocatorias (ordenado por fecha)")
st.caption("Versión cloud estable. Fuentes activables, timeouts cortos, exportables y reseñas claras.")

with st.sidebar:
    st.header("Fuentes")
    enabled = {
        "artealdia": st.checkbox("Arte Al Día", True),
        "catalogos": st.checkbox("Catálogos para Artistas", True),
        "bandadas": st.checkbox("Bandadas (público)", False),
    }
    st.header("Filtros")
    solo_futuras = st.checkbox("Solo futuras", True)
    year_to_show = st.number_input("Año hasta", value=YEAR, step=1)
    q = st.text_input("Buscar (título/descripcion)", "")
    type_filter = st.multiselect("Tipo", ["open_call","grant","prize","residency","other"], default=["open_call","grant","prize","residency"])
    diff_max = st.slider("Dificultad máxima (aprox.)", 2, 45, 45)  # en %
    st.caption("Tip: si tarda, apagá Bandadas y reintentá.")

if st.button("🔎 Cargar convocatorias", type="primary"):
    calls = gather(enabled)

    # Enriquecer campos
    enriched=[]
    for c in calls:
        text_for_heur = (c.get("title","")+" "+c.get("summary",""))
        prize, slots, fee = extract_key_data(text_for_heur)
        loc = guess_location(text_for_heur)
        diff_pct, diff_label = difficulty_estimate(c.get("type","open_call"), text_for_heur)
        enriched.append({
            **c,
            "location": loc,
            "prize": prize,
            "slots": slots,
            "fee": fee,
            "difficulty_pct": diff_pct,
            "difficulty_label": diff_label,
            "tip": rec_tip(c.get("summary","")),
            "days_left": days_left(c.get("deadline")),
        })

    # Filtros
    end_limit = date(year_to_show, 12, 31)
    def keep(x):
        d = x.get("deadline")
        if solo_futuras and d and d < date.today(): return False
        if d and d > end_limit: return False
        if type_filter and x.get("type") not in type_filter: return False
        if q:
            s = (x.get("title","")+" "+x.get("summary","")).lower()
            if q.lower() not in s: return False
        if (x["difficulty_pct"]*100) > diff_max: return False
        return True
    items = [x for x in enriched if keep(x)]

    # Orden por fecha (None al final)
    items.sort(key=lambda r: (r.get("deadline") is None, r.get("deadline") or date(year_to_show,12,31)))

    # Header resumen
    total = len(items)
    first_dl = next((it["deadline"] for it in items if it.get("deadline")), None)
    last_dl = next((it["deadline"] for it in reversed(items) if it.get("deadline")), None)
    colA,colB,colC = st.columns(3)
    colA.metric("Convocatorias encontradas", total)
    colB.metric("Primera fecha", first_dl.strftime("%d/%m/%Y") if first_dl else "—")
    colC.metric("Última fecha", last_dl.strftime("%d/%m/%Y") if last_dl else "—")
    st.markdown("---")

    # Render tarjetas
    for it in items:
        dl = it.get("deadline")
        dl_txt = dl.strftime("%d/%m/%Y") if dl else "Sin dato"
        left_days = it.get("days_left")
        urgency = "🟢" if left_days is None else ("🟡" if left_days and left_days <= 21 else "🟢")
        if left_days is not None and left_days <= 7: urgency = "🔴"
        diff_pct = round(it["difficulty_pct"]*100)

        with st.container(border=True):
            c1,c2 = st.columns([3,1])
            with c1:
                st.subheader(it["title"])
                st.markdown(f"**Fuente:** {it['source']}  •  **Tipo:** `{it['type']}`  •  **Lugar:** {it['location']}")
                st.markdown(f"**Cierra:** {dl_txt}  {urgency}  " + (f"({left_days} días)" if left_days is not None else ""))
                st.markdown(f"[Abrir convocatoria]({it['url']})")
                if it.get("summary"):
                    st.write(it["summary"][:600] + ("…" if len(it["summary"])>600 else ""))
            with c2:
                st.metric("Dificultad", f"{diff_pct}% ({it.get('difficulty_label','')})")
                st.caption("Datos clave")
                st.write(f"• **Premio**: {it.get('prize','—')}")
                st.write(f"• **Cupos**: {it.get('slots','—')}")
                st.write(f"• **Fee**: {it.get('fee','0')}")
                st.caption("Tip de obra")
                st.write("• " + it.get("tip",""))

    # Exportables (CSV + ICS)
    if items:
        # CSV
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["title","source","type","deadline","location","url","prize","slots","fee","difficulty_pct","summary"])
        for c in items:
            writer.writerow([
                c["title"], c["source"], c["type"],
                c["deadline"].strftime("%Y-%m-%d") if c.get("deadline") else "",
                c["location"], c["url"], c["prize"], c["slots"], c["fee"],
                round(c["difficulty_pct"]*100), c.get("summary","")
            ])
        st.download_button("⬇️ Exportar CSV", buf.getvalue(), "artify_convocatorias.csv", "text/csv")

        # ICS (eventos de todo el día en la fecha de cierre)
        def make_ics(items):
            def dtfmt(d): return d.strftime("%Y%m%d")
            ics = ["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//Artify//Convocatorias//ES"]
            for c in items:
                if not c.get("deadline"): continue
                desc = (c.get("summary","")[:200]).replace("\n"," ")
                ics += [
                    "BEGIN:VEVENT",
                    f"SUMMARY:{c['title']} (cierra)",
                    f"DTSTART;VALUE=DATE:{dtfmt(c['deadline'])}",
                    f"DTEND;VALUE=DATE:{dtfmt(c['deadline'] + timedelta(days=1))}",
                    f"DESCRIPTION:{desc}  URL: {c.get('url','')}",
                    "END:VEVENT"
                ]
            ics.append("END:VCALENDAR")
            return "\n".join(ics)
        st.download_button("📅 Agregar a mi calendario (ICS)", make_ics(items), "artify_convocatorias.ics", "text/calendar")

    if not items:
        st.info("No hay resultados con estos filtros. Probá ampliar el rango o quitar búsquedas.")

else:
    st.info("Elegí las fuentes en la izquierda y tocá **“🔎 Cargar convocatorias”**. "
            f"Mostramos en orden por fecha hasta el **{date(YEAR,12,31).strftime('%d/%m/%Y')}**.")
