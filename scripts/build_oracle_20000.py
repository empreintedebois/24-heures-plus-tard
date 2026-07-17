#!/usr/bin/env python3
import csv
import gzip
import json
import math
import re
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

csv.field_size_limit(sys.maxsize)

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "index.html"
OUT = ROOT / "index-20000.html"
REPORT = ROOT / "build-report.json"
CACHE = ROOT / ".imdb-cache"
CACHE.mkdir(exist_ok=True)

URLS = {
    "basics": "https://datasets.imdbws.com/title.basics.tsv.gz",
    "ratings": "https://datasets.imdbws.com/title.ratings.tsv.gz",
    "akas": "https://datasets.imdbws.com/title.akas.tsv.gz",
}
TARGET_TOTAL = 20_000
TARGET_PER_HOUR = 150
MIN_PER_HOUR = 100
RANDOM_RESERVE_PER_BAND = 100
YEARS = (1990, 2025)
TYPES = {"movie", "tvMovie", "video", "short", "tvShort"}
FR = ["zero", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix", "onze", "douze", "treize", "quatorze", "quinze", "seize", "dix sept", "dix huit", "dix neuf", "vingt", "vingt et un", "vingt deux", "vingt trois", "vingt quatre"]
EN = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "twenty one", "twenty two", "twenty three", "twenty four"]
ROM = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX", "XXI", "XXII", "XXIII", "XXIV"]


def download(name):
    path = CACHE / (name + ".tsv.gz")
    if not path.exists() or path.stat().st_size < 1000:
        print("download", URLS[name], flush=True)
        urllib.request.urlretrieve(URLS[name], path)
    return path


def norm(value):
    import unicodedata
    text = "".join(
        char
        for char in unicodedata.normalize(
            "NFD",
            str(value or "").lower().replace("’", "'").replace("–", "-").replace("—", "-"),
        )
        if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"\s+", " ", text).strip()


def values(target):
    return [0, 24] if target == 0 else [target]


def relation(title, target):
    raw = str(title or "")
    normalized = norm(raw)
    if target == 7 and re.search(r"\bse7en\b", raw, re.I):
        return 1, raw, "graphie"

    for match in re.finditer(r"\d+", normalized):
        token = match.group()
        number = int(token)
        if number in values(target):
            if match.end() == len(normalized) and normalized[: match.start()].rstrip(" :-"):
                return 3, raw, "opus"
            return 1, raw, "exact"
        if any(str(value) in token for value in values(target)):
            return 2, raw, "inclus"

    padded = " " + re.sub(r"[^a-z0-9]+", " ", normalized) + " "
    for value in values(target):
        words = [FR[value], EN[value]] + (["une"] if value == 1 else [])
        for word in words:
            if " " + word + " " in padded:
                return 1, raw, "mot"
        roman = ROM[value]
        if roman:
            pattern = (
                r"\b(?:part|episode|chapter)\s+" + re.escape(roman) + r"$"
                if value == 1
                else r"(?:\b(?:part|episode|chapter)\s+|[\s:])" + re.escape(roman) + r"$"
            )
            if re.search(pattern, raw, re.I):
                return 3, raw, "opus"
    return None


def best_relation(names, target):
    best = None
    for title, kind in names:
        found = relation(title, target)
        if not found:
            continue
        item = found[0], found[1], kind, found[2]
        priority = (item[0], 0 if kind == "principal" else 1, len(item[1]))
        if best is None:
            best = item
        else:
            current = (best[0], 0 if best[2] == "principal" else 1, len(best[1]))
            if priority < current:
                best = item
    return best


def score(movie):
    rating = movie["rating"]
    votes = movie["votes"]
    if rating >= 5.5 and votes >= 100:
        quality = 4
    elif rating >= 5.5 and votes >= 10:
        quality = 3
    elif rating >= 4.0 and votes >= 100:
        quality = 2
    else:
        quality = 1
    return quality, rating + math.log10(votes + 10) * 0.55, votes, -len(movie["title"])


def rating_band(movie):
    rating = movie["rating"]
    for band in range(4, 10):
        upper = 10.01 if band == 9 else band + 1
        if band <= rating < upper:
            return band
    return None


