"""
Va chercher les événements des trois calendriers publics Actes if
et écrit data/events.json — lu ensuite par la page.

Ne nécessite aucune clé Google : les calendriers sont publics,
ce script lit directement leur export iCal public (.ics).
Exécuté automatiquement par .github/workflows/update-agenda.yml
"""
import datetime
import json
import os
import re
import html as htmllib
import urllib.request
from urllib.parse import quote

import icalendar
import recurring_ical_events
from PIL import Image, ImageDraw, ImageFont

CALENDARS = {
    "vie-asso":     "ju1pnmknkotcdp73h0qj04r2d8@group.calendar.google.com",
    "partenaires":  "n49s2qcfq130m5sbceo0if4di8@group.calendar.google.com",
    "subventions":  "6o2s2gk2d7npkdfeg6bjuvdts8@group.calendar.google.com",
    "membres":      "b67bcec42779af9a91678c0b676998eb530b1932f0abff8bf3e80907e19e6f84@group.calendar.google.com",
}

FENETRE_JOURS = 365  # horizon : aujourd'hui -> +12 mois


def ics_url(calendar_id):
    return f"https://calendar.google.com/calendar/ical/{quote(calendar_id)}/public/basic.ics"


def strip_tags(text):
    """Retire les balises HTML. Google Agenda stocke les descriptions en HTML
    riche (<div>...</div>, <br>) plutôt qu'avec de vrais retours à la ligne :
    on convertit d'abord les balises de bloc en \\n pour ne pas perdre la
    structure en lignes, sinon 'Lieu :' et 'Inscription :' ne savent plus où
    s'arrêter et avalent tout le reste du texte."""
    if not text:
        return ""
    text = str(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(div|p|li|tr)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return htmllib.unescape(text)


def collapse_whitespace(text):
    text = text.replace("\ufffc", " ")  # caractère invisible laissé par un lien Google Agenda retiré
    return re.sub(r"\s+", " ", text).strip()


def extract_registration_url(text):
    """Cherche une ligne du type 'Inscription : https://...' dans la description
    et la retire du texte affiché. La capture se limite aux caractères valides
    d'une URL, pour ne pas avaler ce qui suit si Google Agenda colle un caractère
    invisible juste après le lien (ça arrive avec les liens insérés en riche texte)."""
    if not text:
        return text, None
    match = re.search(
        r"inscription\s*:?\s*(https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+)",
        text, re.IGNORECASE
    )
    if not match:
        return text, None
    url = match.group(1).rstrip(").,;")
    cleaned = (text[:match.start()] + text[match.end():]).strip()
    return cleaned, url


def extract_info_url(text):
    """Cherche une ligne du type 'Infos : https://...' dans la description
    et la retire du texte affiché — même principe qu'Inscription."""
    if not text:
        return text, None
    match = re.search(
        r"\binfos?\s*:?\s*(https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+)",
        text, re.IGNORECASE
    )
    if not match:
        return text, None
    url = match.group(1).rstrip(").,;")
    cleaned = (text[:match.start()] + text[match.end():]).strip()
    return cleaned, url


def extract_location_line(text):
    """Cherche une ligne du type 'Lieu : ...' dans la description
    et la retire du texte affiché. S'arrête à la fin de ligne, ou avant
    un éventuel 'Inscription :' si tout est resté sur une seule ligne.
    Plafonnée à 100 caractères par sécurité : un lieu ne s'étale pas sur
    tout le reste de la description, même si une frontière a été manquée."""
    if not text:
        return text, None
    match = re.search(r"lieu\s*:\s*(.{1,100}?)(?=\n|$|\s*inscription\s*:)", text, re.IGNORECASE)
    if not match:
        return text, None
    value = match.group(1).strip().rstrip(".,;")
    cleaned = (text[:match.start()] + text[match.end():]).strip()
    return cleaned, value


def clean_desc(text, limit=5000):
    """Le texte complet est envoyé à la page (qui gère elle-même l'affichage
    condensé et le 'Voir plus'). Cette limite très large n'est qu'un garde-fou
    contre un cas extrême, pas une troncature d'affichage."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(",.;: ") + "…"


def fetch_calendar(calendar_id):
    req = urllib.request.Request(ics_url(calendar_id), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


# ---------------------------------------------------------------
# Image de prévisualisation (data/preview.png) — à insérer telle
# quelle dans une newsletter (Brevo, etc.) : les e-mails ne peuvent
# pas afficher la page elle-même, mais une image, oui. Régénérée
# automatiquement à chaque synchronisation, toujours à jour.
# Fond lavande (#D2C7FF) ; corail réservé aux surlignages (catégorie,
# horaire, lieu, bandeau final) ; titres en marine ; coins arrondis ;
# titre d'en-tête dans la vraie police d'affichage de la page
# (Archivo Black, téléchargée à la volée, avec repli si indisponible).
# ---------------------------------------------------------------
LAVANDE = (210, 199, 255)
NAVY = (17, 11, 169)
CORAL = (255, 0, 0)
WHITE = (255, 255, 255)

FONT_DIR = "/usr/share/fonts/truetype/liberation/"
MONTHS_FR = ["janv.", "févr.", "mars", "avr.", "mai", "juin",
             "juil.", "août", "sept.", "oct.", "nov.", "déc."]
CAT_LABEL = {
    "vie-asso": "VIE ASSO",
    "partenaires": "PARTENAIRES",
    "subventions": "SUBVENTIONS",
    "membres": "ÉVÉNEMENTS MEMBRES",
}

ARCHIVO_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/archivoblack/ArchivoBlack-Regular.ttf"
ARCHIVO_LOCAL = "ArchivoBlack-Regular.ttf"


def _get_archivo(size):
    """Police d'affichage réelle de la page (Archivo Black), récupérée à la
    volée ; bascule sur une police de secours si le téléchargement échoue,
    pour ne jamais faire échouer la synchronisation à cause d'une police."""
    try:
        if not os.path.exists(ARCHIVO_LOCAL):
            urllib.request.urlretrieve(ARCHIVO_URL, ARCHIVO_LOCAL)
        return ImageFont.truetype(ARCHIVO_LOCAL, size)
    except Exception:
        return ImageFont.truetype(FONT_DIR + "LiberationSans-Bold.ttf", size)


def _font(name, size):
    try:
        return ImageFont.truetype(FONT_DIR + name, size)
    except Exception:
        return ImageFont.load_default()


def _truncate(text, max_chars):
    return text if len(text) <= max_chars else text[:max_chars - 1].rstrip() + "…"


def _draw_pin(d, x, y, color, size=13):
    r = size * 0.32
    cx, cy = x + r, y + r
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    d.polygon([(cx - r * 0.75, cy + r * 0.4), (cx + r * 0.75, cy + r * 0.4), (cx, cy + size * 0.85)], fill=color)
    return x + size + 6


def _round_corners(img, radius=22):
    img = img.convert("RGBA")
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=radius, fill=255)
    img.putalpha(mask)
    return img


