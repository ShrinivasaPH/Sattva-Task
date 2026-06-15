"""
KPI Evidence Verifier — Streamlit + NER
=======================================

A reference implementation of an on-demand data-assurance tool: each reported
line-item is verified ONLY when you press its button, by running named-entity
recognition over the supporting evidence, extracting the relevant value, and
reconciling it against the reported number, location, and details.

NER engine
----------
- Primary  : spaCy (`en_core_web_sm`) — a real pretrained NLP NER model that tags
             MONEY / CARDINAL / QUANTITY / DATE / GPE / ORG / PERSON, etc.
- Domain    : because the base model doesn't know Indian currency formatting,
              Devanagari numerals, or local village names, we augment it with a
              small rule + gazetteer layer. Domain matches take priority; spaCy
              fills the gaps (people, extra orgs/places).
- Fallback : if spaCy or the model isn't installed, the rule layer alone still
             runs, so the app always works.

Run:
    pip install streamlit spacy
    python -m spacy download en_core_web_sm   # optional but recommended
    streamlit run app.py
"""

import re
import io
import os
import json
import html
from dataclasses import dataclass, field

import streamlit as st

try:
    import spacy
    _HAS_SPACY = True
except ImportError:
    _HAS_SPACY = False

try:
    import fitz  # PyMuPDF — render/locate inside PDFs for the cross-check feature
except ImportError:
    fitz = None

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None

try:
    import docx as _docx  # python-docx — read Word evidence
except ImportError:
    _docx = None


# ============================================================================ #
#  CONSTANTS
# ============================================================================ #
TYPE_COLOR = {
    "MONEY": "#B45309", "COUNT": "#0F766E", "QUANTITY": "#7C3AED",
    "DATE": "#475569", "LOCATION": "#B4451F", "ORG": "#0E7490", "PERSON": "#9333EA",
}

# spaCy label -> our entity type
SPACY_MAP = {
    "MONEY": "MONEY", "CARDINAL": "COUNT", "QUANTITY": "QUANTITY", "PERCENT": "QUANTITY",
    "DATE": "DATE", "TIME": "DATE", "GPE": "LOCATION", "LOC": "LOCATION",
    "FAC": "LOCATION", "ORG": "ORG", "PERSON": "PERSON",
}

# Synthetic gazetteer (villages/districts) + Indic-script aliases
PLACES = ["Bhadwasi", "Khejarli", "Sathin", "Pipar", "Jodhpur", "Barmer", "Jajpur"]
PLACE_ALIAS = {"खेजड़ली": "Khejarli", "जोधपुर": "Jodhpur", "पिपाड़": "Pipar", "ଯାଜପୁର": "Jajpur"}
ORG_HINTS = ["Gram Panchayat", "Cooperative Society", "Mahila Dairy", "Society",
             "Pvt Ltd", "Panchayat", "Hospital", "Trust", "Foundation", "TechSkills"]

# Indic digits — Devanagari (Hindi) + Odia — mapped to ASCII for parsing
DEVA = "०१२३४५६७८९"          # Devanagari
ODIA = "୦୧୨୩୪୫୬୭୮୯"          # Odia
DEVA_MAP = {**{d: str(i) for i, d in enumerate(DEVA)},
            **{d: str(i) for i, d in enumerate(ODIA)}}
INDIC = DEVA + ODIA           # all Indic digit glyphs, for regex classes
WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}

# Trust weight + base extraction confidence by evidence type
SOURCE_REL = {"structured_register": 0.88, "financial_pdf": 0.90, "certificate_pdf": 0.95,
              "scanned_image": 0.75, "multilingual_scan": 0.80, "self_declaration": 0.40}
EXTRACT_BASE = {"structured_register": 0.95, "financial_pdf": 0.90, "certificate_pdf": 0.92,
                "scanned_image": 0.72, "multilingual_scan": 0.80, "self_declaration": 0.30}


# ============================================================================ #
#  NUMBER NORMALISATION
# ============================================================================ #
def deva_to_ascii(s: str) -> str:
    return "".join(DEVA_MAP.get(ch, ch) for ch in s)


