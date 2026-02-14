#!/usr/bin/env python3
"""
Healthcare Business Acquisition Deal Finder
Searches multiple sources for healthcare businesses matching your criteria.
Uses Claude AI to analyze each listing.
Outputs styled HTML report and deploys to GitHub Pages.

Author: Built for Griff
"""

import os
import re
import json
import subprocess
import requests
import time
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote_plus, urljoin

# For HTML parsing
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    print("Note: BeautifulSoup not installed. Run: pip3 install beautifulsoup4")

# For AI analysis
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    print("Note: anthropic package not installed. Run: pip3 install anthropic")

# ============================================================================
# CONFIGURATION
# ============================================================================

CONFIG = {
    "anthropic": {
        "api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
        "model": "claude-sonnet-4-20250514",
        "enabled": True,
    },

    "criteria": {
        "industries": [
            "medical practice", "healthcare", "home health", "home care",
            "senior care", "hospice", "physical therapy", "occupational therapy",
            "behavioral health", "mental health", "healthcare staffing",
            "medical billing", "dental practice", "optometry", "dermatology",
            "urgent care", "clinic", "nursing", "assisted living", "pharmacy",
            "psychiatry", "psychology", "counseling", "therapy", "ABA",
            "substance abuse", "addiction treatment", "rehabilitation",
        ],
        "min_price": 1_000_000,
        "max_price": 5_000_000,
        "locations": ["California", "CA", "Kentucky", "KY", "remote", "anywhere"],
        "keywords_positive": [
            "absentee", "semi-absentee", "manager in place", "management in place",
            "passive", "turnkey", "established", "stable", "recurring revenue",
            "SBA", "SBA eligible", "SBA qualified", "cash flow positive",
            "EBITDA", "cash flow", "SDE", "seller discretionary",
        ],
        "keywords_negative": [
            "owner-operator required", "full-time owner", "hands-on required"
        ],
    },

    "output": {
        "folder": os.environ.get("OUTPUT_DIR", str(Path.home() / "Documents" / "DealFinder")),
        "seen_deals_file": "seen_deals.json",
        "html_file": "index.html",
        "reports_dir": "reports",
        "archive_file": "archive.json",
        "max_reports": 12,
    },
}

# Browser-like headers to avoid blocks
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


@dataclass
class Deal:
    """Represents a business listing"""
    title: str
    source: str
    asking_price: Optional[str]
    revenue: Optional[str]
    cash_flow: Optional[str]
    location: Optional[str]
    description: Optional[str]
    url: str
    ebitda: Optional[str] = None
    ebitda_margin: Optional[str] = None
    owner_involvement: Optional[str] = None
    sba_eligible: Optional[str] = None
    score: int = 0
    found_date: str = ""
    # AI analysis fields
    whats_good: Optional[str] = None
    concerns: Optional[str] = None
    recommendation: Optional[str] = None
    fit_score: Optional[str] = None  # e.g., "A+", "B+", "C"
    criteria_tags: list = field(default_factory=list)  # [{"label": "CA", "type": "hit"}, ...]
    key_details: Optional[str] = None
    next_step: Optional[str] = None
    tier: int = 0  # 1 = top tier, 2 = worth watching, 3 = reference

    def __post_init__(self):
        if not self.found_date:
            self.found_date = datetime.now().strftime("%Y-%m-%d")


