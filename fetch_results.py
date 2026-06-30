#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_results.py
================
Pulls finished 2026 World Cup results from football-data.org (free tier covers
the World Cup) and writes a small `results.json` that the BWAS dashboards read.

The dashboards match results to fixtures BY TEAM NAME (not match number), so
this script just emits every finished match as {teamA, teamB, scores, pens}.
Team names are normalised to the BWAS spellings; any team it can't map is
logged loudly so the SYNONYMS table can be extended.

Auth: set the free API token in the env var FOOTBALL_DATA_TOKEN
      (in GitHub Actions, store it as the repo secret FOOTBALL_DATA_TOKEN).

Usage:  FOOTBALL_DATA_TOKEN=xxxx python fetch_results.py [--out results.json]
No third-party deps (stdlib urllib only).
"""
import argparse
import json
import os
import sys
import unicodedata
import urllib.request
from datetime import datetime, timezone

API = "https://api.football-data.org/v4/competitions/WC/matches"

# The 48 BWAS team spellings (source of truth for names in results.json)
BWAS_TEAMS = [
    "Mexico", "South Africa", "Korea Republic", "Czech Republic", "Canada",
    "Bosnia and Herzegovina", "Qatar", "Switzerland", "Brazil", "Morocco",
    "Haiti", "Scotland", "United States", "Paraguay", "Australia", "Turkey",
    "Germany", "Curaçao", "Ivory Coast", "Ecuador", "Netherlands", "Japan",
    "Sweden", "Tunisia", "Belgium", "Egypt", "Iran", "New Zealand", "Spain",
    "Cape Verde", "Saudi Arabia", "Uruguay", "France", "Senegal", "Iraq",
    "Norway", "Argentina", "Algeria", "Austria", "Jordan", "Portugal",
    "DR Congo", "Uzbekistan", "Colombia", "England", "Croatia", "Ghana",
    "Panama",
]


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.lower() if c.isalnum())


# normalised BWAS name -> canonical BWAS name
_BY_NORM = {norm(t): t for t in BWAS_TEAMS}

# football-data spellings that differ from BWAS, keyed by normalised API name
SYNONYMS = {
    norm("South Korea"): "Korea Republic",
    norm("Korea Republic"): "Korea Republic",
    norm("Czechia"): "Czech Republic",
    norm("Türkiye"): "Turkey",
    norm("Turkiye"): "Turkey",
    norm("Côte d'Ivoire"): "Ivory Coast",
    norm("Cote d'Ivoire"): "Ivory Coast",
    norm("Bosnia-Herzegovina"): "Bosnia and Herzegovina",
    norm("Bosnia & Herzegovina"): "Bosnia and Herzegovina",
    norm("United States"): "United States",
    norm("USA"): "United States",
    norm("Cabo Verde"): "Cape Verde",
    norm("Cape Verde Islands"): "Cape Verde",
    norm("DR Congo"): "DR Congo",
    norm("Congo DR"): "DR Congo",
    norm("Democratic Republic of Congo"): "DR Congo",
    norm("Democratic Republic of the Congo"): "DR Congo",
    norm("IR Iran"): "Iran",
    norm("Iran"): "Iran",
    norm("Republic of Ireland"): "Ireland",   # (not in WC, harmless)
}


def map_team(api_name, unmatched):
    c = norm(api_name)
    if c in SYNONYMS:
        return SYNONYMS[c]
    if c in _BY_NORM:
        return _BY_NORM[c]
    unmatched.add(api_name)
    return api_name  # keep raw; will simply not match a fixture until mapped


def fetch(token):
    req = urllib.request.Request(API, headers={"X-Auth-Token": token})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results.json")
    ap.add_argument("--token", default=os.environ.get("FOOTBALL_DATA_TOKEN", ""))
    ap.add_argument("--from-file", help="parse a saved API JSON instead of calling the API (for testing)")
    args = ap.parse_args()

    if args.from_file:
        data = json.load(open(args.from_file, encoding="utf-8"))
    else:
        if not args.token:
            sys.exit("ERROR: set FOOTBALL_DATA_TOKEN (free key from football-data.org).")
        data = fetch(args.token)

    matches_in = data.get("matches", [])
    unmatched = set()
    out = []
    for m in matches_in:
        status = m.get("status")
        live = status in ("IN_PLAY", "PAUSED")
        if status != "FINISHED" and not live:
            continue
        score = m.get("score", {}) or {}
        dur = score.get("duration")
        ft = score.get("fullTime", {}) or {}
        pens = score.get("penalties", {}) or {}
        pa, pb = pens.get("home"), pens.get("away")
        if live:
            # in-play: use the current running score (no pens / no ET folding yet)
            ga, gb = ft.get("home"), ft.get("away")
            pa = pb = None
        elif dur == "PENALTY_SHOOTOUT":
            # football-data folds the shootout into fullTime; the real result is
            # the score after extra time = regularTime + extraTime, pens separate.
            rt = score.get("regularTime") or {}
            et = score.get("extraTime") or {}
            if rt.get("home") is not None:
                ga = (rt.get("home") or 0) + (et.get("home") or 0)
                gb = (rt.get("away") or 0) + (et.get("away") or 0)
            else:  # fallback: strip the shootout back out of fullTime
                ga = (ft.get("home") or 0) - (pa or 0)
                gb = (ft.get("away") or 0) - (pb or 0)
        else:
            ga, gb = ft.get("home"), ft.get("away")
            pa = pb = None   # only knockout shootouts carry a penalties score
        if ga is None or gb is None:
            continue
        out.append({
            "a": map_team((m.get("homeTeam") or {}).get("name", ""), unmatched),
            "b": map_team((m.get("awayTeam") or {}).get("name", ""), unmatched),
            "ga": ga, "gb": gb, "pa": pa, "pb": pb,
            "stage": m.get("stage"), "date": m.get("utcDate"),
            "live": live,
        })

    n_live = sum(1 for x in out if x["live"])
    payload = {
        "source": "football-data.org (FIFA World Cup)",
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(out),
        "live": n_live,
        "matches": out,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=0)

    print("Wrote %s with %d matches (%d finished, %d in play)."
          % (args.out, len(out), len(out) - n_live, n_live))
    if unmatched:
        print("WARNING: %d team name(s) not mapped to BWAS spellings - add to "
              "SYNONYMS: %s" % (len(unmatched), sorted(unmatched)))


if __name__ == "__main__":
    main()
