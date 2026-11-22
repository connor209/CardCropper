"""
Cardmarket's price guide, offline: made-up product and price files in the
shape Cardmarket publishes, and made-up set lists in the catalogue's shape.

Pins down: an expansion is recognised by its cards, not its number; a later
re-release of the same cards loses to the one added at the set's release;
a close call is marked unsure; same-named Pokémon cards follow card-number
order; same-named Yu-Gi-Oh prints give a range rather than a guess; a
download that is not whole JSON never replaces the copy on disk.
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cardcropper import cardmarket as cm, catalog              # noqa: E402
from cardcropper.catalog import CatalogCard                     # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


SET_NAMES = ["Shuckle", "Burmy", "Wormadam", "Mothim", "Lugia", "Zygarde", "Lugia", "Pikachu"]


def files():
    products, guide = [], []
    pid = 1000

    def add(name, exp, date, trend, holo=None):
        nonlocal pid
        pid += 1
        products.append({"idProduct": pid, "name": f"{name} [Attack]", "idExpansion": exp,
                         "dateAdded": date})
        guide.append({"idProduct": pid, "trend": trend, "avg30": trend + 1, "avg7": trend,
                      "low": trend / 2, "trend-holo": holo})
    for n in SET_NAMES:                       # the real set, added at release
        add(n, 1706, "2016-04-30 00:00:00", 5.0 if n == "Lugia" else 1.0, 12.0)
    for n in SET_NAMES[:-2]:                  # a re-release of most of it, years later
        add(n, 4004, "2021-05-05 00:00:00", 9.0)
    add("Lugia", 2000, "0000-00-00 00:00:00", 99.0)      # another set's Lugia
    for n in ("Ralts", "Kirlia", "Gardevoir"):
        add(n, 2000, "0000-00-00 00:00:00", 1.0)
    # Lugia appears twice in the real set (numbers 78 and 124): products 1005
    # and 1007, the second one dearer.
    guide[[p["idProduct"] for p in products].index(1007)]["trend"] = 30.0
    return ({"version": 1, "createdAt": "2026-11-22T02:43:59+0100", "products": products},
            {"version": 1, "createdAt": "2026-11-22T02:43:59+0100", "priceGuides": guide})


def ygo_files():
    products = [{"idProduct": 1, "name": "Dark Magician", "idExpansion": 1064},
                {"idProduct": 2, "name": "Dark Magician", "idExpansion": 1064},
                {"idProduct": 3, "name": "Blue-Eyes White Dragon", "idExpansion": 1064}]
    guide = [{"idProduct": 1, "trend": 10.51}, {"idProduct": 2, "trend": 435.9},
             {"idProduct": 3, "trend": 900.0}]
    return ({"products": products}, {"createdAt": "2026-11-22", "priceGuides": guide})


def fake_get(url, headers):
    if "pokemontcg" in url:
        nums = ["10", "11", "12", "13", "78", "80", "124", "50"]
        return {"data": [{"name": n, "number": num} for n, num in zip(SET_NAMES, nums)]}
    return {"data": [{"name": "Dark Magician", "card_sets": [
        {"set_name": "Legend of Blue Eyes White Dragon", "set_code": "LOB-EN005"},
        {"set_name": "Legend of Blue Eyes White Dragon", "set_code": "LOB-E005"}]},
        {"name": "Blue-Eyes White Dragon", "card_sets": [
            {"set_name": "Legend of Blue Eyes White Dragon", "set_code": "LOB-EN001"}]}]}


def main():
    work = tempfile.mkdtemp(prefix="cc-cardmarket-")
    old = {k: os.environ.get(k) for k in ("XDG_CONFIG_HOME", "APPDATA")}
    os.environ["XDG_CONFIG_HOME"] = os.environ["APPDATA"] = work
    catalog.set_cache_dir(os.path.join(work, "cache"))
    try:
        served = {}
        for game, (prod, guide) in (("pokemon", files()), ("yugioh", ygo_files())):
            p_url, g_url = cm._urls(game)
            served[p_url], served[g_url] = json.dumps(prod).encode(), json.dumps(guide).encode()
        check(cm.refresh(fetch=served.__getitem__) == {}, "download failed")
        check(cm.age("pokemon") is not None and cm.refresh(fetch=served.__getitem__) == {},
              "a fresh download should not be fetched again")

        lugia = CatalogCard("pokemon", "xy10-78", "Lugia", "78", "Fates Collide", "xy10", 124,
                            "2016-05-02", "Rare Holo", "", "", ["Holo"], {}, "")
        found = cm.price_for(lugia, get=fake_get)
        check(found and found["prices"][0] == ("Trend", 5.0),
              f"the first Lugia (number 78) should be the first product: {found}")
        check(found["approx"] and found["sure"], f"sure set, guessed version: {found}")
        check(found["variant"] and found["variant"][0][1] == 12.0, "reverse holo price missing")
        saved = cm._load_map("pokemon")
        check(saved["xy10"]["expansion"] == 1706,
              f"the re-release (added 2021) beat the set added at release: {saved}")
        second = CatalogCard("pokemon", "xy10-124", "Lugia", "124", "Fates Collide", "xy10",
                             124, "2016-05-02", "", "", "", [], {}, "")
        check(cm.price_for(second, get=fake_get)["prices"][0] == ("Trend", 30.0),
              "the second Lugia (number 124) should be the second product")
        check("Cardmarket (2026-11-22)" in cm.describe(found), cm.describe(found))

        dm = CatalogCard("yugioh", "x", "Dark Magician", "LOB-EN005",
                         "Legend of Blue Eyes White Dragon", "LOB", None, "", "", "", "", [],
                         {}, "")
        got = cm.price_for(dm, get=fake_get)
        check(got["range"] == (10.51, 435.9) and got["versions"] == 2,
              f"same-named Yu-Gi-Oh prints should give a range: {got}")
        check("2 versions" in cm.describe(got), cm.describe(got))
        bewd = CatalogCard("yugioh", "y", "Blue-Eyes White Dragon", "LOB-EN001",
                           "Legend of Blue Eyes White Dragon", "LOB", None, "", "", "", "", [],
                           {}, "")
        check(cm.price_for(bewd, get=fake_get)["prices"][0] == ("Trend", 900.0),
              "a single print should be priced exactly")

        def broken(url):
            return b"<html>maintenance</html>"
        before = os.path.getmtime(cm._paths("pokemon")[0])
        check("pokemon" in cm.refresh(["pokemon"], force=True, fetch=broken),
              "a broken download should be reported")
        check(os.path.getmtime(cm._paths("pokemon")[0]) == before,
              "a broken download replaced the good copy")
    finally:
        catalog.set_cache_dir(None)
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(work, ignore_errors=True)
    print("cardmarket smoke: ok")
    return 0


if __name__ == "__main__":
    main()
