"""
Rule-based classifier: map scraped app content to Stripe policy categories.

Each category from the Stripe Supportability Handling Guide has:
  - keywords/phrases that signal the category
  - supportability level (not_supportable / restricted / not_enabled_for_wix / supportable)
  - confidence thresholds

P&R Index (Prohibited and Restricted) is the main hierarchy — all matches map to P&R IDs.
Mapping: data/classifier_rule_to_p_and_r.json (from data/stripe_policy_to_p_and_r_index.xlsx).

Enriched with compliance_signal_library.json (~120 signals across 19 categories) from the
Compliance PDFs project. Each rule tracks signal_ids for traceability back to regulation sources.

Returns structured result per app with matched categories ranked by confidence.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional
import re


@lru_cache(maxsize=2048)
def _boundary_re(kw: str) -> re.Pattern:
    return re.compile(rf'\b{re.escape(kw)}\b')


def _kw_match(kw: str, text: str) -> bool:
    """Match keyword in text. Single words (≤10 chars, no spaces) use word-boundary
    matching to prevent substring false positives (e.g. 'rum' inside 'forum')."""
    if " " not in kw and len(kw) <= 10:
        return bool(_boundary_re(kw).search(text))
    return kw in text

# Lazy import to avoid circular deps
def _get_p_and_r_for_rule(category: str, subcategory: str) -> list[tuple[int, str]]:
    from uw_app.p_and_r import get_p_and_r_for_rule
    return get_p_and_r_for_rule(category, subcategory)

# ── Policy taxonomy ────────────────────────────────────────────────────────────
# Each entry: (category, subcategory, supportability, keyword_groups, signal_ids, regulation)
# keyword_groups: list of (weight, [keywords]) — weight is how much a single hit counts
# A match means: the keyword appears in the lowered combined text.
# supportability: "not_supportable" | "restricted" | "not_enabled" | "supportable"
# signal_ids: list of compliance signal IDs that contributed keywords to this rule
# regulation: combined regulation references from signal library

_POLICY: list[dict] = [
    # ── Adult Content ──────────────────────────────────────────────────────
    {
        "category": "Adult Content, Products, and Services",
        "subcategory": "Pornographic Imagery",
        "supportability": "not_supportable",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (80, ["pornograph", "xxx", "porn site", "adult video", "nsfw content", "hentai", "onlyfans clone",
                   "deep-fake porn", "explicit sexual", "nude video", "adult entertainment"]),
            (40, ["nsfw", "adult content", "18+ content", "erotic video", "nude photo"]),
        ],
    },
    {
        "category": "Adult Content, Products, and Services",
        "subcategory": "Sexually Driven Services",
        "supportability": "not_supportable",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (80, ["escort service", "escort agency", "gentleman club", "strip club",
                   "mail-order bride", "fetish service", "financial domination", "feet video",
                   "cam girl", "cam model", "sex worker"]),
            (40, ["escort", "erotic massage", "adult service", "sexual service"]),
        ],
    },
    {
        "category": "Adult Content, Products, and Services",
        "subcategory": "Sexually Driven Dating",
        "supportability": "not_supportable",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (70, ["hook-up", "hookup app", "sexual encounter", "casual sex", "ashley madison",
                   "adult dating", "sex dating"]),
            (30, ["dating site", "dating app", "meet singles"]),
        ],
    },
    {
        "category": "Adult Content, Products, and Services",
        "subcategory": "Sex Toys and Accessories",
        "supportability": "restricted",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (60, ["sex toy", "dildo", "vibrator", "adult toy", "sex shop", "sex accessory",
                   "bondage", "bdsm", "fetish gear", "lingerie shop"]),
        ],
    },
    # ── Alcohol ────────────────────────────────────────────────────────────
    {
        "category": "Alcohol",
        "subcategory": "Alcoholic Beverages",
        "supportability": "restricted",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (50, ["wine shop", "beer delivery", "spirits store", "liquor store", "alcohol delivery",
                   "craft brewery", "wine cellar", "distillery"]),
            (10, ["wine", "beer", "whiskey", "vodka", "rum", "tequila", "cocktail"]),
        ],
    },
    # ── Content Creation (Not enabled for Wix) ─────────────────────────────
    {
        "category": "Content Creation",
        "subcategory": "Creator Platform / Tips / Digital Goods",
        "supportability": "not_enabled",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (60, ["content creator platform", "creator monetization", "tip creator",
                   "patreon clone", "fan subscription", "creator economy"]),
            (10, ["tip jar", "creator", "subscriber content", "exclusive content"]),
        ],
    },
    # ── Cryptocurrency ─────────────────────────────────────────────────────
    {
        "category": "Cryptocurrency Products and Services",
        "subcategory": "Trade / Exchange / Wallets",
        "supportability": "not_enabled",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (80, ["crypto exchange", "bitcoin exchange", "crypto trading", "crypto wallet",
                   "buy bitcoin", "buy crypto", "sell bitcoin", "token sale", "ico launch",
                   "initial coin offering", "crypto staking", "yield farm",
                   "crypto lending", "defi platform", "nft marketplace",
                   "virtual wallet", "stored value", "digital wallet", "e-wallet",
                   "in-app currency", "in-app coins", "virtual coins", "virtual currency"]),
            (50, ["cryptocurrency", "blockchain", "ethereum", "solana", "web3",
                   "decentralized finance", "defi", "nft", "crypto mining"]),
            (10, ["bitcoin", "crypto", "token", "wallet"]),
        ],
    },
    # ── Debt and Credit Services ──────────────────────────────────────────
    {
        "category": "Debt and Credit Services",
        "subcategory": "Debt Collection / Relief / Credit Repair",
        "supportability": "not_supportable",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (70, ["debt collection", "debt relief", "debt settlement", "debt consolidation",
                   "credit repair", "credit monitoring", "wipe out your debt",
                   "stop foreclosure", "debt reduction", "collect debt", "credi collect",
                   "credit collect", "credit disputes", "dispute letter",
                   "credit wellness", "credit bureau dispute"]),
            (40, ["debt collector", "past due receivable", "credit score fix",
                   "consolidate your bills", "collect now", "collection agency",
                   "negative inquiries", "challenge inquiries",
                   "automate dispute", "credit improvement"]),
        ],
    },
    # ── Financial Services (Not enabled for Wix) ──────────────────────────
    {
        "category": "Financial Services",
        "subcategory": "Loans / BNPL / Brokerage / Neobank / P2P",
        "supportability": "not_enabled",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (70, ["payday loan", "cash advance", "buy now pay later", "bnpl",
                   "stock brokerage", "forex trading", "binary option", "funded trading",
                   "prop firm", "neobank", "peer-to-peer money", "money transfer",
                   "escrow service", "currency exchange"]),
            (40, ["loan service", "lending platform", "investment platform",
                   "money remittance", "send money", "high-yield return",
                   "loan pay", "easy loan", "quick loan", "fast loan", "loan app"]),
            (10, ["invest", "portfolio", "trading signal", "loan"]),
        ],
    },
    {
        "category": "Financial Services",
        "subcategory": "Dropshipping / Resale Consulting",
        "supportability": "not_enabled",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (60, ["dropshipping course", "reselling course", "how to dropship",
                   "dropship consulting", "amazon fba course", "resale profit"]),
            (10, ["dropshipping", "reselling guide", "make money reselling"]),
        ],
    },
    # ── Fundraising ───────────────────────────────────────────────────────
    {
        "category": "Fundraising",
        "subcategory": "Crowdfunding / Donations",
        "supportability": "restricted",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (50, ["crowdfunding", "gofundme", "fundraise", "donate now", "donation page",
                   "charity raffle", "nonprofit donation"]),
            (10, ["donate", "donation", "charity", "nonprofit"]),
        ],
    },
    # ── Gambling (enriched from GAMB-001) ──────────────────────────────────
    {
        "category": "Gambling and Games of Skill",
        "subcategory": "Betting / Casino / Lottery",
        "supportability": "not_supportable",
        "signal_ids": ["GAMB-001"],
        "regulation": "UIGEA; state gambling laws; BRAM; GBPP",
        "keywords": [
            (80, ["online casino", "sports betting", "place bet", "slot machine",
                   "poker online", "lottery ticket",
                   "fantasy sports betting", "fanduel", "draftkings", "betting odds",
                   "gamble", "gambling site", "bingo online", "trend bet",
                   # from GAMB-001
                   "online gambling", "crypto casino", "e-voucher gambling",
                   "play for real money", "online lottery"]),
            (50, ["casino", "betting", "wager", "jackpot", "lottery",
                   "odds", "sportsbook", "place a bet", "speculating on",
                   "wagering", "slots online", "bet online"]),
            (25, ["slot"]),
            (15, ["poker", "roulette", "blackjack", "baccarat"]),
            (10, ["bet"]),
        ],
    },
    {
        "category": "Gambling and Games of Skill",
        "subcategory": "Games of Skill with Monetary Prizes",
        "supportability": "not_supportable",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (60, ["prize pool", "entry fee tournament", "cash prize game",
                   "pay to play tournament", "esports tournament entry",
                   "fantasy sports betting", "daily fantasy", "pick em contest",
                   "bracket challenge prize", "leaderboard cash prize",
                   "prediction market", "win prize money"]),
            (25, ["win cash", "money prize", "tournament prize", "play for money",
                   "$10,000 prize", "$5,000 prize", "cash giveaway", "prize giveaway",
                   "referrer bonus", "top referrer", "win money", "prize winner"]),
        ],
    },
    # ── Hazardous Materials ───────────────────────────────────────────────
    {
        "category": "Hazardous Materials",
        "subcategory": "Explosives / Fireworks / Toxic / Radioactive",
        "supportability": "not_supportable",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (70, ["firework", "explosive", "gunpowder", "dynamite", "radioactive material",
                   "toxic chemical", "pesticide", "rocket propellant"]),
        ],
    },
    # ── Healthcare (enriched from RX-001..005) ─────────────────────────────
    {
        "category": "Healthcare Products and Services",
        "subcategory": "Prescription Pharmaceuticals",
        "supportability": "restricted",
        "signal_ids": ["RX-001", "RX-002", "RX-003", "RX-004", "RX-005"],
        "regulation": "FDCA; ICANN RAA 3.18; BRAM; GBPP; DEA CSA",
        "keywords": [
            (80, ["online pharmacy", "buy prescription", "prescription drug",
                   "viagra", "cialis", "semaglutide", "ozempic", "modafinil",
                   "tirzepatide", "weight loss injection", "peptide injection",
                   "compounded medication", "telehealth prescription",
                   "selling pharmaceutical", "pharmaceutical product",
                   "pharmaceutical sales",
                   # from RX-001: no-prescription practices
                   "no prescription required", "no rx", "without prescription",
                   "prescription not needed", "order without rx", "no doctor needed",
                   "no script needed",
                   # from RX-002: worldwide shipping
                   "international pharmacy", "worldwide shipping",
                   # from RX-003: unapproved/counterfeit drugs
                   "kamagra", "silagra", "filagra", "bimat", "careprost",
                   "retino-a", "anazole", "black cialis", "boldebolin",
                   "finpecia", "nandrobolin", "reductil", "sibutril", "slimex",
                   "testobolin", "viagra professional", "cialis professional",
                   "levitra professional", "generic viagra", "generic cialis",
                   "generic levitra", "counterfeit drug", "unapproved drug",
                   "falsified medicine",
                   # from RX-005: Rx drugs sold as OTC
                   "sildenafil", "tadalafil", "vardenafil", "wegovy", "rybelsus",
                   "tretinoin", "retin-a", "clobetasol", "dermovate",
                   "bimatoprost", "latisse", "betamethasone", "betnovate",
                   "hydroquinone", "clindamycin", "ketoconazole",
                   "buy ozempic online", "no prescription viagra"]),
            (40, ["pharmacy", "rx drug", "prescription medication", "telemedicine",
                   "prescriptions", "peptides", "semaglutide injection", "hormone therapy",
                   "medical consultation online", "online doctor",
                   # from RX-004: online questionnaire as Rx substitute
                   "online questionnaire", "fill out form", "online consultation",
                   "quick form", "answer a few questions",
                   # from RX-002
                   "ship worldwide", "ships to all countries",
                   "codeine", "tramadol", "opioid", "levitra",
                   "orlistat", "pantoprazole", "acyclovir", "apetamin",
                   "methocarbamol", "minoxidil"]),
            (10, ["prescription", "peptide"]),
        ],
    },
    # ── Nutraceuticals (enriched from SUPP-001..007, CLAIM-001..003) ──────
    {
        "category": "Healthcare Products and Services",
        "subcategory": "Nutraceuticals / Weight Loss / Sexual Enhancement",
        "supportability": "restricted",
        "signal_ids": ["SUPP-001", "SUPP-002", "SUPP-003", "SUPP-004", "SUPP-005",
                       "SUPP-006", "SUPP-007", "CLAIM-001", "CLAIM-002", "CLAIM-003"],
        "regulation": "FDCA; 21 CFR 101.93(g); FTC Act; DASCA 2014; USADA; FDA warning letters",
        "keywords": [
            (80, [# from SUPP-001: DMAA
                  "dmaa", "1,3-dmaa", "1,3-dimethylamylamine", "geranamine", "methylhexanamine",
                  # from SUPP-003: MMS
                  "miracle mineral solution", "miracle mineral supplement", "chlorine dioxide solution",
                  # from SUPP-005: SARMs and steroids
                  "ostarine", "mk-2866", "cardarine", "gw-501516", "sarms",
                  "selective androgen receptor modulator", "anabolic steroid",
                  "anadrol", "anavar", "dianabol", "trenbolone", "winstrol", "stanozolol",
                  # from CLAIM-001: explicit disease claims (Critical)
                  "cures cancer", "fights cancer", "kills cancer", "reverses diabetes",
                  "treats hiv", "cures autism", "prevents alzheimer", "cures anxiety"]),
            (50, ["weight loss pill", "fat burner", "diet pill", "appetite suppressant",
                  "sexual enhancement", "testosterone booster", "libido boost",
                  "erectile dysfunction", "male enhancement",
                  "vitamin subscription", "supplement subscription",
                  # from SUPP-002: Acacia rigidula
                  "acacia rigidula",
                  # from SUPP-004: pure caffeine
                  "pure caffeine", "caffeine powder", "caffeine anhydrous bulk",
                  # from SUPP-006: Picamilon
                  "picamilon", "pikatropin",
                  # from SUPP-007: Yohimbine
                  "yohimbine", "yohimbe",
                  # from CLAIM-002: implied disease claims
                  "alternative to adderall", "like adderall", "like xanax",
                  "opioid alternative", "natural alternative to",
                  "as effective as viagra", "drug-free alternative",
                  # from CLAIM-003: disease name in product
                  "cancer cure", "arthritis relief", "diabetes supplement"]),
            (25, ["weight loss supplement", "metabolism boost", "burn fat",
                  "recurring vitamin", "monthly vitamin", "auto-ship supplement"]),
            (10, ["cures", "treats", "heals", "prevents", "eliminates",
                  "antiviral", "antibiotic", "antidepressant", "antifungal",
                  "anti-inflammatory", "analgesic"]),
        ],
    },
    {
        "category": "Healthcare Products and Services",
        "subcategory": "Telehealth / Medical Marketplace",
        "supportability": "restricted",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (60, ["telehealth platform", "medical marketplace", "doctor marketplace",
                   "physician marketplace", "healthcare marketplace", "medical service marketplace",
                   "find a doctor", "book a doctor", "connect with doctor",
                   "medical advice", "health advice online"]),
            (30, ["telehealth", "telemedicine platform", "virtual clinic",
                   "health clinic online", "patient portal", "emr system", "ehr system",
                   "symptom checker", "symptom navigator"]),
        ],
    },
    # ── Illegal Drugs (enriched from PARA-001) ─────────────────────────────
    {
        "category": "Illegal Drugs and Related Products",
        "subcategory": "Illegal Substances / Paraphernalia",
        "supportability": "not_supportable",
        "signal_ids": ["PARA-001"],
        "regulation": "FDCA; DEA CSA; state drug paraphernalia laws",
        "keywords": [
            (90, ["buy cocaine", "buy heroin", "buy lsd", "buy mdma", "buy ecstasy",
                   "drug dealer", "drug marketplace", "darknet market"]),
            (60, ["drug paraphernalia", "bong shop", "rolling paper shop",
                   "kratom", "kava", "psilocybin", "psychedelic shop",
                   # from PARA-001
                   "crack pipe", "cocaine spoon", "cocaine freebase kit",
                   "dab rig", "chillum", "roach clip", "drug pipe"]),
            (30, ["bong", "bongs", "glass pipe", "water pipe", "cannabis pipe",
                   "herb pipe", "marijuana pipe", "smoking pipe"]),
        ],
    },
    # ── Illegal Products (enriched from IPR-001..003, HATE-001) ────────────
    {
        "category": "Illegal Products and Services",
        "subcategory": "Counterfeit / Fake IDs / Human Trafficking",
        "supportability": "not_supportable",
        "signal_ids": ["IPR-001", "IPR-002"],
        "regulation": "BRAM; GBPP; Lanham Act; DMCA",
        "keywords": [
            (90, ["fake id", "fake passport", "fake diploma", "counterfeit",
                   "replica luxury", "buy followers", "buy likes",
                   "human trafficking", "child exploitation"]),
            (50, ["fake document", "novelty id", "clone website",
                   "knock-off", "replica designer",
                   "brand inspired", "designer inspired", "fake designer",
                   "first copy", "aaa quality", "mirror image",
                   "buy subscribers", "fake reviews", "paid followers"]),
            (10, ["inspired by"]),
        ],
    },
    {
        "category": "Illegal Products and Services",
        "subcategory": "Hate Speech Products",
        "supportability": "not_supportable",
        "signal_ids": ["HATE-001"],
        "regulation": "BRAM; GBPP; platform policies; hate crime laws",
        "keywords": [
            (70, ["white supremac", "nazi merchandise", "hate group", "racist merchandise",
                   "white power", "antisemit",
                   # from HATE-001
                   "neo-nazi", "racial supremacy", "ethnic cleansing",
                   "supremacist merchandise", "swastika merchandise",
                   "hate symbol", "violence against", "death to", "kill all"]),
        ],
    },
    # ── Marijuana and CBD (enriched from CBD-001, CBD-002) ─────────────────
    {
        "category": "Marijuana and Related Businesses",
        "subcategory": "Marijuana / CBD Products",
        "supportability": "not_supportable",
        "signal_ids": ["CBD-001", "CBD-002"],
        "regulation": "FDCA; FDA enforcement; EU Novel Food Regulation; state laws; DEA (THCO)",
        "keywords": [
            (70, ["marijuana dispensary", "cannabis dispensary", "buy weed",
                   "thc product", "cbd oil", "cbd gummies", "hemp extract",
                   "cannabis edible", "weed delivery",
                   "packman vape", "packman cart", "packman disposable",
                   "runtz strain", "gelato strain", "og kush", "weed strain",
                   "exotic strain", "cannabis strain", "thca flower",
                   "delta-8", "delta 8 thc", "delta-9",
                   "420 friendly", "smoke shop", "head shop",
                   "pre-roll", "live resin", "shatter", "wax concentrate",
                   "cannabis brand", "cannabis store", "cannabis shop",
                   # from CBD-001/002
                   "cbd for anxiety", "cbd for pain", "cbd for sleep",
                   "cbd cures", "cbd heals", "cbd treats",
                   "cbd supplement", "cbd capsules", "cbd medical",
                   "thco", "thc-o", "thc acetate", "delta-8 thco",
                   "legal thc", "hemp-derived thc"]),
            (40, ["marijuana", "cannabis", "cbd", "thc", "hemp oil", "dispensary",
                   "zaza", "exotic weed", "gas strain", "loud pack",
                   "packman", "cookies strain", "jungle boys",
                   "cannabidiol", "d8", "delta8"]),
            (10, ["weed", "stoner", "kush", "dank", "hemp flower"]),
        ],
    },
    # ── Weapons (enriched from GEN-002) ───────────────────────────────────
    {
        "category": "Weapons, Ammunition, and Related Products",
        "subcategory": "Firearms / Ammunition / Weapon Parts",
        "supportability": "not_supportable",
        "signal_ids": ["GEN-002"],
        "regulation": "Gun Control Act; state firearm laws; ATF regulations",
        "keywords": [
            (80, ["gun shop", "buy firearm", "buy gun", "ammunition store",
                   "gun dealer", "ar-15", "rifle for sale", "handgun for sale",
                   "ammo shop", "silencer", "suppressor", "bump stock",
                   "3d printed gun", "ghost gun",
                   # from GEN-002
                   "80% lower", "80 percent lower", "unfinished receiver",
                   "polymer 80", "unserialized firearm", "liberator pistol",
                   "ghost gun kit", "untraceable firearm"]),
            (50, ["firearm", "ammunition", "weapon shop", "gun store",
                   "tactical gear", "holster",
                   "no background check", "80% ar lower"]),
        ],
    },
    {
        "category": "Weapons, Ammunition, and Related Products",
        "subcategory": "Knives / Martial Arts Weapons",
        "supportability": "restricted",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (40, ["switchblade", "butterfly knife", "throwing knife", "machete shop",
                   "nunchaku", "brass knuckle", "combat knife"]),
        ],
    },
    # ── Tobacco (enriched from VAPE-001, VAPE-002) ─────────────────────────
    {
        "category": "Tobacco and Nicotine Products",
        "subcategory": "Cigarettes / Vape / E-cigarettes",
        "supportability": "restricted",
        "signal_ids": ["VAPE-001", "VAPE-002"],
        "regulation": "PACT Act; FDA deeming rule; state tobacco laws",
        "keywords": [
            (60, ["vape shop", "e-cigarette", "cigarette shop", "tobacco shop",
                   "nicotine delivery", "hookah shop", "vape juice", "e-liquid",
                   # from VAPE-001/002
                   "juul", "iqos", "disposable vape", "vape pen",
                   "electronic nicotine delivery", "ends",
                   "pod system", "mod kit", "sub-ohm",
                   "nicotine salt", "nic salt", "nicotine pouch",
                   "snus", "chewing tobacco", "snuff",
                   "flavored vape", "vape flavor"]),
            (15, ["vape", "vaping", "e-cig", "nicotine",
                   "e-juice", "ecig", "atomizer", "cartomizer",
                   "clearomizer", "drip tip"]),
            (10, ["coil", "cartridge"]),
        ],
    },
    # ── Unfair / Deceptive (enriched from SCAM-001, GEN-001) ──────────────
    {
        "category": "Unfair, Deceptive, or Abusive Practices",
        "subcategory": "Get Rich Quick / Fake Guarantees / MLM",
        "supportability": "not_supportable",
        "signal_ids": ["GEN-001", "FRAUD-001"],
        "regulation": "FTC Act",
        "keywords": [
            (70, ["get rich quick", "guaranteed income", "make money fast",
                   "guaranteed return", "binary option signal", "forex signal",
                   "pyramid scheme", "mlm opportunity", "network marketing opportunity",
                   # from GEN-001
                   "miracle", "secret formula", "guaranteed results",
                   "100% guaranteed", "too good to be true",
                   # from FRAUD-001: crypto scams
                   "guaranteed crypto returns", "double your bitcoin",
                   "fake cryptocurrency", "crypto giveaway"]),
            (40, ["passive income secret", "money system", "autopilot income",
                   "100% money back guarantee", "risk-free investment",
                   # from GEN-001
                   "as seen on shark tank", "as seen on tv", "celebrity endorsed",
                   "limited crypto offer", "exclusive crypto opportunity"]),
        ],
    },
    # ── Negative Option (enriched from NOB-001..003) ──────────────────────
    {
        "category": "Unfair, Deceptive, or Abusive Practices",
        "subcategory": "Negative Option Marketing / Hidden Subscriptions",
        "supportability": "not_supportable",
        "signal_ids": ["NOB-001", "NOB-002", "NOB-003"],
        "regulation": "FTC Negative Option Rule; Restore Online Shoppers' Confidence Act",
        "keywords": [
            (60, ["free trial auto-renew", "negative option", "hidden subscription",
                   "auto-billed", "pre-checked subscription",
                   # from NOB-001
                   "continuity marketing", "nutraceutical rebill",
                   "subscription trap", "automatic enrollment",
                   "negative option membership",
                   # from NOB-002: job placement scams
                   "guaranteed job placement", "executive job placement",
                   "100% interview rate",
                   # from NOB-003: ESA letter scams
                   "emotional support animal certificate", "esa certificate online",
                   "esa letter", "emotional support animal id"]),
            (30, ["free trial", "auto-renewal", "cancel anytime",
                   "hidden fee", "recurring charge",
                   "pay to access job listings", "cv repair"]),
        ],
    },
    # ── Multi-Level Marketing ─────────────────────────────────────────────
    {
        "category": "Multi-Level Marketing",
        "subcategory": "MLM / Network Marketing",
        "supportability": "restricted",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (50, ["multi-level marketing", "mlm", "network marketing", "downline",
                   "recruit distributor", "team building opportunity"]),
        ],
    },
    # ── Insurance ─────────────────────────────────────────────────────────
    {
        "category": "Insurance",
        "subcategory": "Insurance Products",
        "supportability": "restricted",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (40, ["insurance plan", "insurance broker", "buy insurance",
                   "health insurance", "life insurance", "auto insurance"]),
        ],
    },
    # ── Government Services ───────────────────────────────────────────────
    {
        "category": "Government Products and Services",
        "subcategory": "Unauthorized Government Services / Visa Scams",
        "supportability": "not_supportable",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (60, ["visa processing", "guaranteed visa", "passport service",
                   "government form service", "expedited visa",
                   "visa application", "visa path", "visa assist",
                   "visa agent", "visa consultant"]),
            (30, ["visa service", "immigration service", "travel visa"]),
        ],
    },
    # ── Identity Services ─────────────────────────────────────────────────
    {
        "category": "Identity Related Services",
        "subcategory": "Identity Theft Protection / Monitoring",
        "supportability": "not_supportable",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (50, ["identity theft protection", "identity monitoring",
                   "credit monitoring service", "identity recovery"]),
        ],
    },
    # ── Mortgage ──────────────────────────────────────────────────────────
    {
        "category": "Mortgage Consulting",
        "subcategory": "Predatory Mortgage Services",
        "supportability": "not_supportable",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (60, ["stop foreclosure", "mortgage relief", "mortgage reduction",
                   "loan modification guaranteed"]),
        ],
    },

    # ══════════════════════════════════════════════════════════════════════
    # NEW RULES — from compliance_signal_library.json (categories not
    # previously covered by Stripe policy rules)
    # ══════════════════════════════════════════════════════════════════════

    # ── Cosmetics as Drugs (from COSM-001, COSM-002) ─────────────────────
    {
        "category": "Cosmetics Crossing Into Drug Territory",
        "subcategory": "Cosmetics with Drug Claims / Rx Ingredients",
        "supportability": "restricted",
        "signal_ids": ["COSM-001", "COSM-002"],
        "regulation": "FDCA; FDA cosmetic vs drug distinction; EU Cosmetics Regulation",
        "keywords": [
            (60, [# COSM-001: cosmetics with drug claims
                  "anti-wrinkle", "repairs dna", "changes skin structure",
                  "cosmetic that cures", "cosmetic that treats",
                  "medically proven",
                  # COSM-002: foreign cosmetics with Rx ingredients
                  "clobetasol cream", "betamethasone cream", "dermovate",
                  "bimatoprost eye drops", "eyelash growth serum",
                  "eyelash lengthener", "eyelash serum",
                  "civic cream", "elopro", "janet cream", "kloderma",
                  "h20 jours", "l'abidjanaise"]),
            (30, ["skin lightening", "skin bleaching", "whitening cream",
                  "fda cleared cosmetic", "imported skin cream",
                  "foreign cosmetic cream"]),
        ],
    },
    # ── Psychoactive Substances (from PSY-001, PSY-002, PSY-003) ──────────
    {
        "category": "Psychoactive Substances",
        "subcategory": "Novel Psychoactive / Synthetic / Ethnobotanical",
        "supportability": "not_supportable",
        "signal_ids": ["PSY-001", "PSY-002", "PSY-003"],
        "regulation": "FDCA; DEA CSA; Synthetic Drug Abuse Prevention Act; Federal Analogue Act; state laws",
        "keywords": [
            (80, [# PSY-001: synthetic cathinones / novel psychoactive
                  "bath salts", "synthetic cathinone", "mephedrone", "methylone",
                  "mdpv", "alpha-pvp", "flakka", "cloud 9", "spice",
                  "synthetic cannabinoid", "k2", "diablo",
                  "phenibut", "tianeptine", "etizolam",
                  "ah-7921", "novel psychoactive",
                  # PSY-002: ethnobotanical / plant-based
                  "ayahuasca", "ibogaine", "iboga", "salvia divinorum",
                  "diviner's sage", "dimethyltryptamine", "dmt",
                  "san pedro cactus", "peyote", "mescaline",
                  "amanita muscaria",
                  # PSY-003: kratom
                  "7-hydroxymitragynine", "mitragynine"]),
            (50, ["kratom capsule", "kratom powder", "kratom extract",
                  "maeng da kratom", "kratom tea", "kratom for opioid",
                  "khat", "qat", "coca leaf",
                  "nitrous oxide", "alkyl nitrites", "poppers",
                  "magic mushroom spores", "psilocybin spore",
                  "ethnobotanical"]),
            (15, ["research chemical", "legal high",
                  "herbal incense", "phone screen cleaner",
                  "craft supplies poppy"]),
        ],
    },
    # ── Transaction Laundering (from TL-001, TL-002) ──────────────────────
    {
        "category": "Transaction Laundering",
        "subcategory": "Front Websites / Undisclosed Merchants / Factoring",
        "supportability": "not_supportable",
        "signal_ids": ["TL-001", "TL-002"],
        "regulation": "BRAM; GBPP; BSA/AML; 18 USC 1956",
        "keywords": [
            (80, ["transaction laundering", "payment laundering",
                  "shell merchant", "front website", "payment front",
                  "undisclosed merchant", "undisclosed sub-merchant",
                  "factoring transaction"]),
            (50, ["innocuous storefront", "passive income merchant",
                  "ibo scheme", "bank page",
                  "independent business owner opportunity"]),
        ],
    },
    # ── Marketplaces / Aggregators / Payment Facilitators ─────────────────
    {
        "category": "Marketplaces and Aggregators",
        "subcategory": "Marketplace / Multi-Vendor Platform",
        "supportability": "restricted",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (70, ["marketplace", "multi-vendor", "multi vendor", "seller marketplace",
                   "vendor marketplace", "sell on our platform",
                   "become a seller", "list your products", "sell your items",
                   "sell your stuff", "peer-to-peer marketplace", "p2p marketplace",
                   "buyer and seller", "connect buyers and sellers",
                   "two-sided marketplace", "c2c marketplace",
                   "secondhand marketplace", "resale marketplace",
                   "flea market online", "classified ads platform"]),
            (50, ["buy and sell", "selling platform", "list items for sale",
                   "create a listing", "post your listing", "sell your products",
                   "open a shop", "vendor store", "seller dashboard",
                   "seller account", "multi-seller", "third-party sellers",
                   "sell those forgotten treasures", "find what the market will bear"]),
            (5, ["selling", "for sale", "shop", "store", "listing"]),
        ],
    },
    {
        "category": "Marketplaces and Aggregators",
        "subcategory": "Aggregator / Payment Facilitator",
        "supportability": "restricted",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (70, ["payment facilitator", "payfac", "payment aggregator",
                   "sub-merchant", "sub merchant", "merchant aggregation",
                   "payment service provider", "collect payments on behalf",
                   "accept payments for others", "process payments for sellers"]),
            (50, ["split payments", "payout to sellers", "seller payouts",
                   "vendor payouts", "disbursement to vendors",
                   "payment splitting", "escrow service"]),
        ],
    },
    # ── No-Value-Added Services (from NVA-001) ────────────────────────────
    {
        "category": "No-Value-Added Services",
        "subcategory": "Government Form Resellers / Fee Markup",
        "supportability": "not_supportable",
        "signal_ids": ["NVA-001"],
        "regulation": "FTC Act; state consumer protection",
        "keywords": [
            (60, ["esta application", "esta fee", "esta processing",
                  "dmv renewal", "dmv services online",
                  "driver's license renewal service",
                  "fishing license service", "hunting license service",
                  "travel authorization fee", "visa processing fee",
                  "government form assistance",
                  "immigration service fee", "public records access fee"]),
            (30, ["business incorporation service", "police records",
                  "official esta application", "expedited esta processing",
                  "we submit your application"]),
        ],
    },
    # ── Scam and Fraud (from FRAUD-001, FRAUD-002, FRAUD-003) ─────────────
    {
        "category": "Scam and Fraud",
        "subcategory": "CPN / Charity Fraud / Crypto Scams",
        "supportability": "not_supportable",
        "signal_ids": ["FRAUD-001", "FRAUD-002", "FRAUD-003"],
        "regulation": "18 USC 1028 (identity fraud); SSA Act; FTC Act; state charity fraud laws",
        "keywords": [
            (80, [# FRAUD-003: CPN scams
                  "cpn", "credit privacy number", "credit profile number",
                  "new credit file", "clean credit slate",
                  "secondary number for credit",
                  # FRAUD-002: charity fraud
                  "fake charity", "charity fraud", "donation scam",
                  "fake fundraiser", "disaster relief scam"]),
            (50, ["covid donation", "emergency donation needed",
                  "click to donate", "official relief fund",
                  # FRAUD-001: crypto investment scams
                  "fake cryptocurrency", "crypto investment",
                  "guaranteed crypto returns",
                  "loan stacking", "straw borrower"]),
        ],
    },
    # ── COVID / PPE Fraud (from COVID-001) ────────────────────────────────
    {
        "category": "COVID / PPE Compliance",
        "subcategory": "Fraudulent COVID Cures / Counterfeit PPE",
        "supportability": "not_supportable",
        "signal_ids": ["COVID-001"],
        "regulation": "FDCA; FTC Act; FDA enforcement",
        "keywords": [
            (80, ["covid cure", "covid treatment", "covid-19 remedy",
                  "kills coronavirus", "treats covid-19",
                  "prevents covid", "covid supplement cure",
                  "fake n95", "counterfeit mask", "non-certified mask"]),
            (40, ["#1 covid-19 remedy", "fda approved covid cure",
                  "cures covid", "treats coronavirus", "prevents covid-19"]),
        ],
    },
    # ── Security / Cloaking (from SEC-001) ────────────────────────────────
    {
        "category": "Security / Cloaking",
        "subcategory": "Website Hijacking / Cloaking / Typosquatting",
        "supportability": "not_supportable",
        "signal_ids": ["SEC-001"],
        "regulation": "ICANN policies; BRAM; GBPP",
        "keywords": [
            (60, ["cloaking", "typosquatting", "website hijacking",
                  "content injection", "doorway page", "pharma hack",
                  "hidden redirect", "geo-targeting"]),
        ],
    },
    # ── Illegal Wildlife Trade (from WILD-001) ────────────────────────────
    {
        "category": "Illegal Wildlife Trade",
        "subcategory": "Protected Species / CITES Violations",
        "supportability": "not_supportable",
        "signal_ids": ["WILD-001"],
        "regulation": "CITES; Lacey Act; FATF recommendations; BRAM; GBPP",
        "keywords": [
            (80, ["ivory", "rhino horn", "tiger bone", "shark fin",
                  "endangered species", "cites protected",
                  "illegal wildlife", "poached", "bushmeat"]),
            (40, ["wildlife trade", "exotic animal", "protected species",
                  "ivory for sale", "endangered animal products"]),
        ],
    },
    # ── IPTV Piracy (from IPR-003, TL-002) ───────────────────────────────
    {
        "category": "IP Infringement",
        "subcategory": "IPTV Piracy / Illegal Streaming",
        "supportability": "not_supportable",
        "signal_ids": ["IPR-003", "TL-002"],
        "regulation": "DMCA; Copyright Act; EU Copyright Directive; BRAM",
        "keywords": [
            (80, ["iptv", "internet protocol television",
                  "1000+ channels", "100+ channels", "all channels included",
                  "premium channels", "all sports channels",
                  "cut the cord", "cut cable", "replace cable",
                  "cracked software", "cyberlocker"]),
            (40, ["pay-per-view subscription", "download movies",
                  "download software", "hbo sports included",
                  "premium iptv subscription"]),
        ],
    },
    # ── Psychic / Spiritual / Occult Services ─────────────────────────────
    {
        "category": "Unfair, Deceptive, or Abusive Practices",
        "subcategory": "Psychic / Spiritual / Occult Services",
        "supportability": "restricted",
        "signal_ids": [],
        "regulation": "FTC Act; state consumer protection",
        "keywords": [
            (60, ["psychic reading", "tarot reading", "fortune telling",
                   "spell casting", "love spell", "money spell", "protection spell",
                   "spiritual healer", "voodoo", "hoodoo", "rootwork",
                   "curse removal", "hex removal", "black magic",
                   "palm reading", "clairvoyant", "medium reading",
                   "astrology reading", "natal chart reading",
                   "spiritual consultation", "energy healing",
                   "botanica", "spiritual shop", "occult shop",
                   "witchcraft supplies", "ritual candle", "spell kit"]),
            (25, ["tarot", "psychic", "spell", "divination", "astrology",
                   "horoscope", "numerology", "crystal healing",
                   "chakra healing", "reiki session", "aura reading"]),
            (10, ["spiritual", "mystic", "oracle"]),
        ],
    },
    # ── Investment / Trading Signals ──────────────────────────────────────
    {
        "category": "Financial Services",
        "subcategory": "Trading Signals / Investment Advice",
        "supportability": "restricted",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (60, ["trading signal", "forex signal", "stock signal",
                   "stock picks", "trade alert", "trading bot",
                   "copy trading", "social trading", "trading community",
                   "stock tip service", "insider tips", "market signal"]),
            (30, ["trading course", "learn to trade", "trading mentor",
                   "day trading", "swing trading", "options trading",
                   "trading education", "trading academy"]),
        ],
    },
    # ── Digital Banking / Money Services (from confirmed violations) ──────
    {
        "category": "Financial Services",
        "subcategory": "Digital Banking / Money Services / P2P Payments",
        "supportability": "not_enabled",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (70, ["digital banking", "neobank", "digital bank",
                   "peer-to-peer payment", "p2p payment", "send money",
                   "money transfer", "instant transfer", "global transfer",
                   "personal banking", "banking platform"]),
            (50, ["peer-to-peer", "tipping platform", "digital tipping",
                   "seamless payment", "instant payment", "pay friends",
                   "fintech"]),
            (15, ["transfer", "payout"]),
        ],
    },
    # ── Games of Skill / Virtual Economies (from confirmed violations) ────
    {
        "category": "Gambling and Games of Skill",
        "subcategory": "Virtual Casino / Loot Box / Cash Prize Games",
        "supportability": "not_supportable",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (70, ["virtual casino", "virtual token", "virtual chip",
                   "earn cash", "cash prize", "win prizes",
                   "real-money challenge", "play for real money",
                   "buy coins", "purchase coin", "coin package",
                   "in-game currency purchase", "top up balance",
                   "loot box", "pack opening", "gacha",
                   "fantasy football", "fantasy baseball",
                   "fantasy sports", "dynasty fantasy",
                   "daily fantasy", "picks feed"]),
            (40, ["minigame", "leaderboard cash", "coin shop",
                   "virtual currency", "game token", "play to earn",
                   "high-stakes", "bet settlement",
                   "auction intelligence", "pick em",
                   "sports pick"]),
        ],
    },
    # ── Lottery / Raffle / Giveaway ───────────────────────────────────────
    {
        "category": "Gambling and Games of Skill",
        "subcategory": "Lottery / Raffle / Giveaway with Purchase",
        "supportability": "not_supportable",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (70, ["raffle platform", "raffle ticket", "luxury raffle",
                   "giveaway platform", "ticket purchasing",
                   "luxury giveaway", "premium giveaway",
                   "win a prize", "prize draw", "lucky draw"]),
            (15, ["raffle", "giveaway", "sweepstakes", "ticket draw"]),
        ],
    },
    # ── Sexually Oriented Dating (expanded from confirmed violations) ─────
    {
        "category": "Adult Content, Products, and Services",
        "subcategory": "Dating Apps / Matchmaking / Companionship",
        "supportability": "not_supportable",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (70, ["dating app", "dating platform", "dating experience",
                   "matchmaking", "meet singles", "find matches",
                   "dating service", "companionship", "paid companion",
                   "companion connect", "provocative connection",
                   "adult dating", "gay dating", "hookup",
                   "tinder like", "swipe match"]),
            (40, ["discover matches", "dating", "connection app",
                   "meet people", "singles app", "match app",
                   "spark match", "speed dating"]),
        ],
    },
    # ── Charitable Donations (restricted — needs KYC) ─────────────────────
    {
        "category": "Fundraising",
        "subcategory": "Standalone Donations / Charity Platform",
        "supportability": "restricted",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (50, ["donation app", "donation platform", "fundraiser platform",
                   "online donation", "accept donations", "give back",
                   "fundraising platform", "charity app",
                   "easiest fundraiser", "payments for donations",
                   "collect donations"]),
            (15, ["fundraiser", "fundraising", "raise funds",
                   "donation page", "give now", "donations"]),
        ],
    },
    # ── Dark Web / Hacking Tools ──────────────────────────────────────────
    {
        "category": "Illegal Products and Services",
        "subcategory": "Dark Web Tools / Hacking Services",
        "supportability": "not_supportable",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (80, ["darkweb", "dark web", "darknet", "hacking tool",
                   "exploit kit", "malware", "ransomware",
                   "keylogger", "phishing kit", "ddos service",
                   "stolen data", "data breach", "carding"]),
        ],
    },
    # ── IPR / Luxury Dropshipping ─────────────────────────────────────────
    {
        "category": "Illegal Products and Services",
        "subcategory": "Luxury Dropshipping / IPR Risk",
        "supportability": "restricted",
        "signal_ids": [],
        "regulation": "Lanham Act; DMCA",
        "keywords": [
            (50, ["luxury dropshipping", "designer dropship",
                   "premium designer", "designer footwear",
                   "replica luxury", "inspired by designer",
                   "luxury e-commerce"]),
            (25, ["dropshipping store", "dropship store",
                   "high-end electronics", "brand electronics"]),
        ],
    },
    # ── Security Brokers / Investment Funds ────────────────────────────────
    {
        "category": "Financial Services",
        "subcategory": "Investment Fund / Security Broker",
        "supportability": "not_enabled",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (60, ["investment fund", "invest money into",
                   "investor stake", "property acquisition fund",
                   "capital operations", "real estate investor fund",
                   "profit distribution", "security broker"]),
            (30, ["investment platform", "fund manager", "stake percentage",
                   "profit sharing", "investor portal", "funding portal"]),
        ],
    },
    # ── Financial Advisory / Consulting ───────────────────────────────────
    {
        "category": "Financial Services",
        "subcategory": "Financial Consulting / Advisory",
        "supportability": "restricted",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (50, ["financial consulting", "financial advisor",
                   "financial advisory", "wealth management",
                   "build credit", "secure funding",
                   "protect your wealth", "protect their wealth",
                   "financial strategy"]),
            (25, ["financial solutions",
                   "wealth hub", "mastering wealth"]),
        ],
    },
    # ── Freight / Shipping Brokers ────────────────────────────────────────
    {
        "category": "Shipping and Logistics",
        "subcategory": "Freight Broker / Shipping Intermediary",
        "supportability": "restricted",
        "signal_ids": [],
        "regulation": "",
        "keywords": [
            (50, ["freight broker", "freight brokerage", "load management",
                   "freight matching", "carrier matching",
                   "shipping broker", "logistics broker"]),
        ],
    },
    # ── Research Peptides (pharmaceutical evasion pattern) ────────────────
    {
        "category": "Healthcare Products and Services",
        "subcategory": "Research Peptides / Pharmaceutical Evasion",
        "supportability": "restricted",
        "signal_ids": [],
        "regulation": "FDCA; FDA",
        "keywords": [
            (60, ["research peptide", "research compound",
                   "laboratory-grade peptide", "pharmaceutical-grade peptide",
                   "high-purity peptide", "peptide store",
                   "peptide shop", "peptide lab", "peptide platform",
                   "scientific research compound",
                   "precision peptide", "peptide therapies"]),
            (30, ["peptide e-commerce", "peptide checkout",
                   "research chemical store", "lab supply store"]),
        ],
    },
    # ── Counterfeit Brands / Unauthorized Merchandise (from Stripe IPR Agent Guide) ──
    {
        "category": "Illegal Products and Services",
        "subcategory": "Counterfeit / Fake IDs / Human Trafficking",
        "supportability": "not_supportable",
        "signal_ids": ["IPR-BRANDS"],
        "regulation": "BRAM; GBPP; Lanham Act; DMCA",
        "keywords": [
            (90, ["replica", "knock-off", "imitation", "faux designer",
                   "mirror image product", "first copy", "aaa quality replica"]),
            (20, [
                   "chanel", "gucci", "louis vuitton", "prada", "dior",
                   "ysl", "balenciaga", "hermes", "cartier", "tiffany",
                   "burberry", "fendi", "coach", "michael kors", "versace",
                   "moncler", "canada goose", "rolex", "omega", "patek philippe",
                   "audemars piguet", "hublot",
                   "nike", "adidas", "puma", "supreme", "off-white", "yeezy",
                   "under armour", "north face", "reebok", "new balance",
                   "converse", "vans", "timberland", "birkenstock",
                   "dr. martens", "skechers", "ray-ban", "oakley",
                   "apple", "samsung", "sony", "nvidia", "beats by dre",
                   "bose", "canon", "nikon", "dell",
                   "mac cosmetics", "huda beauty", "olaplex", "la mer",
                   "charlotte tilbury", "urban decay",
                   ]),
        ],
        "negative_keywords": [
            (-40, ["anti-counterfeit", "verify authentic", "authentication",
                    "anti-fraud", "secondhand", "vintage", "consignment",
                    "refurbished", "fan art", "education", "tutorial",
                    "comparison", "review site", "resale marketplace"]),
        ],
    },
    {
        "category": "Illegal Products and Services",
        "subcategory": "Counterfeit / Fake IDs / Human Trafficking",
        "supportability": "not_supportable",
        "signal_ids": ["IPR-MERCH"],
        "regulation": "BRAM; GBPP; Lanham Act; DMCA",
        "keywords": [
            (15, ["disney", "marvel", "dc comics", "star wars",
                   "harry potter", "pokemon", "hello kitty", "barbie",
                   "peppa pig", "paw patrol", "lego", "nintendo",
                   "minecraft", "fortnite", "roblox",
                   "nfl", "nba", "mlb", "nhl", "fifa", "uefa",
                   "premier league", "manchester united", "barcelona",
                   "real madrid", "liverpool", "chelsea", "arsenal",
                   "juventus", "bayern munich", "psg",
                   ]),
            (30, ["unauthorized merchandise", "unofficial jersey",
                   "unofficial merchandise", "team jersey replica",
                   "bootleg", "unlicensed merchandise"]),
        ],
        "negative_keywords": [
            (-40, ["fan art", "original art", "handmade", "bespoke",
                    "education", "tutorial", "review", "news", "blog"]),
        ],
    },
]

# ── Negative keywords per rule — reduce score when context indicates benign use ──
# Keyed by (category, subcategory) -> list of (weight_reduction, [keywords])
_NEGATIVE_KEYWORDS: dict[tuple[str, str], list[tuple[int, list[str]]]] = {
    ("Financial Services", "Trading Signals / Investment Advice"): [
        (-30, ["education", "course", "tutorial", "learn", "academy",
                "guide", "comparison", "simulator", "paper trading"]),
    ],
    ("Financial Services", "Financial Consulting / Advisory"): [
        (-30, ["education", "course", "tutorial", "learn", "academy",
                "guide", "calculator", "comparison", "retirement guide"]),
    ],
    ("Financial Services", "Digital Banking / Money Services / P2P Payments"): [
        (-30, ["education", "course", "tutorial", "learn", "budget tracker",
                "expense tracker", "gift card", "bookstore", "e-commerce"]),
    ],
    ("Cryptocurrency Products and Services", "Trade / Exchange / Wallets"): [
        (-30, ["education", "course", "tutorial", "learn", "academy"]),
    ],
    ("Insurance", "Insurance Products"): [
        (-25, ["comparison", "compare", "finder", "guide", "education"]),
    ],
    ("Gambling and Games of Skill", "Betting / Casino / Lottery"): [
        (-30, ["date night", "save slot", "alarm", "productivity",
                "rpg", "role-playing", "adventure game", "puzzle game"]),
    ],
    ("Gambling and Games of Skill", "Lottery / Raffle / Giveaway with Purchase"): [
        (-20, ["loyalty program", "rental", "bike", "reward points"]),
    ],
    ("Illegal Products and Services", "Counterfeit / Fake IDs / Human Trafficking"): [
        (-40, ["anti-counterfeit", "verify authentic", "authentication",
                "anti-fraud", "secondhand", "vintage", "consignment",
                "refurbished", "fan art", "education", "tutorial"]),
    ],
}


@dataclass
class PolicyMatch:
    category: str
    subcategory: str
    supportability: str  # not_supportable | restricted | not_enabled | supportable
    confidence: int  # 0-100
    matched_keywords: list[str] = field(default_factory=list)
    # P&R Index (main hierarchy) — primary id/name, plus all mapped IDs
    p_and_r_id: Optional[int] = None
    p_and_r_name: Optional[str] = None
    p_and_r_ids: list[int] = field(default_factory=list)
    # Compliance signal traceability
    signal_ids: list[str] = field(default_factory=list)
    regulation: str = ""

    @property
    def verdict(self) -> str:
        if self.supportability == "not_supportable":
            return "Not Supportable"
        if self.supportability == "not_enabled":
            return "Not Enabled for Wix"
        if self.supportability == "restricted":
            return "Restricted"
        return "Supportable"

    @property
    def color(self) -> str:
        if self.supportability == "not_supportable":
            return "red"
        if self.supportability in ("not_enabled", "restricted"):
            return "orange"
        return "green"


@dataclass
class ClassificationResult:
    matches: list[PolicyMatch] = field(default_factory=list)
    top_match: Optional[PolicyMatch] = None
    overall_verdict: str = "Likely Supportable"
    overall_color: str = "green"
    confidence: int = 0

    @property
    def is_clean(self) -> bool:
        return not self.matches or self.overall_verdict == "Likely Supportable"


def classify(
    text: str,
    user_description: str = "",
    categories: str = "",
    extra_context: str = "",
) -> ClassificationResult:
    """
    Classify combined text against the Stripe policy taxonomy.
    text: all scraped content, meta tags, entity data, etc.
    user_description: from Base44 app metadata
    categories: from Base44 app metadata (e.g. "Games & Entertainment")
    extra_context: Trino conversation summary and/or description — run through
                   the same keyword matching as scraped content.
    """
    combined = f"{text} {user_description} {categories} {extra_context}".lower()
    combined = re.sub(r"\s+", " ", combined)

    matches: list[PolicyMatch] = []

    for rule in _POLICY:
        total_score = 0
        matched_kw: list[str] = []

        for weight, keywords in rule["keywords"]:
            for kw in keywords:
                if _kw_match(kw, combined):
                    total_score += weight
                    matched_kw.append(kw)

        if total_score > 0:
            cat_key = (rule["category"], rule["subcategory"])
            neg_groups = rule.get("negative_keywords") or []
            neg_groups += _NEGATIVE_KEYWORDS.get(cat_key, [])
            for neg_weight, neg_kws in neg_groups:
                for nk in neg_kws:
                    if _kw_match(nk, combined):
                        total_score += neg_weight  # neg_weight is already negative
            total_score = max(0, total_score)

            conf = min(100, total_score)
            if conf >= 20:
                pr_pairs = _get_p_and_r_for_rule(rule["category"], rule["subcategory"])
                pr_ids = [p[0] for p in pr_pairs]
                pr_id = pr_ids[0] if pr_ids else None
                pr_name = pr_pairs[0][1] if pr_pairs else None
                matches.append(PolicyMatch(
                    category=rule["category"],
                    subcategory=rule["subcategory"],
                    supportability=rule["supportability"],
                    confidence=conf,
                    matched_keywords=matched_kw[:8],
                    p_and_r_id=pr_id,
                    p_and_r_name=pr_name,
                    p_and_r_ids=pr_ids,
                    signal_ids=rule.get("signal_ids", []),
                    regulation=rule.get("regulation", ""),
                ))

    matches.sort(key=lambda m: (-_severity_order(m.supportability), -m.confidence))

    result = ClassificationResult(matches=matches)

    if matches:
        result.top_match = matches[0]
        top = matches[0]

        if top.supportability == "not_supportable" and top.confidence >= 50:
            result.overall_verdict = "Not Supportable"
            result.overall_color = "red"
            result.confidence = top.confidence
        elif top.supportability == "not_supportable" and top.confidence >= 20:
            result.overall_verdict = "Likely Not Supportable — Review"
            result.overall_color = "red"
            result.confidence = top.confidence
        elif top.supportability == "not_enabled" and top.confidence >= 40:
            result.overall_verdict = "Not Enabled for Wix"
            result.overall_color = "orange"
            result.confidence = top.confidence
        elif top.supportability in ("not_enabled", "restricted") and top.confidence >= 20:
            result.overall_verdict = "Restricted — Review"
            result.overall_color = "orange"
            result.confidence = top.confidence
        else:
            result.overall_verdict = "Likely Supportable"
            result.overall_color = "green"
            result.confidence = max(0, 100 - top.confidence)
    else:
        result.overall_verdict = "Likely Supportable"
        result.overall_color = "green"
        result.confidence = 40  # no keyword flags — low-moderate; needs richer signals

    return result


def _severity_order(s: str) -> int:
    return {"not_supportable": 3, "not_enabled": 2, "restricted": 1, "supportable": 0}.get(s, 0)
