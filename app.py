# Artify — versión PRO pulida (lite, sin pandas)
# - Ordena por fecha hasta fin de año
# - Título correcto, URL aparte, resumen breve
# - Inicio / Cierre, locación, dificultad 1–100 (100 = más difícil)
# - Filtros por tipo, texto y Ámbito (AR / Fuera de AR)
# - Export CSV + ICS
# - Bandadas opcional con st.secrets

import re, io, csv, time
from datetime import date, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
import streamlit as st

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

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
REQUEST_TIMEOUT = 8
TOTAL_HARD_LIMIT = 30

# ---------- Fechas ----------
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

# del X al Y / del dd/mm/aaaa al dd/mm/aaaa
RANGE_PATTERNS = [
    r"del\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})\s+al\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})",
    r"del\s+(\d{1,2}/\d{1,2}/\d{2,4})\s+al\s+(\d{1,2}/\d{1,2}/\d{2,4})",
]

def extract_date_range(text: str):
    if not text: return None, None
    for pat in RANGE_PATTERNS:
        m = re.search(pat, text, re.I)
        if m:
            s, e = parse_spanish_date(m.group(1)), parse_spanish_date(m.group(2))
            return s, e
    # "Inscripción: hasta el ... "
    m = re.search(r"hasta\s+el\s+(\d{1,2}\s+de\s+\w+\s+\d{4})", text, re.I)
    if m:
        end = parse_spanish_date(m.group(1))
        return None, end
    m = re.search(r"hasta\s+(\d{1,2}/\d{1,2}/\d{2,4})", text, re.I)
    if m:
        end = parse_spanish_date(m.group(1))
        return None, end
    # fallback: single date in the text
    end = extract_deadline(text)
    return None, end

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
        m = re.search(pat, text, re.I)
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
    if "premio" in s or "salón" in s or "salon" in s or "concurso" in s: return "prize"
    if "open call" in s or "convocatoria" in s: return "open_call"
    return "other"

COUNTRIES = [
    "argentina","uruguay","chile","mexico","méxico","españa","colombia","peru","perú",
    "brasil","paraguay","bolivia","ecuador","costa rica","guatemala","panamá","panama",
    "estados unidos","usa","reino unido","italia","francia","alemania","grecia","portugal"
]
CITIES_AR = ["caba","buenos aires","rosario","cordoba","córdoba","la plata","mendoza","tucumán","salta","neuquén","bahía blanca","bahia blanca"]

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
    return "AR" if loc.lower() == "argentina" else "EX"

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
    if any(k in s for k in ["digital","video","new media","web"]): tips.append("Obra digital / videoarte.")
    if any(k in s for k in ["instalación","instalacion","escultura","3d"]): tips.append("Instalación con plan de montaje.")
    if any(k in s for k in ["foto","fotograf","lens"]): tips.append("Ensayo fotográfico.")
    if not tips: tips.append("Alineá con el texto curatorial; documentá proceso.")
    return " • ".join(tips[:2])

def difficulty_score(kind: str, text: str):
    # Calculamos chance 0.02..0.45 (heurística) y devolvemos dificultad 1..100 (100 = más difícil)
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
    return max(1, min(100, 100 - round(chance * 100)))  # 100 = difícil

# ---------- Requests ----------
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
        link = a.get("href") if a and a.has_attr("href") else url
        if link.startswith("/"): link = urljoin(url, link)
        title = safe_text(a) or "Convocatoria"
        summary = safe_text(art)
        s,e = extract_date_range(summary)
        end = e or extract_deadline(summary)
        out.append({
            "source":"Arte Al Día","title":title,"url":link,
            "open_at": s, "deadline": end, "type": type_guess(title+" "+summary),
            "summary": summary[:800]
        })
    # dedupe
    uniq, seen = [], set()
    for r in out:
        k=(r["title"], r["url"])
        if k in seen: continue
        seen.add(k); uniq.append(r)
    return uniq[:50]

# --- Catálogos: parser por párrafos para sacar "Título" + "Inscripción" + links ---
def parse_catalogos_blocks(full_text: str):
    paras = [p.strip() for p in full_text.split("\n\n") if p.strip()]
    items = []
    for i, p in enumerate(paras):
        if not re.search(r"inscripci[oó]n\s*:", p, re.I):
            continue
        # título: buscar hacia arriba hasta encontrar una línea candidata
        title = "Convocatoria"
        for j in range(i-1, max(-1, i-4), -1):
            cand = re.sub(r"\s+", " ", paras[j].strip())
            if len(cand) < 4: continue
            if re.search(r"^(suscribite|convocatorias|premios|los que ya pasaron)", cand, re.I): 
                continue
            if re.match(r"^https?://", cand, re.I): 
                continue
            title = cand.split("\n")[0][:120]
            break
        # rangos/fechas + links
        s, e = extract_date_range(p)
        joined = paras[i]
        m_bases = re.search(r"bases\s*:\s*(https?://\S+)", joined, re.I)
        m_form  = re.search(r"inscripci[oó]n\s*:\s*(https?://\S+)", joined, re.I)
        items.append({
            "title": title,
            "open_at": s,
            "deadline": e or extract_deadline(p),
            "bases_url": m_bases.group(1) if m_bases else "",
            "apply_url": m_form.group(1) if m_form else "",
            "summary": (title + ". " + re.sub(r"\s+", " ", p))[:900],
        })
    return items

