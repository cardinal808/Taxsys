#!/usr/bin/env python3
"""Haalt het vervoerschema op uit het Taxsys ouderportaal en geeft JSON terug
voor een Home Assistant command_line sensor.

Gebruik:
  python3 /config/taxsys/taxsys.py

Gegevens komen uit /config/secrets.yaml:
  taxsys_username: ...
  taxsys_password: ...
  taxsys_company: ...   (bedrijfscode uit de login-URL, bijv. G-XXX)
  taxsys_klantnr: ...   (klantNr uit de URL van de deelnemerpagina)

--company en --klantnr op de commandoregel gaan voor op secrets.yaml.

Alleen standaardbibliotheek (plus PyYAML als die er is), zodat het zonder
extra installatie in de Home Assistant-container draait.
"""
import argparse
import hashlib
import http.cookiejar
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime
from html.parser import HTMLParser

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Amsterdam")
except Exception:  # pragma: no cover
    TZ = None

BASE = "https://account.taxsys.nl/taxsys/"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


# ---------------------------------------------------------------- secrets
def _read_secrets_lines(path):
    """Eenvoudige fallback: alleen de taxsys_*-regels lezen."""
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^\s*(taxsys_[A-Za-z0-9_]+)\s*:\s?(.*?)\s*$", line)
            if m:
                val = m.group(2)
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
                    val = val[1:-1]
                out[m.group(1)] = val
    return out


def read_secrets(path):
    try:
        import yaml  # aanwezig in de Home Assistant-container
        class _L(yaml.SafeLoader):
            pass
        # !include, !env_var e.d. negeren
        _L.add_multi_constructor("!", lambda loader, suffix, node: None)
        with open(path, encoding="utf-8") as fh:
            data = yaml.load(fh, Loader=_L) or {}
        if data.get("taxsys_username") and data.get("taxsys_password"):
            return data
    except Exception:
        pass
    return _read_secrets_lines(path)