def generate_preview_image(events, out_path):
    """Fond lavande, surlignages corail (catégorie/horaire/lieu/bandeau),
    titres en marine, coins arrondis, titre d'en-tête en Archivo Black."""
    upcoming = events[:3]
    if not upcoming:
        return

    W = 640
    pad_x = 36

    f_header = _get_archivo(24)
    f_cat = _font("LiberationSans-Bold.ttf", 13)
    f_date = _font("LiberationSans-Bold.ttf", 15)
    f_title = _font("LiberationSans-Bold.ttf", 25)
    f_meta = _font("LiberationSans-Regular.ttf", 14)
    f_cta = _font("LiberationSans-Bold.ttf", 18)

    row_gap = 28
    row_heights = [24 + 8 + 32 + (22 if ev.get("location") else 0) + row_gap for ev in upcoming]
    top_pad = 34
    header_h = 34 + 20
    bar_h = 58
    H = top_pad + header_h + sum(row_heights) + bar_h + 10

    img = Image.new("RGB", (W, H), LAVANDE)
    d = ImageDraw.Draw(img)

    d.text((pad_x, top_pad), "AGENDA PARTAGÉ DU RÉSEAU ACTES IF", font=f_header, fill=CORAL)

    y = top_pad + header_h
    for idx, (ev, row_h) in enumerate(zip(upcoming, row_heights)):
        dt = (datetime.datetime.fromisoformat(ev["start"][:19]) if "T" in ev["start"]
              else datetime.datetime.strptime(ev["start"], "%Y-%m-%d"))

        label = CAT_LABEL.get(ev["calendarKey"], "")
        cat_bbox = d.textbbox((0, 0), label, font=f_cat)
        cat_w = cat_bbox[2] - cat_bbox[0] + 16
        d.rounded_rectangle([pad_x, y, pad_x + cat_w, y + 22], radius=5, fill=CORAL)
        d.text((pad_x + 8, y + 4), label, font=f_cat, fill=WHITE)

        date_txt = f"{dt.day} {MONTHS_FR[dt.month - 1]}"
        if not ev.get("allDay") and "T" in ev["start"]:
            date_txt += f" · {dt.hour:02d}h{dt.minute:02d}"
        d.text((pad_x + cat_w + 10, y + 3), date_txt, font=f_date, fill=CORAL)

        y += 34
        d.text((pad_x, y), _truncate(ev["title"], 42), font=f_title, fill=NAVY)
        y += 32

        if ev.get("location"):
            next_x = _draw_pin(d, pad_x, y + 3, CORAL, size=13)
            d.text((next_x, y), _truncate(ev["location"], 46), font=f_meta, fill=CORAL)
            y += 22

        y += row_gap
        if idx < len(upcoming) - 1:
            d.line([(pad_x, y - 16), (W - pad_x, y - 16)], fill=CORAL, width=1)

    bar_y = H - bar_h
    d.rectangle([0, bar_y, W, H], fill=CORAL)
    cta = "Voir tout l'agenda du réseau →"
    bbox = d.textbbox((0, 0), cta, font=f_cta)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((W - tw) / 2, bar_y + (bar_h - th) / 2 - bbox[1]), cta, font=f_cta, fill=WHITE)

    img = _round_corners(img, radius=22)
    img.save(out_path)