def scrape_catalogos(url: str):
    soup = BeautifulSoup(fetch(url), "html.parser")
    text = safe_text(soup)
    blocks = parse_catalogos_blocks(text)
    items = []
    for b in blocks:
        items.append({
            "source": "Catálogos para Artistas",
            "title": b["title"],
            "url": b["apply_url"] or b["bases_url"] or url,
            "open_at": b["open_at"],
            "deadline": b["deadline"],
            "type": type_guess(b["title"] + " " + b["summary"]),
            "summary": b["summary"]
        })
    # fallback: anchors útiles
    if not items:
        for a in soup.select("a"):
            t = safe_text(a)
            if re.search(r"(premio|sal[oó]n|convocatoria|beca|residenc|concurso)", t, re.I):
                items.append({
                    "source":"Catálogos para Artistas",
                    "title": t,
                    "url": urljoin(url, a.get("href","")),
                    "open_at": None, "deadline": None,
                    "type": type_guess(t), "summary": t
                })
    uniq, seen = [], set()
    for r in items:
        k=(r["title"], r["url"])
        if k in seen: continue
        seen.add(k); uniq.append(r)
    return uniq[:120]

def scrape_bandadas(session: requests.Session):
    out = []
    email = st.secrets.get("BANDADAS_EMAIL")
    password = st.secrets.get("BANDADAS_PASSWORD")
    try:
        if email and password:
            # intento CSRF comunes
            login_html = session.get(SOURCES["bandadas_login"], headers=HEADERS, timeout=REQUEST_TIMEOUT).text
            soup = BeautifulSoup(login_html, "html.parser")
            token_name, token_value = None, None
            for name in ["authenticity_token","csrfmiddlewaretoken","_token","__RequestVerificationToken"]:
                el = soup.find("input", {"name": name})
                if el and el.get("value"): token_name, token_value = name, el.get("value"); break
            payload = {"email": email, "password": password}
            if token_name: payload[token_name] = token_value
            session.post(SOURCES["bandadas_login"], data=payload, headers=HEADERS, timeout=REQUEST_TIMEOUT)

        html = fetch(SOURCES["bandadas_convoc"], session=session)
        soup = BeautifulSoup(html, "html.parser")
        for card in soup.select("article, .card, .convocation, .convocatoria, li"):
            txt = safe_text(card)
            if not re.search(r"(convocatoria|residenc|premio|sal[oó]n|beca|open call|concurso)", txt, re.I):
                continue
            a = card.select_one("a")
            link = (a.get("href") if a and a.has_attr("href") else SOURCES["bandadas_convoc"])
            if link.startswith("/"): link = urljoin(SOURCES["bandadas_convoc"], link)
            title = safe_text(a) or txt.split(".")[0][:80]
            s, e = extract_date_range(txt)
            out.append({
                "source":"Bandadas","title":title,"url":link,"open_at":s,"deadline":e or extract_deadline(txt),
                "type": type_guess(txt),"summary": txt[:800]
            })
    except Exception:
        st.info("Bandadas: no pude leer listados (posible cambio de login/HTML). Probá con otras fuentes o revisemos el selector.")
    uniq, seen = [], set()
    for r in out:
        k=(r["title"], r["url"])
        if k in seen: continue
        seen.add(k); uniq.append(r)
    return uniq[:60]

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
        try:
            out += scrape_catalogos(SOURCES["catalogos_convocatorias"])
        except Exception as e:
            st.warning(f"Catálogos off: {e}")
    if time.time() - start > TOTAL_HARD_LIMIT: return out

    if enabled.get("bandadas"):
        try:
            with requests.Session() as s:
                out += scrape_bandadas(s)
        except Exception:
            st.info("Bandadas requiere login/HTML estable. Si tarda, desactívala.")
    return out

# ---------- UI ----------
st.title("🎨 Artify — Convocatorias (ordenadas por fecha)")
with st.sidebar:
    st.header("Fuentes")
    enabled = {
        "artealdia": st.checkbox("Arte Al Día", True),
        "catalogos": st.checkbox("Catálogos para Artistas", True),
        "bandadas": st.checkbox("Bandadas (usar st.secrets)", False),
    }
    st.header("Filtros")
    solo_futuras = st.checkbox("Solo futuras", True)
    year_to_show = st.number_input("Año hasta", value=YEAR, step=1)
    q = st.text_input("Buscar (título/descripcion)", "")
    type_filter = st.multiselect("Tipo", ["open_call","grant","prize","residency","other"], default=["open_call","grant","prize","residency"])
    ámbito = st.radio("Ámbito", ["Todas", "AR solo", "Fuera de AR"], horizontal=True)
    st.caption("Tip: si tarda, apagá Bandadas y reintentá.")