# ---------------------------------------------------------------- parser
class ScheduleParser(HTMLParser):
    """Leest de tabel <table id="tblvvs"> (Vervoerschema)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_table = False
        self.table_depth = 0
        self.row = None
        self.cell = None
        self.rows = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            if self.in_table:
                self.table_depth += 1
            elif a.get("id") == "tblvvs":
                self.in_table = True
                self.table_depth = 0
            return
        if not self.in_table:
            return
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = {"tag": tag, "id": a.get("id", ""),
                         "class": a.get("class", ""),
                         "colspan": a.get("colspan"), "text": []}
        elif tag in ("br", "div") and self.cell is not None:
            self.cell["text"].append("\n")

    def handle_endtag(self, tag):
        if not self.in_table:
            return
        if tag == "table":
            if self.table_depth:
                self.table_depth -= 1
            else:
                self.in_table = False
        elif tag in ("td", "th") and self.cell is not None:
            raw = "".join(self.cell["text"])
            lines = [re.sub(r"\s+", " ", l).strip() for l in raw.split("\n")]
            self.cell["text"] = "\n".join(l for l in lines if l)
            self.row.append(self.cell)
            self.cell = None
        elif tag == "div" and self.cell is not None:
            self.cell["text"].append("\n")
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"].append(data)


def to_iso(d, hhmm):
    if not d or not re.match(r"^\d{1,2}:\d{2}$", hhmm or ""):
        return None
    h, m = map(int, hhmm.split(":"))
    dt = datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)
    return dt.isoformat()


def parse_schedule(html):
    p = ScheduleParser()
    p.feed(html)
    rides = []
    for row in p.rows:
        if any(c["tag"] == "th" for c in row):
            continue
        cells = [c for c in row if c["id"] == "middle" and "hide" not in c["class"]]
        if len(cells) >= 8:
            t = [c["text"] for c in cells]
            m = re.search(r"(\d{2})-(\d{2})-(\d{2,4})", t[0])
            if not m:
                continue
            y = int(m.group(3))
            d = date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)))
            otijd = t[1].split("\n")[0].strip()
            atijd = t[3].split("\n")[0].strip()
            rides.append({
                "datum": d.isoformat(),
                "dag": t[0].split("\n")[0].strip().capitalize(),
                "deel": "ochtend" if otijd < "12:00" else "middag",
                "o_tijd": otijd,
                "a_tijd": atijd,
                "ophalen": to_iso(d, otijd),
                "aankomst": to_iso(d, atijd),
                "van": t[2].replace("\n", ", "),
                "naar": t[4].replace("\n", ", "),
                "status": t[5],
                "route": t[6],
                "chauffeur": t[7],
            })
        elif len(row) == 1 and rides and "Locatie voertuig" in row[0]["text"]:
            txt = row[0]["text"]
            loc = re.search(r"Locatie voertuig\s*:\s*(.+)", txt)
            tm = re.search(r"Tijdstip meting\s*:\s*(\d{1,2}:\d{2})", txt)
            rides[-1]["live_locatie"] = loc.group(1).strip() if loc else None
            rides[-1]["live_tijd"] = tm.group(1) if tm else None
    return rides


def pick(rides, deel, today):
    for r in rides:
        if r["deel"] == deel and r["datum"] >= today:
            return r
    return None


# ---------------------------------------------------------------- http
def fetch(company, klantnr, username, password):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", UA), ("Accept-Language", "nl-NL,nl;q=0.9")]

    login_url = BASE + "?" + urllib.parse.urlencode({"page": "login", "company": company})
    opener.open(login_url, timeout=20).read()  # sessiecookie ophalen

    form = {
        "submit": "true",
        "username": username,
        "password": password,
        "passwordENC": hashlib.sha512(("BSA" + password).encode("utf-8")).hexdigest(),
        "meta_sw": "1280",
        "meta_sh": "800",
    }
    req = urllib.request.Request(login_url, data=urllib.parse.urlencode(form).encode(),
                                 headers={"Referer": login_url, "Origin": "https://account.taxsys.nl"})
    opener.open(req, timeout=20).read()

    sched_url = BASE + "?" + urllib.parse.urlencode(
        {"page": "deelnemerMutatie", "klantNr": klantnr, "company": company})
    with opener.open(sched_url, timeout=20) as resp:
        return resp.read().decode(resp.headers.get_content_charset() or "utf-8", "replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--company")
    ap.add_argument("--klantnr")
    ap.add_argument("--secrets", default="/config/secrets.yaml")
    ap.add_argument("--html", help="lokaal HTML-bestand parsen (test)")
    args = ap.parse_args()

    now = datetime.now(TZ)
    out = {"status": "ok", "bijgewerkt": now.isoformat(timespec="seconds"),
           "ochtend": None, "middag": None, "ritten": [], "fout": None}
    try:
        if args.html:
            with open(args.html, encoding="utf-8") as fh:
                html = fh.read()
        else:
            s = read_secrets(args.secrets)
            user, pw = s.get("taxsys_username"), s.get("taxsys_password")
            company = args.company or s.get("taxsys_company")
            klantnr = args.klantnr or s.get("taxsys_klantnr")
            if not user or not pw:
                raise RuntimeError("taxsys_username/taxsys_password ontbreken in secrets.yaml")
            if not company or not klantnr:
                raise RuntimeError("taxsys_company/taxsys_klantnr ontbreken (secrets.yaml of --company/--klantnr)")
            html = fetch(str(company), str(klantnr), str(user), str(pw))

        if 'id="tblvvs"' not in html:
            if 'name="password"' in html:
                raise RuntimeError("inloggen mislukt (controleer gebruikersnaam/wachtwoord)")
            raise RuntimeError("vervoerschema niet gevonden op de pagina")

        rides = parse_schedule(html)
        today = now.date().isoformat()
        out["ritten"] = rides
        out["ochtend"] = pick(rides, "ochtend", today)
        out["middag"] = pick(rides, "middag", today)
        if not rides:
            out["status"] = "geen ritten"
    except Exception as exc:  # altijd geldige JSON teruggeven
        out["status"] = "fout"
        out["fout"] = str(exc)[:200]
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
