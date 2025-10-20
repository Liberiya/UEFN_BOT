from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

import cloudscraper
from bs4 import BeautifulSoup


@dataclass
class CreativeListItem:
    rank: Optional[int]
    code: Optional[str]
    title: Optional[str]
    href: Optional[str]
    image: Optional[str]
    image_alt: Optional[str]
    players_now_pretty: Optional[str]
    players_now: Optional[int]
    all_time_peak: Optional[int]
    minutes_played: Optional[str]
    plays_24h: Optional[str]
    favorites_24h: Optional[str]
    recommends_24h: Optional[str]
    players_24h: Optional[str]
    avg_playtime_24h: Optional[str]
    retention_24h: Optional[str]
    by_epic: bool = False


@dataclass
class CreativeDetails:
    code: Optional[str]
    name: Optional[str]
    creator: Optional[str]
    description: Optional[str]
    tags: List[str]
    image: Optional[str]
    players_now_text: Optional[str]
    peak_24h_text: Optional[str]
    all_time_peak_text: Optional[str]
    stats_overview: Dict[str, str]


@dataclass
class CreatorStats:
    name: str
    total_players_now: int
    total_maps: int
    items: List[CreativeListItem]
    avatar: Optional[str] = None


class FortniteGGCreativeScraper:
    BASE = "https://fortnite.gg"

    def __init__(self, *, user_agent: Optional[str] = None, delay: float = 0.3):
        self.scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        if user_agent:
            self.scraper.headers["User-Agent"] = user_agent
        # Delay between requests to be polite
        self.delay = delay

    def _sleep(self):
        if self.delay:
            time.sleep(self.delay)

    def fetch_creative_page(self, page: int = 1, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params = dict(params or {})
        if page and page > 1:
            params["page"] = page
        params["ajax"] = ""
        url = f"{self.BASE}/creative"
        r = self.scraper.get(url, params=params, timeout=30)
        r.raise_for_status()
        self._sleep()
        try:
            return r.json()
        except json.JSONDecodeError:
            # Some edge pages may return HTML; try to extract JSON manually
            return json.loads(r.text)

    @staticmethod
    def _parse_int(text: Optional[str]) -> Optional[int]:
        if not text:
            return None
        # Remove commas and non-digits
        m = re.findall(r"\d+", text.replace(",", ""))
        if not m:
            return None
        try:
            return int("".join(m))
        except Exception:
            return None

    def parse_islands_html(self, html: str) -> List[CreativeListItem]:
        soup = BeautifulSoup(html, "lxml")
        items: List[CreativeListItem] = []
        for a in soup.select("a.island"):
            href = a.get("href")
            by_epic = "byepic" in (a.get("class") or [])
            # Rank
            try:
                rank_text = (a.select_one(".rank") or {}).get_text(strip=True)
                rank = int(rank_text) if rank_text and rank_text.isdigit() else None
            except Exception:
                rank = None
            # Title
            title_el = a.select_one("h3.island-title")
            title = title_el.get_text(strip=True) if title_el else None
            # Image
            img_el = a.select_one(".island-img img")
            image = img_el.get("src") if img_el else None
            image_alt = img_el.get("alt") if img_el else None
            # Players now (pretty short)
            pn_pretty_el = a.select_one(".island-img .players")
            pn_pretty = pn_pretty_el.get_text(" ", strip=True) if pn_pretty_el else None
            # Stats in column-2
            ccu_el = a.select_one(".column-2 .ccu")
            players_now = self._parse_int(ccu_el.get_text(" ", strip=True) if ccu_el else None)
            peak_el = a.select_one(".column-2 .peak")
            all_time_peak = self._parse_int(peak_el.get_text(" ", strip=True) if peak_el else None)

            def stat(label: str) -> Optional[str]:
                for ts in a.select(".column-2 .table-stat"):
                    span = ts.select_one("span")
                    if span and span.get_text(strip=True).lower() == label.lower():
                        return ts.get_text(" ", strip=True).replace(span.get_text(strip=True), "").strip()
                return None

            minutes_played = stat("Minutes Played")
            plays_24h = stat("24h Plays")
            favorites_24h = stat("24h Favorites")
            recommends_24h = stat("24h Recommends")
            players_24h = stat("24h Players")
            avg_playtime_24h = stat("24h Avg Playtime")
            retention_24h = stat("24h Retention")

            code = None
            if href:
                m = re.search(r"code=([0-9\-a-z_]+)", href)
                if m:
                    code = m.group(1)

            items.append(
                CreativeListItem(
                    rank=rank,
                    code=code,
                    title=title,
                    href=f"{self.BASE}{href}" if href else None,
                    image=image,
                    image_alt=image_alt,
                    players_now_pretty=pn_pretty,
                    players_now=players_now,
                    all_time_peak=all_time_peak,
                    minutes_played=minutes_played,
                    plays_24h=plays_24h,
                    favorites_24h=favorites_24h,
                    recommends_24h=recommends_24h,
                    players_24h=players_24h,
                    avg_playtime_24h=avg_playtime_24h,
                    retention_24h=retention_24h,
                    by_epic=by_epic,
                )
            )
        return items

    def iter_creative_list(self, max_pages: Optional[int] = None, params: Optional[Dict[str, Any]] = None, hide_epic: bool = False) -> Iterable[CreativeListItem]:
        page = 1
        seen_pages = 0
        while True:
            merged_params: Dict[str, Any] = dict(params or {})
            if hide_epic:
                merged_params["hideepic"] = ""
            data = self.fetch_creative_page(page=page, params=merged_params)
            islands_html = data.get("islands", "")
            for item in self.parse_islands_html(islands_html):
                yield item
            seen_pages += 1
            if max_pages and seen_pages >= max_pages:
                break
            # detect next page
            pages_html = data.get("pages", "")
            if f"/creative?page={page+1}" in pages_html:
                page += 1
                continue
            break

    def fetch_island_details(self, code_or_url: str) -> CreativeDetails:
        """Fetch island details page and parse key fields."""
        if code_or_url.startswith("http"):
            url = code_or_url
        else:
            # accept pure code or full /island?code=...
            if "code=" in code_or_url:
                url = f"{self.BASE}/island?{code_or_url.split('?',1)[1]}"
            else:
                url = f"{self.BASE}/island?code={code_or_url}"
        # HTML response (no JSON)
        r = self.scraper.get(url + ("&ajax" if "?" in url else "?ajax"), timeout=30)
        r.raise_for_status()
        self._sleep()

        soup = BeautifulSoup(r.text, "lxml")
        get_text = lambda el: (el.get_text(" ", strip=True) if el else None)

        name = get_text(soup.select_one("h1"))
        # Code
        code = None
        code_wrap = soup.select_one(".island-code-wrap")
        if code_wrap:
            m = re.search(r"(\d{4}-\d{4}-\d{4})", get_text(code_wrap) or "")
            if m:
                code = m.group(1)
        if not code:
            m = re.search(r"(\d{4}-\d{4}-\d{4})", r.text)
            code = m.group(1) if m else None

        # Description - prefer full text. Collect all variants and pick the longest, removing "...more" artifacts.
        desc_candidates = []
        for sel in [
            ".island-desc-more",
            ".island-desc",
            ".island-desc-trimmed",
            ".island-desc-wrap",
        ]:
            for el in soup.select(sel):
                t = get_text(el)
                if t:
                    desc_candidates.append(t)
        description = max(desc_candidates, key=len) if desc_candidates else None
        if description:
            description = description.replace("...more", "").replace("…more", "").strip()

        # Tags
        tags = [get_text(t) for t in soup.select(".island-tags .island-tag") if get_text(t)]

        # Image
        def _norm_url(u: Optional[str]) -> Optional[str]:
            if not u:
                return None
            if u.startswith("//"):
                return "https:" + u
            if u.startswith("/"):
                return f"{self.BASE}{u}"
            return u

        img = None
        img_el = soup.select_one(".island-img-thumb img") or soup.select_one(".island-img img")
        if img_el and img_el.has_attr("src"):
            img = _norm_url(img_el["src"])
        else:
            bg = soup.select_one(".island-detail-bg, .island-bg")
            if bg and bg.has_attr("style"):
                m = re.search(r"url\(['\"]?([^'\"]+)['\"]?\)", bg["style"])
                if m:
                    img = _norm_url(m.group(1))
        # Fallbacks from meta tags (works even if ajax minimized)
        if not img:
            og = soup.select_one('meta[property="og:image"]')
            if og and og.has_attr("content"):
                img = _norm_url(og["content"])
        if not img:
            tw = soup.select_one('meta[name="twitter:image"], meta[property="twitter:image"]')
            if tw and tw.has_attr("content"):
                img = _norm_url(tw["content"])
        # Final attempt: fetch non-ajax page to get OG image
        if not img:
            try:
                non_ajax = url if "?" not in url else url.split("?", 1)[0]
                r2 = self.scraper.get(non_ajax, timeout=30)
                r2.raise_for_status()
                s2 = BeautifulSoup(r2.text, "lxml")
                og2 = s2.select_one('meta[property="og:image"]')
                if og2 and og2.has_attr("content"):
                    img = _norm_url(og2["content"])
                if not img:
                    el2 = s2.select_one(".island-img-thumb img, .island-img img")
                    if el2 and el2.has_attr("src"):
                        img = _norm_url(el2["src"])
            except Exception:
                pass

        # Stats
        stats_overview: Dict[str, str] = {}
        for box in soup.select(".stats-overview-box"):
            title = get_text(box.select_one(".stats-overview-title"))
            number = get_text(box.select_one(".stats-overview-number"))
            if title:
                stats_overview[title] = number or ""

        players_now_text = get_text(soup.select_one(".js-players-now"))
        peak_24h_text = get_text(soup.select_one(".js-24h-peak"))
        alltime_peak_text = get_text(soup.select_one(".js-alltime-peak"))

        # Creator
        creator = None
        creator_link = soup.select_one('a[href^="/creator?"]')
        if creator_link:
            creator = get_text(creator_link)

        return CreativeDetails(
            code=code,
            name=name,
            creator=creator,
            description=description,
            tags=tags,
            image=img,
            players_now_text=players_now_text,
            peak_24h_text=peak_24h_text,
            all_time_peak_text=alltime_peak_text,
            stats_overview=stats_overview,
        )

    # ---------------- Creator -----------------
    def fetch_creator_page(self, name: str, page: int = 1) -> Dict[str, Any]:
        params: Dict[str, Any] = {"name": name, "ajax": ""}
        if page and page > 1:
            params["page"] = page
        url = f"{self.BASE}/creator"
        r = self.scraper.get(url, params=params, timeout=30)
        r.raise_for_status()
        self._sleep()
        try:
            return r.json()
        except Exception:
            return json.loads(r.text)

    @staticmethod
    def _extract_creator_name(text: str) -> Optional[str]:
        if not text:
            return None
        if text.startswith("http"):
            try:
                from urllib.parse import urlparse, parse_qs
                qs = parse_qs(urlparse(text).query)
                if "name" in qs and qs["name"]:
                    return qs["name"][0]
            except Exception:
                pass
        # Fallback: treat as raw string
        return text

    def fetch_creator_stats(self, name_or_url: str, max_pages: int = 1) -> CreatorStats:
        name = self._extract_creator_name(name_or_url) or name_or_url
        items: List[CreativeListItem] = []
        page = 1
        seen = 0
        while True:
            data = self.fetch_creator_page(name=name, page=page)
            islands_html = data.get("islands", "")
            part = self.parse_islands_html(islands_html)
            items.extend(part)
            seen += 1
            if max_pages and seen >= max_pages:
                break
            pages_html = data.get("pages", "") if isinstance(data, dict) else ""
            if f"page={page+1}" in str(pages_html):
                page += 1
                continue
            break
        total_players = sum([i.players_now or 0 for i in items])
        # Try to fetch avatar from the non-ajax page
        avatar = None
        try:
            r = self.scraper.get(f"{self.BASE}/creator", params={"name": name}, timeout=30)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            img = (
                soup.select_one(".creator-avatar img")
                or soup.select_one(".creator-header img")
                or soup.select_one("img[alt*='avatar' i]")
            )
            if img and img.has_attr("src"):
                avatar = img["src"]
            if not avatar:
                og = soup.select_one('meta[property="og:image"]')
                if og and og.has_attr("content"):
                    avatar = og["content"]
        except Exception:
            pass
        return CreatorStats(name=name, total_players_now=total_players, total_maps=len(items), items=items, avatar=avatar)

    def scrape(self, max_pages: int = 1, with_details: bool = False) -> List[Dict[str, Any]]:
        """Scrape creative listing and optionally enrich with island details.

        Returns a list of map dicts.
        """
        results: List[Dict[str, Any]] = []
        items = list(self.iter_creative_list(max_pages=max_pages))
        if not with_details:
            for it in items:
                results.append(asdict(it))
            return results

        # With details: fetch per island and merge
        for it in items:
            row = asdict(it)
            code = it.code
            try:
                if code:
                    det = self.fetch_island_details(code)
                    row.update({
                        "name": det.name,
                        "description": det.description,
                        "tags": det.tags,
                        "image_full": det.image,
                        "creator": det.creator,
                        "players_now_text": det.players_now_text,
                        "peak_24h_text": det.peak_24h_text,
                        "alltime_peak_text": det.all_time_peak_text,
                        "stats_overview": det.stats_overview,
                    })
            except Exception:
                # Skip details on failure but keep list info
                pass
            results.append(row)
        return results
