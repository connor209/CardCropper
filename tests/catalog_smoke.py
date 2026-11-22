"""
The card catalogue lookup (cardcropper/catalog.py), offline.

The catalogues' answers are made up here in the shape the real APIs return
(api.pokemontcg.io v2 and YGOPRODeck v7), handed in through `get=`, so this
needs no network, no display and no Tk.

    python3 -m tests.catalog_smoke
"""

import os
import random
import shutil
import sys
import tempfile
import urllib.parse

from PIL import Image, ImageDraw, ImageEnhance

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cardcropper import catalog  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


PIKACHU = {
    "id": "base1-58", "name": "Pikachu", "supertype": "Pokémon", "subtypes": ["Basic"],
    "number": "58", "rarity": "Common",
    "set": {"id": "base1", "name": "Base", "series": "Base", "printedTotal": 102,
            "total": 102, "releaseDate": "1999/01/09"},
    "images": {"small": "https://images.pokemontcg.io/base1/58.png",
               "large": "https://images.pokemontcg.io/base1/58_hires.png"},
    "tcgplayer": {"url": "https://prices.pokemontcg.io/tcgplayer/base1-58",
                  "prices": {"normal": {"low": 2.96, "mid": 6.26, "market": 10.56},
                             "reverseHolofoil": {"low": 9.0, "mid": 12.0, "market": 14.2}}},
    "cardmarket": {"url": "https://prices.pokemontcg.io/cardmarket/base1-58",
                   "prices": {"trendPrice": 14.44, "avg30": 12.24, "reverseHoloTrend": 0.0}},
}
SQUIRTLE = {
    "id": "base1-63", "name": "Squirtle", "number": "63", "rarity": "Common",
    "set": {"id": "base1", "name": "Base", "printedTotal": 102, "releaseDate": "1999/01/09"},
    "images": {"small": "s.png", "large": "l.png"},
    "tcgplayer": {"prices": {"normal": {"market": 3.1}}},
}
DARK_MAGICIAN = {
    "id": 46986414, "name": "Dark Magician", "type": "Normal Monster",
    "ygoprodeck_url": "https://ygoprodeck.com/card/dark-magician-4003",
    "card_sets": [
        {"set_name": "Legend of Blue Eyes White Dragon", "set_code": "LOB-EN005",
         "set_rarity": "Ultra Rare", "set_rarity_code": "(UR)", "set_price": "49.41"},
        {"set_name": "Dark Legends", "set_code": "DLG1-EN004", "set_rarity": "Rare",
         "set_price": "11.64"},
    ],
    "card_images": [{"id": 46986414,
                     "image_url": "https://images.ygoprodeck.com/images/cards/46986414.jpg",
                     "image_url_small":
                         "https://images.ygoprodeck.com/images/cards_small/46986414.jpg"}],
    "card_prices": [{"cardmarket_price": "0.02", "tcgplayer_price": "0.33",
                     "ebay_price": "0.99", "amazon_price": "0"}],
}


