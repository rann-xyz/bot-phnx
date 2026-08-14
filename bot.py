"""
🤖 AlphaDrop Bot v18.1 — Fresh-Only + NFT Sources + Persistent Dedup
Changes vs v12:
  - Removed CoinGecko Trending & DexScreener (pure token/price data, not alpha/NFT/airdrop)
  - Real freshness enforcement: items with an expired/past deadline are dropped, not just
    items whose *fetch time* is recent (fixes old Magic Eden listings showing as "NEW")
  - Persistent dedup cache on disk so restarting the bot (common on mobile/UserLAnd) does
    NOT resend the same drops again
  - Tighter, configurable freshness windows (default: only items <=6h old / not-yet-expired)
"""

import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Set, Tuple
from collections import deque, Counter
from email.utils import parsedate_to_datetime
import os
import hashlib
import re
import json

# ==================== ENV ====================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
NOTIFY_CHANNEL_ID = int(os.getenv("NOTIFY_CHANNEL_ID", "0"))
COMMAND_PREFIX = "!"

# --- Freshness knobs (all overridable via env, no code edits needed) ---
# How old (in hours) an item's real publish time can be before it's considered stale.
MAX_ITEM_AGE_HOURS = float(os.getenv("MAX_ITEM_AGE_HOURS", "6"))
# Same, but specifically for NFT RSS sources.
NFT_MAX_AGE_HOURS = float(os.getenv("NFT_MAX_AGE_HOURS", "18"))
# How often the scraper re-checks all sources.
SCRAPE_INTERVAL_MINUTES = int(os.getenv("SCRAPE_INTERVAL_MINUTES", "15"))
# How long (days) a seen item is remembered to avoid re-sending duplicates.
DEDUP_WINDOW_DAYS = int(os.getenv("DEDUP_WINDOW_DAYS", "14"))
# Where the persistent dedup cache is stored on disk (survives bot restarts).
DEDUP_CACHE_PATH = os.getenv("DEDUP_CACHE_PATH", "seen_cache.json")
# Grace window: how far in the past a deadline can be before we call it "expired".
DEADLINE_GRACE_HOURS = float(os.getenv("DEADLINE_GRACE_HOURS", "3"))

HEADERS = {
    "User-Agent": "AlphaDropBot/13.0 (Bot; Fresh-Only; No-Token-Noise; Persistent-Dedup)",
    "Accept": "application/json, application/rss+xml, text/html, text/xml",
}

COLORS = {
    "instant": 0x57F287, "testnet": 0x5865F2, "waitlist": 0xFEE75C,
    "watchlist": 0x00BFFF, "retroactive": 0x9B59B6, "early": 0xFF6B35,
    "nft": 0xE91E63, "general": 0x99AAB5, "header": 0xF1C40F,
    "error": 0xED4245, "success": 0x57F287, "deep": 0xFF6B35,
    "s_tier": 0xFF0000, "a_tier": 0x9B59B6, "b_tier": 0x3498DB,
    "scam": 0x000000,
}

# ==================== ENHANCED KEYWORDS ====================
AIRDROP_KEYWORDS = [
    "airdrop",
    "claim",
    "testnet",
    "devnet",
    "beta",
    "waitlist",
    "whitelist",
    "early access",
    "watchlist",
    "potential",
    "upcoming",
    "retroactive",
    "retro",
    "snapshot",
    "reward early",
    "past users",
    "incentivized",
    "🪂",
    "💰",
    "🎁",
    "alpha",
    "early alpha",
    "stealth launch",
    "just launched",
    "new project",
    "pre-launch",
    "pre seed",
    "seed round",
    "under radar",
    "hidden gem",
    "first users",
    "early adopters",
    "og users",
    "genesis",
    "founding",
    "incentivized testnet",
    "node",
    "validator",
    "galxe",
    "zealy",
    "crew3",
    "guild",
    "layer3",
    "quest",
    "campaign",
    "rewards",
    "points",
    "retrodrop",
    "voyage",
    "odyssey",
    "journey",
    "expedition",
    "register",
    "sign up",
    "join waitlist", "join the waitlist", "join our waitlist",
    "waitlist open", "waitlist now open", "waitlist signup",
    "waitlist registration", "waiting list", "priority access",
    "priority waitlist", "priority access list", "early access list",
    "access list", "access application", "application open",
    "applications open", "apply for access", "request access",
    "request early access", "get on the list", "get on waitlist",
    "reserve your spot", "reserve a spot", "limited access",
    "invite only", "invitation only", "early adopter", "early users",
    "first users", "founding users", "founding members",
    "genesis access", "genesis pass", "launch access", "prelaunch access",

    "apply now",
    "get early",
    "mainnet beta",
    "public testnet",
    "private beta",
    "closed beta",
    "ambassador",
    "influencer",
    "advocate",
    "contributor",
    "early supporter",
    "confirmed airdrop",
    "token confirmed",
    "launching token",
    "tge",
    "token generation",
    "backed by",
    "raised",
    "funding",
    "invested by",
    "supported by",
    "allocation",
    "eligible",
    "distribution",
    "season",
    "epoch",
    "farming",
    "yield",
    "liquidity mining",
    "stake drop",
    "delegate drop",
    "merkle",
    "claim page",
    "vesting",
    "cliff",
    "unlocks",
]

NFT_KEYWORDS = [
    "nft",
    "mint",
    "drop",
    "collection",
    "whitelist",
    "allowlist",
    "presale",
    "free mint",
    "open edition",
    "limited edition",
    "genesis",
    "1/1",
    "pfp",
    "🎨",
    "🖼️",
    "🃏",
    "💎",
    "freemint",
    "airdrop nft",
    "nft claim",
    "new collection",
    "launching soon",
    "minting now",
    "live mint",
    "blur",
    "opensea",
    "magic eden",
    "tensor",
    "nft marketplace",
    "launchpad",
    "premint",
    "mintlist",
    "raffle",
    "wl spot",
    "ordinal",
    "brc20",
    "rune",
    "nft airdrop",
    "holder airdrop",
    "mint pass", "digital collectible", "onchain art", "on-chain art",
    "art drop", "creator drop", "artist drop", "community mint",
    "public mint", "private mint", "minting soon", "mint date",
    "nft launch", "nft launchpad", "nft marketplace", "nft rewards",
    "nft points", "holder rewards", "collector", "season pass",
    "membership pass", "digital pass", "soulbound", "sbt",
    "inscription", "ordinals", "runes", "erc-721", "erc-1155",
    "token gated", "token-gated", "collectors pass", "founder pass",
]

SCAM_KEYWORDS = [
    "send eth",
    "send sol",
    "send bnb",
    "double your",
    "2x your",
    "3x your",
    "guaranteed profit",
    "guaranteed return",
    "100% return",
    "risk free",
    "connect wallet to verify",
    "verify wallet",
    "wallet verification",
    "claim now limited",
    "first come first serve",
    "urgent claim",
    "send 0.1",
    "send 0.5",
    "send 1",
    "get 10x back",
    "send and receive",
    "trust wallet sync",
    "wallet sync",
    "validate wallet",
    "restore wallet",
    "seed phrase",
    "private key",
    "connect to dapp",
    "approve all",
    "you won",
    "congratulations you",
    "selected winner",
    "exclusive winner",
    "limited spots",
    "only 100 spots",
    "hurry up",
    "act fast",
    "last chance",
    "dm to claim",
    "message to claim",
    "contact admin",
    "send fee",
    "gas fee reimbursement",
    "pay gas fee",
    "refundable gas",
]

HIGH_VALUE_KEYWORDS = [
    "confirmed airdrop",
    "token confirmed",
    "launching token",
    "tge",
    "token generation",
    "backed by a16z",
    "backed by paradigm",
    "backed by coinbase",
    "backed by binance",
    "raised $100m",
    "raised $50m",
    "raised $500m",
    "raised $1b",
    "layerzero",
    "zksync",
    "starknet",
    "eigenlayer",
    "hyperliquid",
    "berachain",
    "monad",
    "scroll",
    "linea",
    "base",
    "blast",
    "mantle",
    "mode",
    "manta",
    "celestia",
    "dymension",
    "initia",
    "movement",
    "story",
    "megaeth",
    "anoma",
    "namada",
    "penumbra",
    "ritual",
    "nillion",
    "incentivized testnet",
    "confirmed snapshot",
    "snapshot taken",
    "ambassador program",
    "founding member",
    "genesis user",
    "og role",
    "free mint",
    "allowlist open",
    "whitelist open",
    "early bird",
    "retrodrop confirmed",
    "voyage confirmed",
    "odyssey confirmed",
]

TIER_1_INVESTORS = ["a16z", "paradigm", "coinbase ventures", "binance labs", "sequoia"]
TIER_2_INVESTORS = ["polychain", "multicoin", "jump crypto", "dragonfly", "framework",
                    "delphi digital", "electric capital", "placeholder", "1kx", "spartan"]
TIER_3_INVESTORS = ["hashed", "arrington", "blocktower", "mechanism", "union square",
                    "naval", "balaji", "founders fund", "greylock", "pantera", "draper",
                    "animoca", "maven11", "dao5", "shima capital", "big brain"]

TOP_INVESTORS = TIER_1_INVESTORS + TIER_2_INVESTORS + TIER_3_INVESTORS + [
    "galaxy digital", "huobi ventures", "okx ventures", "kucoin", "amber group",
    "wintermute", "gSR", "amber", "brevan howard", "softbank",
]


# Expanded knowledge signals: NFT, waitlist, access programs, testnets,
# devnets, mainnets, points, quests, campaigns, ecosystem programs,
# grants, funding, builder programs, beta launches, mints, collections,
# digital collectibles, SBTs, token-gated access, holder rewards,
# founding users, genesis access, and prelaunch access.

TARGET_TYPES = {"instant", "testnet", "waitlist", "watchlist", "retroactive", "early", "nft"}
PRIORITY_CHAINS = ["robinhood chain", "robinhoodchain", "robinhood mainnet", "robinhood"]

BLOCKED = [
    "airdropalert.com",
    "blogs.airdropalert.com",
    "t.me",
    "discord.com",
    "discord.gg",
    "youtube.com",
    "youtu.be",
    "github.com",
    "medium.com",
    "reddit.com",
    "tiktok.com",
    "instagram.com",
    "facebook.com",
    "linkedin.com",
]

# ==================== UTILS ====================
def strip_html(text):
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = clean.replace("&nbsp;", " ").replace("&amp;", "&")
    clean = clean.replace("&lt;", "<").replace("&gt;", ">")
    clean = clean.replace('&quot;', '"').replace("&#39;", "'")
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean

def extract_urls(text):
    if not text:
        return []
    pattern = r"https?://[^\s<>'\"]+"
    return re.findall(pattern, text)

def pick_official(desc_html, fallback):
    urls = extract_urls(desc_html)
    for url in urls:
        domain = re.sub(r"^https?://(www\.)?", "", url.lower()).split("/")[0]
        if not any(b in domain for b in BLOCKED):
            return url
    return fallback

def extract_deadline(text):
    t = text.lower()
    patterns = [
        r"\b(\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*(?:\s+\d{4})?)\b",
        r"\b(\d{4}-\d{2}-\d{2})\b",
        r"\b(q[1-4]\s*\d{4})\b",
        r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b",
        r"(?:ends?|deadline|until|before)\s*:?\s*(\d{1,2}\s+\w+\s+\d{4})",
        r"\b(\d+)\s+days?\s+(?:left|remaining)\b",
    ]
    for pat in patterns:
        m = re.search(pat, t)
        if m:
            return m.group(1).strip().title()
    return None

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