class DealFinder:
    """Main class to find and score healthcare business acquisitions"""

    def __init__(self, config: dict):
        self.config = config
        self.deals: list[Deal] = []
        self.seen_deals = self._load_seen_deals()
        self._ensure_output_folder()
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.scraper_api_key = os.environ.get("SCRAPER_API_KEY", "")

    def _fetch(self, url: str, timeout: int = 60, render: bool = False) -> Optional[requests.Response]:
        """Fetch a URL, routing through ScraperAPI if key is set (for cloud runs).
        Set render=True for JS-heavy pages (costs 5 credits instead of 1)."""
        try:
            if self.scraper_api_key:
                proxy_url = f"http://api.scraperapi.com?api_key={self.scraper_api_key}&url={url}"
                if render:
                    proxy_url += "&render=true"
                resp = self.session.get(proxy_url, timeout=timeout)
            else:
                resp = self.session.get(url, timeout=timeout)
            return resp
        except Exception as e:
            print(f"  Fetch error for {url}: {e}")
            return None

    def _ensure_output_folder(self):
        Path(self.config["output"]["folder"]).mkdir(parents=True, exist_ok=True)

    def _load_seen_deals(self) -> set:
        """Load seen deals — but only keep entries from the current run.
        We no longer persist across runs so every weekly scan gets fresh results.
        The set is used only for within-run deduplication."""
        return set()

    def _save_seen_deals(self):
        """Write seen_deals.json — kept empty so the next run starts fresh."""
        seen_file = Path(self.config["output"]["folder"]) / self.config["output"]["seen_deals_file"]
        with open(seen_file, "w") as f:
            json.dump([], f)

    # US state abbreviation to name mapping
    _STATE_ABBREVS = {
        'al': 'Alabama', 'ak': 'Alaska', 'az': 'Arizona', 'ar': 'Arkansas',
        'ca': 'California', 'co': 'Colorado', 'ct': 'Connecticut', 'de': 'Delaware',
        'fl': 'Florida', 'ga': 'Georgia', 'hi': 'Hawaii', 'id': 'Idaho',
        'il': 'Illinois', 'in': 'Indiana', 'ia': 'Iowa', 'ks': 'Kansas',
        'ky': 'Kentucky', 'la': 'Louisiana', 'me': 'Maine', 'md': 'Maryland',
        'ma': 'Massachusetts', 'mi': 'Michigan', 'mn': 'Minnesota', 'ms': 'Mississippi',
        'mo': 'Missouri', 'mt': 'Montana', 'ne': 'Nebraska', 'nv': 'Nevada',
        'nh': 'New Hampshire', 'nj': 'New Jersey', 'nm': 'New Mexico', 'ny': 'New York',
        'nc': 'North Carolina', 'nd': 'North Dakota', 'oh': 'Ohio', 'ok': 'Oklahoma',
        'or': 'Oregon', 'pa': 'Pennsylvania', 'ri': 'Rhode Island', 'sc': 'South Carolina',
        'sd': 'South Dakota', 'tn': 'Tennessee', 'tx': 'Texas', 'ut': 'Utah',
        'vt': 'Vermont', 'va': 'Virginia', 'wa': 'Washington', 'wv': 'West Virginia',
        'wi': 'Wisconsin', 'wy': 'Wyoming', 'dc': 'Washington DC',
    }

    def _extract_location_from_url(self, url: str, page_text: str = "", title: str = "") -> Optional[str]:
        """Extract location from URL slug, title, or page text. Prefers URL slug."""
        # Try URL slug first (e.g., "-nj/", "-ca/", "-az/")
        slug_match = re.search(r'-([a-z]{2})/?$', url.rstrip('/').lower())
        if slug_match:
            state_code = slug_match.group(1)
            if state_code in self._STATE_ABBREVS:
                return self._STATE_ABBREVS[state_code]

        # Try title for state names/city names
        combined = f"{title} {page_text[:500]}"
        for state_name in ['California', 'Kentucky', 'New York', 'New Jersey', 'Virginia',
                           'Maryland', 'Arizona', 'Texas', 'Connecticut', 'Florida',
                           'Pennsylvania', 'Illinois', 'Ohio', 'Colorado', 'Georgia',
                           'Massachusetts', 'Oregon', 'Washington', 'Tennessee', 'Michigan',
                           'North Carolina', 'South Carolina', 'Minnesota', 'Indiana']:
            if state_name in combined:
                return state_name

        # Try common city names
        for city, state in [('San Diego', 'California'), ('Los Angeles', 'California'),
                            ('San Francisco', 'California'), ('Houston', 'Texas'),
                            ('New York', 'New York'), ('Chicago', 'Illinois'),
                            ('Phoenix', 'Arizona'), ('Denver', 'Colorado'),
                            ('Fresno', 'California'), ('Sacramento', 'California')]:
            if city in combined:
                return f"{city}, {state}"

        return None

    def _score_deal(self, deal: Deal) -> int:
        score = 0
        text = f"{deal.title} {deal.description or ''} {deal.location or ''}".lower()

        for loc in self.config["criteria"]["locations"]:
            if loc.lower() in text:
                score += 20
                break

        for kw in self.config["criteria"]["keywords_positive"]:
            if kw.lower() in text:
                score += 10
                if "absentee" in kw.lower():
                    deal.owner_involvement = "Low (absentee mentioned)"
                if "sba" in kw.lower():
                    deal.sba_eligible = "Likely (SBA mentioned)"

        for kw in self.config["criteria"]["keywords_negative"]:
            if kw.lower() in text:
                score -= 15
                deal.owner_involvement = "High (owner-operator mentioned)"

        for ind in self.config["criteria"]["industries"]:
            if ind.lower() in text:
                score += 5

        return score

    def _parse_price(self, price_str: str) -> Optional[int]:
        """Extract numeric price from string like '$1,200,000' or '1.2M'"""
        if not price_str:
            return None
        price_str = price_str.upper().replace(",", "").replace("$", "").strip()
        try:
            if "M" in price_str:
                return int(float(price_str.replace("M", "").strip()) * 1_000_000)
            elif "K" in price_str:
                return int(float(price_str.replace("K", "").strip()) * 1_000)
            else:
                num = re.sub(r'[^\d.]', '', price_str)
                if num:
                    return int(float(num))
        except (ValueError, TypeError):
            pass
        return None

    def _is_in_price_range(self, price_str: str) -> bool:
        """Check if asking price is between $1M and $5M"""
        price = self._parse_price(price_str)
        if price is None:
            return True  # Include if price unknown - let AI analyze
        return self.config["criteria"]["min_price"] <= price <= self.config["criteria"]["max_price"]

    def _analyze_deal_with_claude(self, deal: Deal) -> Deal:
        """Use Claude to analyze a deal and produce structured output for the HTML report"""
        if not ANTHROPIC_AVAILABLE or not self.config.get("anthropic", {}).get("enabled", False):
            return deal

        try:
            client = anthropic.Anthropic(api_key=self.config["anthropic"]["api_key"])

            prompt = f"""Analyze this healthcare business listing for acquisition. Provide a structured analysis.

LISTING:
- Title: {deal.title}
- Source: {deal.source}
- Asking Price: {deal.asking_price or 'Not listed'}
- Revenue: {deal.revenue or 'Not listed'}
- Cash Flow / SDE: {deal.cash_flow or 'Not listed'}
- EBITDA: {deal.ebitda or 'Not listed'}
- Location: {deal.location or 'Not listed'}
- Description: {deal.description or 'Not available'}
- URL: {deal.url}

BUYER CRITERIA:
- Healthcare services (behavioral health, mental health, psychiatry, therapy, home health, allied health)
- Asking Price: $1M - $5M
- Need to see financial metrics (cash flow, SDE, EBITDA) but no strict threshold - just need them to exist
- Locations preferred: California, Kentucky, or remote/telehealth
- Priorities: Semi-absentee or manager in place, stable operations, SBA-financeable
- Interested in: multi-provider practices, insurance-paneled, therapy + prescribing combos

Respond in this EXACT format (each field on its own line):

FIT_SCORE: [A+/A/B+/B/B-/C+/C/C-]
TIER: [1 if strong match, 2 if worth watching, 3 if marginal]
RECOMMENDATION: [Pursue/Investigate/Skip] - [one sentence reason]
CRITERIA_TAGS: [comma-separated list of tags, each prefixed with +, -, or ? to indicate meets/fails/unknown. Example: +CA, +Multi-provider, -Too expensive, ?EBITDA unknown]
KEY_DETAILS: [2-4 sentences about red flags, opportunities, and strategic notes]
NEXT_STEP: [specific action to take, e.g., "Sign NDA to see CIM" or "Request financials"]"""

            response = client.messages.create(
                model=self.config["anthropic"]["model"],
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}]
            )

            analysis = response.content[0].text

            # Parse structured response
            for line in analysis.split("\n"):
                line = line.strip()
                if line.startswith("FIT_SCORE:"):
                    deal.fit_score = line.split(":", 1)[1].strip()
                elif line.startswith("TIER:"):
                    try:
                        deal.tier = int(line.split(":", 1)[1].strip()[0])
                    except (ValueError, IndexError):
                        deal.tier = 2
                elif line.startswith("RECOMMENDATION:"):
                    deal.recommendation = line.split(":", 1)[1].strip()
                elif line.startswith("CRITERIA_TAGS:"):
                    tags_str = line.split(":", 1)[1].strip()
                    deal.criteria_tags = []
                    for tag in tags_str.split(","):
                        tag = tag.strip()
                        if tag.startswith("+"):
                            deal.criteria_tags.append({"label": tag[1:].strip(), "type": "hit"})
                        elif tag.startswith("-"):
                            deal.criteria_tags.append({"label": tag[1:].strip(), "type": "miss"})
                        elif tag.startswith("?"):
                            deal.criteria_tags.append({"label": tag[1:].strip(), "type": "maybe"})
                        elif tag:
                            deal.criteria_tags.append({"label": tag, "type": "maybe"})
                elif line.startswith("KEY_DETAILS:"):
                    deal.key_details = line.split(":", 1)[1].strip()
                elif line.startswith("NEXT_STEP:"):
                    deal.next_step = line.split(":", 1)[1].strip()

        except Exception as e:
            print(f"  AI analysis failed: {e}")
            deal.recommendation = "Investigate - AI analysis failed"
            deal.fit_score = "?"
            deal.tier = 2

        return deal

    # =========================================================================
    # SEARCH METHODS
    # =========================================================================

    def search_bizbuysell(self):
        """Scrape listings from BizBuySell"""
        print("Searching BizBuySell...")

        if not BS4_AVAILABLE:
            print("  Skipping - BeautifulSoup not installed")
            return

        urls = [
            "https://www.bizbuysell.com/california/health-care-and-fitness-businesses-for-sale/",
            "https://www.bizbuysell.com/kentucky/health-care-and-fitness-businesses-for-sale/",
        ]

        for search_url in urls:
            try:
                time.sleep(1)
                resp = self._fetch(search_url, render=True)
                if not resp or resp.status_code != 200:
                    print(f"  Got status {getattr(resp, 'status_code', 'None')} from BizBuySell")
                    continue

                soup = BeautifulSoup(resp.text, 'html.parser')
                state_name = search_url.split('/')[3]
                default_location = "California" if "california" in search_url else "Kentucky"

                # BizBuySell uses Angular — the best approach is to find all
                # Business-Opportunity links and extract context from surrounding text
                all_biz_links = soup.find_all('a', href=re.compile(r'/[Bb]usiness-[Oo]pportunity/'))
                print(f"  Found {len(all_biz_links)} Business-Opportunity links from {state_name}")

                processed_urls = set()
                added = 0

                for link_tag in all_biz_links[:25]:
                    try:
                        link = link_tag.get('href', '')
                        full_url = urljoin('https://www.bizbuysell.com', link)

                        if full_url in processed_urls or full_url in self.seen_deals:
                            continue
                        processed_urls.add(full_url)

                        # Get link text as title, but truncate at location/description boundary
                        raw_title = link_tag.get_text(strip=True)
                        if not raw_title or len(raw_title) < 5:
                            continue

                        # Title often concatenates with location + description.
                        # Try to split at state/city patterns
                        title = raw_title
                        for split_pat in [r'(?:Louisville|Lexington|Bowling Green|San Diego|Los Angeles|Sacramento|San Francisco|Fresno)',
                                          r'(?:California|Kentucky|CA|KY)',
                                          r'(?:\w+ County)']:
                            m = re.search(split_pat, raw_title)
                            if m and m.start() > 15:  # Only split if there's enough title before
                                title = raw_title[:m.start()].rstrip(' ,.-')
                                break

                        # Get surrounding context for price/financials
                        # Walk up to find parent container with pricing info
                        context_el = link_tag.parent
                        for _ in range(5):
                            if context_el and context_el.parent:
                                context_text = context_el.get_text(' ', strip=True)
                                if '$' in context_text and len(context_text) > 50:
                                    break
                                context_el = context_el.parent

                        context_text = context_el.get_text(' ', strip=True) if context_el else raw_title

                        # Extract price
                        price = None
                        # Look for asking price pattern
                        asking_match = re.search(r'(?:asking|price)[:\s]*\$?([\d,]+)', context_text, re.IGNORECASE)
                        if asking_match:
                            price = f"${asking_match.group(1)}"
                        else:
                            # Find all dollar amounts and pick the most likely asking price
                            all_prices = re.findall(r'\$([\d,]+)', context_text)
                            for p in all_prices:
                                parsed = self._parse_price(f"${p}")
                                if parsed and 100_000 <= parsed <= 50_000_000:
                                    price = f"${p}"
                                    break

                        if not self._is_in_price_range(price):
                            continue

                        # Extract financials
                        cf_match = re.search(r'Cash Flow[:\s]*\$?([\d,]+)', context_text, re.IGNORECASE)
                        rev_match = re.search(r'Revenue[:\s]*\$?([\d,]+)', context_text, re.IGNORECASE)

                        # Extract location from link text or context
                        location = default_location
                        loc_match = re.search(r'((?:Louisville|Lexington|Bowling Green|San Diego|Los Angeles|Sacramento|San Francisco|Fresno|[\w\s]+ County)[,\s]*(?:CA|KY|California|Kentucky)?)', raw_title + ' ' + context_text[:200])
                        if loc_match:
                            location = loc_match.group(1).strip().rstrip(',')

                        # Description from context
                        desc = context_text[:400] if context_text != raw_title else raw_title

                        deal = Deal(
                            title=title[:100],
                            source="BizBuySell",
                            asking_price=price,
                            revenue=f"${rev_match.group(1)}" if rev_match else None,
                            cash_flow=f"${cf_match.group(1)}" if cf_match else None,
                            location=location,
                            description=desc,
                            url=full_url,
                        )
                        deal.score = self._score_deal(deal)
                        self.deals.append(deal)
                        added += 1

                    except Exception:
                        continue

                print(f"  Added {added} BizBuySell deals from {state_name}")

            except Exception as e:
                print(f"  Error: {e}")

    def search_dealstream(self):
        """Search DealStream for healthcare businesses"""
        print("Searching DealStream...")

        if not BS4_AVAILABLE:
            return

        urls = [
            "https://dealstream.com/california/health-care-businesses-for-sale",
            "https://dealstream.com/california/behavioral-health-businesses-for-sale",
            "https://dealstream.com/california/home-health-care-businesses-for-sale",
            "https://dealstream.com/california/medical-practices-for-sale",
            "https://dealstream.com/kentucky/health-care-businesses-for-sale",
            "https://dealstream.com/home-health-care-businesses-for-sale",
            "https://dealstream.com/counseling-businesses-for-sale",
        ]

        for search_url in urls:
            try:
                time.sleep(1.5)
                resp = self._fetch(search_url, render=True)
                if not resp or resp.status_code != 200:
                    print(f"  Got status {getattr(resp, 'status_code', 'None')} from DealStream ({search_url.split('/')[-1]})")
                    continue

                page_slug = search_url.split('/')[-1]

                soup = BeautifulSoup(resp.text, 'html.parser')

                # DealStream: find all links to individual listing pages
                # Listing URLs look like /businesses-for-sale/<id> or /listing/<id>
                all_links = soup.find_all('a', href=True)
                listing_links = []
                processed_urls = set()
                for a in all_links:
                    href = a.get('href', '')
                    full = urljoin('https://dealstream.com', href)
                    # DealStream listing detail pages have numeric IDs or specific slugs
                    if re.search(r'dealstream\.com/[^/]+/[^/]+-\d+', full):
                        if full not in processed_urls:
                            processed_urls.add(full)
                            listing_links.append((a, full))

                # Also try card-based selectors
                cards = soup.select('[class*="listing"], [class*="card"], [class*="result"], article')

                print(f"  Found {len(listing_links)} listing links, {len(cards)} cards from DealStream ({page_slug})")

                added = 0

                # Process cards first (they have more context)
                for listing in cards[:20]:
                    try:
                        link_tag = listing.find('a', href=True) if listing.name != 'a' else listing
                        if not link_tag:
                            continue

                        link = link_tag.get('href', '')
                        full_url = urljoin('https://dealstream.com', link)

                        if 'dealstream.com' not in full_url:
                            continue
                        skip_patterns = ['/businesses-for-sale', '/small-businesses', '/search',
                                         'inc.com', 'facebook.com', 'twitter.com', 'linkedin.com']
                        if any(pat in full_url.lower() for pat in skip_patterns):
                            # Exception: allow if URL has a numeric ID (actual listing)
                            if not re.search(r'-\d+$', full_url.rstrip('/')):
                                continue
                        if full_url.rstrip('/') == 'https://dealstream.com':
                            continue

                        if full_url in self.seen_deals:
                            continue

                        text = listing.get_text(' ', strip=True)
                        title_tag = listing.find(['h2', 'h3', 'h4', 'h5']) or listing.find(class_=re.compile(r'title|name|heading'))
                        title = title_tag.get_text(strip=True) if title_tag else ""
                        if not title:
                            title = link_tag.get_text(strip=True)

                        if not title or title.lower() in ('healthcare business', 'business for sale', 'view listing', ''):
                            continue

                        if 'no listings found' in text.lower():
                            continue

                        # Extract price from card text
                        price = None
                        price_patterns = [
                            r'(?:asking|price)[:\s]*\$?([\d,\.]+[MK]?)',
                            r'\$([\d,]{7,})',
                            r'\$([\d,]+)',
                        ]
                        for pat in price_patterns:
                            m = re.search(pat, text, re.IGNORECASE)
                            if m:
                                val = self._parse_price(f"${m.group(1)}")
                                if val and val >= 100_000:
                                    price = f"${m.group(1)}"
                                    break

                        if not self._is_in_price_range(price):
                            continue

                        rev_match = re.search(r'(?:revenue|gross)[:\s]*\$?([\d,\.]+[MK]?)', text, re.IGNORECASE)
                        cf_match = re.search(r'(?:cash flow|SDE|EBITDA)[:\s]*\$?([\d,\.]+[MK]?)', text, re.IGNORECASE)

                        loc_tag = listing.find(class_=re.compile(r'location|city|state|geo'))
                        location = loc_tag.get_text(strip=True) if loc_tag else None

                        desc = listing.get_text(strip=True)[:300]

                        deal = Deal(
                            title=title[:100],
                            source="DealStream",
                            asking_price=price,
                            revenue=f"${rev_match.group(1)}" if rev_match else None,
                            cash_flow=f"${cf_match.group(1)}" if cf_match else None,
                            location=location,
                            description=desc,
                            url=full_url,
                        )
                        deal.score = self._score_deal(deal)
                        self.deals.append(deal)
                        added += 1

                    except Exception:
                        continue

                print(f"  Added {added} DealStream deals from {page_slug}")

            except Exception as e:
                print(f"  DealStream error: {e}")

    def search_american_healthcare_capital(self):
        """Search American Healthcare Capital for listings"""
        print("Searching American Healthcare Capital...")

        if not BS4_AVAILABLE:
            return

        urls = [
            "https://americanhealthcarecapital.com/current-listings/",
            "https://americanhealthcarecapital.com/listings-by-category/",
        ]

        for search_url in urls:
            try:
                time.sleep(1.5)
                resp = self._fetch(search_url)
                if not resp or resp.status_code != 200:
                    print(f"  Got status {resp.status_code} from AHC")
                    continue

                soup = BeautifulSoup(resp.text, 'html.parser')

                # AHC uses /listing/CODE/ URL pattern
                listing_links = soup.find_all('a', href=re.compile(r'/listing/'))
                seen_urls_local = set()

                print(f"  Found {len(listing_links)} listing links from AHC")

                for link_tag in listing_links[:25]:
                    try:
                        link = link_tag.get('href', '')
                        full_url = urljoin('https://americanhealthcarecapital.com', link)

                        if full_url in self.seen_deals or full_url in seen_urls_local:
                            continue
                        seen_urls_local.add(full_url)

                        title = link_tag.get_text(strip=True)
                        if not title or len(title) < 5:
                            continue

                        # Try to fetch the individual listing page for more detail
                        time.sleep(1)
                        detail_resp = self._fetch(full_url)
                        if not detail_resp or detail_resp.status_code != 200:
                            continue

                        detail_soup = BeautifulSoup(detail_resp.text, 'html.parser')
                        page_text = detail_soup.get_text()

                        # Extract financials from listing page
                        price_match = re.search(r'(?:asking price|price)[:\s]*\$?([\d,\.]+[MK]?)', page_text, re.IGNORECASE)
                        rev_match = re.search(r'(?:revenue|gross revenue)[:\s]*\$?([\d,\.]+[MK]?)', page_text, re.IGNORECASE)
                        ebitda_match = re.search(r'(?:EBITDA)[:\s]*\$?([\d,\.]+[MK]?)', page_text, re.IGNORECASE)
                        cf_match = re.search(r'(?:cash flow|SDE)[:\s]*\$?([\d,\.]+[MK]?)', page_text, re.IGNORECASE)
                        loc_match = re.search(r'(?:location|based in|located in)[:\s]*([\w\s,]+?)(?:\.|$)', page_text, re.IGNORECASE)

                        price = f"${price_match.group(1)}" if price_match else None
                        if not self._is_in_price_range(price):
                            continue

                        # Get description from meta or first paragraph
                        meta_desc = detail_soup.find('meta', attrs={'name': 'description'})
                        desc = meta_desc.get('content', '') if meta_desc else ''
                        if not desc:
                            first_p = detail_soup.find('p')
                            desc = first_p.get_text(strip=True)[:500] if first_p else ""

                        deal = Deal(
                            title=title[:100],
                            source="American Healthcare Capital",
                            asking_price=price,
                            revenue=f"${rev_match.group(1)}" if rev_match else None,
                            cash_flow=f"${cf_match.group(1)}" if cf_match else None,
                            ebitda=f"${ebitda_match.group(1)}" if ebitda_match else None,
                            location=loc_match.group(1).strip() if loc_match else None,
                            description=desc[:500],
                            url=full_url,
                        )
                        deal.score = self._score_deal(deal)
                        self.deals.append(deal)

                    except Exception:
                        continue

            except Exception as e:
                print(f"  AHC error: {e}")

    def search_synergy(self):
        """Search Synergy Business Brokers"""
        print("Searching Synergy Business Brokers...")

        if not BS4_AVAILABLE:
            return

        urls = [
            "https://synergybb.com/businesses-for-sale/mental-healthcare-facilities-for-sale/",
            "https://synergybb.com/businesses-for-sale/medical-practices-for-sale/",
            "https://synergybb.com/industries/buy-a-health-care-company/",
        ]

        for search_url in urls:
            try:
                time.sleep(1.5)
                resp = self._fetch(search_url)
                if not resp or resp.status_code != 200:
                    print(f"  Got status {resp.status_code} from Synergy")
                    continue

                soup = BeautifulSoup(resp.text, 'html.parser')

                # Find listing links
                listing_links = soup.find_all('a', href=re.compile(r'/listings/'))
                seen_urls_local = set()

                print(f"  Found {len(listing_links)} listing links from Synergy")

                for link_tag in listing_links[:15]:
                    try:
                        link = link_tag.get('href', '')
                        full_url = urljoin('https://synergybb.com', link)

                        if full_url in self.seen_deals or full_url in seen_urls_local:
                            continue
                        seen_urls_local.add(full_url)

                        title = link_tag.get_text(strip=True)
                        if not title or len(title) < 5:
                            continue

                        # Fetch individual listing
                        time.sleep(1)
                        detail_resp = self._fetch(full_url)
                        if not detail_resp or detail_resp.status_code != 200:
                            continue

                        detail_soup = BeautifulSoup(detail_resp.text, 'html.parser')
                        page_text = detail_soup.get_text()

                        price_match = re.search(r'(?:asking price|price)[:\s]*\$?([\d,\.]+[MK]?)', page_text, re.IGNORECASE)
                        rev_match = re.search(r'(?:revenue|gross)[:\s]*\$?([\d,\.]+[MK]?)', page_text, re.IGNORECASE)
                        cf_match = re.search(r'(?:cash flow|SDE|EBITDA|profit)[:\s]*\$?([\d,\.]+[MK]?)', page_text, re.IGNORECASE)

                        price = f"${price_match.group(1)}" if price_match else None
                        if not self._is_in_price_range(price):
                            continue

                        # Extract location from URL slug (e.g., "-nj/", "-va/", "-az/")
                        location = self._extract_location_from_url(full_url, page_text, title)

                        meta_desc = detail_soup.find('meta', attrs={'name': 'description'})
                        desc = meta_desc.get('content', '') if meta_desc else ''
                        if not desc:
                            first_p = detail_soup.find('p')
                            desc = first_p.get_text(strip=True)[:500] if first_p else ""

                        deal = Deal(
                            title=title[:100],
                            source="Synergy Business Brokers",
                            asking_price=price,
                            revenue=f"${rev_match.group(1)}" if rev_match else None,
                            cash_flow=f"${cf_match.group(1)}" if cf_match else None,
                            location=location,
                            description=desc[:500],
                            url=full_url,
                        )
                        deal.score = self._score_deal(deal)
                        self.deals.append(deal)

                    except Exception:
                        continue

            except Exception as e:
                print(f"  Synergy error: {e}")

    def search_transition_consultants(self):
        """Search Transition Consultants"""
        print("Searching Transition Consultants...")

        if not BS4_AVAILABLE:
            return

        urls = [
            "https://www.transitionconsultants.com/practices-for-sale",
        ]

        for search_url in urls:
            try:
                time.sleep(1.5)
                resp = self._fetch(search_url)
                if not resp or resp.status_code != 200:
                    print(f"  Got status {resp.status_code} from Transition Consultants")
                    continue

                soup = BeautifulSoup(resp.text, 'html.parser')

                # Find practice listing links
                listing_links = soup.find_all('a', href=re.compile(r'/practices-for-sale/'))
                seen_urls_local = set()

                print(f"  Found {len(listing_links)} listing links from Transition Consultants")

                for link_tag in listing_links[:15]:
                    try:
                        link = link_tag.get('href', '')
                        # Skip category links, only want individual listings
                        if link.count('/') < 4:
                            continue

                        full_url = urljoin('https://www.transitionconsultants.com', link)

                        if full_url in self.seen_deals or full_url in seen_urls_local:
                            continue
                        seen_urls_local.add(full_url)

                        title = link_tag.get_text(strip=True)
                        if not title or len(title) < 5 or 'SOLD' in title.upper():
                            continue

                        # Fetch individual listing
                        time.sleep(1)
                        detail_resp = self._fetch(full_url)
                        if not detail_resp or detail_resp.status_code != 200:
                            continue

                        detail_soup = BeautifulSoup(detail_resp.text, 'html.parser')
                        page_text = detail_soup.get_text()

                        price_match = re.search(r'(?:asking price|price|listed at)[:\s]*\$?([\d,\.]+[MK]?)', page_text, re.IGNORECASE)
                        rev_match = re.search(r'(?:revenue|collections|gross)[:\s]*\$?([\d,\.]+[MK]?)', page_text, re.IGNORECASE)

                        price = f"${price_match.group(1)}" if price_match else None
                        if not self._is_in_price_range(price):
                            continue

                        # Extract location from title or text
                        loc_match = re.search(r'(California|CA|Kentucky|KY|[\w\s]+County)', title + " " + page_text[:500], re.IGNORECASE)

                        meta_desc = detail_soup.find('meta', attrs={'name': 'description'})
                        desc = meta_desc.get('content', '') if meta_desc else ''
                        if not desc:
                            article = detail_soup.find(['article', '.content', 'main'])
                            if article:
                                desc = article.get_text(strip=True)[:500]

                        deal = Deal(
                            title=title[:100],
                            source="Transition Consultants",
                            asking_price=price,
                            revenue=f"${rev_match.group(1)}" if rev_match else None,
                            cash_flow=None,
                            location=loc_match.group(1).strip() if loc_match else None,
                            description=desc[:500],
                            url=full_url,
                        )
                        deal.score = self._score_deal(deal)
                        self.deals.append(deal)

                    except Exception:
                        continue

            except Exception as e:
                print(f"  TC error: {e}")

    def search_loopnet(self):
        """Search LoopNet for healthcare businesses"""
        print("Searching LoopNet...")

        if not BS4_AVAILABLE:
            return

        urls = [
            "https://www.loopnet.com/search/businesses-for-sale/california/for-sale/?sk=healthcare",
            "https://www.loopnet.com/search/businesses-for-sale/kentucky/for-sale/?sk=healthcare",
        ]

        for search_url in urls:
            try:
                time.sleep(1)
                resp = self._fetch(search_url)
                if not resp or resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.text, 'html.parser')
                listings = soup.select('[class*="listing"], [class*="property-card"], article')

                for listing in listings[:10]:
                    try:
                        link_tag = listing.find('a', href=True)
                        if not link_tag:
                            continue

                        url = urljoin('https://www.loopnet.com', link_tag.get('href', ''))
                        if url in self.seen_deals or 'loopnet.com' not in url:
                            continue

                        title = listing.find(['h2', 'h3', 'h4'])
                        title = title.get_text(strip=True) if title else "Business for Sale"

                        price_match = re.search(r'\$[\d,]+', listing.get_text())
                        price = price_match.group() if price_match else None

                        if not self._is_in_price_range(price):
                            continue

                        deal = Deal(
                            title=title[:100],
                            source="LoopNet",
                            asking_price=price,
                            revenue=None,
                            cash_flow=None,
                            location="CA/KY",
                            description=listing.get_text(strip=True)[:300],
                            url=url,
                        )
                        deal.score = self._score_deal(deal)
                        self.deals.append(deal)

                    except Exception:
                        continue

            except Exception as e:
                print(f"  LoopNet error: {e}")

    def search_businessesforsale(self):
        """Search BusinessesForSale.com"""
        print("Searching BusinessesForSale.com...")

        if not BS4_AVAILABLE:
            return

        urls = [
            "https://www.businessesforsale.com/us/search/healthcare-businesses-for-sale-in-california",
            "https://www.businessesforsale.com/us/search/healthcare-businesses-for-sale-in-kentucky",
        ]

        for search_url in urls:
            try:
                time.sleep(1)
                resp = self._fetch(search_url)
                if not resp or resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.text, 'html.parser')
                listings = soup.select('.listing, .search-result, [itemtype*="Product"]')

                for listing in listings[:10]:
                    try:
                        link = listing.find('a', href=True)
                        if not link:
                            continue

                        url = urljoin('https://www.businessesforsale.com', link.get('href', ''))
                        if url in self.seen_deals:
                            continue

                        title = listing.find(['h2', 'h3', 'h4', '.title'])
                        title = title.get_text(strip=True) if title else "Healthcare Business"

                        price_match = re.search(r'\$[\d,]+', listing.get_text())
                        price = price_match.group() if price_match else None

                        if not self._is_in_price_range(price):
                            continue

                        deal = Deal(
                            title=title[:100],
                            source="BusinessesForSale",
                            asking_price=price,
                            revenue=None,
                            cash_flow=None,
                            location="CA/KY",
                            description=listing.get_text(strip=True)[:300],
                            url=url,
                        )
                        deal.score = self._score_deal(deal)
                        self.deals.append(deal)

                    except Exception:
                        continue

            except Exception as e:
                print(f"  BFS error: {e}")


    def run_all_searches(self):
        """Run all search sources"""
        print(f"\n{'='*60}")
        print(f"Healthcare Deal Finder - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"Criteria: $1M-$5M asking price | Healthcare/Behavioral Health")
        print(f"{'='*60}\n")

        self.search_bizbuysell()
        self.search_dealstream()
        self.search_american_healthcare_capital()
        self.search_synergy()
        self.search_transition_consultants()
        self.search_loopnet()
        self.search_businessesforsale()

        # Remove duplicates by URL and filter junk titles
        junk_titles = {"all matching deals", "businesses for sale", "search results",
                       "business for sale", "view listing", ""}
        seen_urls = set()
        unique_deals = []
        filtered_count = 0
        for deal in self.deals:
            title_lower = deal.title.lower().strip()
            if deal.url in seen_urls:
                continue
            if title_lower in junk_titles:
                filtered_count += 1
                continue
            if 'no listings found' in (deal.description or '').lower():
                filtered_count += 1
                continue
            if len(deal.title) <= 5:
                filtered_count += 1
                continue
            seen_urls.add(deal.url)
            unique_deals.append(deal)
        if filtered_count:
            print(f"  Filtered out {filtered_count} junk/duplicate entries")
        self.deals = unique_deals

        # Sort by score
        self.deals.sort(key=lambda d: d.score, reverse=True)

        print(f"\nFound {len(self.deals)} unique deals in price range")
        self._save_seen_deals()

    def analyze_all_deals(self):
        """Run AI analysis on all deals"""
        if not self.config.get("anthropic", {}).get("enabled", False):
            print("AI analysis disabled")
            return

        print(f"\nAnalyzing {len(self.deals)} deals with Claude...")
        for i, deal in enumerate(self.deals):
            print(f"  [{i+1}/{len(self.deals)}] {deal.title[:50]}...")
            self._analyze_deal_with_claude(deal)
            time.sleep(0.5)
        print("AI analysis complete")

    # =========================================================================
    # HTML REPORT GENERATION
    # =========================================================================

    # Shared CSS used in both dated reports and hub page
    _REPORT_CSS = """
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 2rem; background: #f8f9fa; color: #1a1a1a; }
  h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
  .subtitle { color: #666; font-size: 0.9rem; margin-bottom: 1.5rem; }
  .criteria { background: #e8f4f8; border-left: 4px solid #0077b6; padding: 0.75rem 1rem; margin-bottom: 1.5rem; font-size: 0.85rem; border-radius: 0 4px 4px 0; }
  .criteria strong { color: #0077b6; }
  .summary { display: flex; gap: 1rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
  .summary-card { background: #fff; padding: 0.75rem 1.25rem; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); text-align: center; min-width: 100px; }
  .summary-card .num { font-size: 1.5rem; font-weight: 700; }
  .summary-card .label { font-size: 0.75rem; color: #666; text-transform: uppercase; }
  .num-pursue { color: #155724; }
  .num-investigate { color: #856404; }
  .num-skip { color: #721c24; }
  table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); font-size: 0.82rem; }
  th { background: #0077b6; color: #fff; padding: 10px 12px; text-align: left; font-weight: 600; white-space: nowrap; }
  td { padding: 10px 12px; border-bottom: 1px solid #eee; vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #f0f7ff; }
  a { color: #0077b6; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .tag { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.72rem; font-weight: 600; margin: 1px 2px; }
  .tag-ca { background: #d4edda; color: #155724; }
  .tag-notca { background: #fff3cd; color: #856404; }
  .tag-hit { background: #d4edda; color: #155724; }
  .tag-miss { background: #f8d7da; color: #721c24; }
  .tag-maybe { background: #fff3cd; color: #856404; }
  .fit-score { font-weight: 700; font-size: 1rem; }
  .fit-high { color: #155724; }
  .fit-med { color: #856404; }
  .fit-low { color: #721c24; }
  .notes { font-size: 0.78rem; color: #555; line-height: 1.4; }
  .section-header { background: #f1f3f5; }
  .section-header td { font-weight: 700; color: #0077b6; font-size: 0.9rem; padding: 8px 12px; }
  .source { font-size: 0.72rem; color: #888; }
  .action { font-size: 0.78rem; color: #0077b6; font-weight: 600; }
  .legend { font-size: 0.8rem; color: #666; margin-bottom: 1rem; }
  .bottom-note { margin-top: 1.5rem; padding: 1rem; background: #fff; border-radius: 8px; font-size: 0.85rem; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
  .bottom-note h3 { margin: 0 0 0.5rem 0; font-size: 0.95rem; color: #0077b6; }
  .last-updated { text-align: center; color: #999; font-size: 0.75rem; margin-top: 2rem; }
  .quick-links { margin-bottom: 1.5rem; }
  .quick-links h3 { font-size: 0.9rem; color: #333; margin: 0 0 0.5rem 0; }
  .quick-links a { display: inline-block; padding: 6px 14px; margin: 3px 4px; background: #0077b6; color: #fff; border-radius: 6px; font-size: 0.78rem; font-weight: 600; text-decoration: none; transition: background 0.2s; }
  .quick-links a:hover { background: #005f8f; color: #fff; text-decoration: none; }
  .quick-links .source-note { font-size: 0.72rem; color: #999; margin-top: 0.4rem; }
  .scan-toolbar { display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem; flex-wrap: wrap; }
  #scanBtn { background: #28a745; color: #fff; border: none; padding: 10px 20px; border-radius: 6px; font-size: 0.85rem; font-weight: 600; cursor: pointer; transition: background 0.2s; }
  #scanBtn:hover { background: #218838; }
  #scanBtn:disabled { background: #ccc; cursor: not-allowed; }
  .scan-pending { color: #856404; font-size: 0.85rem; }
  .scan-success { color: #155724; font-size: 0.85rem; }
  .scan-error { color: #721c24; font-size: 0.85rem; }
  .scan-link { font-size: 0.72rem; color: #999; }
  .tab-bar { display: flex; gap: 0.5rem; margin-bottom: 1.5rem; flex-wrap: wrap; align-items: center; }
  .tab-bar-label { font-size: 0.8rem; color: #666; font-weight: 600; margin-right: 0.5rem; }
  .tab-btn { background: #e9ecef; border: none; padding: 8px 16px; border-radius: 6px; font-size: 0.8rem; cursor: pointer; transition: all 0.2s; font-weight: 500; color: #333; }
  .tab-btn:hover { background: #dee2e6; }
  .tab-btn.active { background: #0077b6; color: #fff; font-weight: 600; }
  .tab-btn .deal-count { font-size: 0.7rem; opacity: 0.8; }
  .loading-msg { text-align: center; color: #666; padding: 2rem; font-size: 0.9rem; }
  @media (max-width: 768px) {
    body { margin: 0.5rem; }
    table { font-size: 0.72rem; }
    td, th { padding: 6px 8px; }
    .scan-toolbar { flex-direction: column; align-items: flex-start; }
  }"""

    # Source name -> search page URL mapping
    _SOURCE_URLS = {
        "DealStream": "https://dealstream.com/california/health-care-businesses-for-sale",
        "Synergy Business Brokers": "https://synergybb.com/businesses-for-sale/mental-healthcare-facilities-for-sale/",
        "American Healthcare Capital": "https://americanhealthcarecapital.com/current-listings/",
        "Transition Consultants": "https://www.transitionconsultants.com/practices-for-sale",
        "BizBuySell": "https://www.bizbuysell.com/california/health-care-and-fitness-businesses-for-sale/",
        "LoopNet": "https://www.loopnet.com/search/businesses-for-sale/california/for-sale/?sk=healthcare",
        "BusinessesForSale": "https://www.businessesforsale.com/us/search/healthcare-businesses-for-sale-in-california",
    }

    @staticmethod
    def _make_tag(tag):
        tag_type = tag.get("type", "maybe")
        css_class = {"hit": "tag-hit", "miss": "tag-miss", "maybe": "tag-maybe"}.get(tag_type, "tag-maybe")
        return f'<span class="tag {css_class}">{tag["label"]}</span>'

    @staticmethod
    def _make_fit_class(score):
        if not score:
            return "fit-med"
        if score.startswith("A"):
            return "fit-high"
        elif score.startswith("B"):
            return "fit-med"
        return "fit-low"

    @staticmethod
    def _make_location_tag(loc):
        if not loc:
            return '<span class="tag tag-maybe">Unknown</span>'
        loc_upper = loc.upper()
        if "CA" in loc_upper or "CALIFORNIA" in loc_upper:
            return f'<span class="tag tag-ca">{loc}</span>'
        elif "KY" in loc_upper or "KENTUCKY" in loc_upper:
            return f'<span class="tag tag-ca">{loc}</span>'
        else:
            return f'<span class="tag tag-notca">{loc}</span>'

    def _source_link(self, source_name):
        url = self._SOURCE_URLS.get(source_name, "#")
        return f'<a href="{url}" target="_blank">{source_name}</a>'

    def _deal_row(self, idx, deal):
        tags_html = "\n    ".join(self._make_tag(t) for t in deal.criteria_tags) if deal.criteria_tags else ""
        return f"""<tr>
  <td>{idx}</td>
  <td><strong><a href="{deal.url}" target="_blank">{deal.title}</a></strong><br>{(deal.description or '')[:200]}</td>
  <td>{self._make_location_tag(deal.location)}</td>
  <td><strong>{deal.revenue or 'N/A'}</strong></td>
  <td>{deal.cash_flow or deal.ebitda or 'N/A'}<br>{deal.ebitda_margin or ''}</td>
  <td><strong>{deal.asking_price or 'N/A'}</strong></td>
  <td><span class="fit-score {self._make_fit_class(deal.fit_score)}">{deal.fit_score or '?'}</span><br>
    {tags_html}
  </td>
  <td class="notes">{deal.key_details or deal.recommendation or 'No analysis available'}</td>
  <td class="source">{self._source_link(deal.source)}<br><span class="action">{deal.next_step or ''}</span></td>
</tr>"""

    def _build_deal_table_html(self) -> str:
        """Build the deal table HTML fragment (summary cards + table). No <html> wrapper."""
        tier1 = [d for d in self.deals if d.tier == 1]
        tier2 = [d for d in self.deals if d.tier == 2]
        tier3 = [d for d in self.deals if d.tier == 3 or d.tier == 0]

        rows_html = ""
        idx = 1
        if tier1:
            rows_html += '<tr class="section-header"><td colspan="9">TIER 1 — Strongest Matches (Pursue)</td></tr>\n'
            for deal in tier1:
                rows_html += self._deal_row(idx, deal) + "\n"
                idx += 1
        if tier2:
            rows_html += '<tr class="section-header"><td colspan="9">TIER 2 — Worth Investigating</td></tr>\n'
            for deal in tier2:
                rows_html += self._deal_row(idx, deal) + "\n"
                idx += 1
        if tier3:
            rows_html += '<tr class="section-header"><td colspan="9">TIER 3 — Marginal / Watch List</td></tr>\n'
            for deal in tier3:
                rows_html += self._deal_row(idx, deal) + "\n"
                idx += 1

        sources = set(d.source for d in self.deals)
        sources_str = ", ".join(sorted(sources)) if sources else "None"
        pursue = len([d for d in self.deals if d.recommendation and "Pursue" in d.recommendation])
        investigate = len([d for d in self.deals if d.recommendation and "Investigate" in d.recommendation])
        skip = len([d for d in self.deals if d.recommendation and "Skip" in d.recommendation])

        return f"""<div class="summary">
  <div class="summary-card"><div class="num">{len(self.deals)}</div><div class="label">Total Deals</div></div>
  <div class="summary-card"><div class="num num-pursue">{pursue}</div><div class="label">Pursue</div></div>
  <div class="summary-card"><div class="num num-investigate">{investigate}</div><div class="label">Investigate</div></div>
  <div class="summary-card"><div class="num num-skip">{skip}</div><div class="label">Skip</div></div>
</div>

<p class="legend"><span class="tag tag-hit">Meets Criteria</span> <span class="tag tag-miss">Fails Criteria</span> <span class="tag tag-maybe">Partial / Unknown</span></p>

<table>
<thead>
<tr>
  <th>#</th>
  <th>Listing / Description</th>
  <th>Location</th>
  <th>Revenue</th>
  <th>EBITDA / Cash Flow</th>
  <th>Asking Price</th>
  <th>Criteria Fit</th>
  <th>Key Details &amp; Red Flags</th>
  <th>Source / Next Step</th>
</tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>

<div class="bottom-note">
  <h3>About This Report</h3>
  <p>Auto-generated by Deal Finder. Each listing is scraped from public sources and analyzed by Claude AI for criteria fit.
  Deals are filtered to $1M&ndash;$5M asking price range. Financial data (cash flow, SDE, EBITDA) is displayed when available but not used as a hard filter.</p>
  <p style="font-size:0.75rem;color:#999;">Sources: {sources_str} | Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} | Analyzed by Claude AI</p>
</div>"""

    def _save_dated_report(self, date_str: str, table_html: str) -> str:
        """Save a standalone HTML report to reports/YYYY-MM-DD.html"""
        output_folder = Path(self.config["output"]["folder"])
        reports_dir = output_folder / self.config["output"]["reports_dir"]
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / f"{date_str}.html"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Healthcare Deals — {date_str}</title>
<style>{self._REPORT_CSS}</style>
</head>
<body>
{table_html}
</body>
</html>"""
        with open(report_path, "w") as f:
            f.write(html)
        return str(report_path)

    def _update_archive(self, date_str: str) -> list:
        """Update archive.json with the new report entry. Returns the archive list."""
        output_folder = Path(self.config["output"]["folder"])
        archive_path = output_folder / self.config["output"]["archive_file"]
        max_reports = self.config["output"].get("max_reports", 12)

        archive = []
        if archive_path.exists():
            try:
                with open(archive_path, "r") as f:
                    archive = json.load(f)
            except (json.JSONDecodeError, IOError):
                archive = []

        # Remove duplicate for same date
        archive = [e for e in archive if e.get("date") != date_str]

        pursue = len([d for d in self.deals if d.recommendation and "Pursue" in d.recommendation])
        archive.append({
            "date": date_str,
            "file": f"reports/{date_str}.html",
            "deal_count": len(self.deals),
            "pursue_count": pursue,
        })

        # Sort newest first
        archive.sort(key=lambda e: e["date"], reverse=True)

        # Prune old reports
        reports_dir = output_folder / self.config["output"]["reports_dir"]
        while len(archive) > max_reports:
            old = archive.pop()
            old_path = output_folder / old["file"]
            if old_path.exists():
                old_path.unlink()
                print(f"  Pruned old report: {old['date']}")

        with open(archive_path, "w") as f:
            json.dump(archive, f, indent=2)

        return archive

    def _generate_hub_page(self, archive: list, current_date: str, table_html: str) -> str:
        """Generate the hub index.html with date tabs, scan button, and embedded latest report."""
        output_folder = Path(self.config["output"]["folder"])
        hub_path = output_folder / self.config["output"]["html_file"]

        date_display = datetime.now().strftime("%B %d, %Y")
        sources = set(d.source for d in self.deals)
        sources_str = ", ".join(sorted(sources)) if sources else "None"

        # Build tab buttons
        tab_buttons = []
        for i, entry in enumerate(archive):
            active = " active" if entry["date"] == current_date else ""
            count = entry.get("deal_count", 0)
            pursue = entry.get("pursue_count", 0)
            # Format date for display (YYYY-MM-DD -> Mon DD)
            try:
                dt = datetime.strptime(entry["date"], "%Y-%m-%d")
                label = dt.strftime("%b %d")
            except ValueError:
                label = entry["date"]
            tab_buttons.append(
                f'<button class="tab-btn{active}" data-date="{entry["date"]}" '
                f'onclick="switchTab(\'{entry["date"]}\')">'
                f'{label} <span class="deal-count">({count})</span></button>'
            )
        tab_buttons_html = "\n  ".join(tab_buttons)

        # Build timestamp for cache-busting poll
        build_ts = datetime.now().strftime('%Y-%m-%dT%H:%M')

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Healthcare Acquisition Targets — {date_display}</title>
<style>{self._REPORT_CSS}</style>
</head>
<body>

<h1>Healthcare Acquisition Targets</h1>
<p class="subtitle">Last updated: {date_display} | Sources: {sources_str}</p>

<div class="scan-toolbar">
  <button id="scanBtn" onclick="triggerScan()">&#x1f504; Run New Scan</button>
  <span id="scanStatus"></span>
</div>

<div class="criteria">
  <strong>Search Criteria:</strong> Asking Price $1M&ndash;$5M &bull; Healthcare / Behavioral Health &bull; Prefer CA or KY &bull; Need financial data (cash flow, SDE, EBITDA) &bull; Semi-absentee / manager in place preferred &bull; SBA-financeable
</div>

<div class="tab-bar">
  <span class="tab-bar-label">Scan History:</span>
  {tab_buttons_html}
</div>

<div class="quick-links">
  <h3>Browse Source Platforms</h3>
  <a href="https://dealstream.com/california/health-care-businesses-for-sale" target="_blank">DealStream — CA Healthcare</a>
  <a href="https://dealstream.com/california/behavioral-health-businesses-for-sale" target="_blank">DealStream — CA Behavioral</a>
  <a href="https://dealstream.com/kentucky/health-care-businesses-for-sale" target="_blank">DealStream — KY Healthcare</a>
  <a href="https://dealstream.com/counseling-businesses-for-sale" target="_blank">DealStream — Counseling</a>
  <a href="https://synergybb.com/businesses-for-sale/mental-healthcare-facilities-for-sale/" target="_blank">Synergy — Mental Health</a>
  <a href="https://synergybb.com/businesses-for-sale/medical-practices-for-sale/" target="_blank">Synergy — Medical Practices</a>
  <a href="https://americanhealthcarecapital.com/current-listings/" target="_blank">American Healthcare Capital</a>
  <a href="https://www.transitionconsultants.com/practices-for-sale" target="_blank">Transition Consultants</a>
  <a href="https://www.bizbuysell.com/california/health-care-and-fitness-businesses-for-sale/" target="_blank">BizBuySell — CA</a>
  <a href="https://www.bizbuysell.com/kentucky/health-care-and-fitness-businesses-for-sale/" target="_blank">BizBuySell — KY</a>
  <a href="https://www.businessesforsale.com/us/search/healthcare-businesses-for-sale-in-california" target="_blank">BusinessesForSale — CA</a>
  <p class="source-note">Click any link above to browse listings directly on the source platform.</p>
</div>

<div id="report-content">
{table_html}
</div>

<p class="last-updated">Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} | Analyzed by Claude AI</p>

<script>
// === Tab switching ===
var loadedReports = {{}};
loadedReports['{current_date}'] = document.getElementById('report-content').innerHTML;

function switchTab(date) {{
  document.querySelectorAll('.tab-btn').forEach(function(b) {{ b.classList.remove('active'); }});
  var activeBtn = document.querySelector('[data-date="' + date + '"]');
  if (activeBtn) activeBtn.classList.add('active');

  var container = document.getElementById('report-content');

  if (loadedReports[date]) {{
    container.innerHTML = loadedReports[date];
    return;
  }}

  container.innerHTML = '<p class="loading-msg">Loading report...</p>';

  fetch('reports/' + date + '.html')
    .then(function(resp) {{
      if (!resp.ok) throw new Error('Not found');
      return resp.text();
    }})
    .then(function(html) {{
      var parser = new DOMParser();
      var doc = parser.parseFromString(html, 'text/html');
      var body = doc.querySelector('body');
      loadedReports[date] = body ? body.innerHTML : html;
      container.innerHTML = loadedReports[date];
    }})
    .catch(function(e) {{
      container.innerHTML = '<p class="scan-error">Could not load report for ' + date + '</p>';
    }});
}}

// === Scan trigger ===
// Opens the GitHub Actions workflow dispatch page, then polls for new results.
var DISPATCH_URL = 'https://github.com/g-riffm/healthcare-deals/actions/workflows/scan-deals.yml';
var BUILD_TS = '{build_ts}';

function triggerScan() {{
  var btn = document.getElementById('scanBtn');
  var status = document.getElementById('scanStatus');

  // Open workflow dispatch page — user clicks "Run workflow" there
  window.open(DISPATCH_URL, '_blank');

  btn.disabled = true;
  btn.textContent = 'Waiting for scan...';
  status.textContent = 'GitHub Actions opened — click "Run workflow" then come back here. Will auto-refresh when done.';
  status.className = 'scan-pending';

  pollForNewDeploy();
}}

function pollForNewDeploy() {{
  var status = document.getElementById('scanStatus');
  var btn = document.getElementById('scanBtn');
  var attempts = 0;
  var startTime = Date.now();

  // Poll the public GitHub API for workflow runs (no auth needed for public repos)
  var interval = setInterval(function() {{
    attempts++;
    var elapsed = Math.floor((Date.now() - startTime) / 60000);

    if (attempts > 40) {{
      clearInterval(interval);
      status.textContent = 'Timed out waiting. Refresh the page manually.';
      status.className = 'scan-error';
      btn.disabled = false;
      btn.textContent = '\\u1f504 Run New Scan';
      return;
    }}

    status.textContent = 'Watching for scan completion... (' + elapsed + ' min)';

    // Check latest workflow run via public API
    fetch('https://api.github.com/repos/g-riffm/healthcare-deals/actions/runs?per_page=1&event=workflow_dispatch', {{
      headers: {{ 'Accept': 'application/vnd.github+json' }}
    }})
    .then(function(resp) {{ return resp.json(); }})
    .then(function(data) {{
      if (!data.workflow_runs || !data.workflow_runs.length) return;
      var run = data.workflow_runs[0];

      // Check if this is a newer run than when page was built
      if (run.status === 'completed' && run.created_at > BUILD_TS) {{
        clearInterval(interval);
        if (run.conclusion === 'success') {{
          status.textContent = 'Scan complete! Reloading in 10 seconds...';
          status.className = 'scan-success';
          btn.textContent = 'Done!';
          // Wait a bit for GitHub Pages to deploy the new content
          setTimeout(function() {{ location.reload(); }}, 10000);
        }} else {{
          status.textContent = 'Scan finished: ' + run.conclusion + '. Check GitHub Actions.';
          status.className = 'scan-error';
          btn.disabled = false;
          btn.textContent = '\\u1f504 Run New Scan';
        }}
      }} else if (run.status === 'in_progress' || run.status === 'queued') {{
        status.textContent = 'Scan running... (' + elapsed + ' min elapsed)';
        btn.textContent = 'Scan Running...';
      }}
    }})
    .catch(function() {{
      // Silently continue polling
    }});
  }}, 20000);
}}
</script>

</body>
</html>"""

        with open(hub_path, "w") as f:
            f.write(html)
        return str(hub_path)

    def generate_html_report(self) -> str:
        """Generate HTML reports: dated report + hub page with tabs."""
        date_str = datetime.now().strftime("%Y-%m-%d")
        output_folder = Path(self.config["output"]["folder"])

        # 1. Build the deal table HTML fragment
        table_html = self._build_deal_table_html()

        # 2. Save as a dated standalone report
        report_path = self._save_dated_report(date_str, table_html)
        print(f"  Dated report: {report_path}")

        # 3. Update the archive manifest
        archive = self._update_archive(date_str)
        print(f"  Archive: {len(archive)} reports tracked")

        # 4. Generate the hub index.html with tabs
        hub_path = self._generate_hub_page(archive, date_str, table_html)
        print(f"HTML report saved: {hub_path}")

        return str(hub_path)

def main():
    finder = DealFinder(CONFIG)
    finder.run_all_searches()
    finder.analyze_all_deals()
    html_file = finder.generate_html_report()
    print(f"\nDone! Report at: {html_file}")


if __name__ == "__main__":
    main()
