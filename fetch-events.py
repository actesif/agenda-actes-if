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
# ---------------------------------------------------------------
NAVY = (17, 11, 169)
NAVY_DEEP = (12, 8, 122)
CORAL = (255, 0, 0)
WHITE = (255, 255, 255)
MUTED = (176, 172, 232)

FONT_DIR = "/usr/share/fonts/truetype/liberation/"
CAT_DOT_COLOR = {
    "vie-asso": (255, 255, 255),
    "partenaires": (198, 168, 255),
    "subventions": (255, 176, 214),
    "membres": (255, 140, 120),
}
MONTHS_FR = ["JANV.", "FÉVR.", "MARS", "AVR.", "MAI", "JUIN",
             "JUIL.", "AOÛT", "SEPT.", "OCT.", "NOV.", "DÉC."]


def _font(name, size):
    try:
        return ImageFont.truetype(FONT_DIR + name, size)
    except Exception:
        return ImageFont.load_default()


def _truncate(text, max_chars):
    return text if len(text) <= max_chars else text[:max_chars - 1].rstrip() + "…"


def generate_preview_image(events, out_path):
    """events : liste déjà triée, événements confirmés et à venir uniquement."""
    upcoming = events[:3]
    if not upcoming:
        return  # rien à montrer, pas d'image générée

    W, H = 600, 460
    img = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(img)

    f_label = _font("LiberationSans-Bold.ttf", 20)
    f_brand = _font("LiberationSans-Bold.ttf", 34)
    f_date = _font("LiberationSans-Bold.ttf", 26)
    f_title = _font("LiberationSans-Bold.ttf", 24)
    f_meta = _font("LiberationSans-Regular.ttf", 18)
    f_cta = _font("LiberationSans-Bold.ttf", 20)

    d.rectangle([0, 0, W, 84], fill=NAVY_DEEP)
    d.text((28, 18), "AGENDA DU RÉSEAU", font=f_label, fill=CORAL)
    d.text((28, 44), "ACTES IF", font=f_brand, fill=WHITE)

    y = 104
    row_h = 100
    for ev in upcoming:
        dt = (datetime.datetime.fromisoformat(ev["start"][:19]) if "T" in ev["start"]
              else datetime.datetime.strptime(ev["start"], "%Y-%m-%d"))

        d.ellipse([28, y + 8, 40, y + 20], fill=CAT_DOT_COLOR.get(ev["calendarKey"], WHITE))
        d.text((56, y), f"{dt.day} {MONTHS_FR[dt.month - 1]}", font=f_date, fill=WHITE)
        d.text((56, y + 36), _truncate(ev["title"], 34), font=f_title, fill=WHITE)

        meta_parts = []
        if not ev.get("allDay") and "T" in ev["start"]:
            meta_parts.append(f"{dt.hour:02d}h{dt.minute:02d}")
        if ev.get("location"):
            meta_parts.append(_truncate(ev["location"], 24))
        if meta_parts:
            d.text((56, y + 66), "  ·  ".join(meta_parts), font=f_meta, fill=MUTED)

        y += row_h
        if ev != upcoming[-1]:
            d.line([(28, y - 16), (W - 28, y - 16)], fill=NAVY_DEEP, width=1)

    d.rectangle([0, H - 56, W, H], fill=CORAL)
    cta = "VOIR TOUT L'AGENDA DU RÉSEAU →"
    bbox = d.textbbox((0, 0), cta, font=f_cta)
    d.text(((W - (bbox[2] - bbox[0])) / 2, H - 56 + 16), cta, font=f_cta, fill=WHITE)

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
            desc2, lieu_from_desc = extract_location_line(desc1)
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