class FakeGet:
    """Answers like the real APIs; the strict Pokémon query finds nothing."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append(url)
        u = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(u.query)
        if "pokemontcg" in u.netloc:
            q = qs["q"][0]
            check("select" in qs and "tcgplayer" in qs["select"][0], "select sent")
            if "set.name" in q:                 # "Base Set" is not the catalogue's "Base"
                return {"data": [], "page": 1, "count": 0, "totalCount": 0}
            if 'name:"pikachu*"' in q:
                return {"data": [PIKACHU], "page": 1, "count": 1, "totalCount": 1}
            if 'name:"squirtle*"' in q:
                return {"data": [SQUIRTLE], "count": 1}
            return {"data": []}
        if u.path.endswith("cardsetsinfo.php"):
            check(qs["setcode"] == ["LOB-EN005"], "set code asked")
            return {"id": 46986421, "name": "Dark Magician", "set_name":
                    "Legend of Blue Eyes White Dragon", "set_code": "LOB-EN005",
                    "set_rarity": "Ultra Rare", "set_price": "49.41"}
        if u.path.endswith("cardinfo.php"):
            return {"data": [DARK_MAGICIAN]}
        raise AssertionError(f"unexpected url {url}")


def title_checks():
    p = catalog.parse_title("Pikachu 58/102 Base Set Pokemon NM")
    check(p["game"] == "pokemon", p)
    check((p["name"], p["number"], p["total"]) == ("Pikachu", "58", 102), p)
    check(p["set_name"] == "Base Set", p)
    check(p["printing"] == set(), p)

    p = catalog.parse_title("Riolu (Cosmos Holo) BW33 Black and White Promos Pokemon")
    check((p["name"], p["number"], p["total"]) == ("Riolu", "BW33", None), p)
    check(p["set_name"] == "Black and White Promos", p)
    check(p["printing"] == {"cosmos"}, p)

    p = catalog.parse_title("Dark Magician LOB-EN005 Ultra Rare 1st Edition Yu-Gi-Oh")
    check(p["game"] == "yugioh", p)
    check((p["name"], p["code"], p["rarity"]) == ("Dark Magician", "LOB-EN005", "Ultra Rare"),
          p)
    check("1st-edition" in p["printing"], p)

    p = catalog.parse_title("Charizard EX 12/106 Flashfire Reverse Holo LP")
    check(p["name"] == "Charizard EX" and p["printing"] == {"reverse"}, p)

    qs = catalog.pokemon_queries(catalog.parse_title("Pikachu 58/102 Base Set"))
    check(qs[0] == 'name:"pikachu*" number:58 set.name:"base*" set.printedTotal:102', qs)
    check(qs[1] == 'name:"pikachu*" number:58 set.printedTotal:102', qs)


def search_checks():
    get = FakeGet()
    res = catalog.search("Pikachu 58/102 Base Set Pokemon NM", get=get)
    check(len(res) == 1, res)
    check("set.name" not in res.query and "number:58" in res.query, res.query)
    c = res[0]
    check((c.game, c.id, c.name, c.number, c.set_name, c.set_total) ==
          ("pokemon", "base1-58", "Pikachu", "58", "Base", 102), c)
    check(c.release_date == "1999-01-09", c.release_date)
    check(c.printings == ["Normal", "Reverse Holo"], c.printings)
    check(c.prices["TCGplayer market (USD)"] == {"Normal": 10.56, "Reverse Holo": 14.2},
          c.prices)
    check(c.prices["Cardmarket trend (EUR)"] == 14.44, c.prices)
    check("Cardmarket reverse holo trend (EUR)" not in c.prices, "zero prices are dropped")
    check(c.image_small.endswith("58.png") and c.url.endswith("base1-58"), c)
    check(catalog.ref_price(c) == "$10.56", catalog.ref_price(c))
    check(catalog.title_for(c) == "Pikachu 58/102 Base Set", catalog.title_for(c))

    # Cached: asking again does not touch the network.
    n = len(get.calls)
    again = catalog.search("Pikachu 58/102 Base Set Pokemon NM", get=get)
    check(len(get.calls) == n, f"cache missed: {get.calls[n:]}")
    check(again[0] == c, "cached answer is the same card")
    # Offline: the strict query's empty answer and the loose one are both cached.
    def broken(url, headers):
        raise OSError("offline")
    check(catalog.search("Pikachu 58/102 Base Set", get=broken)[0].id == "base1-58",
          "works offline from the cache")

    # Yu-Gi-Oh by set code: one print, its rarity the printing.
    ygo = catalog.search("Dark Magician LOB-EN005 Ultra Rare 1st Edition Yu-Gi-Oh", get=get)
    check(len(ygo) == 1, ygo)
    y = ygo[0]
    check((y.game, y.name, y.number, y.set_name) ==
          ("yugioh", "Dark Magician", "LOB-EN005", "Legend of Blue Eyes White Dragon"), y)
    check(y.printings == ["Ultra Rare"], y.printings)
    check(y.prices["Cardmarket, any print (EUR)"] == 0.02, y.prices)
    check(y.prices["Set price (USD)"] == {"Ultra Rare": 49.41}, y.prices)
    check(catalog.title_for(y) == "Dark Magician LOB-EN005", catalog.title_for(y))
    # By name only: a row per print.
    rows = catalog.search("dark magician", game="yugioh", get=get)
    check([r.number for r in rows] == ["LOB-EN005", "DLG1-EN004"], rows)
    return c, y


def variant_checks(pikachu, magician):
    squirtle = catalog.normalise_pokemon(SQUIRTLE)
    check(squirtle.printings == ["Normal"], squirtle.printings)
    f = catalog.check_variant({"reverse"}, squirtle)
    check(any(x.level == "warn" and "no Reverse Holo printing" in x and "Normal" in x
              for x in f), f)
    f = catalog.check_variant(set(), pikachu)
    check(any(x.level == "warn" and "also exists as Reverse Holo" in x for x in f), f)
    f = catalog.check_variant({"reverse"}, pikachu)
    check(any(x.level == "ok" for x in f) and not any("also exists" in x for x in f), f)
    f = catalog.check_variant(set(), squirtle)
    check([x.level for x in f] == ["ok"], f)
    f = catalog.check_variant({"cosmos"}, pikachu)
    check(any("check it on the card" in x for x in f), f)
    f = catalog.check_variant({"1st-edition"}, magician, rarity="Secret Rare")
    check(any(x.level == "warn" and "Ultra Rare" in x for x in f), f)
    check(any("1st Edition versus Unlimited" in x for x in f), f)
    check(isinstance(f[0], str), "findings are plain strings too")


def synthetic(seed, size=(240, 330)):
    rnd = random.Random(seed)
    im = Image.new("RGB", size, tuple(rnd.randrange(256) for _ in range(3)))
    d = ImageDraw.Draw(im)
    for _ in range(14):
        x0, y0 = rnd.randrange(size[0]), rnd.randrange(size[1])
        box = (x0, y0, x0 + rnd.randrange(20, 120), y0 + rnd.randrange(20, 120))
        fill = tuple(rnd.randrange(256) for _ in range(3))
        (d.ellipse if rnd.random() < 0.5 else d.rectangle)(box, fill=fill)
    return im


def image_checks():
    a = synthetic(1)
    a2 = ImageEnhance.Brightness(a.rotate(2, resample=Image.BICUBIC)).enhance(1.12)
    a2 = a2.resize((360, 495))
    b = synthetic(2)
    fa, fa2, fb = catalog.fingerprint(a), catalog.fingerprint(a2), catalog.fingerprint(b)
    check(fa.shape == (190,), fa.shape)
    same, diff = catalog.similarity(fa, fa2), catalog.similarity(fa, fb)
    check(catalog.similarity(fa, fa) == 1.0, "identical scores 1")
    check(same > diff, f"same {same} vs different {diff}")
    check(catalog.verdict(0.9) == "likely the same card" and catalog.verdict(0.7) ==
          "uncertain" and catalog.verdict(0.3) == "different", "verdicts")
    ranked = catalog.rank_matches(a2, [("b", b), ("none", None), ("a", a)])
    check([c for _, c in ranked] == ["a", "b"], ranked)

    # A raw scan on a black bed is cut out before hashing.
    bed = Image.new("RGB", (500, 640), (5, 5, 5))
    bed.paste(a.resize((360, 495)), (70, 72))
    check(catalog._looks_raw(bed) and not catalog._looks_raw(a), "raw scan detection")
    check(catalog.similarity(catalog.fingerprint(bed), fa) > diff, "bed scan still matches")


def batch_checks(work):
    """A prepared batch checked card by card: right scans pass, a swapped one is caught."""
    import csv as _csv
    folder = os.path.join(work, "prepared")
    pika, other = synthetic(1), synthetic(2)
    for sku, img in (("KA1", pika), ("KA2", other)):
        os.makedirs(os.path.join(folder, "cards", sku))
        img.save(os.path.join(folder, "cards", sku, "front.jpg"), quality=95)
    with open(os.path.join(folder, "triage.csv"), "w", encoding="utf-8", newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=["sku", "title"])
        w.writeheader()
        w.writerow({"sku": "KA1", "title": "Pikachu 58/102 Base Set Pokemon NM"})
        w.writerow({"sku": "KA2", "title": "Pikachu 58/102 Base Set Pokemon NM"})
        w.writerow({"sku": "KA3", "title": "Pikachu 58/102 Base Set Pokemon NM"})
    items = catalog.batch_items(folder)
    check([i[0] for i in items] == ["KA1", "KA2", "KA3"], items)
    get = FakeGet()
    got = [catalog.check_card(t, f, get=get, load_image=lambda url: pika)
           for _, t, f in items]
    check(got[0]["verdict"] == "ok" and got[0]["best"].id == "base1-58", got[0])
    check(got[1]["verdict"] == "mismatch", f"a scan of another card passed: {got[1]}")
    check(got[2]["verdict"] == "no scan", got[2])


def ebay_checks():
    url = catalog.ebay_sold_url("Pikachu 58/102 Base Set & co")
    check(url.startswith("https://www.ebay.co.uk/sch/i.html?"), url)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    check(q["_nkw"] == ["Pikachu 58/102 Base Set & co"], q)
    check(q["LH_Sold"] == ["1"] and q["LH_Complete"] == ["1"] and q["_sop"] == ["13"], q)
    check("%2F" in url and "+%26+" in url, "query is encoded")
    check(catalog.ebay_sold_url("x", site="com").startswith("https://www.ebay.com/"), "site")
    modern = catalog.CatalogCard("pokemon", "sv1-13", "Pikachu", "13", "Scarlet & Violet",
                                 set_total=198, release_date="2023-03-31")
    check(catalog.title_for(modern, "Reverse Holo") ==
          "Pikachu 013/198 Scarlet & Violet Reverse Holo", catalog.title_for(modern))


def main():
    # Only meaningful run on its own: inside tests.smoke the window tests
    # have already imported Tk.
    tk_before = "tkinter" in sys.modules
    work = tempfile.mkdtemp(prefix="cc-catalog-")
    catalog.set_cache_dir(work)
    try:
        title_checks()
        pikachu, magician = search_checks()
        variant_checks(pikachu, magician)
        image_checks()
        ebay_checks()
        batch_checks(tempfile.mkdtemp(prefix="cc-batch-"))
        check(catalog.clear_cache() > 0 and not os.listdir(work), "cache cleared")
    finally:
        catalog.set_cache_dir(None)
        shutil.rmtree(work, ignore_errors=True)
    check(tk_before or "tkinter" not in sys.modules, "the catalogue must not need Tk")
    print("catalog smoke: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