def main():
    ratings = {}
    with gzip.open(download("ratings"), "rt", encoding="utf-8", newline="") as source:
        next(source)
        for line in source:
            tconst, rating_text, votes_text = line.rstrip("\n").split("\t")
            rating = float(rating_text)
            votes = int(votes_text)
            if rating >= 4.0 and votes >= 10:
                ratings[tconst] = rating, votes
    print("ratings", len(ratings), flush=True)

    movies = {}
    with gzip.open(download("basics"), "rt", encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source, delimiter="\t"):
            tconst = row["tconst"]
            if tconst not in ratings or row["isAdult"] != "0" or row["titleType"] not in TYPES:
                continue
            year = row["startYear"]
            if not year.isdigit() or not (YEARS[0] <= int(year) <= YEARS[1]):
                continue
            rating, votes = ratings[tconst]
            movies[tconst] = {
                "id": tconst,
                "title": row["primaryTitle"],
                "original": row["originalTitle"],
                "year": int(year),
                "rating": rating,
                "votes": votes,
                "genres": "" if row["genres"] == "\\N" else row["genres"],
                "type": row["titleType"],
                "aliases": [],
            }
    print("eligible basics", len(movies), flush=True)

    with gzip.open(download("akas"), "rt", encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source, delimiter="\t"):
            movie = movies.get(row["titleId"])
            if not movie or len(movie["aliases"]) >= 12:
                continue
            title = row["title"]
            if title == movie["title"] or title == movie["original"]:
                continue
            if any(relation(title, hour) for hour in range(24)):
                region = row["region"]
                language = row["language"]
                title_type = row["types"]
                priority = 0 if region in {"FR", "US", "GB", "CA", "AU"} or "imdbDisplay" in title_type else 1
                movie["aliases"].append((priority, title, region, language))

    for movie in movies.values():
        movie["aliases"].sort(key=lambda item: (item[0], len(item[1])))

    candidates = [[] for _ in range(24)]
    evidences = {}
    all_category_ids = set()
    for movie in movies.values():
        names = [(movie["title"], "principal")]
        if movie["original"] != movie["title"]:
            names.append((movie["original"], "original"))
        names.extend((alias[1], "alternatif") for alias in movie["aliases"])
        for hour in range(24):
            evidence = best_relation(names, hour)
            if evidence:
                candidates[hour].append(movie)
                evidences[(movie["id"], hour)] = evidence
                all_category_ids.add(movie["id"])

    for hour in range(24):
        candidates[hour].sort(key=score, reverse=True)
    counts_all = [len(group) for group in candidates]
    print("candidate counts", counts_all, flush=True)
    if min(counts_all) < MIN_PER_HOUR:
        raise RuntimeError("Couverture insuffisante: " + str(counts_all))

    selected = {}
    for hour in range(24):
        for movie in candidates[hour][:TARGET_PER_HOUR]:
            selected[movie["id"]] = movie

    outside_categories = [movie for movie in movies.values() if movie["id"] not in all_category_ids]
    outside_categories.sort(key=score, reverse=True)
    reserve_counts = {}
    for band in range(4, 10):
        reserve = [movie for movie in outside_categories if rating_band(movie) == band][:RANDOM_RESERVE_PER_BAND]
        if not reserve:
            raise RuntimeError("Aucun film hors catégorie pour la tranche IMDb %d" % band)
        reserve_counts[str(band)] = len(reserve)
        for movie in reserve:
            selected[movie["id"]] = movie

    all_movies = sorted(movies.values(), key=score, reverse=True)
    for movie in all_movies:
        if len(selected) >= TARGET_TOTAL:
            break
        if movie["rating"] >= 5.5:
            selected[movie["id"]] = movie
    for movie in all_movies:
        if len(selected) >= TARGET_TOTAL:
            break
        selected[movie["id"]] = movie

    if len(selected) != TARGET_TOTAL:
        raise RuntimeError("La sélection contient %d films au lieu de %d" % (len(selected), TARGET_TOTAL))

    chosen = list(selected.values())
    chosen.sort(key=lambda movie: (movie["year"], movie["title"], movie["id"]))
    position = {movie["id"]: index for index, movie in enumerate(chosen)}

    title_index = []
    final_category_ids = set()
    final_counts = []
    for hour in range(24):
        entries = []
        for movie in candidates[hour]:
            if movie["id"] not in position:
                continue
            tier, evidence, kind, mode = evidences[(movie["id"], hour)]
            entries.append([position[movie["id"]], tier, evidence, kind, mode])
            final_category_ids.add(movie["id"])
        entries.sort(
            key=lambda entry: (
                entry[1],
                -score(chosen[entry[0]])[0],
                -chosen[entry[0]]["rating"],
                -chosen[entry[0]]["votes"],
            )
        )
        title_index.append(entries)
        final_counts.append(len(entries))

    if min(final_counts) < MIN_PER_HOUR:
        raise RuntimeError("La sélection finale perd la couverture: " + str(final_counts))

    random_index = [index for index, movie in enumerate(chosen) if movie["id"] not in final_category_ids]
    records = [
        [
            movie["id"],
            movie["title"],
            movie["year"],
            int(round(movie["rating"] * 10)),
            movie["votes"],
            movie["genres"],
            0,
            movie["type"],
        ]
        for movie in chosen
    ]

    if len(records) != TARGET_TOTAL:
        raise RuntimeError("Nombre d’enregistrements final invalide")
    if any(not (0 <= entry[0] < len(records)) for group in title_index for entry in group):
        raise RuntimeError("Un index horaire pointe hors de RAW_MOVIES")
    if any(not (0 <= index < len(records)) for index in random_index):
        raise RuntimeError("Un index aléatoire pointe hors de RAW_MOVIES")

    random_band_counts = {str(band): 0 for band in range(4, 10)}
    for index in random_index:
        band = rating_band(chosen[index])
        if band is not None:
            random_band_counts[str(band)] += 1
    if min(random_band_counts.values()) < 1:
        raise RuntimeError("Une tranche aléatoire est vide: " + str(random_band_counts))

    html = BASE.read_text(encoding="utf-8")
    html = re.sub(r'var APP_VERSION = "[^"]+";', 'var APP_VERSION = "11.1.0-imdb-20000";', html, count=1)
    html = re.sub(r'var STORAGE_KEY = "[^"]+";', 'var STORAGE_KEY = "oracle24-github-pages-v11";', html, count=1)

    visible_replacements = {
        "Oracle cinématographique de 5 000 films": "Oracle cinématographique de 20 000 films",
        "5 000 films · IMDb": "20 000 films · IMDb",
        "base autoportante de 5 000 films": "base autoportante de 20 000 films",
        '<strong id="catalogCount">5 000</strong>': '<strong id="catalogCount">20 000</strong>',
        "5 000 films sont physiquement inclus": "20 000 films sont physiquement inclus",
    }
    for old, new in visible_replacements.items():
        html = html.replace(old, new)

    data_pattern = r"var RAW_MOVIES = .*?;\n  var TITLE_INDEX = .*?;"
    data_replacement = (
        "var RAW_MOVIES = "
        + json.dumps(records, ensure_ascii=False, separators=(",", ":"))
        + ";\n  var TITLE_INDEX = "
        + json.dumps(title_index, ensure_ascii=False, separators=(",", ":"))
        + ";\n  var RANDOM_INDEX = "
        + json.dumps(random_index, separators=(",", ":"))
        + ";"
    )
    html, replacements = re.subn(data_pattern, lambda _: data_replacement, html, count=1, flags=re.S)
    if replacements != 1:
        raise RuntimeError("Impossible de remplacer la base intégrée dans index.html")

    html = html.replace(
        '<button data-panel="data">Données</button>',
        '<button data-panel="random">Film au hasard</button>\n  <button data-panel="data">Données</button>',
        1,
    )
    html = html.replace(
        "  function titleRelation(movie, target) {",
        '  function indexedRelation(movie,target,entry) {\n    var evidence=entry[2]||movie.t; var kind=entry[3]||"principal"; var mode=entry[4]||"exact"; var label=kind==="alternatif"?"titre alternatif IMDb":(kind==="original"?"titre original":"titre principal"); var type=mode==="opus"?"Numéro de suite ou d’opus":(mode==="inclus"?"Nombre inclus dans un nombre du titre":(mode==="mot"?"Nombre écrit dans le titre":"Nombre exact dans le titre")); return {tier:entry[1],type:type,reason:"La relation avec "+(target===0?"0/24":target)+" apparaît dans le "+label+" « "+evidence+" ».",source:"title"};\n  }\n\n  function titleRelation(movie, target) {',
        1,
    )
    html = html.replace(
        "relation = titleRelation(movie,target);",
        "relation = indexedRelation(movie,target,entry) || titleRelation(movie,target);",
        1,
    )
    html = html.replace(
        '    } else {\n      renderData();\n    }\n  }',
        '    } else if (type === "random") {\n      renderRandom();\n    } else {\n      renderData();\n    }\n  }',
        1,
    )

    random_js = r'''  function randomBand(movie,band) { var r=movie.q/10; return r>=band && r<(band===9?10.01:band+1); }
  function randomMovieByBand(band) { var eligible=[],i,m; for(i=0;i<RANDOM_INDEX.length;i+=1){m=MOVIES[RANDOM_INDEX[i]];if(m&&randomBand(m,band))eligible.push(m);} if(!eligible.length)return null; return eligible[Math.floor(Math.random()*eligible.length)]; }
  function showRandomMovie(band) { var m=randomMovieByBand(band),body=el("randomResult"); if(!m){body.innerHTML='<p class="data-note">Aucun film disponible dans cette tranche.</p>';return;} body.innerHTML='<div class="film-card"><div class="film-head"><div class="poster">'+esc(m.t)+'</div><div class="film-info"><h2>'+esc(m.t)+'</h2><div class="pills"><span class="pill">'+m.y+'</span><span class="pill">'+esc(ratingText(m))+'</span><span class="pill">'+Number(m.v||0).toLocaleString("fr-FR")+' votes</span><span class="pill">'+esc(m.g||"genre non renseigné")+'</span></div><div class="pitch"><b>Pitch.</b> '+esc(pitchFor(m))+'<span class="note">Film hors catégories horaires</span></div></div></div><div class="main-links"><a class="imdb" href="'+imdbUrl(m)+'" target="_blank" rel="noopener">IMDb · fiche exacte</a><a href="https://www.justwatch.com/fr/recherche?q='+encodeURIComponent(m.t)+'" target="_blank" rel="noopener">JustWatch</a><button id="randomAgain" type="button">Autre film</button></div></div>'; el("randomAgain").onclick=function(){showRandomMovie(band);}; }
  function renderRandom() { var h='<div class="warning"><b>Films hors catégories horaires.</b> Choisissez une tranche de note IMDb. Le bouton 9 couvre de 9,0 à 10.</div><div class="toolbar">',b; el("modalTitle").innerHTML="Film au hasard"; for(b=4;b<=9;b+=1)h+='<button class="ghost" type="button" data-random-band="'+b+'">Note '+b+'</button>'; h+='</div><div id="randomResult"><p class="data-note">Sélectionnez une tranche de note.</p></div>'; el("modalBody").innerHTML=h; el("modalBody").onclick=function(e){var t=e.target||e.srcElement,v=t.getAttribute&&t.getAttribute("data-random-band");if(v!==null&&v!==undefined)showRandomMovie(parseInt(v,10));}; }

'''
    html = html.replace("  function renderData() {", random_js + "  function renderData() {", 1)
    html = html.replace(
        "Période : 1990–2025.",
        "Période : 1990–2025.<br>Minimum garanti : 100 films strictement éligibles par heure.<br>Films hors catégories disponibles dans « Film au hasard ».",
        1,
    )

    OUT.write_text(html, encoding="utf-8")
    report = {
        "source": "IMDb Non-Commercial Datasets",
        "generated_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "total": len(chosen),
        "years": [min(movie["year"] for movie in chosen), max(movie["year"] for movie in chosen)],
        "rating_min": min(movie["rating"] for movie in chosen),
        "hour_counts": final_counts,
        "candidate_counts_before_selection": counts_all,
        "random_outside_categories": len(random_index),
        "random_band_counts": random_band_counts,
        "random_reserve_counts": reserve_counts,
        "types": dict(Counter(movie["type"] for movie in chosen)),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