def parse_flexible_date(s):
    """Best-effort parse of the messy deadline strings we scrape into a datetime.
    Returns None if it can't confidently parse (better to under-filter than
    wrongly drop something with a fuzzy date like 'Q1 2026')."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    if s.upper() in ("TBA", "N/A", ""):
        return None
    # 1) ISO datetime, e.g. 2025-12-18T16:00:00.000Z
    try:
        iso = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo:
            dt = dt.replace(tzinfo=None)
        return dt
    except Exception:
        pass
    # 2) "18 Dec 2025" / "18 December 2025"
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]{3,})\s*(\d{4})?$", s)
    if m:
        day, mon_str, year = m.group(1), m.group(2).lower()[:3], m.group(3)
        if mon_str in MONTHS:
            year = int(year) if year else datetime.now().year
            try:
                return datetime(year, MONTHS[mon_str], int(day))
            except Exception:
                return None
    # 3) "12/18/2025" or "12/18/25"
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", s)
    if m:
        mo, day, year = m.groups()
        year = int(year) if len(year) == 4 else 2000 + int(year)
        try:
            return datetime(int(year), int(mo), int(day))
        except Exception:
            return None
    return None

def is_deadline_expired(deadline_str, grace_hours=None):
    """True only if we can confidently parse the deadline AND it's already
    passed (beyond the grace window). Unparseable/fuzzy dates ('Q1 2026',
    'TBA') are treated as not-expired so we don't over-filter."""
    if grace_hours is None:
        grace_hours = DEADLINE_GRACE_HOURS
    dt = parse_flexible_date(deadline_str)
    if dt is None:
        return False
    return dt < (datetime.now() - timedelta(hours=grace_hours))

def extract_funding(text):
    m = re.search(r"\$([\d,.]+)\s*[MBKmbk](?:\s*(?:million|billion|m|b))?", text, re.I)
    if m:
        return m.group(0)
    m = re.search(r"raised\s+\$?([\d,.]+\s*[MBK]?)\b", text, re.I)
    if m:
        return m.group(0)
    return None

def extract_investors(text):
    investors = []
    t = text.lower()
    for inv in TOP_INVESTORS:
        if inv in t:
            investors.append(inv.title())
    return investors[:8]

def extract_socials(text):
    socials = {}
    urls = extract_urls(text)
    for url in urls:
        if "twitter.com" in url or "x.com" in url:
            socials["twitter"] = url
        elif "t.me" in url or "telegram" in url:
            socials["telegram"] = url
        elif "discord.gg" in url or "discord.com" in url:
            socials["discord"] = url
        elif "github.com" in url:
            socials["github"] = url
        elif "medium.com" in url:
            socials["medium"] = url
    return socials