def to_num(raw: str):
    """Parse a number from messy text: handle commas + Devanagari digits."""
    s = deva_to_ascii(str(raw)).replace(",", "").replace(" ", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


# ============================================================================ #
#  ENTITY MODEL + NER
# ============================================================================ #
@dataclass
class Entity:
    type: str
    text: str
    start: int
    end: int
    value: float = field(default=None)


def _overlaps(a_start, a_end, ents):
    return any(a_start < e.end and a_end > e.start for e in ents)


def _rule_entities(text: str):
    """Domain rule + gazetteer layer — the part spaCy can't do on its own."""
    ents = []
    add = lambda t, s, e: ents.append(Entity(t, text[s:e], s, e))

    def scan(pattern, etype, flags=0):
        for m in re.finditer(pattern, text, flags):
            add(etype, m.start(), m.end())

    # MONEY — Indian currency formatting (₹ / Rs / INR)
    scan(r"(?:₹|Rs\.?|INR)\s?[\d,०-९୦-୯]+(?:\.\d+)?", "MONEY", re.IGNORECASE)
    # PERCENT / QUANTITY
    scan(r"\b[\d,०-९୦-୯]+(?:\.\d+)?\s?%", "QUANTITY")
    scan(r"\b[\d,०-९୦-୯]+(?:\.\d+)?\s?(?:kL|kl|km|m3|acres?|hectares?|ha|litres?|liters?|L|kg|units?)\b",
         "QUANTITY")
    # DATE
    scan(r"\b\d{1,2}[-/ ](?:\d{1,2}|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|"
         r"January|February|March|April|June|July|August|September|October|November|December)"
         r"[a-z]*[-/ ]\d{2,4}\b", "DATE", re.IGNORECASE)
    scan(r"\b(?:FY\s?)?20\d{2}[-–]\d{2,4}\b", "DATE")
    # number-word with digit in parens, e.g. "three (3)"
    scan(r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s?\(\d+\)",
         "COUNT", re.IGNORECASE)
    # COUNT — standalone integers (incl. Devanagari/Odia); skip digits embedded in IDs
    for m in re.finditer(r"[\d०-९୦-୯][\d,०-९୦-୯]*", text):
        s, e = m.start(), m.end()
        before = text[s - 1] if s > 0 else " "
        after = text[e] if e < len(text) else " "
        if re.match(r"[A-Za-z]", before) or re.match(r"[A-Za-z]", after):
            continue
        if before == "-" or after == "-":
            continue
        add("COUNT", s, e)
    # LOCATION — gazetteer (ascii + Devanagari/Odia aliases) with letter boundaries
    for pl in list(PLACES) + list(PLACE_ALIAS):
        for m in re.finditer(r"(?<![A-Za-z\u0900-\u097F\u0B00-\u0B7F])" + re.escape(pl) +
                             r"(?![A-Za-z\u0900-\u097F\u0B00-\u0B7F])", text):
            add("LOCATION", m.start(), m.end())
    # ORG — hint phrases
    low = text.lower()
    for h in ORG_HINTS:
        i = low.find(h.lower())
        while i >= 0:
            add("ORG", i, i + len(h))
            i = low.find(h.lower(), i + len(h))

    # resolve overlaps: keep the longer span; upgrade a bare COUNT to a richer entity
    ents.sort(key=lambda e: (e.start, -(e.end - e.start)))
    kept = []
    for e in ents:
        clash = next((k for k in kept if e.start < k.end and e.end > k.start), None)
        if clash is None:
            kept.append(e)
        elif (e.end - e.start) > (clash.end - clash.start) and clash.type == "COUNT":
            kept[kept.index(clash)] = e
    return kept


@st.cache_resource(show_spinner=False)
def load_nlp():
    """Load spaCy once per session (cached). Returns None if unavailable."""
    if not _HAS_SPACY:
        return None
    try:
        return spacy.load("en_core_web_sm")
    except Exception:
        return None


def extract_entities(text: str, nlp=None):
    """Run the domain rule layer, then let spaCy fill non-overlapping gaps."""
    ents = _rule_entities(text)
    if nlp is not None:
        try:
            for ent in nlp(text).ents:
                etype = SPACY_MAP.get(ent.label_)
                if not etype:
                    continue
                if _overlaps(ent.start_char, ent.end_char, ents):
                    continue
                ents.append(Entity(etype, ent.text, ent.start_char, ent.end_char))
        except Exception:
            pass
    ents.sort(key=lambda e: e.start)
    for e in ents:
        if e.type in ("MONEY", "QUANTITY", "COUNT"):
            e.value = to_num(e.text)
    return ents


# ============================================================================ #
#  CANDIDATE SELECTION · RECONCILE · CONFIDENCE · DECISION
# ============================================================================ #
CUE_RE = re.compile(r"(total|कुल|treated|distributed|generated|placed|deployed|"
                    r"constructed|completed)", re.IGNORECASE)


def pick_candidate(text, ents, rec):
    """Choose the entity to reconcile, guided by the KPI's expected type + cues."""
    want = "MONEY" if rec["unit"] == "INR" else rec.get("expect", "COUNT")
    pool = [e for e in ents if e.type == want and e.value is not None]
    if not pool and want != "COUNT":
        pool = [e for e in ents if e.type == "COUNT" and e.value is not None]
    if not pool:
        return None
    best, best_score = None, -1
    for e in pool:
        before = text[max(0, e.start - 40):e.start]
        score = 5 if CUE_RE.search(before) else 0
        score += 1 if (e.value or 0) > 0 else 0
        if e.value and rec["reported"] and abs(e.value - rec["reported"]) / max(1, rec["reported"]) < 0.5:
            score += 2
        if score > best_score:
            best, best_score = e, score
    return best


def verify(rec, nlp=None):
    """Full per-record verification — returns a result dict for the UI."""
    text = rec["evidence"]
    ents = extract_entities(text, nlp)
    cand = pick_candidate(text, ents, rec)

    # location reconciliation
    loc_found = list(dict.fromkeys(
        PLACE_ALIAS.get(e.text, e.text) for e in ents if e.type == "LOCATION"))
    if not rec.get("location"):
        loc_status = "na"
    elif not loc_found:
        loc_status = "notfound"
    elif any(l.lower() == rec["location"].lower() for l in loc_found):
        loc_status = "match"
    else:
        loc_status = "mismatch"

    # numeric reconciliation
    disc, closeness, status = None, 0.0, None
    if cand is None:
        status = "ESCALATE"
    else:
        tol = {"count": 0.0, "INR": 0.02}.get(rec["unit"], 0.05)  # measurements: small variance
        disc = abs(cand.value - rec["reported"]) / max(1, abs(rec["reported"]))
        closeness = max(0.0, 1 - disc)
        status = "MATCH" if disc <= tol else ("MINOR" if disc <= 0.10 else "MISMATCH")

    # confidence — blend extraction quality, source trust, match closeness, location
    ex = EXTRACT_BASE.get(rec["type"], 0.6)
    src = SOURCE_REL.get(rec["type"], 0.6)
    loc_agree = {"match": 1.0, "na": 0.7, "notfound": 0.5, "mismatch": 0.0}[loc_status]
    if cand is not None:
        conf = 0.45 * ex + 0.25 * src + 0.20 * closeness + 0.10 * loc_agree
    else:
        conf = 0.45 * 0.30 + 0.25 * src
    conf = round(min(1.0, conf), 2)

    # decision
    if cand is None:
        decision, reason = "escalate", "No verifiable value could be extracted from the evidence."
    elif loc_status == "mismatch":
        decision = "flag"
        reason = (f"Value reconciles, but the evidence location ({', '.join(loc_found)}) "
                  f"does not match the reported location ({rec['location']}).")
    elif status == "MISMATCH":
        decision = "flag"
        reason = f"Extracted value differs from the reported value by {disc * 100:.1f}%."
    elif status == "MATCH" and conf >= 0.85:
        decision, reason = "verified", "Extracted value matches the reported value with high confidence."
    else:
        decision = "review"
        reason = (f"Minor variance of {disc * 100:.1f}% — within review range."
                  if status == "MINOR"
                  else "Value matches but confidence is below the auto-verify threshold (source quality).")

    return {"ents": ents, "cand": cand, "loc_found": loc_found, "loc_status": loc_status,
            "status": status, "disc": disc, "confidence": conf, "decision": decision, "reason": reason}


# ============================================================================ #
#  DATA — reported line-items + synthetic evidence
# ============================================================================ #
RECORDS = [
    dict(id=1, project="Project 1", kpi="Number of Farmers Trained", location="Bhadwasi",
         period="Jul 2025", reported=47, unit="count", expect="COUNT", type="structured_register",
         evidence="Training attendance register, Mahila Dairy Cooperative Society, Bhadwasi. A total of 47 farmers were trained on improved dairy practices on 15 July 2025. All entries signed.",
         evidence_file="farmers_trained_register.xlsx"),
    dict(id=2, project="Project 1", kpi="Total Revenue Generated through Dairy Enterprises", location="Bhadwasi",
         period="Q1 2025-26", reported=590000, unit="INR", type="financial_pdf",
         evidence="Quarterly sales & revenue statement, Bhadwasi Dairy Society, FY 2025-26. Total Revenue Generated (Q1): Rs 4,86,400. Certified by Society Secretary.",
         evidence_file="dairy_revenue_statement.pdf", pin_query="4,86,400"),
    dict(id=3, project="Project 3", kpi="Number of New Nadis Constructed", location="Sathin",
         period="Aug 2025", reported=4, unit="count", expect="COUNT", type="certificate_pdf",
         evidence="Gram Panchayat work completion certificate: three (3) new Nadi structures have been constructed and verified on 12 August 2025 at Bhadwasi, Khejarli and Sathin.",
         evidence_file="nadi_completion_certificate.pdf", pin_query="(3)"),
    dict(id=4, project="Project 4", kpi="Number of Patients Treated Through Health Camps", location="Pipar",
         period="Sep 2025", reported=128, unit="count", expect="COUNT", type="scanned_image",
         evidence="Mobile Health Van — health camp attendance, Village: Pipar, 09-09-2025, Dr. S. Meher. Male 71, Female 57. Total patients treated: 128.",
         evidence_file="health_camp_attendance.png"),
    dict(id=5, project="Project 3", kpi="Number of Soil Health Cards Distributed", location="Khejarli",
         period="2025-26", reported=250, unit="count", expect="COUNT", type="multilingual_scan",
         evidence="मृदा स्वास्थ्य कार्ड वितरण पंजी, गाँव: खेजड़ली. कुल वितरित मृदा स्वास्थ्य कार्ड: २५०. (Khejarli — Total Soil Health Cards distributed: 250.)"),
    dict(id=6, project="Project 5", kpi="Number of Youth Placed", location="Jodhpur",
         period="2025-26", reported=60, unit="count", expect="COUNT", type="certificate_pdf",
         evidence="Placement record, TechSkills Pvt Ltd, Jodhpur. Appointment letters on file confirm 52 youth placed during the reporting year.",
         evidence_file="youth_placement_record.pdf", pin_query="52 youth"),
    dict(id=7, project="Project 5", kpi="Number of Self-employed Youth", location="Jodhpur",
         period="2025-26", reported=35, unit="count", expect="COUNT", type="self_declaration",
         evidence="Centre coordinator note: several youth started their own work after the course; exact number not maintained at the centre."),
    dict(id=8, project="Project 2", kpi="Total Patients Treated Through Specialist Doctor Support (OPDs)", location="Barmer",
         period="2025-26", reported=1340, unit="count", expect="COUNT", type="structured_register",
         evidence="OPD register summary, Specialist Doctor Support programme, Barmer, FY 2025-26: total 1,340 patients treated through OPDs."),
    dict(id=9, project="Project 1", kpi="Number of Mobile Veterinary Vans (MVVs) Deployed", location="Bhadwasi",
         period="2025-26", reported=3, unit="count", expect="COUNT", type="certificate_pdf",
         evidence="Deployment record: 3 Mobile Veterinary Vans deployed (RJ-19-GA-1023 and others), Bhadwasi block. Photographs of vans attached."),
    dict(id=10, project="Project 3", kpi="Number of RWH Structures Built", location="Sathin",
         period="2025-26", reported=5, unit="count", expect="COUNT", type="certificate_pdf",
         evidence="Completion certificate, Gram Panchayat: 5 rainwater harvesting (RWH) structures completed at Pipar village and certified satisfactory.",
         evidence_file="rwh_completion_certificate.pdf", pin_query="Pipar"),
    dict(id=11, project="Project 3", kpi="Income Generated Through Agricultural Activities", location="Khejarli",
         period="2025-26", reported=250000, unit="INR", type="financial_pdf",
         evidence="Agricultural produce sales statement, Khejarli SHG: income generated through agricultural activities Rs 2,48,000 for FY 2025-26."),
    dict(id=12, project="Project 4", kpi="Number of Mobile Health Vans Deployed", location="Pipar",
         period="2025-26", reported=2, unit="count", expect="COUNT", type="structured_register",
         evidence="Two (2) Mobile Health Vans deployed under the project covering Pipar and nearby villages; deployment log maintained."),
    # ---- broader evidence types required by the technical specification ----
    dict(id=13, project="Project 3", kpi="Water Holding Capacity Increased (kL)", location="Khejarli",
         period="2025-26", reported=1500, unit="kL", expect="QUANTITY", type="certificate_pdf",
         evidence="Watershed engineering measurement record, Khejarli: on physical measurement the "
                  "water holding capacity increased by 1,250 kL after desilting; certified by the "
                  "Junior Engineer and Panchayat representative.",
         evidence_file="whc_measurement_record.pdf", pin_query="1,250 kL"),
    dict(id=14, project="Project 5", kpi="Number of Exposure Tours Conducted", location="Jodhpur",
         period="Q2 2025-26", reported=3, unit="count", expect="COUNT", type="structured_register",
         evidence="Quarterly field report (Word): during the quarter, 3 exposure tours were conducted "
                  "for enrolled youth to industry sites; travel logs and photographs on file. TechSkills, Jodhpur.",
         evidence_file="field_report_exposure_tours.docx", pin_query="3 exposure tours"),
    dict(id=15, project="Project 3", kpi="Number of Households Benefited", location="Jajpur",
         period="2025-26", reported=320, unit="count", expect="COUNT", type="multilingual_scan",
         evidence="ଗୃହ ସର୍ବେକ୍ଷଣ ପଞ୍ଜିକା, ଗ୍ରାମ: ଯାଜପୁର। ସର୍ବମୋଟ ଉପକୃତ ପରିବାର: ୩୨୦। "
                  "(Jajpur — Total households benefited: 320.)",
         evidence_file="household_register_odia.txt"),
    dict(id=16, project="Project 3", kpi="Number of Check Dams Constructed", location="Sathin",
         period="2025-26", reported=2, unit="count", expect="COUNT", type="scanned_image",
         evidence="Project signboard photograph — Watershed Development, Check Dam Construction. "
                  "Units completed: 2. FY 2025-26. Gram Panchayat, Sathin.",
         evidence_file="check_dam_signboard.png"),
]

DECISION_COLOR = {"pending": "#5b6862", "verified": "#0F766E", "flag": "#B4451F",
                  "review": "#B45309", "escalate": "#6b4a3a"}
DECISION_LABEL = {"pending": "Pending", "verified": "Verified", "flag": "Flagged",
                  "review": "Review", "escalate": "Escalate"}


# ============================================================================ #
#  EVIDENCE FILES — locate & pinpoint the discrepant value in the source doc
# ============================================================================ #
EV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evidence")
HILITE_RGB = (0.71, 0.27, 0.12)          # clay, for PyMuPDF (0–1 floats)
HILITE_PIL = (180, 69, 31)               # clay, for PIL (0–255)


def ev_path(name):
    return os.path.join(EV_DIR, name)


@st.cache_data(show_spinner=False)
def load_bbox():
    try:
        return json.load(open(ev_path("bbox.json")))
    except Exception:
        return {}


def locate_in_pdf(path, query):
    """Find `query` in the PDF; return the page, its bounding box, and the line."""
    if fitz is None:
        return None
    try:
        doc = fitz.open(path)
    except Exception:
        return None
    for pi, page in enumerate(doc):
        hits = page.search_for(query) if query else []
        if hits:
            snippet = ""
            for blk in page.get_text("blocks"):
                line = " ".join(blk[4].split())
                if query and query.replace(" ", "") in line.replace(" ", ""):
                    snippet = line
                    break
            return {"page": pi, "rect": hits[0], "snippet": snippet, "npages": doc.page_count}
    return {"page": 0, "rect": None, "snippet": "", "npages": doc.page_count}


def render_pdf_highlight(path, page_idx, rect):
    """Render the page to PNG with a box drawn around the located value."""
    if fitz is None:
        return None
    doc = fitz.open(path)
    page = doc[page_idx]
    if rect is not None:
        page.draw_rect(rect, color=HILITE_RGB, width=2)
    return page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")


def render_pdf_all_pages(path, pin_query=None):
    """Render every page to PNG; box the located value on its page (full preview)."""
    if fitz is None:
        return []
    doc = fitz.open(path)
    target = locate_in_pdf(path, pin_query) if pin_query else None
    pages = []
    for pi in range(doc.page_count):
        page = doc[pi]
        if target and target["rect"] is not None and target["page"] == pi:
            page.draw_rect(target["rect"], color=HILITE_RGB, width=2)
        pages.append(page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png"))
    return pages


def render_image_highlight(path, bbox):
    """Return the evidence image (PNG) with a box around the value region."""
    if Image is None:
        return open(path, "rb").read()
    im = Image.open(path).convert("RGB")
    if bbox:
        ImageDraw.Draw(im).rectangle(bbox, outline=HILITE_PIL, width=3)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


# ============================================================================ #
#  UI HELPERS
# ============================================================================ #
def fmt(v, unit):
    if v is None:
        return "—"
    if unit == "INR":
        return f"₹{int(v):,}"
    if unit == "count":
        return f"{int(v):,}"
    return f"{int(v):,} {unit}"          # measurements, e.g. "1,250 kL"


def doc_label(r):
    ef = r.get("evidence_file")
    if not ef:
        return "field note"
    ext = ef.rsplit(".", 1)[-1].lower()
    return {"pdf": "PDF", "xlsx": "Excel", "png": "image/photo", "jpg": "image/photo",
            "jpeg": "image/photo", "docx": "Word", "txt": "scan"}.get(ext, ext.upper())


def badge_html(key):
    c = DECISION_COLOR[key]
    return (f"<span style='background:{c}22;color:{c};font-weight:600;font-size:12px;"
            f"padding:3px 11px;border-radius:20px'>{DECISION_LABEL[key]}</span>")


def chips_html(ents):
    if not ents:
        return "<span style='color:#888;font-size:12px'>no entities found</span>"
    out = []
    for e in ents:
        out.append(
            f"<span style='background:{TYPE_COLOR[e.type]};color:#fff;font-size:11.5px;"
            f"padding:3px 9px;border-radius:13px;margin:0 5px 5px 0;display:inline-block'>"
            f"{html.escape(e.text)} <small style='opacity:.8;font-size:9px;letter-spacing:.04em'>"
            f"{e.type}</small></span>")
    return "".join(out)


def highlight_html(text, ents, cand):
    pieces, i = [], 0
    for e in sorted(ents, key=lambda x: x.start):
        if e.start < i:
            continue
        pieces.append(html.escape(text[i:e.start]))
        ring = ("outline:2px solid #16201C;outline-offset:1px;"
                if cand and e.start == cand.start and e.end == cand.end else "")
        pieces.append(
            f"<mark style='background:{TYPE_COLOR[e.type]};color:#fff;padding:1px 3px;"
            f"border-radius:4px;{ring}'>{html.escape(text[e.start:e.end])}</mark>")
        i = e.end
    pieces.append(html.escape(text[i:]))
    return ("<div style='background:#fff;border:1px solid #e2e8e5;border-radius:9px;"
            "padding:12px 14px;font-size:14px;line-height:1.8;color:#27332e'>"
            + "".join(pieces) + "</div>")


def doc_preview(r):
    """Full source-document preview (all pages / full image / table / text)."""
    ef = r["evidence_file"]
    path, ext = ev_path(ef), ef.rsplit(".", 1)[-1].lower()
    if ext == "pdf":
        pages = render_pdf_all_pages(path, r.get("pin_query"))
        if not pages:
            st.info("Install PyMuPDF to preview PDFs inline — use the download button.")
        for i, png in enumerate(pages):
            st.image(png, use_container_width=True,
                     caption=f"{ef} · page {i + 1} of {len(pages)}")
    elif ext in ("png", "jpg", "jpeg"):
        st.image(render_image_highlight(path, load_bbox().get(ef)),
                 use_container_width=True, caption=ef)
    elif ext in ("xlsx", "csv"):
        try:
            import pandas as pd
            df = pd.read_excel(path) if ext == "xlsx" else pd.read_csv(path)
            st.caption(f"{ef} · {len(df)} rows")
            st.dataframe(df, use_container_width=True, hide_index=True, height=300)
        except Exception:
            st.caption("Could not render the spreadsheet — use the download button.")
    elif ext == "docx":
        text = ""
        if _docx:
            try:
                text = "\n".join(p.text for p in _docx.Document(path).paragraphs)
            except Exception:
                text = ""
        st.caption(f"{ef} · Word document")
        st.markdown("> " + html.escape(text).replace("\n", "  \n> ") if text else "_(empty)_")
    elif ext == "txt":
        try:
            text = open(path, encoding="utf-8").read()
        except Exception:
            text = ""
        st.caption(f"{ef} · transcribed scan")
        st.markdown("> " + html.escape(text).replace("\n", "  \n> "))


def context_snippet(r):
    """A one-line pointer to where the value sits, without the full render."""
    ef = r["evidence_file"]
    path, ext = ev_path(ef), ef.rsplit(".", 1)[-1].lower()
    if ext == "pdf":
        loc = locate_in_pdf(path, r.get("pin_query"))
        if loc and loc["snippet"]:
            return f"page {loc['page'] + 1} · “{loc['snippet'][:100]}”"
    if ext in ("xlsx", "csv"):
        return "tabular register (count reconciled against the reported value)"
    return r.get("pin_query") or ""


def render_cross_check(r, res):
    """Pinpoint the source doc; offer a side-by-side 'Go to the doc' preview."""
    ef = r.get("evidence_file")
    discrepancy = res["decision"] in ("flag", "review", "escalate")

    if discrepancy:
        st.markdown("<span style='color:#B4451F;font-weight:600'>⚠ Cross-check the source "
                    "document</span>", unsafe_allow_html=True)
    else:
        st.markdown("<span style='font-size:11px;letter-spacing:.1em;color:#7c8983'>"
                    "SOURCE DOCUMENT</span>", unsafe_allow_html=True)

    if not ef:
        st.caption("Source: inline field note (no attached file).")
        if res["cand"] or discrepancy:
            st.markdown(f"> {html.escape(r['evidence'])}")
        return

    snip = context_snippet(r)
    st.markdown(f"**{ef}**" + (f" — {snip}" if snip else ""))

    # 'Go to the doc' toggle — preview is opened on demand
    is_open = r["id"] in st.session_state.preview
    btn = "✕ Close preview" if is_open else "📄 Go to the doc"
    if st.button(btn, key=f"go{r['id']}"):
        if is_open:
            st.session_state.preview.discard(r["id"])
            is_open = False
        else:
            st.session_state.preview.add(r["id"])
            is_open = True

    if is_open:
        left, right = st.columns([1, 1.25])
        with left:
            st.markdown("<span style='font-size:11px;letter-spacing:.1em;color:#7c8983'>"
                        "WHAT THE SYSTEM FOUND</span>", unsafe_allow_html=True)
            st.metric("Reported", fmt(r["reported"], r["unit"]))
            st.metric("Extracted from document",
                      fmt(res["cand"].value, r["unit"]) if res["cand"] else "—")
            st.metric("Discrepancy", "—" if res["disc"] is None else f"{res['disc']*100:.1f}%")
            loc = ("—" if res["loc_status"] == "na"
                   else "not found" if res["loc_status"] == "notfound"
                   else ", ".join(res["loc_found"]) + (" ✓" if res["loc_status"] == "match" else " ✗"))
            st.caption(f"Location (reported {r['location']}): {loc}")
            col = DECISION_COLOR[res["decision"]]
            st.markdown(f"<span style='color:{col};font-weight:600'>{DECISION_LABEL[res['decision']]}"
                        f"</span> — {res['reason']}", unsafe_allow_html=True)
            data = open(ev_path(ef), "rb").read() if os.path.exists(ev_path(ef)) else b""
            st.download_button("⬇ Download original", data, file_name=ef, key=f"dl{r['id']}")
        with right:
            st.markdown("<span style='font-size:11px;letter-spacing:.1em;color:#7c8983'>"
                        "SOURCE DOCUMENT</span>", unsafe_allow_html=True)
            doc_preview(r)


# ============================================================================ #
#  APP
# ============================================================================ #
def render():
    st.markdown("<style>div[data-testid='stMetricValue']{font-size:26px}</style>",
                unsafe_allow_html=True)

    if "results" not in st.session_state:
        st.session_state.results = {}          # id -> verification result (on-demand cache)
    if "preview" not in st.session_state:
        st.session_state.preview = set()       # ids whose 'Go to the doc' preview is open

    nlp = load_nlp()
    engine = "spaCy en_core_web_sm + domain rules" if nlp else "domain rules (rule + lexicon)"

    st.title("KPI Evidence Verifier")
    #st.caption(f"NER engine: **{engine}** · verification runs per record, on demand — "
    #           "nothing is verified until you ask for it.")
    st.write("Press **Verify** on a line-item to run NER over its supporting evidence, "
             "extract the relevant value, and reconcile it against the reported number, "
             "location, and details.")
    st.caption("Handles diverse real-world evidences like PDFs, Word, Excel, certificates, photographs, "
               "signboards, scans across English, Hindi, and Odia. Low-confidence or unreadable "
               "cases are routed to review or escalated rather than guessed.")

    # ---- summary ----
    counts = {k: 0 for k in DECISION_LABEL}
    for r in RECORDS:
        counts[st.session_state.results.get(r["id"], {}).get("decision", "pending")] += 1
    c = st.columns(5)
    c[0].metric("Records", len(RECORDS))
    c[1].metric("Pending", counts["pending"])
    c[2].metric("Verified", counts["verified"])
    c[3].metric("Flagged", counts["flag"])
    c[4].metric("Review / Esc.", counts["review"] + counts["escalate"])

    # ---- documents flagged for cross-check (populates as you verify) ----
    to_check = [r for r in RECORDS
                if st.session_state.results.get(r["id"], {}).get("decision") in ("flag", "review", "escalate")]
    if to_check:
        with st.expander(f"⚠ Documents to cross-check ({len(to_check)})", expanded=False):
            for r in to_check:
                res = st.session_state.results[r["id"]]
                d1, d2, d3 = st.columns([5, 2, 2.2])
                d1.markdown(f"**{r['kpi']}**  \n<span style='color:#7c8983;font-size:12px'>"
                            f"{r['project']} · {r['location']}</span>", unsafe_allow_html=True)
                d2.markdown(badge_html(res["decision"]), unsafe_allow_html=True)
                ef = r.get("evidence_file")
                if ef and os.path.exists(ev_path(ef)):
                    d3.download_button("⬇ " + ef, open(ev_path(ef), "rb").read(),
                                       file_name=ef, key=f"cc{r['id']}")
                else:
                    d3.caption("inline note")

    # ---- filters ----
    f1, f2, f3 = st.columns([3, 1.3, 1.3])
    query = f1.text_input("Filter", placeholder="Filter by KPI, project, or location…",
                          label_visibility="collapsed")
    proj = f2.selectbox("Project", ["All projects"] + sorted({r["project"] for r in RECORDS}),
                        label_visibility="collapsed")
    stat = f3.selectbox("Status", ["All statuses", "pending", "verified", "flag", "review", "escalate"],
                        label_visibility="collapsed")

    def visible(r):
        hay = f"{r['kpi']} {r['project']} {r['location']}".lower()
        cur = st.session_state.results.get(r["id"], {}).get("decision", "pending")
        return ((not query or query.lower() in hay)
                and (proj == "All projects" or r["project"] == proj)
                and (stat == "All statuses" or cur == stat))

    st.divider()

    # ---- records ----
    for r in RECORDS:
        if not visible(r):
            continue
        cur = st.session_state.results.get(r["id"], {}).get("decision", "pending")
        with st.container(border=True):
            h = st.columns([6, 2, 1.4, 1.4])
            h[0].markdown(f"**{r['kpi']}**  \n"
                          f"<span style='color:#7c8983;font-size:12.5px'>{r['project']} · "
                          f"{r['location']} · {r['period']} · <b>{doc_label(r)}</b></span>",
                          unsafe_allow_html=True)
            h[1].markdown(f"<div style='text-align:right'><span style='color:#7c8983;font-size:11px'>"
                          f"Reported</span><br><b style='font-size:15px'>{fmt(r['reported'], r['unit'])}"
                          f"</b></div>", unsafe_allow_html=True)
            h[2].markdown(f"<div style='padding-top:6px'>{badge_html(cur)}</div>", unsafe_allow_html=True)
            if h[3].button("Re-verify" if cur != "pending" else "Verify", key=f"v{r['id']}",
                           type="secondary" if cur != "pending" else "primary"):
                st.session_state.results[r["id"]] = verify(r, nlp)
                cur = st.session_state.results[r["id"]]["decision"]

            res = st.session_state.results.get(r["id"])
            if res:
                col = DECISION_COLOR[res["decision"]]
                # collapsible detail — collapse to keep the list tidy
                with st.expander(f"Verification detail · {DECISION_LABEL[res['decision']]}",
                                 expanded=True):
                    m = st.columns(4)
                    m[0].metric("Reported", fmt(r["reported"], r["unit"]))
                    m[1].metric("Extracted", fmt(res["cand"].value, r["unit"]) if res["cand"] else "—")
                    m[2].metric("Discrepancy", "—" if res["disc"] is None else f"{res['disc']*100:.1f}%")
                    loc = ("—" if res["loc_status"] == "na"
                           else "not found" if res["loc_status"] == "notfound"
                           else ", ".join(res["loc_found"]) + (" ✓" if res["loc_status"] == "match" else " ✗"))
                    m[3].metric(f"Location (rep. {r['location']})", loc)

                    st.progress(int(res["confidence"] * 100),
                                text=f"{int(res['confidence']*100)}% confidence")
                    st.markdown(f"<span style='color:{col};font-weight:600'>Decision:</span> "
                                f"{res['reason']}", unsafe_allow_html=True)
                    st.markdown("<span style='font-size:11px;letter-spacing:.1em;color:#7c8983'>"
                                "ENTITIES RECOGNISED (NER)</span>", unsafe_allow_html=True)
                    st.markdown(chips_html(res["ents"]), unsafe_allow_html=True)
                    st.markdown("<span style='font-size:11px;letter-spacing:.1em;color:#7c8983'>"
                                "SUPPORTING EVIDENCE</span>", unsafe_allow_html=True)
                    st.markdown(highlight_html(r["evidence"], res["ents"], res["cand"]),
                                unsafe_allow_html=True)
                    st.write("")
                    render_cross_check(r, res)

    st.divider()
    st.caption("Proof-of-concept on synthetic evidence. spaCy provides the pretrained NER; a domain "
               "rule + gazetteer layer handles Indian currency, Devanagari numerals, and local place "
               "names. No real beneficiary data is used.")


if __name__ == "__main__":
    st.set_page_config(page_title="KPI Evidence Verifier", page_icon="✓", layout="wide")
    render()
