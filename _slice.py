def stats_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001F3E0 \u0413\u043b\u0430\u0432\u043d\u0430\u044F", callback_data="nav_home")],
    ])

def try_get_popular_releases_week(ttl_sec: int = 300, limit: int = 10):
    key = "popular_releases"
    try:
        entry = _COUNTS_CACHE.get(key) or {}
        ts = entry.get("ts")
        if isinstance(ts, (int, float)) and (time.time() - float(ts) < ttl_sec):
            return entry.get("val")
    except Exception:
        pass
    try:
        sc = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "mobile": False})
        r = sc.get("https://fortnite.gg/player-count", timeout=15)
        soup = BeautifulSoup(r.text, 'lxml')
        anchor = soup.find(string=re.compile(r"popular\s+releases", re.I))
        container = None
        if anchor:
            cur = anchor.parent
            for _ in range(8):
                if not cur: break
                if len(list(cur.select("a[href*='/island?']"))) > 0:
                    container = cur
                    break
                cur = cur.parent
        if not container:
            container = soup
        items = []
        for a in container.select("a[href*='/island?']"):
            href = a.get("href")
            name = a.get_text(" ", strip=True)
            code = None
            try:
                from urllib.parse import urlparse, parse_qs
                qs = parse_qs(urlparse(href).query)
                code = qs.get("code", [None])[0]
            except Exception:
                pass
            around = " ".join((a.parent.get_text(" ", strip=True) if a.parent else name).split())
            m = re.findall(r"\d+", around.replace(",",""))
            now = int(m[0]) if m else None
            items.append({"name": name, "code": code, "href": ("https://fortnite.gg" + href if href and href.startswith("/") else href), "now": now})
            if len(items) >= limit:
                break
        _COUNTS_CACHE[key] = {"ts": time.time(), "val": items}
        return items
    except Exception:
        return []

async def send_stats_popular_releases(target_message):
    items = try_get_popular_releases_week() or []
    def fmtn(v):
        return f"{int(v):,}".replace(",", " ") if isinstance(v, int) else "?"
    blocks = []
    for it in items:
        name = esc(it.get("name"))
        href = esc(it.get("href")) if it.get("href") else None
        now = it.get("now")
        title = f"• <a href='{href}'><b>{name}</b></a>" if href else f"• <b>{name}</b>"
        meta = f"\n\U0001F465 Now: <b>{fmtn(now)}</b>" if now is not None else ""
        blocks.append(title + meta)
    body = "\n\n".join(blocks) if blocks else "\u041d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445"
    text = f"<b>Popular Releases (7d)</b>\n{body}\n\n<i>????????: fortnite.gg/player-count</i>"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("\u25C0\uFE0F \u041d\u0430\u0437\u0430\u0434", callback_data="stats:home")],
        [InlineKeyboardButton("\U0001F3E0 \u0413\u043b\u0430\u0432\u043d\u0430\u044F", callback_data="nav_home")],
    ])
    await send_one(target_message, text=text, reply_markup=kb)

async def send_stats_popular_releases_rich(target_message):
    """Render Popular Releases (7d) in the same visual style as Top 10."""
    items = try_get_popular_releases_week() or []

    def fmtn(v):
        try:
            if v is None:
                return "?"
            return f"{int(v):,}".replace(",", " ")
        except Exception:
            return esc(str(v)) if v is not None else "?"

    s = FortniteGGCreativeScraper()
    enriched = []
    for idx, it in enumerate(items, start=1):
        code = it.get("code")
        href = it.get("href")
        name = it.get("name") or code or "Map"
        now_val = it.get("now")
        p24_val = None
        ap_val = None
        try:
            if code:
                det = s.fetch_island_details(code)
                now_parsed = _toint(det.players_now_text)
                if now_parsed is not None:
                    now_val = now_parsed
                p24_val = _toint(det.peak_24h_text)
                ap_val = _toint(det.all_time_peak_text)
        except Exception:
            pass
        enriched.append({
            "rank": idx,
            "name": name,
            "code": code,
            "href": href,
            "now": now_val,
            "p24": p24_val,
            "ap": ap_val,
        })

    blocks = []
    for it in enriched:
        rank = it.get("rank")
        title = esc(it.get("name"))
        href = esc(it.get("href")) if it.get("href") else None
        code = esc(it.get("code") or "")
        now_v = it.get("now")
        ap_v = it.get("ap")
        p24_v = it.get("p24")

        header = f"<b>#{rank}</b> • " + (f"<a href='{href}'>{title}</a>" if href else title)
        code_line = f"\n<code>{code}</code>" if code else ""
        stats_line = (
            f"\n\U0001F465 \u041E\u043D\u043B\u0430\u0439\u043D: <b>{fmtn(now_v)}</b>   \U0001F53A \u041F\u0438\u043A: <b>{fmtn(ap_v)}</b>   \u25B6\uFE0F 24\u0447 \u0438\u0433\u0440\u043e\u043A\u043E\u0432: {fmtn(p24_v)}"
        )
        blocks.append(header + code_line + stats_line)

    body = "\n\n".join(blocks) if blocks else "\u041D\u0435\u0442 \u0434\u0430\u043D\u043D\u044B\u0445"
    text = f"<b>Popular Releases (7d)</b>\n{body}\n\n<i>\u0418\u0441\u0442\u043E\u0447\u043D\u0438\u043A: fortnite.gg/player-count</i>"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("\u25C0\uFE0F \u041D\u0430\u0437\u0430\u0434", callback_data="stats:home")],
        [InlineKeyboardButton("\U0001F3E0 \u0413\u043B\u0430\u0432\u043D\u0430\u044F", callback_data="nav_home")],
    ])
    await send_one(target_message, text=text, reply_markup=kb)

async def send_stats_home(target_message):
    await send_one(target_message, text="\u0421\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430 Fortnite (Player Count)", reply_markup=stats_home_kb())

async def send_stats_build_zero(target_message):
    sp = try_get_build_zero_split() or {}
    build_now = sp.get("build_now")
    zero_now = sp.get("zero build_now") or sp.get("zero_now")
    build_pct = sp.get("build_pct")