if st.button("🔎 Cargar convocatorias", type="primary"):
    calls = gather(enabled)

    # Enriquecer
    enriched=[]
    for c in calls:
        text = (c.get("title","")+" "+c.get("summary",""))
        prize, slots, fee = extract_key_data(text)
        loc = guess_location(text)
        scope = scope_from_location(loc)
        diff = difficulty_score(c.get("type"), text)
        enriched.append({
            **c, "location":loc, "scope":scope,
            "prize":prize, "slots":slots, "fee":fee,
            "difficulty": diff, "tip": rec_tip(c.get("summary","")),
            "days_left": days_left(c.get("deadline")),
        })

    # Filtros
    end_limit = date(year_to_show, 12, 31)
    def keep(x):
        d = x.get("deadline")
        if solo_futuras and d and d < date.today(): return False
        if d and d > end_limit: return False
        if type_filter and x.get("type") not in type_filter: return False
        if ámbito == "AR solo" and x.get("scope") != "AR": return False
        if ámbito == "Fuera de AR" and x.get("scope") == "AR": return False
        if q:
            s = (x.get("title","")+" "+x.get("summary","")).lower()
            if q.lower() not in s: return False
        return True
    items = [x for x in enriched if keep(x)]

    # Orden por fecha (None al final)
    items.sort(key=lambda r: (r.get("deadline") is None, r.get("deadline") or date(year_to_show,12,31)))

    # Resumen
    total = len(items)
    first_dl = next((it["deadline"] for it in items if it.get("deadline")), None)
    last_dl  = next((it["deadline"] for it in reversed(items) if it.get("deadline")), None)
    c1,c2,c3 = st.columns(3)
    c1.metric("Convocatorias", total)
    c2.metric("Primera fecha", first_dl.strftime("%d/%m/%Y") if first_dl else "—")
    c3.metric("Última fecha",  last_dl.strftime("%d/%m/%Y")  if last_dl  else "—")
    st.markdown("---")

    # Tarjetas
    for it in items:
        open_at = it.get("open_at")
        open_txt = open_at.strftime("%d/%m/%Y") if open_at else "—"
        dl = it.get("deadline")
        dl_txt = dl.strftime("%d/%m/%Y") if dl else "Sin dato"
        left_days = it.get("days_left")
        urgency = "🟢" if left_days is None else ("🟡" if left_days and left_days <= 21 else "🟢")
        if left_days is not None and left_days <= 7: urgency = "🔴"

        with st.container(border=True):
            a,b = st.columns([3,1])
            with a:
                st.subheader(it["title"])
                st.markdown(f"[Abrir convocatoria]({it['url']})")
                st.markdown(f"**Fuente:** {it['source']}  •  **Tipo:** `{it['type']}`  •  **Lugar:** {it['location']}")
                st.markdown(f"**Abre:** {open_txt}  •  **Cierra:** {dl_txt} {('(' + str(left_days) + ' días)') if left_days is not None else ''}  {urgency}")
                if it.get("summary"):
                    st.write(it["summary"][:700] + ("…" if len(it["summary"])>700 else ""))
            with b:
                st.metric("Dificultad (1–100)", it["difficulty"])
                st.caption("Datos clave")
                st.write(f"• **Premio:** {it['prize']}")
                st.write(f"• **Cupos:** {it['slots']}")
                st.write(f"• **Fee:** {it['fee']}")
                st.caption("Tip de obra")
                st.write("• " + it["tip"])

    # Export
    if items:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["title","source","type","location","scope","open_at","deadline","url","prize","slots","fee","difficulty","summary"])
        for c in items:
            w.writerow([
                c["title"], c["source"], c["type"], c["location"], c["scope"],
                c["open_at"].strftime("%Y-%m-%d") if c.get("open_at") else "",
                c["deadline"].strftime("%Y-%m-%d") if c.get("deadline") else "",
                c["url"], c["prize"], c["slots"], c["fee"], c["difficulty"], c.get("summary","")
            ])
        st.download_button("⬇️ Exportar CSV", buf.getvalue(), "artify_convocatorias.csv", "text/csv")

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
        st.download_button("📅 Exportar calendario (ICS)", make_ics(items), "artify_convocatorias.ics", "text/calendar")

else:
    st.info("Elegí las fuentes y tocá **“🔎 Cargar convocatorias”**. Mostramos por fecha hasta el "
            f"**{date(YEAR,12,31).strftime('%d/%m/%Y')}**.")
