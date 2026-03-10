"""
Rule-based classifier: map scraped app content to Stripe policy categories.

Each category from the Stripe Supportability Handling Guide has:
  - keywords/phrases that signal the category
  - supportability level (not_supportable / restricted / not_enabled_for_wix / supportable)
  - confidence thresholds

P&R Index (Prohibited and Restricted) is the main hierarchy — all matches map to P&R IDs.
Mapping: data/classifier_rule_to_p_and_r.json (from data/stripe_policy_to_p_and_r_index.xlsx).

Returns structured result per app with matched categories ranked by confidence.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import re

# Lazy import to avoid circular deps
def _get_p_and_r_for_rule(category: str, subcategory: str) -> list[tuple[int, str]]:
    from uw_app.p_and_r import get_p_and_r_for_rule
    return get_p_and_r_for_rule(category, subcategory)

# ── Policy taxonomy ────────────────────────────────────────────────────────────
# Each entry: (category, subcategory, supportability, keyword_groups)
# keyword_groups: list of (weight, [keywords]) — weight is how much a single hit counts
# A match means: the keyword appears in the lowered combined text.
# supportability: "not_supportable" | "restricted" | "not_enabled" | "supportable"

_POLICY: list[dict] = [
    # ── Adult Content ──────────────────────────────────────────────────────
    {
        "category": "Adult Content, Products, and Services",
        "subcategory": "Pornographic Imagery",
        "supportability": "not_supportable",
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
        "keywords": [
            (50, ["wine shop", "beer delivery", "spirits store", "liquor store", "alcohol delivery",
                   "craft brewery", "wine cellar", "distillery"]),
            (20, ["wine", "beer", "whiskey", "vodka", "rum", "tequila", "cocktail"]),
        ],
    },
    # ── Content Creation (Not enabled for Wix) ─────────────────────────────
    {
        "category": "Content Creation",
        "subcategory": "Creator Platform / Tips / Digital Goods",
        "supportability": "not_enabled",
        "keywords": [
            (60, ["content creator platform", "creator monetization", "tip creator",
                   "patreon clone", "fan subscription", "creator economy"]),
            (25, ["tip jar", "creator", "subscriber content", "exclusive content"]),
        ],
    },
    # ── Cryptocurrency ─────────────────────────────────────────────────────
    {
        "category": "Cryptocurrency Products and Services",
        "subcategory": "Trade / Exchange / Wallets",
        "supportability": "not_enabled",
        "keywords": [
            (80, ["crypto exchange", "bitcoin exchange", "crypto trading", "crypto wallet",
                   "buy bitcoin", "buy crypto", "sell bitcoin", "token sale", "ico launch",
                   "initial coin offering", "crypto staking", "yield farm",
                   "crypto lending", "defi platform", "nft marketplace",
                   "virtual wallet", "stored value", "digital wallet", "e-wallet",
                   "in-app currency", "in-app coins", "virtual coins", "virtual currency"]),
            (50, ["cryptocurrency", "blockchain", "ethereum", "solana", "web3",
                   "decentralized finance", "defi", "nft", "crypto mining"]),
            (20, ["bitcoin", "crypto", "token", "wallet"]),
        ],
    },
    # ── Debt and Credit Services ──────────────────────────────────────────
    {
        "category": "Debt and Credit Services",
        "subcategory": "Debt Collection / Relief / Credit Repair",
        "supportability": "not_supportable",
        "keywords": [
            (70, ["debt collection", "debt relief", "debt settlement", "debt consolidation",
                   "credit repair", "credit monitoring", "wipe out your debt",
                   "stop foreclosure", "debt reduction", "collect debt", "credi collect",
                   "credit collect"]),
            (40, ["debt collector", "past due receivable", "credit score fix",
                   "consolidate your bills", "collect now", "collection agency"]),
        ],
    },
    # ── Financial Services (Not enabled for Wix) ──────────────────────────
    {
        "category": "Financial Services",
        "subcategory": "Loans / BNPL / Brokerage / Neobank / P2P",
        "supportability": "not_enabled",
        "keywords": [
            (70, ["payday loan", "cash advance", "buy now pay later", "bnpl",
                   "stock brokerage", "forex trading", "binary option", "funded trading",
                   "prop firm", "neobank", "peer-to-peer money", "money transfer",
                   "escrow service", "currency exchange"]),
            (40, ["loan service", "lending platform", "investment platform",
                   "money remittance", "send money", "high-yield return",
                   "loan pay", "easy loan", "quick loan", "fast loan", "loan app"]),
            (20, ["invest", "portfolio", "trading signal", "loan"]),
        ],
    },
    {
        "category": "Financial Services",
        "subcategory": "Dropshipping / Resale Consulting",
        "supportability": "not_enabled",
        "keywords": [
            (60, ["dropshipping course", "reselling course", "how to dropship",
                   "dropship consulting", "amazon fba course", "resale profit"]),
            (30, ["dropshipping", "reselling guide", "make money reselling"]),
        ],
    },
    # ── Fundraising ───────────────────────────────────────────────────────
    {
        "category": "Fundraising",
        "subcategory": "Crowdfunding / Donations",
        "supportability": "restricted",
        "keywords": [
            (50, ["crowdfunding", "gofundme", "fundraise", "donate now", "donation page",
                   "charity raffle", "nonprofit donation"]),
            (20, ["donate", "donation", "charity", "nonprofit"]),
        ],
    },
    # ── Gambling ──────────────────────────────────────────────────────────
    {
        "category": "Gambling and Games of Skill",
        "subcategory": "Betting / Casino / Lottery",
        "supportability": "not_supportable",
        "keywords": [
            (80, ["online casino", "sports betting", "place bet", "slot machine",
                   "poker online", "blackjack", "roulette", "lottery ticket",
                   "fantasy sports betting", "fanduel", "draftkings", "betting odds",
                   "gamble", "gambling site", "bingo online", "trend bet"]),
            (50, ["casino", "betting", "wager", "jackpot", "slot", "lottery",
                   "odds", "sportsbook", "place a bet", "speculating on"]),
            (20, ["poker", "roulette", "blackjack", "baccarat", " bet "]),
        ],
    },
    {
        "category": "Gambling and Games of Skill",
        "subcategory": "Games of Skill with Monetary Prizes",
        "supportability": "not_supportable",
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
        "keywords": [
            (70, ["firework", "explosive", "gunpowder", "dynamite", "radioactive material",
                   "toxic chemical", "pesticide", "rocket propellant"]),
        ],
    },
    # ── Healthcare ────────────────────────────────────────────────────────
    {
        "category": "Healthcare Products and Services",
        "subcategory": "Prescription Pharmaceuticals",
        "supportability": "restricted",
        "keywords": [
            (70, ["online pharmacy", "buy prescription", "prescription drug",
                   "viagra", "cialis", "semaglutide", "ozempic", "modafinil",
                   "tirzepatide", "weight loss injection", "peptide injection",
                   "compounded medication", "telehealth prescription"]),
            (40, ["pharmacy", "rx drug", "prescription medication", "telemedicine",
                   "prescriptions", "peptides", "semaglutide injection", "hormone therapy",
                   "medical consultation online", "online doctor"]),
            (20, ["prescription", "peptide"]),
        ],
    },
    {
        "category": "Healthcare Products and Services",
        "subcategory": "Nutraceuticals / Weight Loss / Sexual Enhancement",
        "supportability": "restricted",
        "keywords": [
            (50, ["weight loss pill", "fat burner", "diet pill", "appetite suppressant",
                   "sexual enhancement", "testosterone booster", "libido boost",
                   "erectile dysfunction", "male enhancement",
                   "vitamin subscription", "supplement subscription"]),
            (25, ["weight loss supplement", "metabolism boost", "burn fat",
                   "recurring vitamin", "monthly vitamin", "auto-ship supplement"]),
        ],
    },
    {
        "category": "Healthcare Products and Services",
        "subcategory": "Telehealth / Medical Marketplace",
        "supportability": "restricted",
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
    # ── Illegal Drugs ─────────────────────────────────────────────────────
    {
        "category": "Illegal Drugs and Related Products",
        "subcategory": "Illegal Substances / Paraphernalia",
        "supportability": "not_supportable",
        "keywords": [
            (90, ["buy cocaine", "buy heroin", "buy lsd", "buy mdma", "buy ecstasy",
                   "drug dealer", "drug marketplace", "darknet market"]),
            (60, ["drug paraphernalia", "bong shop", "rolling paper shop",
                   "kratom", "kava", "psilocybin", "psychedelic shop"]),
        ],
    },
    # ── Illegal Products and Services ─────────────────────────────────────
    {
        "category": "Illegal Products and Services",
        "subcategory": "Counterfeit / Fake IDs / Human Trafficking",
        "supportability": "not_supportable",
        "keywords": [
            (90, ["fake id", "fake passport", "fake diploma", "counterfeit",
                   "replica luxury", "buy followers", "buy likes",
                   "human trafficking", "child exploitation"]),
            (50, ["fake document", "novelty id", "clone website",
                   "knock-off", "replica designer"]),
        ],
    },
    {
        "category": "Illegal Products and Services",
        "subcategory": "Hate Speech Products",
        "supportability": "not_supportable",
        "keywords": [
            (70, ["white supremac", "nazi merchandise", "hate group", "racist merchandise",
                   "white power", "antisemit"]),
        ],
    },
    # ── Marijuana and CBD ─────────────────────────────────────────────────
    {
        "category": "Marijuana and Related Businesses",
        "subcategory": "Marijuana / CBD Products",
        "supportability": "not_supportable",
        "keywords": [
            (70, ["marijuana dispensary", "cannabis dispensary", "buy weed",
                   "thc product", "cbd oil", "cbd gummies", "hemp extract",
                   "cannabis edible", "weed delivery",
                   # street/brand slang — common in Base44 cannabis shops
                   "packman vape", "packman cart", "packman disposable",
                   "runtz strain", "gelato strain", "og kush", "weed strain",
                   "exotic strain", "cannabis strain", "thca flower",
                   "delta-8", "delta 8 thc", "delta-9",
                   "420 friendly", "smoke shop", "head shop",
                   "pre-roll", "live resin", "shatter", "wax concentrate",
                   "cannabis brand", "cannabis store", "cannabis shop"]),
            (40, ["marijuana", "cannabis", "cbd", "thc", "hemp oil", "dispensary",
                   # slang used in app names / descriptions
                   "zaza", "exotic weed", "gas strain", "loud pack",
                   "packman", "cookies strain", "jungle boys"]),
            (20, ["weed", "stoner", "kush", "dank", "hemp flower"]),
        ],
    },
    # ── Weapons ───────────────────────────────────────────────────────────
    {
        "category": "Weapons, Ammunition, and Related Products",
        "subcategory": "Firearms / Ammunition / Weapon Parts",
        "supportability": "not_supportable",
        "keywords": [
            (80, ["gun shop", "buy firearm", "buy gun", "ammunition store",
                   "gun dealer", "ar-15", "rifle for sale", "handgun for sale",
                   "ammo shop", "silencer", "suppressor", "bump stock",
                   "3d printed gun", "ghost gun"]),
            (50, ["firearm", "ammunition", "weapon shop", "gun store",
                   "tactical gear", "holster"]),
        ],
    },
    {
        "category": "Weapons, Ammunition, and Related Products",
        "subcategory": "Knives / Martial Arts Weapons",
        "supportability": "restricted",
        "keywords": [
            (40, ["switchblade", "butterfly knife", "throwing knife", "machete shop",
                   "nunchaku", "brass knuckle", "combat knife"]),
        ],
    },
    # ── Tobacco ───────────────────────────────────────────────────────────
    {
        "category": "Tobacco and Nicotine Products",
        "subcategory": "Cigarettes / Vape / E-cigarettes",
        "supportability": "restricted",
        "keywords": [
            (60, ["vape shop", "e-cigarette", "cigarette shop", "tobacco shop",
                   "nicotine delivery", "hookah shop", "vape juice", "e-liquid"]),
            (25, ["vape", "vaping", "e-cig", "nicotine"]),
        ],
    },
    # ── Unfair / Deceptive Practices ──────────────────────────────────────
    {
        "category": "Unfair, Deceptive, or Abusive Practices",
        "subcategory": "Get Rich Quick / Fake Guarantees / MLM",
        "supportability": "not_supportable",
        "keywords": [
            (70, ["get rich quick", "guaranteed income", "make money fast",
                   "guaranteed return", "binary option signal", "forex signal",
                   "pyramid scheme", "mlm opportunity", "network marketing opportunity"]),
            (40, ["passive income secret", "money system", "autopilot income",
                   "100% money back guarantee", "risk-free investment"]),
        ],
    },
    {
        "category": "Unfair, Deceptive, or Abusive Practices",
        "subcategory": "Negative Option Marketing / Hidden Subscriptions",
        "supportability": "not_supportable",
        "keywords": [
            (50, ["free trial auto-renew", "negative option", "hidden subscription",
                   "auto-billed", "pre-checked subscription"]),
        ],
    },
    # ── Multi-Level Marketing ─────────────────────────────────────────────
    {
        "category": "Multi-Level Marketing",
        "subcategory": "MLM / Network Marketing",
        "supportability": "restricted",
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
        "keywords": [
            (60, ["stop foreclosure", "mortgage relief", "mortgage reduction",
                   "loan modification guaranteed"]),
        ],
    },
]


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
                if kw in combined:
                    total_score += weight
                    matched_kw.append(kw)

        if total_score > 0:
            conf = min(100, total_score)
            # Only report if confidence is meaningful
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
        result.confidence = 70  # no flags found — moderate confidence

    return result


def _severity_order(s: str) -> int:
    return {"not_supportable": 3, "not_enabled": 2, "restricted": 1, "supportable": 0}.get(s, 0)