def extract_contract(text):
    patterns = [
        r"0x[a-fA-F0-9]{40}",
        r"[A-Za-z0-9]{32,44}",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            addr = m.group(0)
            if len(addr) >= 40:
                return addr[:20] + "..." + addr[-6:]
    return None

def extract_chain(text):
    chains = ["ethereum", "solana", "arbitrum", "optimism", "base", "zksync",
              "starknet", "polygon", "avalanche", "bnb", "cosmos", "sui",
              "aptos", "ton", "near", "monad", "berachain", "scroll", "linea",
              "fuel", "movement", "celestia", "injective", "dymension", "sei",
              "blast", "mantle", "mode", "manta", "karak", "hyperliquid", "story",
              "initia", "penumbra", "nillion", "ritual", "eclipse", "bera",
              "taiko", "kroma", "parallel", "gravity", "nibiru", "initia",
              "megaeth", "anoma", "namada", "dusk", "mina", "robinhood chain", "robinhoodchain", "robinhood",
              "o1labs", "ironfish", "aleo", "aztec", "zircuit"]
    lower = text.lower()
    if any(x in lower for x in PRIORITY_CHAINS):
        found = ["Robinhood Chain"] + [c.title() for c in chains if c in lower and c not in PRIORITY_CHAINS]
    else:
        found = [c.title() for c in chains if c in lower]
    return ", ".join(dict.fromkeys(found[:3])) if found else "N/A"

def detect_type(text):
    t = text.lower()
    if any(x in t for x in ["nft", "mint", "collection", "🎨", "🖼️", "pfp", "freemint", "allowlist", "ordinal", "ordinals", "brc20", "rune", "runes", "mint pass", "digital collectible", "onchain art", "nft launch", "nft marketplace", "sbt"]):
        return "nft"
    if any(x in t for x in ["early alpha", "stealth launch", "just launched", "brand new",
                           "pre-launch", "pre seed", "seed round", "under radar", "hidden gem",
                           "first 100", "genesis users", "founding member", "og users", "early bird",
                           "ambassador program", "early supporter", "contributor program", "ido", "ieo", "launchpad"]):
        return "early"
    if any(x in t for x in ["claim now", "claimable", "live claim", "instant airdrop", "free claim", "merkle", "distribution live"]):
        return "instant"
    if any(x in t for x in ["retroactive", "retro", "reward early", "past users", "snapshot taken",
                             "retrodrop", "voyage", "odyssey", "journey", "expedition", "loyalty program",
                             "season 1", "season 2", "epoch", "phase"]):
        return "retroactive"
    if any(x in t for x in ["testnet", "incentivized testnet", "devnet", "beta", "public testnet",
                             "mainnet beta", "private beta", "closed beta", "node testnet", "validator testnet"]):
        return "testnet"
    if any(x in t for x in ["waitlist", "wait list", "waiting list", "waitlist open", "priority access", "access list", "early access", "early access list", "whitelist", "pre-register",
                             "register now", "sign up", "apply now", "applications open", "request access", "invite only", "get early access", "join list", "get on waitlist", "reserve your spot",
                             "early bird", "founding member", "whitelist spot", "allowlist", "premint"]):
        return "waitlist"
    if any(x in t for x in ["watchlist", "potential airdrop", "rumored", "upcoming", "expected", "no token yet",
                             "token soon", "launching soon", "coming soon", "prepare for", "get ready", "pre-token"]):
        return "watchlist"
    if "airdrop" in t or "🪂" in t:
        return "watchlist"
    return "general"

def detect_difficulty(text):
    t = text.lower()
    if any(x in t for x in ["easy", "simple", "just", "only need", "free", "no cost", "one click", "few clicks", "connect wallet only"]):
        return "Easy"
    if any(x in t for x in ["hard", "difficult", "complex", "run node", "validator", "kyc", "technical", "code", "deploy", "smart contract"]):
        return "Hard"
    return "Medium"

def detect_scam_signals(text):
    t = text.lower()
    reasons = []
    score = 0.0

    for kw in SCAM_KEYWORDS:
        if kw in t:
            reasons.append(f"Scam keyword: '{kw}'")
            score += 0.15

    urls = extract_urls(text)
    suspicious_domains = ["claim-", "airdrop-", "free-", "verify-", "wallet-"]
    for url in urls:
        domain = re.sub(r"^https?://(www\.)?", "", url.lower()).split("/")[0]
        if any(sd in domain for sd in suspicious_domains):
            reasons.append(f"Suspicious domain: {domain}")
            score += 0.2
        lookalikes = {
            "opensea": ["opensea-nft", "opensea-verify", "opensea-claim"],
            "blur": ["blur-io", "blur-claim", "blur-nft"],
            "magiceden": ["magiceden-claim", "magiceden-nft"],
            "uniswap": ["uniswap-claim", "uniswap-airdrop"],
            "1inch": ["1inch-claim", "1inch-airdrop"],
            "layerzero": ["layerzero-claim", "lz-claim"],
        }
        for real, fakes in lookalikes.items():
            if any(fake in domain for fake in fakes):
                reasons.append(f"Lookalike domain (fake {real}): {domain}")
                score += 0.3

    spam_patterns = [
        r"[!?]{3,}",
        r"\b[A-Z]{5,}\b",
        r"(click here|claim here|get here){2,}",
    ]
    for pat in spam_patterns:
        if re.search(pat, t):
            reasons.append("Spam pattern detected")
            score += 0.1

    if any(x in t for x in ["guaranteed", "100% profit", "risk free", "no risk"]):
        reasons.append("Too good to be true promises")
        score += 0.25

    if re.search(r"send\s+\d*\.?\d+\s*(eth|sol|bnb|usdt|usdc)", t):
        reasons.append("Requests sending funds")
        score += 0.4

    is_scam = score >= 0.3
    return is_scam, reasons, min(score, 1.0)

# ==================== ENHANCED RATING SYSTEM ====================
def calculate_rating(item):
    score = 0
    text = (item.get("name", "") + " " + item.get("desc", "")).lower()
    deep = item.get("deep", {})

    # === SCAM PENALTY ===
    is_scam, scam_reasons, scam_score = detect_scam_signals(text)
    if is_scam:
        return 1
    score -= scam_score * 2

    # === FUNDING TIER (0-4 points) ===
    fund_text = item.get("funding", "")
    if fund_text and fund_text != "N/A":
        m = re.search(r"[\$]?([\d,.]+)\s*([MBKmbk]?)", fund_text)
        if m:
            try:
                raw_val = m.group(1).replace(",", "").strip(".")
                if raw_val.count(".") > 1:
                    parts = raw_val.split(".")
                    raw_val = parts[0] + "." + parts[1]
                val = float(raw_val)
                unit = (m.group(2) or "").lower()
                if unit in ["b", "billion"]:
                    val *= 1000
                elif unit in ["k", "thousand"]:
                    val /= 1000
                if val >= 1000:
                    score += 4
                elif val >= 100:
                    score += 3
                elif val >= 10:
                    score += 2
                elif val >= 1:
                    score += 1
            except (ValueError, AttributeError):
                pass

    # === INVESTOR TIER (0-4 points) ===
    investors = deep.get("investors", [])
    inv_score = 0
    for inv in investors:
        inv_l = inv.lower()
        if any(t1 in inv_l for t1 in TIER_1_INVESTORS):
            inv_score += 3
        elif any(t2 in inv_l for t2 in TIER_2_INVESTORS):
            inv_score += 2
        elif any(t3 in inv_l for t3 in TIER_3_INVESTORS):
            inv_score += 1
    score += min(inv_score, 4)

    # === SOCIAL PRESENCE (0-3 points) ===
    socials = deep.get("socials", {})
    score += min(len(socials), 3)

    # === CHAIN TIER (0-2.5 points) ===
    chain = item.get("chain", "")
    if chain:
        chain_l = chain.lower()
        if any(c in chain_l for c in PRIORITY_CHAINS):
            score += 2.0
        elif any(c in chain_l for c in ["ethereum", "solana"]):
            score += 2
        elif any(c in chain_l for c in ["arbitrum", "optimism", "base", "zksync", "starknet", "polygon", "avalanche", "bnb", "sui", "aptos", "ton", "monad", "berachain"]):
            score += 1.5
        elif any(c in chain_l for c in ["movement", "megaeth", "initia", "hyperliquid", "story", "sei", "blast", "mantle", "scroll", "linea"]):
            score += 1
        else:
            score += 0.5

    # === TYPE URGENCY (0-2.5 points) ===
    t = item.get("type", "general")
    urgency = {
        "instant": 2.5, "retroactive": 2.5, "testnet": 1.5, "waitlist": 1.5,
        "early": 2.0, "watchlist": 0.5, "nft": 1, "general": 0
    }
    score += urgency.get(t, 0)

    # === ROBINHOOD CHAIN PRIORITY ===
    if any(x in text for x in PRIORITY_CHAINS):
        score += 1.0

    # === WAITLIST POTENTIAL ===
    waitlist_signals = [
        "waitlist", "waiting list", "early access", "priority access",
        "access list", "application open", "applications open",
        "request access", "invite only", "invitation only",
        "closed beta", "private beta", "founding users",
        "founding members", "genesis access", "prelaunch access"
    ]
    waitlist_hits = sum(1 for x in waitlist_signals if x in text)
    if t == "waitlist":
        score += min(waitlist_hits * 0.25, 1.0)
    elif waitlist_hits >= 2:
        score += 0.5

    # === FRESHNESS (0-2 points) ===
    try:
        pub = datetime.fromisoformat(item.get("published_at", "2000-01-01"))
        age = datetime.now() - pub
        if age < timedelta(hours=1):
            score += 2
        elif age < timedelta(hours=6):
            score += 1.5
        elif age < timedelta(hours=24):
            score += 1
        elif age < timedelta(days=3):
            score += 0.5
    except:
        pass

    # === HIGH-POTENTIAL INDICATORS ===
    if t == "early":
        if any(x in text for x in ["ambassador", "founding", "genesis", "first 100", "og users", "early supporter", "ido", "launchpad"]):
            score += 2
        elif any(x in text for x in ["stealth", "under radar", "hidden gem", "pre-launch", "seed round"]):
            score += 1.5

    if t == "testnet":
        if any(x in text for x in ["incentivized", "rewards", "points", "campaign", "confirmed", "mainnet soon"]):
            score += 1.5
        elif "testnet" in text:
            score += 0.5

    if t == "retroactive":
        if any(x in text for x in ["snapshot taken", "snapshot done", "confirmed", "live claim", "season", "epoch"]):
            score += 2
        elif any(x in text for x in ["retrodrop", "voyage", "odyssey", "loyalty", "phase"]):
            score += 1

    if t == "waitlist":
        if any(x in text for x in ["whitelist", "allowlist", "closed beta", "private beta", "premint"]):
            score += 1

    if t == "nft":
        if any(x in text for x in ["free mint", "freemint", "allowlist", "whitelist", "open edition"]):
            score += 1.5

    # === DEEP DATA BONUS ===
    if deep.get("team"):
        score += 0.5
    if deep.get("contract"):
        score += 0.5
    if deep.get("roadmap"):
        score += 0.5

    # === HYPE KEYWORDS ===
    if any(x in text for x in ["confirmed airdrop", "token confirmed", "launching token", "tge", "token generation"]):
        score += 2
    if any(x in text for x in ["backed by", "raised", "funding", "invested by"]):
        score += 1
    if any(x in text for x in ["layerzero", "zksync", "starknet", "eigenlayer", "hyperliquid", "berachain", "monad"]):
        score += 1.5
    if any(x in text for x in ["binance", "coinbase", "paradigm", "a16z"]):
        score += 1

    # === CROSS-VALIDATION BONUS ===
    source_count = item.get("source_count", 1)
    if source_count >= 3:
        score += 1.5
    elif source_count == 2:
        score += 0.5

    return max(1, min(10, int(score)))

def rating_stars(rating):
    full = "⭐"
    empty = "⚫"
    return full * rating + empty * (10 - rating)

def rating_label(rating):
    labels = {
        10: "S-Tier", 9: "S-Tier",
        8: "A-Tier", 7: "A-Tier",
        6: "B-Tier", 5: "B-Tier",
        4: "C-Tier", 3: "C-Tier",
        2: "D-Tier", 1: "D-Tier"
    }
    return labels.get(rating, "N/A")

def get_tier_from_rating(rating):
    if rating >= 9:
        return "s"
    elif rating >= 7:
        return "a"
    elif rating >= 5:
        return "b"
    elif rating >= 3:
        return "c"
    return "d"

# ==================== ENHANCED DEEP FETCHER ====================
class DeepFetcher:
    def __init__(self):
        self.session = None
        self.semaphore = asyncio.Semaphore(5)

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(headers=HEADERS)
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()

    async def _get(self, url, timeout=15, fmt="json"):
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                if r.status == 200:
                    if fmt == "json":
                        return await r.json()
                    return await r.text()
        except Exception:
            pass
        return None

    async def deep_scrape_page(self, url):
        if not url or any(b in url for b in BLOCKED):
            return {}

        html = await self._get(url, timeout=15, fmt="text")
        if not html:
            return {}

        info = {}
        text = strip_html(html).lower()

        # Team extraction
        team_patterns = [
            r"team[:\s]+([^\.\n]{10,200})",
            r"founder[s]?[:\s]+([^\.\n]{10,200})",
            r"by[:\s]+([^\.\n]{10,100})",
            r"built by[:\s]+([^\.\n]{10,100})",
            r"created by[:\s]+([^\.\n]{10,100})",
        ]
        for pat in team_patterns:
            m = re.search(pat, text, re.I)
            if m:
                info["team"] = m.group(1).strip()[:100]
                break

        info["investors"] = extract_investors(text)
        info["contract"] = extract_contract(html)
        info["socials"] = extract_socials(html)

        # Scam detection on page
        is_scam, scam_reasons, scam_score = detect_scam_signals(html)
        info["scam_signals"] = {
            "is_scam": is_scam,
            "reasons": scam_reasons,
            "score": scam_score
        }

        # Roadmap/Status extraction
        roadmap = []
        if "mainnet" in text:
            if "mainnet live" in text or "mainnet launched" in text:
                roadmap.append("Mainnet LIVE")
            else:
                roadmap.append("Mainnet planned")
        if "token" in text and "launch" in text:
            roadmap.append("Token launch")
        if "testnet" in text:
            if "testnet live" in text or "testnet active" in text:
                roadmap.append("Testnet active")
            else:
                roadmap.append("Testnet planned")
        if any(x in text for x in ["early", "alpha", "stealth"]):
            roadmap.append("Early stage")
        if any(x in text for x in ["waitlist", "whitelist", "register", "premint"]):
            roadmap.append("Registration open")
        if any(x in text for x in ["retroactive", "retrodrop", "snapshot", "season", "epoch"]):
            roadmap.append("Retroactive eligible")
        if any(x in text for x in ["ido", "ieo", "launchpad", "public sale"]):
            roadmap.append("Token sale upcoming")
        info["roadmap"] = roadmap

        info["chain_confirmed"] = extract_chain(html)

        if any(x in text for x in ["kyc required", "kyc needed", "verify identity", "aml check"]):
            info["kyc_required"] = True
        else:
            info["kyc_required"] = False

        if any(x in text for x in ["gas fee", "transaction fee", "need eth", "need sol", "pay fee"]):
            info["has_cost"] = True
        else:
            info["has_cost"] = False

        return info

    async def _enrich_one(self, item):
        async with self.semaphore:
            try:
                deep = await self.deep_scrape_page(item.get('link', ''))
                item['deep'] = deep
                item['rating'] = calculate_rating(item)
                item['tier'] = get_tier_from_rating(item['rating'])
                await asyncio.sleep(0.3)
            except Exception as e:
                print(f"  ⚠️  Deep scrape error for {item.get('name', 'Unknown')[:40]}: {e}")
                item['rating'] = calculate_rating(item)
                item['tier'] = get_tier_from_rating(item['rating'])
            return item

    async def enrich_all(self, items):
        print(f"  🔍 Deep scraping ALL {len(items)} items...")
        tasks_list = [self._enrich_one(item) for item in items]
        enriched = await asyncio.gather(*tasks_list)
        print(f"  ✅ Deep scrape complete for {len(enriched)} items")
        return list(enriched)

# ==================== ENHANCED FETCHER ====================
class Fetcher:
    def __init__(self):
        self.session = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(headers=HEADERS)
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()

    async def _get(self, url, timeout=15, fmt="json"):
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                if r.status == 200:
                    if fmt == "json":
                        return await r.json()
                    return await r.text()
        except Exception:
            pass
        return None


    # === RSS SOURCES ===
    async def rss_airdropalert(self):
        text = await self._get("https://airdropalert.com/feed/rssfeed", 20, "text")
        return self._parse_rss(text, "AirdropAlert")

    async def rss_airdropalert_blog(self):
        text = await self._get("https://blogs.airdropalert.com/feed", 20, "text")
        return self._parse_rss(text, "AirdropAlert Blog")

    async def rss_coinmarketcap(self):
        text = await self._get("https://coinmarketcap.com/rss/airdrops/", 20, "text")
        return self._parse_rss(text, "CoinMarketCap")

    async def rss_airdrops_io(self):
        text = await self._get("https://airdrops.io/feed/", 20, "text")
        return self._parse_rss(text, "Airdrops.io")

    async def rss_dropsearn(self):
        text = await self._get("https://dropsearn.com/feed/", 20, "text")
        return self._parse_rss(text, "DropsEarn")

    async def rss_dropsearn_testnet(self):
        text = await self._get("https://dropsearn.com/category/testnet/feed/", 20, "text")
        return self._parse_rss(text, "DropsEarn Testnet")

    async def rss_dropsearn_waitlist(self):
        text = await self._get("https://dropsearn.com/category/waitlist/feed/", 20, "text")
        return self._parse_rss(text, "DropsEarn Waitlist")

    async def rss_dropsearn_retroactive(self):
        text = await self._get("https://dropsearn.com/category/retroactive/feed/", 20, "text")
        return self._parse_rss(text, "DropsEarn Retroactive")

    async def rss_mirror_alpha(self):
        text = await self._get("https://mirror.xyz/feed/en", 20, "text")
        return self._parse_rss(text, "Mirror.xyz")

    async def rss_medium_crypto(self):
        text = await self._get("https://medium.com/feed/tag/crypto-airdrop", 20, "text")
        return self._parse_rss(text, "Medium")

    async def rss_medium_early_crypto(self):
        text = await self._get("https://medium.com/feed/tag/early-crypto", 20, "text")
        return self._parse_rss(text, "Medium Early Crypto")

    async def rss_medium_ambassador(self):
        text = await self._get("https://medium.com/feed/tag/ambassador-program", 20, "text")
        return self._parse_rss(text, "Medium Ambassador")

    async def rss_cointelegraph(self):
        text = await self._get("https://cointelegraph.com/rss/tag/airdrop", 20, "text")
        return self._parse_rss(text, "CoinTelegraph")

    async def rss_coindesk(self):
        text = await self._get("https://coindesk.com/arc/outboundfeeds/rss/?outputType=xml", 20, "text")
        return self._parse_rss(text, "CoinDesk")

    async def rss_airdropsmob(self):
        text = await self._get("https://airdropsmob.com/feed/", 20, "text")
        return self._parse_rss(text, "AirdropsMob")

    async def rss_airdropbob(self):
        text = await self._get("https://airdropbob.com/feed/", 20, "text")
        return self._parse_rss(text, "AirdropBob")
        text = await self._get("https://bankless.com/rss", 20, "text")
        return self._parse_rss(text, "Bankless")

    async def rss_bitcoinist(self):
        text = await self._get("https://bitcoinist.com/feed/", 20, "text")
        return self._parse_rss(text, "Bitcoinist")

    async def rss_newsbtc(self):
        text = await self._get("https://www.newsbtc.com/feed/", 20, "text")
        return self._parse_rss(text, "NewsBTC")

    async def rss_cryptopotato(self):
        text = await self._get("https://cryptopotato.com/feed/", 20, "text")
        return self._parse_rss(text, "CryptoPotato")

    async def rss_blockworks(self):
        text = await self._get("https://blockworks.co/news.rss", 20, "text")
        return self._parse_rss(text, "Blockworks")

    async def rss_earndrop_io(self):
        text = await self._get("https://earndrop.io/feed/", 20, "text")
        return self._parse_rss(text, "EarnDrop.io")

    async def rss_gitcoin_blog(self):
        text = await self._get("https://gitcoin.co/blog/feed/", 20, "text")
        return self._parse_rss(text, "Gitcoin Blog")

    async def rss_layer3_blog(self):
        text = await self._get("https://blog.layer3.xyz/rss/", 20, "text")
        return self._parse_rss(text, "Layer3 Blog")

    async def rss_galxe_blog(self):
        text = await self._get("https://blog.galxe.com/rss/", 20, "text")
        return self._parse_rss(text, "Galxe Blog")

    # === NEW: NFT-Specific Sources ===
    async def rss_nftcalendar(self):
        text = await self._get("https://nftcalendar.io/feed/", 20, "text")
        return self._parse_nft_rss(text, "NFTCalendar")

    async def rss_nftevening(self):
        text = await self._get("https://nftevening.com/feed/", 20, "text")
        return self._parse_nft_rss(text, "NFTEvening")

    async def rss_nftplazas(self):
        text = await self._get("https://nftplazas.com/feed/", 20, "text")
        return self._parse_nft_rss(text, "NFTPlazas")

    async def rss_cryptoart(self):
        text = await self._get("https://cryptoart.net/feed/", 20, "text")
        return self._parse_nft_rss(text, "CryptoArt")

    # === NEW: Retroactive & Campaign Sources ===
    async def rss_retroactive_hunters(self):
        text = await self._get("https://medium.com/feed/tag/retroactive-airdrop", 20, "text")
        return self._parse_rss(text, "Medium Retroactive")

    async def rss_defi_alpha(self):
        text = await self._get("https://medium.com/feed/tag/defi-alpha", 20, "text")
        return self._parse_rss(text, "Medium DeFi Alpha")

    # === NEW: Early Stage / IDO Sources ===
    async def rss_icodrops(self):
        text = await self._get("https://icodrops.com/feed/", 20, "text")
        return self._parse_rss(text, "ICODrops")

    async def rss_cryptorank_io(self):
        text = await self._get("https://cryptorank.io/blog/rss", 20, "text")
        return self._parse_rss(text, "CryptoRank Blog")

    # === EXPANDED NFT SOURCES (NO NITTER / NO TWITTER SCRAPING) ===
    async def rss_nftnow(self):
        text = await self._get("https://nftnow.com/feed/", 20, "text")
        return self._parse_nft_rss(text, "NFT Now")

    async def rss_nftculture(self):
        text = await self._get("https://www.nftculture.com/feed/", 20, "text")
        return self._parse_nft_rss(text, "NFT Culture")

    async def rss_nftlately(self):
        text = await self._get("https://nftlately.com/feed/", 20, "text")
        return self._parse_nft_rss(text, "NFT Lately")

    async def rss_nftnewspro(self):
        text = await self._get("https://nftnewspro.com/feed/", 20, "text")
        return self._parse_nft_rss(text, "NFT News Pro")

    async def rss_nftcalendar_blog(self):
        text = await self._get("https://nftcalendar.io/blog/feed/", 20, "text")
        return self._parse_nft_rss(text, "NFTCalendar Blog")

    # === EXPANDED WAITLIST SOURCES ===
    async def rss_producthunt(self):
        text = await self._get("https://www.producthunt.com/feed", 20, "text")
        return self._parse_waitlist_rss(text, "Product Hunt")

    async def rss_indiehackers(self):
        text = await self._get("https://www.indiehackers.com/feed", 20, "text")
        return self._parse_waitlist_rss(text, "Indie Hackers")

    async def rss_alchemy_blog(self):
        text = await self._get("https://www.alchemy.com/blog/rss.xml", 20, "text")
        return self._parse_waitlist_rss(text, "Alchemy")

    # === MORE NFT SOURCES ===
    async def rss_nftnews(self):
        text = await self._get("https://www.nft.news/feed/", 20, "text")
        return self._parse_nft_rss(text, "NFT News")

    async def rss_nftstreet(self):
        text = await self._get("https://nftstreet.com/feed/", 20, "text")
        return self._parse_nft_rss(text, "NFT Street")

    async def rss_nftculture_blog(self):
        text = await self._get("https://www.nftculture.com/feed/", 20, "text")
        return self._parse_nft_rss(text, "NFT Culture Blog")

    # === API SOURCES ===
    async def api_alpha_drops(self):
        data = await self._get("https://alphadrops.net/api/v1/airdrops", 15, "json")
        return self._parse_api(data, "AlphaDrops")

    async def dappradar_airdrops(self):
        data = await self._get("https://dappradar.com/api/airdrops", 20, "json")
        items = []
        now = datetime.now().isoformat()
        if isinstance(data, dict) and "data" in data:
            for p in data["data"][:10]:
                t = detect_type(p.get("description", ""))
                if t not in TARGET_TYPES:
                    t = "watchlist"
                items.append({
                    "name": p.get("title", "Unknown"),
                    "desc": p.get("description", "")[:280],
                    "link": p.get("url", ""),
                    "source": "DappRadar",
                    "type": t,
                    "hype": min(50 + p.get("participants", 0) // 100, 95),
                    "chain": p.get("chain", "Multi"),
                    "deadline": p.get("endDate", "TBA"),
                    "difficulty": "Medium",
                    "funding": "N/A",
                    "published_at": now,
                })
        return [i for i in items if i["type"] in TARGET_TYPES]

    async def cryptorank_upcoming(self):
        data = await self._get("https://api.cryptorank.io/v1/airdrops", 20, "json")
        items = []
        now = datetime.now().isoformat()
        if isinstance(data, list):
            for p in data[:10]:
                t = detect_type(p.get("description", ""))
                if t not in TARGET_TYPES:
                    t = "early"
                items.append({
                    "name": p.get("name", "Unknown"),
                    "desc": f"Upcoming IDO/Token | {p.get('description', '')[:200]}",
                    "link": p.get("website", ""),
                    "source": "CryptoRank",
                    "type": t,
                    "hype": min(50 + p.get("interest", 0), 95),
                    "chain": p.get("platform", "Multi"),
                    "deadline": p.get("startDate", "TBA"),
                    "difficulty": "Medium",
                    "funding": p.get("funding", "N/A"),
                    "published_at": now,
                })
        return [i for i in items if i["type"] in TARGET_TYPES]

    async def defillama_alpha(self):
        data = await self._get("https://api.llama.fi/protocols", 20, "json")
        items = []
        now = datetime.now().isoformat()
        if isinstance(data, list):
            for p in data:
                if p.get("tvl", 0) > 5000000 and not p.get("token", ""):
                    items.append({
                        "name": p.get("name", "Unknown"),
                        "desc": f"Pre-token protocol | TVL: ${p.get('tvl',0)/1e6:.1f}M",
                        "link": f"https://defillama.com/protocol/{p.get('slug', '')}",
                        "source": "DeFiLlama",
                        "type": "watchlist",
                        "hype": min(50 + int(p.get("tvl", 0) / 1e7), 95),
                        "chain": ", ".join(p.get("chains", ["N/A"])[:2]),
                        "deadline": "TBA",
                        "difficulty": "Medium",
                        "funding": "N/A",
                        "published_at": now,
                        "deep": {"roadmap": ["Pre-token", f"TVL ${p.get('tvl',0)/1e6:.1f}M"]},
                    })
        return sorted(items, key=lambda x: x.get("tvl", 0), reverse=True)[:8]

    async def nft_opensea_trending(self):
        items = []
        now = datetime.now().isoformat()
        try:
            data = await self._get("https://api.opensea.io/api/v2/collections?order_by=trending&limit=10", 15, "json")
            if isinstance(data, dict) and "collections" in data:
                for c in data["collections"][:8]:
                    stats = c.get("stats", {})
                    items.append({
                        "name": f"🎨 {c.get('name', 'Unknown')}",
                        "desc": f"NFT Collection | Floor: {stats.get('floor_price', 'N/A')} ETH | Vol: {stats.get('total_volume', 0):.1f} ETH",
                        "link": c.get("opensea_url", f"https://opensea.io/collection/{c.get('slug','')}"),
                        "source": "OpenSea",
                        "type": "nft",
                        "hype": min(50 + int(stats.get("total_volume", 0) / 100), 95),
                        "chain": c.get("contracts", [{}])[0].get("chain", "ETH").upper() if c.get("contracts") else "ETH",
                        "deadline": "TBA",
                        "difficulty": "Easy",
                        "funding": "N/A",
                        "published_at": now,
                        "deep": {"socials": extract_socials(c.get("description", "")), "roadmap": ["NFT Collection", "Trending"]},
                    })
        except Exception as e:
            print(f"  ⚠️  OpenSea error: {e}")
        return items

    async def magiceden_launchpad(self):
        """Only surfaces mints that are still upcoming or launched very recently.
        Magic Eden's API keeps old/ended launchpad entries in the list, which is
        exactly what caused stale (already-expired) mints to show as 'NEW'."""
        items = []
        try:
            data = await self._get("https://api-mainnet.magiceden.dev/v2/launchpad/collections?limit=20", 15, "json")
            if isinstance(data, list):
                for c in data[:20]:
                    launch_raw = c.get("launchDatetime", "TBA")
                    launch_dt = parse_flexible_date(launch_raw) if isinstance(launch_raw, str) else None

                    # Skip mints whose launch/deadline already happened (stale) —
                    # this is the main fix for old-drops-showing-as-new.
                    if launch_dt and launch_dt < (datetime.now() - timedelta(hours=DEADLINE_GRACE_HOURS)):
                        continue

                    # published_at reflects real launch time when we have it, so the
                    # freshness filter downstream (max_item_age_hours) is meaningful
                    # instead of always reading "just now".
                    published_at = (launch_dt or datetime.now()).isoformat()

                    items.append({
                        "name": f"🚀 {c.get('name', 'Unknown')}",
                        "desc": f"Magic Eden Launchpad | Supply: {c.get('supply', 'N/A')} | Price: {c.get('price', 'N/A')} SOL",
                        "link": f"https://magiceden.io/launchpad/{c.get('symbol', '')}",
                        "source": "Magic Eden",
                        "type": "nft",
                        "hype": min(50 + (c.get('supply', 0) or 0) // 100, 95),
                        "chain": "Solana",
                        "deadline": launch_raw,
                        "difficulty": "Easy",
                        "funding": "N/A",
                        "published_at": published_at,
                        "deep": {"roadmap": ["NFT Launchpad", "Upcoming mint"]},
                    })
        except Exception as e:
            print(f"  ⚠️  Magic Eden error: {e}")
        return items

    async def snapshot_spaces(self):
        items = []
        now = datetime.now().isoformat()
        try:
            data = await self._get("https://hub.snapshot.org/api/explore", 15, "json")
            if isinstance(data, dict) and "spaces" in data:
                spaces = list(data["spaces"].items())[:10]
                for space_id, space_data in spaces:
                    followers = space_data.get("followers", 0)
                    if followers > 1000:
                        items.append({
                            "name": f"🏛️ {space_data.get('name', space_id)}",
                            "desc": f"Snapshot Governance | Followers: {followers} | Proposals: {space_data.get('proposalsCount', 0)}",
                            "link": f"https://snapshot.org/#/{space_id}",
                            "source": "Snapshot",
                            "type": "watchlist",
                            "hype": min(50 + followers // 100, 95),
                            "chain": "Multi",
                            "deadline": "TBA",
                            "difficulty": "Medium",
                            "funding": "N/A",
                            "published_at": now,
                            "deep": {"roadmap": ["Governance active", f"{followers} followers"], "socials": {"twitter": space_data.get("twitter", "")}},
                        })
        except Exception as e:
            print(f"  ⚠️  Snapshot error: {e}")
        return items

    async def rootdata_funding(self):
        items = []
        now = datetime.now().isoformat()
        try:
            data = await self._get("https://api.rootdata.com/open/ai_getProjects?size=15&page=1&sort=time", 20, "json")
            if isinstance(data, dict) and "data" in data:
                for p in data["data"]:
                    funding = p.get("funding", {})
                    amount = funding.get("amount", "N/A")
                    investors_list = funding.get("investors", [])
                    items.append({
                        "name": p.get("name", "Unknown"),
                        "desc": f"RootData | {p.get('description', '')[:150]} | Funding: {amount}",
                        "link": p.get("website", f"https://rootdata.com/Projects/detail/{p.get('id', '')}"),
                        "source": "RootData",
                        "type": "early",
                        "hype": min(50 + len(investors_list) * 5, 95),
                        "chain": extract_chain(p.get("description", "")),
                        "deadline": "TBA",
                        "difficulty": "Medium",
                        "funding": amount,
                        "published_at": now,
                        "deep": {
                            "investors": [i.get("name", "") for i in investors_list[:5]],
                            "roadmap": [f"Funding: {amount}", f"{len(investors_list)} investors"],
                        },
                    })
        except Exception as e:
            print(f"  ⚠️  RootData error: {e}")
        return items

    # === PARSERS ===
    def _parse_rss(self, text, source):
        if not text:
            return []
        items = []
        try:
            root = ET.fromstring(text)
            for item in root.findall(".//item"):
                title_el = item.find("title")
                link_el = item.find("link")
                desc_el = item.find("description")
                pubdate_el = item.find("pubDate")
                if title_el is None:
                    continue

                raw_title = (title_el.text or "").strip()
                raw_desc = (desc_el.text or "").strip()
                rss_link = (link_el.text or "").strip()

                published_at = datetime.now()
                if pubdate_el is not None and pubdate_el.text:
                    try:
                        pub = parsedate_to_datetime(pubdate_el.text)
                        if pub.tzinfo:
                            pub = pub.replace(tzinfo=None)
                        published_at = pub
                    except Exception:
                        pass

                clean_title = strip_html(raw_title)
                clean_desc = strip_html(raw_desc)
                combined = (clean_title + " " + clean_desc).lower()

                if not any(kw in combined for kw in AIRDROP_KEYWORDS):
                    continue

                t = detect_type(combined)
                if t not in TARGET_TYPES:
                    continue

                official = pick_official(raw_desc, rss_link)
                dl = extract_deadline(clean_desc) or extract_deadline(clean_title)
                fund = extract_funding(clean_desc) or extract_funding(clean_title)
                socials = extract_socials(raw_desc)
                investors = extract_investors(clean_desc)

                items.append({
                    "name": clean_title[:100],
                    "desc": clean_desc[:280],
                    "link": official,
                    "source": source,
                    "type": t,
                    "hype": self._hype_score(combined),
                    "chain": extract_chain(combined),
                    "deadline": dl if dl else "TBA",
                    "difficulty": detect_difficulty(combined),
                    "funding": fund if fund else "N/A",
                    "published_at": published_at.isoformat(),
                    "deep": {
                        "socials": socials,
                        "investors": investors,
                        "roadmap": [],
                    },
                })
        except Exception:
            pass
        return items

    def _parse_waitlist_rss(self, text, source):
        if not text:
            return []
        items = []
        try:
            root = ET.fromstring(text)
            for item in root.findall(".//item"):
                title_el = item.find("title")
                link_el = item.find("link")
                desc_el = item.find("description")
                pubdate_el = item.find("pubDate")
                if title_el is None:
                    continue

                raw_title = (title_el.text or "").strip()
                raw_desc = (desc_el.text or "").strip()
                rss_link = (link_el.text or "").strip()
                published_at = datetime.now()

                if pubdate_el is not None and pubdate_el.text:
                    try:
                        pub = parsedate_to_datetime(pubdate_el.text)
                        if pub.tzinfo:
                            pub = pub.replace(tzinfo=None)
                        published_at = pub
                    except Exception:
                        pass

                clean_title = strip_html(raw_title)
                clean_desc = strip_html(raw_desc)
                combined = (clean_title + " " + clean_desc).lower()

                signals = [
                    "waitlist", "waiting list", "early access", "priority access",
                    "access list", "application open", "applications open",
                    "request access", "invite only", "invitation only",
                    "closed beta", "private beta", "founding users",
                    "founding members", "genesis access", "prelaunch access",
                    "join the list", "reserve your spot", "get on the list"
                ]
                if not any(x in combined for x in signals):
                    continue

                if datetime.now() - published_at > timedelta(hours=MAX_ITEM_AGE_HOURS):
                    continue

                official = pick_official(raw_desc, rss_link)
                items.append({
                    "name": clean_title[:100],
                    "desc": clean_desc[:280],
                    "link": official,
                    "source": source,
                    "type": "waitlist",
                    "hype": self._hype_score(combined),
                    "chain": extract_chain(combined),
                    "deadline": extract_deadline(clean_desc) or "TBA",
                    "difficulty": detect_difficulty(combined),
                    "funding": extract_funding(clean_desc) or "N/A",
                    "published_at": published_at.isoformat(),
                    "deep": {
                        "socials": extract_socials(raw_desc),
                        "investors": extract_investors(clean_desc),
                        "roadmap": ["Waitlist / Early Access"],
                    },
                })
        except Exception:
            pass
        return items

    def _parse_general_rss(self, text, source):
        if not text:
            return []
        items = []
        try:
            root = ET.fromstring(text)
            for item in root.findall(".//item"):
                title_el = item.find("title")
                link_el = item.find("link")
                desc_el = item.find("description")
                pub_el = item.find("pubDate")
                if title_el is None:
                    continue

                title = strip_html((title_el.text or "").strip())
                desc = strip_html((desc_el.text or "").strip())
                link = (link_el.text or "").strip() if link_el is not None else ""
                published_at = datetime.now()

                if pub_el is not None and pub_el.text:
                    try:
                        pub = parsedate_to_datetime(pub_el.text)
                        if pub.tzinfo:
                            pub = pub.replace(tzinfo=None)
                        published_at = pub
                    except Exception:
                        pass

                if datetime.now() - published_at > timedelta(hours=MAX_ITEM_AGE_HOURS):
                    continue

                combined = (title + " " + desc).lower()
                signals = [
                    "testnet", "devnet", "mainnet", "launch", "early access",
                    "waitlist", "whitelist", "allowlist", "airdrop", "points",
                    "campaign", "incentivized", "funding", "raised", "nft",
                    "mint", "collection", "ecosystem", "beta", "sale"
                ]
                if not any(s in combined for s in signals):
                    continue

                items.append({
                    "name": title[:100],
                    "desc": desc[:280],
                    "link": pick_official(desc, link),
                    "source": source,
                    "type": detect_type(combined),
                    "hype": self._hype_score(combined),
                    "chain": extract_chain(combined),
                    "deadline": extract_deadline(desc) or "TBA",
                    "difficulty": detect_difficulty(combined),
                    "funding": extract_funding(desc) or "N/A",
                    "published_at": published_at.isoformat(),
                    "deep": {
                        "socials": extract_socials(desc),
                        "investors": extract_investors(desc),
                    },
                })
        except Exception:
            pass
        return items

    def _parse_nft_rss(self, text, source):
        if not text:
            return []
        items = []
        try:
            root = ET.fromstring(text)
            for item in root.findall(".//item"):
                title_el = item.find("title")
                link_el = item.find("link")
                desc_el = item.find("description")
                pubdate_el = item.find("pubDate")
                if title_el is None:
                    continue

                raw_title = (title_el.text or "").strip()
                raw_desc = (desc_el.text or "").strip()
                rss_link = (link_el.text or "").strip()

                published_at = datetime.now()
                if pubdate_el is not None and pubdate_el.text:
                    try:
                        pub = parsedate_to_datetime(pubdate_el.text)
                        if pub.tzinfo:
                            pub = pub.replace(tzinfo=None)
                        published_at = pub
                    except Exception:
                        pass

                clean_title = strip_html(raw_title)
                clean_desc = strip_html(raw_desc)
                combined = (clean_title + " " + clean_desc).lower()

                if not any(kw in combined for kw in NFT_KEYWORDS):
                    continue

                age = datetime.now() - published_at
                if age > timedelta(hours=NFT_MAX_AGE_HOURS):
                    continue

                t = "nft"
                official = pick_official(raw_desc, rss_link)
                dl = extract_deadline(clean_desc)
                socials = extract_socials(raw_desc)

                items.append({
                    "name": clean_title[:100],
                    "desc": clean_desc[:280],
                    "link": official,
                    "source": source,
                    "type": t,
                    "hype": self._hype_score(combined),
                    "chain": extract_chain(combined),
                    "deadline": dl if dl else "TBA",
                    "difficulty": detect_difficulty(combined),
                    "funding": "N/A",
                    "published_at": published_at.isoformat(),
                    "deep": {"socials": socials, "investors": [], "roadmap": ["NFT Drop"]},
                })
        except Exception:
            pass
        return items

    def _parse_api(self, data, source):
        if not data:
            return []
        items = []
        now = datetime.now().isoformat()
        arr = data if isinstance(data, list) else data.get("airdrops", data.get("data", []))
        for item in arr[:15]:
            name = strip_html(item.get("name", item.get("title", "Unknown")))
            desc = strip_html(item.get("description", item.get("desc", "")))
            combined = (name + " " + desc).lower()
            if not any(kw in combined for kw in AIRDROP_KEYWORDS):
                continue

            t = item.get("type", item.get("category", detect_type(combined)))
            if t not in TARGET_TYPES:
                continue

            link = item.get("url", item.get("link", item.get("website", "")))
            better = pick_official(item.get("description", ""), link)
            socials = extract_socials(item.get("description", ""))
            investors = extract_investors(desc)

            items.append({
                "name": name[:100],
                "desc": desc[:280],
                "link": better,
                "source": source,
                "type": t,
                "hype": item.get("hype_score", item.get("score", self._hype_score(combined))),
                "chain": item.get("chain", item.get("network", extract_chain(combined))),
                "deadline": item.get("deadline", item.get("endDate", extract_deadline(desc) or "TBA")),
                "difficulty": item.get("difficulty", detect_difficulty(combined)),
                "funding": item.get("funding", extract_funding(desc) or "N/A"),
                "published_at": now,
                "deep": {"socials": socials, "investors": investors, "roadmap": []},
            })
        return items

    def _hype_score(self, text):
        t = text.lower()
        s = 50
        if any(x in t for x in ["early alpha", "stealth", "under radar", "first 100", "genesis", "founding"]):
            s += 12
        if any(x in t for x in ["$100m", "$500m", "$1b", "huge", "massive", "confirmed", "live"]):
            s += 25
        elif any(x in t for x in ["$10m", "$50m", "large", "major", "binance", "coinbase"]):
            s += 10
        if any(x in t for x in ["robinhood chain", "robinhoodchain", "robinhood mainnet"]):
            s += 20
        elif any(x in t for x in ["layerzero", "zksync", "starknet", "eigenlayer", "hyperliquid", "berachain", "monad"]):
            s += 15
        if "🪂" in t or "💰" in t:
            s += 10
        if any(x in t for x in ["testnet", "mainnet", "launch", "snapshot"]):
            s += 5
        if any(x in t for x in ["waitlist", "waiting list", "whitelist", "allowlist", "register", "sign up", "premint", "priority access", "access list", "invite only", "application open"]):
            s += 8
        if any(x in t for x in ["retroactive", "retrodrop", "voyage", "odyssey", "season", "epoch"]):
            s += 7
        if any(x in t for x in ["confirmed airdrop", "token confirmed", "tge"]):
            s += 20
        if any(x in t for x in ["free mint", "freemint", "open edition", "allowlist", "mint pass", "public mint", "minting soon", "nft launch", "digital collectible", "onchain art"]):
            s += 15
        if any(x in t for x in ["ido", "ieo", "launchpad", "public sale"]):
            s += 10
        return min(s, 100)

    # Name -> coroutine factory. CoinGecko Trending and DexScreener were removed
    # on purpose: both surface token price/volume info, not new alpha
    # projects or NFT/airdrop drops.
    async def _safe_source(self, method_name):
        method = getattr(self, method_name, None)
        if method is None:
            return []
        try:
            return await method()
        except Exception:
            return []

    def _source_map(self):
        return {
            "AirdropAlert": self._safe_source('rss_airdropalert'), "AirdropAlert Blog": self._safe_source('rss_airdropalert_blog'),
            "CoinMarketCap": self._safe_source('rss_coinmarketcap'), "Airdrops.io": self._safe_source('rss_airdrops_io'),
            "DropsEarn": self._safe_source('rss_dropsearn'), "DropsEarn Testnet": self._safe_source('rss_dropsearn_testnet'),
            "DropsEarn Waitlist": self._safe_source('rss_dropsearn_waitlist'), "DropsEarn Retroactive": self._safe_source('rss_dropsearn_retroactive'),
            "Mirror.xyz": self._safe_source('rss_mirror_alpha'), "Medium": self._safe_source('rss_medium_crypto'),
            "Medium Early Crypto": self._safe_source('rss_medium_early_crypto'), "Medium Ambassador": self._safe_source('rss_medium_ambassador'),
            "CoinTelegraph": self._safe_source('rss_cointelegraph'), "CoinDesk": self._safe_source('rss_coindesk'),
            "AirdropsMob": self._safe_source('rss_airdropsmob'), "AirdropBob": self._safe_source('rss_airdropbob'),
            "NewsBTC": self._safe_source('rss_newsbtc'), "CryptoPotato": self._safe_source('rss_cryptopotato'),
            "Blockworks": self._safe_source('rss_blockworks'), "EarnDrop.io": self._safe_source('rss_earndrop_io'),
            "Gitcoin Blog": self._safe_source('rss_gitcoin_blog'), "Layer3 Blog": self._safe_source('rss_layer3_blog'),
            "Galxe Blog": self._safe_source('rss_galxe_blog'),
            "NFTCalendar": self._safe_source('rss_nftcalendar'), "NFTEvening": self._safe_source('rss_nftevening'),
            "NFTPlazas": self._safe_source('rss_nftplazas'), "CryptoArt": self._safe_source('rss_cryptoart'),
            "Medium Retroactive": self._safe_source('rss_retroactive_hunters'), "Medium DeFi Alpha": self._safe_source('rss_defi_alpha'),
            "ICODrops": self._safe_source('rss_icodrops'), "CryptoRank Blog": self._safe_source('rss_cryptorank_io'),
            "NFT Now": self._safe_source('rss_nftnow'), "NFT Culture": self._safe_source('rss_nftculture'),
            "NFT Lately": self._safe_source('rss_nftlately'), "NFT News Pro": self._safe_source('rss_nftnewspro'),
            "NFTCalendar Blog": self._safe_source('rss_nftcalendar_blog'),
            "NFT News": self._safe_source('rss_nftnews'), "NFT Street": self._safe_source('rss_nftstreet'),
            "NFT Culture Blog": self._safe_source('rss_nftculture_blog'),
            "Blockworks": self._safe_source('rss_blockworks'), "Cointelegraph": self._safe_source('rss_cointelegraph'),
            "Product Hunt": self._safe_source('rss_producthunt'), "Indie Hackers": self._safe_source('rss_indiehackers'),
            "Alchemy": self._safe_source('rss_alchemy_blog'),
            "AlphaDrops": self._safe_source('api_alpha_drops'), "DappRadar": self._safe_source('dappradar_airdrops'),
            "CryptoRank": self._safe_source('cryptorank_upcoming'), "DeFiLlama": self._safe_source('defillama_alpha'),
            "OpenSea": self._safe_source('nft_opensea_trending'), "Magic Eden": self._safe_source('magiceden_launchpad'),
            "Snapshot": self._safe_source('snapshot_spaces'), "RootData": self._safe_source('rootdata_funding'),
        }

    async def fetch_all(self):
        print(f"\n{'='*50}")
        print(f"🔍 [{datetime.now().strftime('%H:%M:%S')}] Scraping alpha/NFT/airdrop sources (no token-price sources)...")

        source_coros = self._source_map()
        names = list(source_coros.keys())
        results = await asyncio.gather(*source_coros.values(), return_exceptions=True)
        sources = dict(zip(names, results))

        # Any source that errored/returned an exception becomes an empty list
        # instead of silently poisoning downstream processing.
        for name, val in sources.items():
            if not isinstance(val, list):
                sources[name] = []

        # ---- Global freshness gate: drop anything with a confidently-parsed,
        # already-expired deadline. This is what stops old/ended NFT mints or
        # airdrops (like a mint whose deadline was months ago) from being
        # posted as if they were new. ----
        for name, items in sources.items():
            sources[name] = [it for it in items if not is_deadline_expired(it.get("deadline"))]

        total = sum(len(v) for v in sources.values())
        print(f"✅ Fresh scrape: {total} items (post-expiry-filter, target categories)")
        for name, items in sources.items():
            if items:
                types = ", ".join(set(i["type"] for i in items))
                print(f"   • {name}: {len(items)} ({types})")

        flat = []
        for src_items in sources.values():
            flat.extend(src_items)

        name_counter = Counter()
        for it in flat:
            name_counter[it.get("name", "").lower().split("(")[0].strip()] += 1

        for it in flat:
            base_name = it.get("name", "").lower().split("(")[0].strip()
            it["source_count"] = name_counter.get(base_name, 1)

        print(f"\n🔍 Deep scraping ALL {len(flat)} items...")
        async with DeepFetcher() as deep:
            enriched = await deep.enrich_all(flat)

        s_tier = sum(1 for i in enriched if i.get("rating", 0) >= 9)
        a_tier = sum(1 for i in enriched if 7 <= i.get("rating", 0) <= 8)
        print(f"🏆 Tiers: S={s_tier} | A={a_tier} | B-D={len(enriched)-s_tier-a_tier}")

        enriched_sources = {}
        idx = 0
        for name, items in sources.items():
            count = len(items)
            enriched_sources[name] = enriched[idx:idx+count]
            idx += count

        return enriched_sources


# ==================== DETECTOR ====================
class Detector:
    def __init__(self):
        self.queue = deque(maxlen=50)
        self.first = True
        self.max_item_age_hours = MAX_ITEM_AGE_HOURS
        self.dedup_window_days = DEDUP_WINDOW_DAYS
        self.s_tier_notified = set()
        self.seen = {}
        self._load_seen_cache()

    # ---- persistent dedup so restarting the bot doesn't resend old items ----
    def _load_seen_cache(self):
        try:
            if os.path.exists(DEDUP_CACHE_PATH):
                with open(DEDUP_CACHE_PATH, "r") as f:
                    raw = json.load(f)
                self.seen = {k: datetime.fromisoformat(v) for k, v in raw.items()}
                cutoff = datetime.now() - timedelta(days=self.dedup_window_days)
                self.seen = {k: v for k, v in self.seen.items() if v > cutoff}
                print(f"💾 Loaded {len(self.seen)} cached dedup entries from {DEDUP_CACHE_PATH}")
        except Exception as e:
            print(f"  ⚠️  Could not load dedup cache: {e}")
            self.seen = {}

    def _save_seen_cache(self):
        try:
            raw = {k: v.isoformat() for k, v in self.seen.items()}
            with open(DEDUP_CACHE_PATH, "w") as f:
                json.dump(raw, f)
        except Exception as e:
            print(f"  ⚠️  Could not save dedup cache: {e}")

    def _hash(self, item):
        # Normalize name (strip emoji/punctuation/case) and use just the link's
        # domain+path so the same drop announced with slightly different
        # wording or tracking params across sources is still recognized as
        # a duplicate ("hindari pesan yang sama").
        name = re.sub(r"[^a-z0-9]+", " ", item.get("name", "").lower()).strip()
        link = item.get("link", "")
        link_key = re.sub(r"^https?://(www\.)?", "", link.lower()).split("?")[0].rstrip("/")
        return hashlib.md5(f"{name}:{link_key}".encode()).hexdigest()

    def add(self, items):
        now = datetime.now()
        items_sorted = sorted(items, key=lambda x: x.get("published_at", now.isoformat()))
        new_high_potential = []
        added_any = False

        for it in items_sorted:
            h = self._hash(it)
            if h in self.seen:
                last_seen = self.seen[h]
                if (now - last_seen).days < self.dedup_window_days:
                    continue

            self.seen[h] = now
            it["fetched_at"] = now.isoformat()
            if "first_seen" not in it:
                it["first_seen"] = now.isoformat()

            self.queue.appendleft(it)
            added_any = True

            rating = it.get("rating", 0)
            if rating >= 9 and h not in self.s_tier_notified:
                self.s_tier_notified.add(h)
                new_high_potential.append(it)

        cutoff = now - timedelta(days=self.dedup_window_days)
        self.seen = {k: v for k, v in self.seen.items() if v > cutoff}

        cutoff_st = now - timedelta(days=7)
        self.s_tier_notified = {h for h in self.s_tier_notified
                                 if h in self.seen and self.seen[h] > cutoff_st}

        if added_any:
            self._save_seen_cache()

        return new_high_potential

    def pop(self):
        now = datetime.now()
        cutoff = now - timedelta(hours=self.max_item_age_hours)
        while self.queue:
            it = self.queue[0]
            try:
                pub = datetime.fromisoformat(it.get("published_at", "2000-01-01"))
                if pub > cutoff:
                    return self.queue.popleft()
                else:
                    self.queue.popleft()
            except Exception:
                return self.queue.popleft()
        return None

    def pop_high_potential(self, min_rating=7):
        now = datetime.now()
        cutoff = now - timedelta(hours=self.max_item_age_hours)
        best_idx = -1
        best_rating = -1

        for idx, it in enumerate(self.queue):
            try:
                pub = datetime.fromisoformat(it.get("published_at", "2000-01-01"))
                if pub <= cutoff:
                    continue
                rating = it.get("rating", 0)
                if rating >= min_rating and rating > best_rating:
                    best_rating = rating
                    best_idx = idx
            except Exception:
                continue

        if best_idx >= 0:
            return self.queue.pop(best_idx)
        return None

    def pop_by_tier(self, tier):
        now = datetime.now()
        cutoff = now - timedelta(hours=self.max_item_age_hours)
        tier = tier.lower()
        tier_ranges = {
            "s": (9, 10), "a": (7, 8), "b": (5, 6),
            "c": (3, 4), "d": (1, 2)
        }
        min_r, max_r = tier_ranges.get(tier, (0, 0))

        for idx, it in enumerate(self.queue):
            try:
                pub = datetime.fromisoformat(it.get("published_at", "2000-01-01"))
                if pub <= cutoff:
                    continue
                rating = it.get("rating", 0)
                if min_r <= rating <= max_r:
                    return self.queue.pop(idx)
            except Exception:
                continue
        return None

    def pop_early(self):
        now = datetime.now()
        cutoff = now - timedelta(hours=self.max_item_age_hours)
        for idx, it in enumerate(self.queue):
            try:
                pub = datetime.fromisoformat(it.get("published_at", "2000-01-01"))
                if pub <= cutoff:
                    continue
                if it.get("type") == "early":
                    return self.queue.pop(idx)
            except Exception:
                continue
        return None

    def pop_nft(self):
        now = datetime.now()
        cutoff = now - timedelta(hours=self.max_item_age_hours)
        for idx, it in enumerate(self.queue):
            try:
                pub = datetime.fromisoformat(it.get("published_at", "2000-01-01"))
                if pub <= cutoff:
                    continue
                if it.get("type") == "nft":
                    return self.queue.pop(idx)
            except Exception:
                continue
        return None

    def get_tier_counts(self):
        counts = {"s": 0, "a": 0, "b": 0, "c": 0, "d": 0}
        for it in self.queue:
            tier = it.get("tier", get_tier_from_rating(it.get("rating", 0)))
            if tier in counts:
                counts[tier] += 1
        return counts

    def size(self):
        return len(self.queue)

    def size_early(self):
        return sum(1 for it in self.queue if it.get("type") == "early")

    def size_nft(self):
        return sum(1 for it in self.queue if it.get("type") == "nft")

    def size_high_potential(self, min_rating=7):
        return sum(1 for it in self.queue if it.get("rating", 0) >= min_rating)


# ==================== UI ====================
class UI:
    TYPE_EMOJIS = {
        "instant": "⚡", "testnet": "🧪", "waitlist": "📋",
        "watchlist": "👁️", "retroactive": "🎯", "early": "🔥",
        "nft": "🎨", "general": "📌"
    }

    TYPE_LABELS = {
        "instant": "⚡ INSTANT CLAIM", "testnet": "🧪 TESTNET",
        "waitlist": "📋 WAITLIST", "watchlist": "👁️ WATCHLIST",
        "retroactive": "🎯 RETROACTIVE", "early": "🔥 EARLY ALPHA",
        "nft": "🎨 NFT DROP", "general": "📌 GENERAL"
    }

    TIER_COLORS = {
        "s": COLORS["s_tier"], "a": COLORS["a_tier"],
        "b": COLORS["b_tier"], "c": COLORS["general"], "d": COLORS["general"]
    }

    TIER_EMOJIS = {
        "s": "🏆", "a": "💎", "b": "⚡", "c": "📈", "d": "📝"
    }

    @classmethod
    def _age_text(cls, item):
        ts = item.get("published_at") or item.get("fetched_at")
        if not ts:
            return "N/A"
        try:
            pub = datetime.fromisoformat(ts)
            age = datetime.now() - pub
            if age.days > 0:
                return f"{age.days}d ago"
            elif age.seconds // 3600 > 0:
                return f"{age.seconds // 3600}h ago"
            else:
                return f"{age.seconds // 60}m ago"
        except Exception:
            return "N/A"

    @classmethod
    def card(cls, item, is_new=False, highlight=False):
        t = item.get("type", "general")
        tier = item.get("tier", get_tier_from_rating(item.get("rating", 0)))

        if highlight and tier in ["s", "a"]:
            color = cls.TIER_COLORS.get(tier, COLORS.get(t, COLORS["general"]))
        else:
            color = COLORS.get(t, COLORS["general"])

        emoji = cls.TYPE_EMOJIS.get(t, "•")
        tier_label = {
            "s": "S-Tier", "a": "A-Tier", "b": "B-Tier",
            "c": "C-Tier", "d": "D-Tier"
        }.get(tier, "N/A")

        name = item.get("name", "Unknown")[:90]
        link = item.get("link") or "https://discord.com"
        rating = item.get("rating", calculate_rating(item))

        title_prefix = "NEW • " if is_new else ""
        if highlight and tier == "s":
            title_prefix = "S-TIER • "
        elif highlight and tier == "a":
            title_prefix = "A-TIER • "

        embed = discord.Embed(
            title=f"{title_prefix}{name}",
            url=link,
            color=color,
            timestamp=datetime.now()
        )

        desc = item.get("desc", "").strip()
        if desc:
            embed.description = desc[:280]

        age = cls._age_text(item)
        type_label = cls.TYPE_LABELS.get(t, t.upper()).replace("⚡ ", "").replace("🧪 ", "").replace("📋 ", "").replace("👁️ ", "").replace("🎯 ", "").replace("🔥 ", "").replace("🎨 ", "").replace("📌 ", "")
        source = item.get("source", "N/A")

        info_lines = [
            f"Type: **{type_label}**",
            f"Source: `{source}`  •  Age: `{age}`",
        ]

        chain = item.get("chain", "N/A")
        if chain and chain != "N/A":
            info_lines.append(f"Chain: `{chain}`")

        dl = item.get("deadline", "TBA")
        if dl and dl not in ("TBA", "N/A"):
            info_lines.append(f"Deadline: `{dl}`")

        diff = item.get("difficulty", "Medium")
        info_lines.append(f"Difficulty: `{diff}`")

        fund = item.get("funding", "N/A")
        if fund and fund != "N/A":
            info_lines.append(f"Funding: `{fund}`")

        embed.add_field(name="Info", value="\n".join(info_lines), inline=False)

        deep = item.get("deep", {})
        deep_lines = []

        investors = deep.get("investors", [])
        if investors:
            deep_lines.append(f"Investors: `{' • '.join(investors[:5])}`")

        team = deep.get("team")
        if team:
            deep_lines.append(f"Team: `{team[:80]}`")

        contract = deep.get("contract")
        if contract:
            deep_lines.append(f"Contract: `{contract}`")

        roadmap = deep.get("roadmap", [])
        if roadmap:
            deep_lines.append(f"Status: `{' • '.join(roadmap)}`")

        if deep.get("kyc_required"):
            deep_lines.append("KYC: `Required`")
        if deep.get("has_cost"):
            deep_lines.append("Cost/Gas: `Yes`")

        scam = deep.get("scam_signals", {})
        if scam.get("is_scam"):
            deep_lines.append("SCAM WARNING: `Auto-flagged`")
        elif scam.get("score", 0) > 0.15:
            deep_lines.append(f"Risk signal: `{scam.get('score', 0):.0%}`")

        if deep_lines:
            embed.add_field(name="Details", value="\n".join(deep_lines), inline=False)

        socials = deep.get("socials", {})
        if socials:
            social_text = []
            for platform, url in socials.items():
                if not url:
                    continue
                social_text.append(f"[{platform.title()}]({url})")
            if social_text:
                embed.add_field(name="Links", value=" • ".join(social_text), inline=False)

        embed.add_field(
            name="Rating",
            value=f"`{rating}/10` • {tier_label}",
            inline=False
        )

        embed.set_footer(text="DYOR • Fresh-only • No Nitter")
        return embed

    @classmethod
    def s_tier_alert(cls, item):
        embed = cls.card(item, is_new=True, highlight=True)
        embed.title = f"S-TIER ALERT • {item.get('name', 'Unknown')[:80]}"
        embed.description = (
            "High-potential project detected.\n\n"
            + (embed.description or "")
        )
        return embed

    @classmethod
    def status(cls, total, queued, detector, continuous_count=0):
        avg_age = "Empty"
        type_counts = {}
        tier_counts = detector.get_tier_counts()
        high_potential = detector.size_high_potential(7)

        if detector.queue:
            now = datetime.now()
            ages = []
            for it in detector.queue:
                try:
                    pub = datetime.fromisoformat(it.get("published_at", it.get("fetched_at", "2000-01-01")))
                    sec = (now - pub).total_seconds()
                    ages.append(sec)
                    t = it.get("type", "unknown")
                    type_counts[t] = type_counts.get(t, 0) + 1
                except:
                    pass
            if ages:
                avg_sec = sum(ages) / len(ages)
                if avg_sec > 86400:
                    avg_age = f"{avg_sec/86400:.1f} days"
                elif avg_sec > 3600:
                    avg_age = f"{avg_sec/3600:.0f} hours"
                else:
                    avg_age = f"{avg_sec/60:.0f} minutes"

        type_lines = "\n".join([f"   • `{k.upper()}`: {v}" for k, v in sorted(type_counts.items())])
        tier_lines = "\n".join([f"   • `{k.upper()}-Tier`: {v}" for k, v in sorted(tier_counts.items()) if v > 0])

        return discord.Embed(
            title="📊 Bot Status",
            description=(
                f"🟢 **Online** | Fresh-Only v14.0\n"
                f"📦 **{total}** items collected (last fetch)\n"
                f"📬 **{queued}** in queue (max 50)\n"
                f"🏆 **{high_potential}** high potential (rating 7+)\n"
                f"🔴 **{continuous_count}** channel(s) in continuous mode\n"
                f"⏰ Auto-send **1 item per 1 min** (fresh first)\n"
                f"🚨 S-Tier auto-alert every **2 min**\n"
                f"🛡️ Scam detection: **ON**\n"
                f"🕐 Avg queue age: **{avg_age}**\n\n"
                f"📂 **Category Breakdown:**\n{type_lines}\n\n"
                f"🏆 **Tier Breakdown:**\n{tier_lines}\n\n"
                f"🗑️ Auto-remove items >{int(MAX_ITEM_AGE_HOURS)}h or expired deadline\n"
                f"🔍 Deep scrape: ALL items\n"
                f"━━━━━━━━━━━━━━━"
            ),
            color=COLORS["header"],
            timestamp=datetime.now()
        )

    @classmethod
    def no_data(cls, t=""):
        extra = f" category `{t.upper()}`" if t else ""
        return discord.Embed(
            title="📭 No Data",
            description=f"**No{extra} data found.**\nBot monitors every {SCRAPE_INTERVAL_MINUTES} minutes.",
            color=COLORS["error"],
            timestamp=datetime.now()
        )

    @classmethod
    def help_menu(cls):
        embed = discord.Embed(
            title="🤖 AlphaDrop Bot v18.1 — Fresh-Only, Expanded NFT Sources",
            description=(
                "Monitoring **alpha project / NFT / airdrop sources only** — no CoinGecko, "
                "no DexScreener, no token price data.\n\n"
                "**S-Tier (9-10)** — Mega potential | Auto-alert\n"
                "**A-Tier (7-8)** — High potential | Top priority\n"
                "**B-Tier (5-6)** — Good potential\n"
                "**C-Tier (3-4)** — Average\n"
                "**D-Tier (1-2)** — Low priority\n\n"
                "**Categories:** Instant | Testnet | Waitlist | Watchlist | Retroactive | Early | NFT\n\n"
                "✅ Auto-send: 1 item/min (fresh first)\n"
                "🚨 S-Tier auto-alert every 2 min\n"
                "🛡️ Scam Detection: Lookalike domains, suspicious keywords\n"
                "✅ Deep Search: Team, Investors, Contract, Social, KYC, Cost\n"
                f"✅ Freshness: items >{int(MAX_ITEM_AGE_HOURS)}h old or with an expired deadline are dropped\n"
                f"✅ Anti-Spam: {DEDUP_WINDOW_DAYS}-day **persistent** dedup (survives restarts) | Max Queue: 50"
            ),
            color=COLORS["header"],
            timestamp=datetime.now()
        )
        embed.add_field(name="🪂 `!airdrop`", value="Start continuous mode", inline=False)
        embed.add_field(name="🛑 `!stop`", value="Stop continuous mode", inline=False)
        embed.add_field(name="🏆 `!top`", value="Show HIGHEST rated item (rating 7+)", inline=False)
        embed.add_field(name="💎 `!tier [s|a|b|c|d]`", value="Show item by tier (e.g. `!tier s`)", inline=False)
        embed.add_field(name="🔥 `!early`", value="View latest early alpha", inline=False)
        embed.add_field(name="🎨 `!nft`", value="View latest NFT drop", inline=False)
        embed.add_field(name="📊 `!status`", value="Bot & queue status with tier counts", inline=False)
        embed.add_field(name="❓ `!help`", value="This menu", inline=False)
        embed.set_footer(text="DYOR ⚠️")
        return embed

    @classmethod
    def continuous_on(cls, channel_name):
        return discord.Embed(
            title="🟢 Continuous Mode ON",
            description=(
                f"Channel **#{channel_name}** is now in continuous mode.\n"
                f"Bot will auto-send latest items here every **30 seconds** when available.\n\n"
                f"Use `!stop` to disable."
            ),
            color=COLORS["success"],
            timestamp=datetime.now()
        )

    @classmethod
    def continuous_off(cls, channel_name):
        return discord.Embed(
            title="🔴 Continuous Mode OFF",
            description=(
                f"Channel **#{channel_name}** continuous mode stopped.\n"
                f"Use `!airdrop` to re-enable."
            ),
            color=COLORS["error"],
            timestamp=datetime.now()
        )


# ==================== BOT ====================
class AlphaBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)
        self.start_time = datetime.now()
        self.detector = Detector()
        self.all_data = {}
        self.last_fetch = None
        self.continuous_channels = set()

    async def setup_hook(self):
        self.scraper.start()
        self.sender.start()
        self.continuous_sender.start()
        self.high_priority_sender.start()

    async def on_ready(self):
        print(f"🚀 {self.user} online!")
        print(f"📡 Servers: {len(self.guilds)}")
        print(f"🔔 Channel: {NOTIFY_CHANNEL_ID}")
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="High Potential Airdrops")
        )

    @tasks.loop(minutes=SCRAPE_INTERVAL_MINUTES)
    async def scraper(self):
        print(f"\n{'='*50}")
        print(f"🔍 [{datetime.now().strftime('%H:%M:%S')}] Scraping alpha/NFT/airdrop sources...")

        async with Fetcher() as f:
            self.all_data = await f.fetch_all()

        self.last_fetch = datetime.now()
        flat = []
        for src_items in self.all_data.values():
            if isinstance(src_items, list):
                flat.extend(src_items)

        before = self.detector.size()
        new_s_tier = self.detector.add(flat)
        after = self.detector.size()

        print(f"📦 Queue: {before} → {after} (+{after - before})")
        tier_counts = self.detector.get_tier_counts()
        print(f"🏆 Tiers in queue: S={tier_counts['s']} | A={tier_counts['a']} | B={tier_counts['b']} | C={tier_counts['c']} | D={tier_counts['d']}")

        if NOTIFY_CHANNEL_ID != 0 and new_s_tier:
            ch = self.get_channel(NOTIFY_CHANNEL_ID)
            if ch:
                for item in new_s_tier[:3]:
                    print(f"🚨 S-TIER ALERT: {item.get('name', 'Unknown')[:50]}...")
                    try:
                        await ch.send(embed=UI.s_tier_alert(item))
                    except Exception as e:
                        print(f"  ⚠️  Failed to send S-tier alert: {e}")

        if self.detector.first:
            self.detector.first = False
            ch = self.get_channel(NOTIFY_CHANNEL_ID)
            if ch:
                await ch.send(embed=discord.Embed(
                    title="🤖 AlphaDrop Bot Active — v13.0 Fresh-Only",
                    description=(
                        f"✅ Monitoring **{len(flat)}** fresh alpha/NFT/airdrop items (no token-price sources)\n"
                        f"🏆 **Tier System:** S-Tier (9-10) | A-Tier (7-8) | B-Tier (5-6) | C-Tier (3-4) | D-Tier (1-2)\n"
                        f"🛡️ **Scam Detection:** ON (lookalike domains, suspicious keywords)\n"
                        f"🚨 **S-Tier Auto-Alert:** Every 2 minutes + immediate on detection\n"
                        f"💎 **High Potential Filter:** `!top` for rating 7+ items\n"
                        f"📦 **{after}** items in queue | **{tier_counts['s']}** S-Tier | **{tier_counts['a']}** A-Tier\n"
                        f"🔔 Auto-send **1 item/min** + S-Tier priority\n"
                        f"⏱️ Rescan every **{SCRAPE_INTERVAL_MINUTES} min** | Items older than **{int(MAX_ITEM_AGE_HOURS)}h** or with an expired deadline are skipped\n"
                        f"🟢 `!airdrop` = continuous mode | `!tier s` = S-Tier only"
                    ),
                    color=COLORS["success"],
                    timestamp=datetime.now()
                ))
            print("✅ First run complete.")

    @scraper.before_loop
    async def before_scraper(self):
        await self.wait_until_ready()
        print(f"🔔 Scraper ready. Fresh-Only mode. Every {SCRAPE_INTERVAL_MINUTES} min.")

    @tasks.loop(minutes=1)
    async def sender(self):
        if NOTIFY_CHANNEL_ID == 0:
            return
        ch = self.get_channel(NOTIFY_CHANNEL_ID)
        if not ch:
            return

        item = self.detector.pop_high_potential(min_rating=6)
        if not item:
            item = self.detector.pop()

        if item:
            print(f"📤 Auto-send: {item.get('name', 'Unknown')[:50]}... (rating: {item.get('rating', 0)})")
            await ch.send(embed=UI.card(item, is_new=True))
        else:
            print("📤 Auto-send: Queue empty or all items stale.")

    @sender.before_loop
    async def before_sender(self):
        await self.wait_until_ready()
        print("📤 Sender ready. Auto-send every 1 min (high potential priority).")

    @tasks.loop(minutes=2)
    async def high_priority_sender(self):
        if NOTIFY_CHANNEL_ID == 0:
            return
        ch = self.get_channel(NOTIFY_CHANNEL_ID)
        if not ch:
            return

        item = self.detector.pop_high_potential(min_rating=9)
        if item:
            print(f"🚨 High Priority S-Tier: {item.get('name', 'Unknown')[:50]}...")
            await ch.send(embed=UI.s_tier_alert(item))

    @high_priority_sender.before_loop
    async def before_high_priority(self):
        await self.wait_until_ready()
        print("🚨 High Priority sender ready. S-Tier checks every 2 min.")

    @tasks.loop(seconds=30)
    async def continuous_sender(self):
        if not self.continuous_channels:
            return

        for channel_id in list(self.continuous_channels):
            ch = self.get_channel(channel_id)
            if not ch:
                self.continuous_channels.discard(channel_id)
                continue

            item = self.detector.pop()
            if item:
                print(f"📤 Continuous to #{ch.name}: {item.get('name', 'Unknown')[:50]}...")
                try:
                    await ch.send(embed=UI.card(item, is_new=True))
                except Exception as e:
                    print(f"  ⚠️  Failed to send to {channel_id}: {e}")
            else:
                print(f"📤 Continuous to #{ch.name}: Queue empty.")

    @continuous_sender.before_loop
    async def before_continuous_sender(self):
        await self.wait_until_ready()
        print("📤 Continuous sender ready. Checks every 30s.")


bot = AlphaBot()


@bot.command(name="help")
async def cmd_help(ctx):
    await ctx.send(embed=UI.help_menu())


@bot.command(name="airdrop")
async def cmd_airdrop(ctx):
    async with ctx.typing():
        channel_id = ctx.channel.id
        if channel_id in bot.continuous_channels:
            await ctx.send(embed=discord.Embed(
                title="⚠️ Already Active",
                description="Continuous mode is already ON for this channel. Use `!stop` to disable.",
                color=COLORS["error"], timestamp=datetime.now()
            ))
            return

        bot.continuous_channels.add(channel_id)
        await ctx.send(embed=UI.continuous_on(ctx.channel.name))

        item = bot.detector.pop()
        if item:
            await ctx.send(embed=UI.card(item, is_new=True))


@bot.command(name="stop")
async def cmd_stop(ctx):
    async with ctx.typing():
        channel_id = ctx.channel.id
        if channel_id not in bot.continuous_channels:
            await ctx.send(embed=discord.Embed(
                title="⚠️ Not Active",
                description="Continuous mode is not active for this channel. Use `!airdrop` to start.",
                color=COLORS["error"], timestamp=datetime.now()
            ))
            return

        bot.continuous_channels.discard(channel_id)
        await ctx.send(embed=UI.continuous_off(ctx.channel.name))


@bot.command(name="top")
async def cmd_top(ctx):
    async with ctx.typing():
        item = bot.detector.pop_high_potential(min_rating=7)
        if item:
            await ctx.send(embed=UI.card(item, is_new=True, highlight=True))
            return
        await ctx.send(embed=discord.Embed(
            title="📭 No High Potential Data",
            description=f"No items with rating 7+ found in queue.\nTry again after next scrape (every {SCRAPE_INTERVAL_MINUTES} min).",
            color=COLORS["error"], timestamp=datetime.now()
        ))


@bot.command(name="tier")
async def cmd_tier(ctx, tier_name=""):
    async with ctx.typing():
        if not tier_name or tier_name.lower() not in ["s", "a", "b", "c", "d"]:
            await ctx.send(embed=discord.Embed(
                title="⚠️ Invalid Tier",
                description="Usage: `!tier s` | `!tier a` | `!tier b` | `!tier c` | `!tier d`\n\n"
                           "🏆 S-Tier (9-10) | 💎 A-Tier (7-8) | ⚡ B-Tier (5-6) | 📈 C-Tier (3-4) | 📝 D-Tier (1-2)",
                color=COLORS["error"], timestamp=datetime.now()
            ))
            return

        item = bot.detector.pop_by_tier(tier_name)
        if item:
            highlight = tier_name.lower() in ["s", "a"]
            await ctx.send(embed=UI.card(item, is_new=True, highlight=highlight))
            return

        tier_labels = {"s": "S-Tier", "a": "A-Tier", "b": "B-Tier", "c": "C-Tier", "d": "D-Tier"}
        await ctx.send(embed=discord.Embed(
            title=f"📭 No {tier_labels[tier_name.lower()]} Data",
            description=f"No {tier_labels[tier_name.lower()]} items found in queue.",
            color=COLORS["error"], timestamp=datetime.now()
        ))


@bot.command(name="early")
async def cmd_early(ctx):
    async with ctx.typing():
        item = bot.detector.pop_early()
        if item:
            await ctx.send(embed=UI.card(item, is_new=True))
            return
        await ctx.send(embed=UI.no_data("early"))


@bot.command(name="nft")
async def cmd_nft(ctx):
    async with ctx.typing():
        item = bot.detector.pop_nft()
        if item:
            await ctx.send(embed=UI.card(item, is_new=True))
            return
        await ctx.send(embed=UI.no_data("nft"))


@bot.command(name="status")
async def cmd_status(ctx):
    flat = []
    for src_items in bot.all_data.values():
        if isinstance(src_items, list):
            flat.extend(src_items)
    await ctx.send(embed=UI.status(len(flat), bot.detector.size(), bot.detector, len(bot.continuous_channels)))


# ==================== RUN ====================
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("=" * 60)
        print("⚠️  DISCORD_TOKEN not found!")
        print("=" * 60)
        print("\nSet token & channel:")
        print("  export DISCORD_TOKEN='your_token'")
        print("  export NOTIFY_CHANNEL_ID='123456789012345678'")
        print("  python bot.py")
        print("=" * 60)
    else:
        bot.run(DISCORD_TOKEN)