def main():
    start_window = datetime.date.today()
    end_window = start_window + datetime.timedelta(days=FENETRE_JOURS)

    all_events = []
    for key, calendar_id in CALENDARS.items():
        raw = fetch_calendar(calendar_id)
        cal = icalendar.Calendar.from_ical(raw)
        occurrences = recurring_ical_events.of(cal).between(start_window, end_window)

        for ev in occurrences:
            summary = str(ev.get("SUMMARY", "(Sans titre)")).strip()
            dtstart = ev["DTSTART"].dt
            dtend = ev["DTEND"].dt if ev.get("DTEND") else dtstart
            is_all_day = not isinstance(dtstart, datetime.datetime)
            uid = str(ev.get("UID", ""))
            location = str(ev.get("LOCATION", "") or "").replace("\\,", ",").replace("\\;", ";")
            raw_description = strip_tags(ev.get("DESCRIPTION", ""))
            desc1, registration_url = extract_registration_url(raw_description)
            desc1b, info_url = extract_info_url(desc1)
            desc2, lieu_from_desc = extract_location_line(desc1b)
            if not location and lieu_from_desc:
                location = lieu_from_desc
            description = clean_desc(collapse_whitespace(desc2))

            all_events.append({
                "id": f"{uid}-{dtstart.isoformat()}",
                "title": summary,
                "start": dtstart.isoformat(),
                "end": dtend.isoformat(),
                "allDay": is_all_day,
                "location": location,
                "calendarKey": key,
                "description": description,
                "registrationUrl": registration_url,
                "infoUrl": info_url,
            })

    all_events.sort(key=lambda e: e["start"])

    os.makedirs("data", exist_ok=True)
    with open("data/events.json", "w", encoding="utf-8") as f:
        json.dump(all_events, f, ensure_ascii=False, indent=2)

    print(f"{len(all_events)} événements écrits dans data/events.json")

    today = datetime.date.today().isoformat()
    upcoming_confirmed = [
        e for e in all_events
        if e["end"][:10] >= today and not re.search(r"\boption\b", e["title"], re.IGNORECASE)
    ]
    generate_preview_image(upcoming_confirmed, "data/preview.png")
    print("data/preview.png généré")


if __name__ == "__main__":
    main()
