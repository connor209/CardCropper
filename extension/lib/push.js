/*
 * CardCropper push — pure logic, no DOM, no network.
 *
 * Turns a CardCropper change file into the exact per-card bodies CardUploader's
 * batch page sends to POST /inventory/batch (captured 21 Nov 2026), and checks
 * a re-read job against what was sent.
 *
 * Rules:
 *  - Only SKUs in the change file are touched, and only the fields named.
 *  - `_delta: true` on every card: only the fields sent change.
 *  - Condition and variant edits carry the rebuilt title and the TCGplayer SKU
 *    for the new variant x condition, as the page does. The title is the
 *    CURRENT title with only the variant words and/or the trailing condition
 *    code swapped — never rebuilt from scratch (title configs differ per game).
 *  - A change whose "old" value no longer matches CardUploader is refused (the
 *    card changed since the file was written).
 *  - Yu-Gi-Oh prices never go below £1.99.
 *
 * Loaded as a content script (globals) and by node for tests (module.exports).
 */
(function (root) {
  "use strict";

  const CONDITION_NAMES = {
    NM: "Near Mint", LP: "Lightly Played", MP: "Moderately Played",
    HP: "Heavily Played", DMG: "Damaged"
  };
  const CODES = Object.keys(CONDITION_NAMES);
  const YGO_MIN_PRICE = 1.99;

  // How a variant can be spelled in a title. Normal / Unlimited are often not
  // in the title at all, so a switch AWAY from them inserts the new words
  // before the condition code; a switch TO them removes the old words.
  const VARIANT_SPELLINGS = {
    "reverse holofoil": ["Reverse Holofoil", "Reverse Holo"],
    "holofoil": ["Holofoil", "Holo"],
    "1st edition": ["1st Edition", "1st Ed"],
    "unlimited": ["Unlimited"],
    "normal": ["Normal"],
    "cosmos holofoil": ["Cosmos Holofoil", "Cosmos Holo", "Cosmos"],
    "1st edition holofoil": ["1st Edition Holofoil", "1st Edition Holo"],
    "unlimited holofoil": ["Unlimited Holofoil", "Unlimited Holo"]
  };
  const SILENT_VARIANTS = new Set(["normal", "unlimited"]);
  const CODE_AT_END = /(\s)(NM|LP|MP|HP|DMG)\s*$/;

  function cards(job) {
    if (Array.isArray(job)) return job;
    return (job && job.results && job.results.cards) || (job && job.cards) || [];
  }

  function gameOfTcg(tcg) {
    const t = String(tcg || "").toLowerCase();
    if (t.includes("yugioh") || t.includes("yu-gi-oh")) return "yugioh";
    if (t.includes("mtg") || t.includes("magic")) return "mtg";
    return "pokemon";
  }

  function conditionCode(c) {
    const t = String(c || "").trim();
    if (CODES.includes(t.toUpperCase())) return t.toUpperCase();
    for (const [code, name] of Object.entries(CONDITION_NAMES)) {
      if (name.toLowerCase() === t.toLowerCase()) return code;
    }
    return null;
  }

  function escapeRe(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function spellings(variant) {
    const key = String(variant || "").trim().toLowerCase();
    const list = VARIANT_SPELLINGS[key] || [String(variant || "").trim()];
    return [...list].sort((a, b) => b.length - a.length);
  }

  /** The title with its trailing condition code swapped. Throws if it has none. */
  function swapCondition(title, code) {
    if (!CODE_AT_END.test(title)) throw new Error(`title has no trailing condition code: "${title}"`);
    return title.replace(CODE_AT_END, `$1${code}`);
  }

  /** The title with the old variant's words swapped for the new one's. */
  function swapVariant(title, oldVariant, newVariant) {
    const newWords = spellings(newVariant)[0];
    const oldKey = String(oldVariant || "").trim().toLowerCase();
    for (const s of spellings(oldVariant)) {
      const re = new RegExp(`(^|\\s)${escapeRe(s)}(?=\\s|$)`, "i");
      // Swapped, never dropped — even to Normal / Unlimited: the page wrote
      // "Unlimited" into KH54's title ("… Japanese Unlimited NM").
      if (re.test(title)) return title.replace(re, `$1${newWords}`);
    }
    if (SILENT_VARIANTS.has(oldKey)) {
      if (!CODE_AT_END.test(title)) throw new Error(`title has no trailing condition code: "${title}"`);
      return title.replace(CODE_AT_END, ` ${newWords}$1$2`);
    }
    throw new Error(`cannot find "${oldVariant}" in the title "${title}" — edit this card by hand`);
  }

  /** The TCGplayer SKU for variant x condition from the card's skus[]. */
  function tcgplayerSku(card, variant, code) {
    const name = CONDITION_NAMES[code];
    const hit = (card.skus || []).find((s) =>
      String(s.variant || "").toLowerCase() === String(variant || "").toLowerCase() &&
      String(s.condition || "").toLowerCase() === name.toLowerCase());
    if (!hit) throw new Error(`no TCGplayer SKU for ${variant} / ${name}`);
    return hit.sku;
  }

  function samePrice(a, b) {
    if ((a === "" || a == null) && (b === "" || b == null)) return true;
    return Math.abs(Number(a) - Number(b)) < 0.005;
  }

  /**
   * Plan a push. Returns { tcg, game, items, refused, rows } where
   *  items:   [{ sku, inventoryId, listed, body }] — one /inventory/batch card each
   *  refused: [{ sku, field, reason }]
   *  rows:    [{ sku, field, old, value, listed }] — the diff to show
   */
  function plan(changeFile, job) {
    if (!changeFile || changeFile.kind !== "cardcropper-changes") {
      throw new Error("not a CardCropper change file");
    }
    const tcg = (job && job.settings && job.settings.tcg) || changeFile.tcg || "";
    const game = changeFile.game || gameOfTcg(tcg);
    const bySku = new Map(cards(job).map((c) => [c.sku, c]));
    const perSku = new Map();
    const refused = [];
    for (const ch of changeFile.changes || []) {
      if (!["price", "condition", "variant"].includes(ch.field)) {
        refused.push({ sku: ch.sku, field: ch.field, reason: "unknown field" });
        continue;
      }
      const card = bySku.get(ch.sku);
      if (!card) {
        refused.push({ sku: ch.sku, field: ch.field, reason: "SKU not in this job" });
        continue;
      }
      if (!perSku.has(ch.sku)) perSku.set(ch.sku, { card, changes: {} });
      const entry = perSku.get(ch.sku);
      if (entry.changes[ch.field]) {
        refused.push({ sku: ch.sku, field: ch.field, reason: "changed twice in one file" });
        continue;
      }
      entry.changes[ch.field] = ch;
    }

    const items = [];
    const rows = [];
    for (const [sku, { card, changes }] of perSku) {
      try {
        const body = { inventoryId: card.inventoryId, _delta: true };
        const listed = card.status === "listed";
        const curCode = conditionCode(card.condition);
        let variant = card.variant;
        let code = curCode;
        let title = card.title;

        const p = changes.price;
        if (p) {
          if (p.old !== "" && p.old != null && !samePrice(p.old, card.price)) {
            throw new Error(`price is £${card.price} in CardUploader, the file expected £${p.old}`);
          }
          if (p.value !== "" && !(Number(p.value) > 0)) throw new Error(`price "${p.value}"`);
          if (game === "yugioh" && p.value !== "" && Number(p.value) < YGO_MIN_PRICE) {
            throw new Error(`Yu-Gi-Oh price £${p.value} is below £${YGO_MIN_PRICE.toFixed(2)}`);
          }
          body.price = p.value === "" ? "" : Number(p.value).toFixed(2);
          rows.push({ sku, field: "price", old: card.price, value: body.price, listed });
        }
        const v = changes.variant;
        if (v) {
          if (v.old && String(v.old).toLowerCase() !== String(card.variant).toLowerCase()) {
            throw new Error(`variant is ${card.variant} in CardUploader, the file expected ${v.old}`);
          }
          title = swapVariant(title, card.variant, v.value);
          variant = v.value;
          body.variant = v.value;
          body.variants = [v.value];
          rows.push({ sku, field: "variant", old: card.variant, value: v.value, listed });
        }
        const c = changes.condition;
        if (c) {
          const target = conditionCode(c.value);
          if (!target) throw new Error(`condition "${c.value}"`);
          if (c.old && conditionCode(c.old) !== curCode) {
            throw new Error(`condition is ${card.condition} in CardUploader, the file expected ${c.old}`);
          }
          title = swapCondition(title, target);
          code = target;
          body.condition = target;
          body.wasOptimized = false;
          rows.push({ sku, field: "condition", old: card.condition, value: target, listed });
        }
        if (v || c) {
          if (!code) throw new Error(`current condition "${card.condition}" is not on the scale`);
          body.tcgplayer_sku = tcgplayerSku(card, variant, code);
          body.title = title;
          rows.push({ sku, field: "title", old: card.title, value: title, listed });
        }
        items.push({ sku, inventoryId: card.inventoryId, listed, body });
      } catch (err) {
        for (const f of Object.keys(changes)) refused.push({ sku, field: f, reason: err.message });
      }
    }
    return { tcg, game, items, refused, rows };
  }

  /** Request bodies for POST /inventory/batch, `per` cards each. */
  function requests(jobId, tcg, items, per = 3) {
    const out = [];
    for (let i = 0; i < items.length; i += per) {
      out.push({ jobId, jobType: "ungraded", tcg, cards: items.slice(i, i + per).map((x) => x.body),
                 photoEditMarks: true });
    }
    return out;
  }

  /** Compare a re-read job with what was sent. Returns [{ sku, field, sent, now }]. */
  function verify(items, job) {
    const bySku = new Map(cards(job).map((c) => [c.sku, c]));
    const bad = [];
    for (const { sku, body } of items) {
      const card = bySku.get(sku);
      if (!card) { bad.push({ sku, field: "*", sent: "", now: "missing" }); continue; }
      if ("price" in body && !samePrice(body.price, card.price)) {
        bad.push({ sku, field: "price", sent: body.price, now: card.price });
      }
      if ("condition" in body && conditionCode(card.condition) !== body.condition) {
        bad.push({ sku, field: "condition", sent: body.condition, now: card.condition });
      }
      if ("variant" in body && String(card.variant).toLowerCase() !== body.variant.toLowerCase()) {
        bad.push({ sku, field: "variant", sent: body.variant, now: card.variant });
      }
      if ("title" in body && card.title !== body.title) {
        bad.push({ sku, field: "title", sent: body.title, now: card.title });
      }
    }
    return bad;
  }

  /** Lenient read of /automatic-inventory/row-sync-status: { id: status }. */
  function syncStatuses(resp) {
    const out = {};
    const walk = (n) => {
      if (Array.isArray(n)) return n.forEach(walk);
      if (n && typeof n === "object") {
        const id = n.cardId || n.inventoryId || n.id;
        const st = n.syncStatus || n.status || n.state;
        if (id && typeof st === "string") out[id] = st;
        else Object.entries(n).forEach(([k, v]) => {
          if (typeof v === "string" && /sync|pend|fail|error/i.test(v)) out[k] = v;
          else walk(v);
        });
      }
    };
    walk(resp);
    return out;
  }

  const api = { CONDITION_NAMES, YGO_MIN_PRICE, cards, gameOfTcg, conditionCode, swapCondition,
                swapVariant, tcgplayerSku, plan, requests, verify, syncStatuses };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.CCPush = api;
})(typeof self !== "undefined" ? self : this);
