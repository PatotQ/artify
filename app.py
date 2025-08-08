# --- PARCHE ROBUSTO DE FECHAS ---

from datetime import date

MONTHS = {
    'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
    'julio':7,'agosto':8,'septiembre':9,'setiembre':9,'octubre':10,'noviembre':11,'diciembre':12
}

def _mk_date(y, m, d):
    """Devuelve date válida o None (no explota)."""
    try:
        return date(y, m, d)
    except Exception:
        return None

def parse_spanish_date(txt: str):
    """Soporta '12 de agosto de 2025', '12/08/2025', '12-08-25', '12.08.2025'.
    Valida y prueba dd/mm y mm/dd si hace falta."""
    if not txt:
        return None
    s = str(txt).lower()
    s = s.replace("º", "").replace("°", "").replace("º", "")
    s = re.sub(r"(\d)(?:st|nd|rd|th)", r"\1", s)  # 1st, 2nd...

    # 12 de agosto de 2025
    m = re.search(r"(\d{1,2})\s+de\s+([a-zá]+)\s+de\s+(\d{4})", s)
    if m:
        d = int(m.group(1))
        mon = m.group(2).replace("á", "a")
        y = int(m.group(3))
        if mon in MONTHS:
            return _mk_date(y, MONTHS[mon], d)

    # 12/08/2025 o 12-08-25
    m = re.search(r"(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})", s)
    if m:
        a = int(m.group(1)); b = int(m.group(2)); y = int(m.group(3))
        if y < 100: y += 2000
        # intenta dd/mm
        dt = _mk_date(y, b, a)
        if dt: return dt
        # intenta mm/dd
        dt = _mk_date(y, a, b)
        if dt: return dt

    # 12.08.2025
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
    if not text:
        return None
    s = str(text)
    # patrones dirigidos
    for pat in DATE_PATS:
        m = re.search(pat, s, re.I)
        if m:
            d = parse_spanish_date(m.group(1))
            if d:
                return d
    # último recurso: cualquier fecha dd/mm/aaaa en el texto
    m = re.search(r"\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}", s)
    if m:
        d = parse_spanish_date(m.group(0))
        if d:
            return d
    return None

def extract_range(text: str):
    if not text:
        return (None, None)
    s = str(text)
    for pat in RANGE_PATS:
        m = re.search(pat, s, re.I)
        if m:
            start = parse_spanish_date(m.group(1))
            end   = parse_spanish_date(m.group(2))
            return start, end
    # "hasta el ... "
    m = re.search(r"hasta(?:\s+el)?\s+([^\.;,\n]+)", s, re.I)
    if m:
        end = parse_spanish_date(m.group(1))
        return None, end
    return (None, extract_deadline(s))
# --- FIN PARCHE ---
