// node extension/test/push.test.js — the payloads the push module builds.
"use strict";
const assert = require("assert");
const P = require("../lib/push.js");

const skus = (variants) => variants.flatMap((v, i) =>
  Object.values(P.CONDITION_NAMES).map((c, j) => ({ sku: 1000 + i * 10 + j, variant: v, condition: c })));
const job = {
  settings: { tcg: "mtg" },
  results: { cards: [
    { inventoryId: "inv-1", sku: "MTG-I1", status: "processed", price: "", condition: "LP",
      variant: "Normal", title: "Divine Transformation Uncommon Fourth Edition Magic: The Gathering LP",
      skus: skus(["Normal", "Foil"]) },
    { inventoryId: "inv-54", sku: "KH54", status: "listed", price: "3.49", condition: "NM",
      variant: "1st Edition", title: "Growlithe DPBP#063 Shining Darkness Pokemon Japanese 1st Edition NM",
      skus: skus(["1st Edition", "Unlimited"]) },
    { inventoryId: "inv-7", sku: "KA7", status: "processed", price: "2.49", condition: "NM",
      variant: "Normal", title: "Lugia 78/124 Fates Collide Pokemon NM",
      skus: skus(["Normal", "Reverse Holofoil"]) },
    { inventoryId: "inv-9", sku: "KA9", status: "processed", price: "2.49", condition: "NM",
      variant: "Holofoil", title: "Weird Title NM", skus: skus(["Holofoil", "Reverse Holofoil"]) }
  ] }
};
const file = (changes, game = "mtg") => ({ kind: "cardcropper-changes", jobId: "j", game, changes });

// Price, as captured: {"inventoryId","_delta":true,"price":"0.99"}
let p = P.plan(file([{ sku: "MTG-I1", field: "price", value: "0.99", old: "" }]), job);
assert.deepStrictEqual(p.items[0].body, { inventoryId: "inv-1", _delta: true, price: "0.99" });
const reqs = P.requests("j", p.tcg, p.items);
assert.deepStrictEqual(reqs[0], { jobId: "j", jobType: "ungraded", tcg: "mtg",
  cards: [p.items[0].body], photoEditMarks: true });

// Condition, as captured on MTG-I1 LP -> MP: title's last word and the SKU for the new condition.
p = P.plan(file([{ sku: "MTG-I1", field: "condition", value: "MP", old: "LP" }]), job);
assert.deepStrictEqual(p.items[0].body, { inventoryId: "inv-1", _delta: true, condition: "MP",
  wasOptimized: false, tcgplayer_sku: 1002,
  title: "Divine Transformation Uncommon Fourth Edition Magic: The Gathering MP" });

// Variant, as captured on KH54 1st Edition -> Unlimited.
p = P.plan(file([{ sku: "KH54", field: "variant", value: "Unlimited", old: "1st Edition" }], "pokemon"), job);
assert.deepStrictEqual(p.items[0].body, { inventoryId: "inv-54", _delta: true, variant: "Unlimited",
  variants: ["Unlimited"], tcgplayer_sku: 1010,
  title: "Growlithe DPBP#063 Shining Darkness Pokemon Japanese Unlimited NM" });
assert.ok(p.items[0].listed, "a listed card is flagged");

// Normal -> Reverse Holofoil inserts the words before the condition; with a condition change too.
p = P.plan(file([{ sku: "KA7", field: "variant", value: "Reverse Holofoil", old: "Normal" },
                 { sku: "KA7", field: "condition", value: "LP", old: "NM" },
                 { sku: "KA7", field: "price", value: "4.49", old: "2.49" }], "pokemon"), job);
assert.strictEqual(p.items.length, 1);
assert.strictEqual(p.items[0].body.title, "Lugia 78/124 Fates Collide Pokemon Reverse Holofoil LP");
assert.strictEqual(p.items[0].body.tcgplayer_sku, 1011);
assert.strictEqual(p.items[0].body.price, "4.49");

// Refusals: unknown SKU, stale old value, YGO under £1.99, title without the variant words.
p = P.plan(file([{ sku: "NOPE", field: "price", value: "1" },
                 { sku: "KA7", field: "price", value: "4.49", old: "9.99" },
                 { sku: "KA9", field: "variant", value: "Reverse Holofoil", old: "Holofoil" }], "pokemon"), job);
assert.strictEqual(p.items.length, 0);
assert.strictEqual(p.refused.length, 3);
p = P.plan(file([{ sku: "MTG-I1", field: "price", value: "0.99" }], "yugioh"), job);
assert.ok(p.refused[0].reason.includes("below"), "Yu-Gi-Oh floor");
assert.throws(() => P.plan({ changes: [] }, job), /not a CardCropper change file/);

// Verify against a re-read.
p = P.plan(file([{ sku: "MTG-I1", field: "price", value: "0.99" }]), job);
assert.deepStrictEqual(P.verify(p.items, job), [{ sku: "MTG-I1", field: "price", sent: "0.99", now: "" }]);
const after = JSON.parse(JSON.stringify(job));
after.results.cards[0].price = "0.99";
assert.deepStrictEqual(P.verify(p.items, after), []);

assert.deepStrictEqual(P.syncStatuses({ statuses: [{ cardId: "a", syncStatus: "Synced" }] }), { a: "Synced" });
assert.deepStrictEqual(P.syncStatuses({ a: "Pending", b: "Synced" }), { a: "Pending", b: "Synced" });
console.log("extension push: ok");
