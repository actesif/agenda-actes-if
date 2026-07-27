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

CALENDARS = {
    "vie-asso":     "ju1pnmknkotcdp73h0qj04r2d8@group.calendar.google.com",
    "partenaires":  "n49s2qcfq130m5sbceo0if4di8@group.calendar.google.com",
    "subventions":  "6o2s2gk2d7npkdfeg6bjuvdts8@group.calendar.google.com",
}

FENETRE_JOURS = 365  # horizon : aujourd'hui -> +12 mois


def ics_url(calendar_id):
    return f"https://calendar.google.com/calendar/ical/{quote(calendar_id)}/public/basic.ics"


def strip_tags(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(text))
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
    un éventuel 'Inscription :' si tout est resté sur une seule ligne."""
    if not text:
        return text, None
    match = re.search(r"lieu\s*:\s*(.+?)(?=\n|$|\s*inscription\s*:)", text, re.IGNORECASE)
    if not match:
        return text, None
    value = match.group(1).strip().rstrip(".,;")
    cleaned = (text[:match.start()] + text[match.end():]).strip()
    return cleaned, value


def clean_desc(text, limit=170):
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


if __name__ == "__main__":
    main()
