"""
UTF-8 safe Telegram bot for fortnite.gg data (single-post UI).
All user-facing strings use Unicode escapes to prevent mojibake in shells.
"""

import os
import time
import json
import re
import urllib.parse as up
from typing import List, Dict, Optional
from pathlib import Path

import cloudscraper
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, InputFile
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters

from uefn_scraper.fortnite_gg import FortniteGGCreativeScraper


# -------------------- Paths and env --------------------
STATE_PATH = "bot_state.json"
SUBS_PATH = "bot_subs.json"
BANNER_URL = os.getenv("BOT_BANNER_URL", "https://fortnite.gg/img/og/creative.png")
LOCAL_BANNER = os.getenv("BOT_BANNER_FILE", "banner.jpg")


def load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


STATE: Dict[str, Dict[str, object]] = load_json(STATE_PATH, {})
SUBS: Dict[str, Dict[str, list]] = load_json(SUBS_PATH, {})


def chat_settings(chat_id: int) -> Dict[str, object]:
    key = str(chat_id)
    if key not in STATE:
        STATE[key] = {"hide_epic": True, "last_msg_id": None}
        save_json(STATE_PATH, STATE)
    return STATE[key]  # type: ignore


def set_setting(chat_id: int, key: str, value: object) -> None:
    s = chat_settings(chat_id)
    s[key] = value
    save_json(STATE_PATH, STATE)


# -------------------- Threshold input (one-shot) --------------------
def set_pending_threshold(chat_id: int, target: str, ident: str) -> None:
    s = chat_settings(chat_id)
    s["await_thr"] = {"target": target, "id": ident}
    save_json(STATE_PATH, STATE)


def pop_pending_threshold(chat_id: int):
    s = chat_settings(chat_id)
    val = s.pop("await_thr", None)
    save_json(STATE_PATH, STATE)
    return val


def subs_bucket(chat_id: int):
    key = str(chat_id)
    if key not in SUBS:
        SUBS[key] = {"maps": [], "creators": []}
        save_json(SUBS_PATH, SUBS)
    if "maps" not in SUBS[key]:
        SUBS[key]["maps"] = []
    if "creators" not in SUBS[key]:
        SUBS[key]["creators"] = []
    return SUBS[key]


def add_map_sub(chat_id: int, code: str, thr: int):
    b = subs_bucket(chat_id)["maps"]
    for s in b:
        if s.get("code") == code:
            s.update({"threshold": thr, "last_players": None})
            save_json(SUBS_PATH, SUBS)
            return
    b.append({"code": code, "threshold": thr, "last_players": None})
    save_json(SUBS_PATH, SUBS)


def add_creator_sub(chat_id: int, name: str, thr: int):
    b = subs_bucket(chat_id)["creators"]
    for s in b:
        if s.get("name") == name:
            s.update({"threshold": thr, "last_players": None})
            save_json(SUBS_PATH, SUBS)
            return
    b.append({"name": name, "threshold": thr, "last_players": None})
    save_json(SUBS_PATH, SUBS)


# --- Growth alert helpers (delta over window minutes) ---
def add_map_growth_sub(chat_id: int, code: str, delta: int, window_min: int):
    b = subs_bucket(chat_id)["maps"]
    for s in b:
        if s.get("code") == code:
            s.update({"growth_delta": int(delta), "growth_window": int(window_min)})
            save_json(SUBS_PATH, SUBS)
            return
    b.append({"code": code, "growth_delta": int(delta), "growth_window": int(window_min)})
    save_json(SUBS_PATH, SUBS)


def add_creator_growth_sub(chat_id: int, name: str, delta: int, window_min: int):
    b = subs_bucket(chat_id)["creators"]
    for s in b:
        if s.get("name") == name:
            s.update({"growth_delta": int(delta), "growth_window": int(window_min)})
            save_json(SUBS_PATH, SUBS)
            return
    b.append({"name": name, "growth_delta": int(delta), "growth_window": int(window_min)})
    save_json(SUBS_PATH, SUBS)


def esc(s: Optional[str]) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_list_items(items, limit: int = 10) -> List[str]:
    out = []
    for it in items[:limit]:
        title = esc(it.title)
        code = esc(it.code)
        now_ = it.players_now or 0
        peak = it.all_time_peak or 0
        href = esc(it.href)
        out.append(
            f"<b>#{it.rank or ''}</b> \u2022 <a href='{href}'>{title}</a>\n"
            f"<code>{code or ''}</code>\n"
            f"\U0001F465 Онлайн: <b>{now_}</b>   \U0001F53A Пик: <b>{peak}</b>   \u25B6\uFE0F 24ч Игроков: {esc(it.plays_24h)}"
        )
    return out


# -------------------- Single-post helpers --------------------
def _get_last_msg(chat_id: int) -> Optional[int]:
    s = chat_settings(chat_id)
    return s.get("last_msg_id") if isinstance(s.get("last_msg_id"), int) else None


def _set_last_msg(chat_id: int, msg_id: int) -> None:
    set_setting(chat_id, "last_msg_id", msg_id)


def home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("\U0001F3E0 \u0413\u043b\u0430\u0432\u043d\u0430\u044f", callback_data="nav_home")]])


async def send_one(target_message, *, text: str, reply_markup=None, parse_mode=ParseMode.HTML, photo: Optional[str] = None):
    chat = target_message.chat
    bot = target_message.get_bot()
    chat_id = chat.id
    last_id = _get_last_msg(chat_id)
    if last_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=last_id)
        except Exception:
            pass
    try:
        if photo:
            msg = await chat.send_photo(photo=photo, caption=text, parse_mode=parse_mode, reply_markup=reply_markup)
        else:
            msg = await chat.send_message(text=text, parse_mode=parse_mode, disable_web_page_preview=True, reply_markup=reply_markup)
    except Exception:
        msg = await chat.send_message(text=text, parse_mode=parse_mode, disable_web_page_preview=True, reply_markup=reply_markup)
    _set_last_msg(chat_id, msg.message_id)
    return msg


def get_banner_media() -> Optional[str]:
    try:
        p = Path(LOCAL_BANNER)
        if p.is_file():
            return str(p)
    except Exception:
        pass
    return BANNER_URL


def _toint(txt: Optional[str]) -> Optional[int]:
    if not txt:
        return None
    ds = re.findall(r"\d+", str(txt).replace(",", ""))
    return int("".join(ds)) if ds else None

def _toint_abbrev(txt: Optional[str]) -> Optional[int]:
    # Parse numbers like 462.2K, 1.3M, 987
    if not txt:
        return None
    s = str(txt).strip().replace(',', '')
    m = re.match(r"(?i)^([0-9]+(?:\.[0-9]+)?)\s*([KMB]?)$", s)
    if not m:
        # fallback to digits-only
        return _toint(txt)
    val = float(m.group(1))
    suf = (m.group(2) or '').upper()
    mult = 1
    if suf == 'K':
        mult = 1_000
    elif suf == 'M':
        mult = 1_000_000
    elif suf == 'B':
        mult = 1_000_000_000
    try:
        return int(val * mult)
    except Exception:
        return None


# -------------------- Counts helpers (Fortnite / UEFN) --------------------
_COUNTS_CACHE: Dict[str, Dict[str, Optional[int]]] = {
    "fortnite": {"ts": None, "val": None},
    "uefn": {"ts": None, "val": None},
}

def _cache_get(name: str, ttl_sec: int) -> Optional[int]:
    try:
        entry = _COUNTS_CACHE.get(name) or {}
        ts = entry.get("ts")
        if isinstance(ts, (int, float)) and (time.time() - float(ts) < ttl_sec):
            v = entry.get("val")
            return int(v) if v is not None else None
    except Exception:
        pass
    return None

def _cache_set(name: str, value: Optional[int]) -> None:
    try:
        _COUNTS_CACHE[name] = {"ts": time.time(), "val": (int(value) if value is not None else None)}
    except Exception:
        pass

def try_get_fortnite_players_total(ttl_sec: int = 180) -> Optional[int]:
    v = _cache_get("fortnite", ttl_sec)
    if v is not None:
        return v
    total_global = None
    try:
        sc = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "mobile": False})
        r = sc.get("https://fortnite.gg/player-count", timeout=10)
        soup = BeautifulSoup(r.text, 'lxml')
        plain = ' '.join(soup.stripped_strings)
        m = re.search(r"([0-9][0-9,\.\s]+)\s*PLAYERS\s+RIGHT\s+NOW", plain, flags=re.I)
        if m:
            total_global = _toint(m.group(1))
    except Exception:
        total_global = None
    _cache_set("fortnite", total_global)
    return total_global

def try_get_uefn_players_total(max_pages: int = 3, hide_epic: bool = True, ttl_sec: int = 180) -> Optional[int]:
    v = _cache_get("uefn", ttl_sec)
    if v is not None:
        return v
    total = None
    try:
        s = FortniteGGCreativeScraper()
        acc = 0
        for it in s.iter_creative_list(max_pages=max_pages, hide_epic=hide_epic):
            if it.players_now:
                acc += int(it.players_now)
        total = acc
    except Exception:
        total = None
    _cache_set("uefn", total)
    return total

def try_get_epic_ugc_split(ttl_sec: int = 180) -> Optional[Dict[str, Optional[float]]]:
    # Return dict with epic_now, ugc_now, epic_pct, ugc_pct from player-count stats card.
    key = "split"
    # store as floats to allow pct as float
    try:
        entry = _COUNTS_CACHE.get(key) or {}
        ts = entry.get("ts")
        if isinstance(ts, (int, float)) and (time.time() - float(ts) < ttl_sec):
            return entry.get("val")  # type: ignore
    except Exception:
        pass

    try:
        sc = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "mobile": False})
        # The EPIC vs UGC block is on the Player Count page
        r = sc.get("https://fortnite.gg/player-count", timeout=15)
        soup = BeautifulSoup(r.text, 'lxml')
        # Find the card by title text
        anchor = soup.find(string=re.compile(r"EPIC\s+VS\s+UGC", re.I))
        container = None
        if anchor:
            # Walk up a few levels to get a block with both Epic and UGC
            cur = anchor.parent
            for _ in range(6):
                if not cur:
                    break
                txt = ' '.join(cur.stripped_strings).lower()
                if 'epic' in txt and 'ugc' in txt and '%' in txt:
                    container = cur
                    break
                cur = cur.parent
        if not container:
            container = soup

        # Stream tokens in order and bind to labels
        tokens = list(container.stripped_strings)
        last_pct = None
        last_num = None
        epic = {"pct": None, "now": None}
        ugc = {"pct": None, "now": None}
        for t in tokens:
            ts = t.strip()
            # percentage
            mp = re.match(r"^([0-9]+(?:\.[0-9]+)?)%$", ts)
            if mp:
                try:
                    last_pct = float(mp.group(1))
                except Exception:
                    last_pct = None
                continue
            # numbers with possible K/M suffix
            mn = re.match(r"^(?:[0-9]+(?:\.[0-9]+)?)(?:[KMkm])?$", ts.replace(',', ''))
            if mn:
                v = _toint_abbrev(ts)
                if v is not None:
                    last_num = v
                continue
            # labels
            low = ts.lower()
            if low == 'epic':
                if last_pct is not None:
                    epic["pct"] = last_pct
                if last_num is not None:
                    epic["now"] = last_num
                last_pct = None
                last_num = None
                continue
            if low == 'ugc':
                if last_pct is not None:
                    ugc["pct"] = last_pct
                if last_num is not None:
                    ugc["now"] = last_num
                last_pct = None
                last_num = None
                continue

        # Build result and cache
        res = {
            "epic_now": epic["now"],
            "ugc_now": ugc["now"],
            "epic_pct": epic["pct"],
            "ugc_pct": ugc["pct"],
        }
        _COUNTS_CACHE[key] = {"ts": time.time(), "val": res}  # type: ignore
        return res
    except Exception:
        return None

def _parse_split_from_player_count(title_regex: str, left_label: str, right_label: str, cache_key: str, ttl_sec: int = 180) -> Optional[Dict[str, Optional[float]]]:
    try:
        entry = _COUNTS_CACHE.get(cache_key) or {}
        ts = entry.get("ts")
        if isinstance(ts, (int, float)) and (time.time() - float(ts) < ttl_sec):
            return entry.get("val")  # type: ignore
    except Exception:
        pass
    try:
        sc = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "mobile": False})
        r = sc.get("https://fortnite.gg/player-count", timeout=15)
        soup = BeautifulSoup(r.text, 'lxml')
        anchor = soup.find(string=re.compile(title_regex, re.I))
        container = None
        if anchor:
            cur = anchor.parent
            for _ in range(6):
                if not cur:
                    break
                txt = ' '.join(cur.stripped_strings).lower()
                if left_label.lower() in txt and right_label.lower() in txt and '%' in txt:
                    container = cur
                    break
                cur = cur.parent
        if not container:
            container = soup
        tokens = list(container.stripped_strings)
        last_pct = None
        last_num = None
        left = {"pct": None, "now": None}
        right = {"pct": None, "now": None}
        for t in tokens:
            ts = t.strip()
            mp = re.match(r"^([0-9]+(?:\.[0-9]+)?)%$", ts)
            if mp:
                try:

# -------------------- Stats menus --------------------
                    last_pct = float(mp.group(1))
                except Exception:
                    last_pct = None
                continue
            mn = re.match(r"^(?:[0-9]+(?:\.[0-9]+)?)(?:[KMkm])?$", ts.replace(',', ''))
            if mn:
                v = _toint_abbrev(ts)
                if v is not None:
                    last_num = v
                continue
            low = ts.lower()
            if low == left_label.lower():
                if last_pct is not None:
                    left["pct"] = last_pct
                if last_num is not None:
                    left["now"] = last_num
                last_pct = None
                last_num = None
                continue
            if low == right_label.lower():
                if last_pct is not None:
                    right["pct"] = last_pct
                if last_num is not None:
                    right["now"] = last_num
                last_pct = None
                last_num = None
                continue
        res = {
            f"{left_label.lower()}_now": left["now"],
            f"{right_label.lower()}_now": right["now"],
            f"{left_label.lower()}_pct": left["pct"],
            f"{right_label.lower()}_pct": right["pct"],
        }
        _COUNTS_CACHE[cache_key] = {"ts": time.time(), "val": res}  # type: ignore
        return res
    except Exception:
        return None

def try_get_build_zero_split(ttl_sec: int = 180) -> Optional[Dict[str, Optional[float]]]:
    return _parse_split_from_player_count(r"BUILD\s+VS\s+ZERO\s+BUILD", "Build", "Zero Build", cache_key="split_bz", ttl_sec=ttl_sec)

def try_get_ranked_nonranked_split(ttl_sec: int = 180) -> Optional[Dict[str, Optional[float]]]:
    return _parse_split_from_player_count(r"RANKED\s+VS\s+NON-?RANKED", "Ranked", "Non-Ranked", cache_key="split_ranked", ttl_sec=ttl_sec)

def try_get_genres_top(ttl_sec: int = 300, limit: int = 10) -> Optional[List[Dict[str, object]]]:
    key = "genres"
    try:
        entry = _COUNTS_CACHE.get(key) or {}
        ts = entry.get("ts")
        if isinstance(ts, (int, float)) and (time.time() - float(ts) < ttl_sec):
            return entry.get("val")  # type: ignore
    except Exception:
        pass
    try:
        sc = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "mobile": False})
        r = sc.get("https://fortnite.gg/player-count", timeout=15)
        soup = BeautifulSoup(r.text, 'lxml')
        anchor = soup.find(string=re.compile(r"MOST\s+PLAYED\s+GENRES", re.I))
        container = None
        if anchor:
            cur = anchor.parent
            for _ in range(6):
                if not cur:
                    break
                # Heuristic: list with many numbers and genre-like labels
                txt = ' '.join(cur.stripped_strings)
                if len(re.findall(r"\d", txt)) > 10:
                    container = cur
                    break
                cur = cur.parent
        if not container:
            container = soup
        # Collect pairs: take lines alternating NAME then NUMBER
        items: List[Dict[str, object]] = []
        name_hold = None
        for ts in container.stripped_strings:
            s = ts.strip()
            if not name_hold:
                # Skip obvious headers
                if re.search(r"players?\s+now", s, re.I):
                    continue
                if len(s) <= 22 and not re.search(r"%|/|\\|:|,", s):
                    name_hold = s
                    continue
            else:
                val = _toint_abbrev(s)
                if val is not None:
                    items.append({"name": name_hold, "now": int(val)})
                    name_hold = None
                    if len(items) >= limit:
                        break
                else:
                    # reset if not a number
                    name_hold = None
        if not items:
            return None
        _COUNTS_CACHE[key] = {"ts": time.time(), "val": items}  # type: ignore
        return items
    except Exception:
        return None


def build_home_kb_dynamic(chat_id: int) -> InlineKeyboardMarkup:
    # Live totals (with small cache to avoid frequent requests)
    total_global = try_get_fortnite_players_total(ttl_sec=int(os.getenv("BOT_COUNTS_TTL", "180")))
    # Prefer official EPIC vs UGC split from creative stats; fallback to summed listing
    s = chat_settings(chat_id)
    hide_epic = bool(s.get("hide_epic", True))
    split = try_get_epic_ugc_split(ttl_sec=int(os.getenv("BOT_COUNTS_TTL", "180"))) or {}
    uefn_total = split.get("ugc_now") if isinstance(split.get("ugc_now"), int) else None
    if uefn_total is None:
        uefn_total = try_get_uefn_players_total(
            max_pages=int(os.getenv("BOT_UEFN_PAGES", "3")),
            hide_epic=hide_epic,
            ttl_sec=int(os.getenv("BOT_COUNTS_TTL", "180")),
        )

    maps_count = len(SUBS.get(str(chat_id), {}).get("maps", []))
    creators_count = len(SUBS.get(str(chat_id), {}).get("creators", []))
    subs_count = maps_count + creators_count

    def fmt(n: Optional[int]) -> str:
        if n is None:
            return "?"
        return f"{int(n):,}".replace(",", " ")

    sub_label = f"\U0001F514 \u041f\u043e\u0434\u043f\u0438\u0441\u043a\u0438 ({subs_count})"
    pct = split.get("ugc_pct") if isinstance(split, dict) else None
    pct_txt = f" ({pct:.1f}%)" if isinstance(pct, (int, float)) else ""
    total_label = f"\U0001F4C8 Fortnite: {fmt(total_global)} | UEFN: {fmt(uefn_total)}{pct_txt}"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(total_label, callback_data="stats:home")],
        [InlineKeyboardButton("\U0001F50E \u041d\u0430\u0439\u0442\u0438 \u043a\u0430\u0440\u0442\u0443", callback_data="start:map"), InlineKeyboardButton("\U0001F464 \u041a\u0440\u0435\u0430\u0442\u043e\u0440", callback_data="start:creator")],
        [InlineKeyboardButton(sub_label, callback_data="start:alerts"), InlineKeyboardButton("\u2699\uFE0F \u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438", callback_data="start:settings")],
        [InlineKeyboardButton("\U0001F525 \u0422\u043e\u043f 10", callback_data="nav_top:10"), InlineKeyboardButton("\u2753 \u041f\u043e\u043c\u043e\u0449\u044c", callback_data="start:help")],
    ])

# -------------------- Stats menus --------------------

def stats_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001F4CA EPIC vs UGC", callback_data="stats:epicugc")],
        [InlineKeyboardButton("\U0001F9F1 Build vs Zero Build", callback_data="stats:bz")],
        [InlineKeyboardButton("\U0001F3C6 Ranked vs Non-Ranked", callback_data="stats:ranked")],
        [InlineKeyboardButton("\U0001F3AD \u0416\u0430\u043d\u0440\u044b (Top)", callback_data="stats:genres")],
        [InlineKeyboardButton("\U0001F3E0 \u0413\u043b\u0430\u0432\u043d\u0430\u044F", callback_data="nav_home")],
    ])

async def send_stats_home(target_message):
    await send_one(target_message, text="\u0421\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430 Fortnite (Player Count)", reply_markup=stats_home_kb())

async def send_stats_epicugc(target_message):
    sp = try_get_epic_ugc_split() or {}
    epic_now = sp.get("epic_now")
    ugc_now = sp.get("ugc_now")
    epic_pct = sp.get("epic_pct")
    ugc_pct = sp.get("ugc_pct")
    def fmtn(v):
        return f"{int(v):,}".replace(",", " ") if isinstance(v, int) else "?"
    def fmtp(v):
        return f"{float(v):.1f}%" if isinstance(v, (int, float)) else "?%"
    text = (
        f"<b>EPIC vs UGC</b>\n"
        f"Epic: <b>{fmtp(epic_pct)}</b> ({fmtn(epic_now)})\n"
        f"UGC: <b>{fmtp(ugc_pct)}</b> ({fmtn(ugc_now)})\n"
        f"\n<i>Источник: fortnite.gg/player-count</i>"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("\u25C0\uFE0F \u041d\u0430\u0437\u0430\u0434", callback_data="stats:home")], [InlineKeyboardButton("\U0001F3E0 \u0413\u043b\u0430\u0432\u043d\u0430\u044F", callback_data="nav_home")]])
    await send_one(target_message, text=text, reply_markup=kb)

async def send_stats_build_zero(target_message):
    sp = try_get_build_zero_split() or {}
    build_now = sp.get("build_now")
    zero_now = sp.get("zero build_now") or sp.get("zero_now")
    build_pct = sp.get("build_pct")
    zero_pct = sp.get("zero build_pct") or sp.get("zero_pct")
    def fmtn(v):
        return f"{int(v):,}".replace(",", " ") if isinstance(v, int) else "?"
    def fmtp(v):
        return f"{float(v):.1f}%" if isinstance(v, (int, float)) else "?%"
    text = (
        f"<b>Build vs Zero Build</b>\n"
        f"Build: <b>{fmtp(build_pct)}</b> ({fmtn(build_now)})\n"
        f"Zero Build: <b>{fmtp(zero_pct)}</b> ({fmtn(zero_now)})\n"
        f"\n<i>Источник: fortnite.gg/player-count</i>"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("\u25C0\uFE0F \u041d\u0430\u0437\u0430\u0434", callback_data="stats:home")], [InlineKeyboardButton("\U0001F3E0 \u0413\u043b\u0430\u0432\u043d\u0430\u044F", callback_data="nav_home")]])
    await send_one(target_message, text=text, reply_markup=kb)

async def send_stats_ranked(target_message):
    sp = try_get_ranked_nonranked_split() or {}
    ranked_now = sp.get("ranked_now")
    non_now = sp.get("non-ranked_now") or sp.get("non ranked_now")
    ranked_pct = sp.get("ranked_pct")
    non_pct = sp.get("non-ranked_pct") or sp.get("non ranked_pct")
    def fmtn(v):
        return f"{int(v):,}".replace(",", " ") if isinstance(v, int) else "?"
    def fmtp(v):
        return f"{float(v):.1f}%" if isinstance(v, (int, float)) else "?%"
    text = (
        f"<b>Ranked vs Non-Ranked</b>\n"
        f"Ranked: <b>{fmtp(ranked_pct)}</b> ({fmtn(ranked_now)})\n"
        f"Non-Ranked: <b>{fmtp(non_pct)}</b> ({fmtn(non_now)})\n"
        f"\n<i>Источник: fortnite.gg/player-count</i>"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("\u25C0\uFE0F \u041d\u0430\u0437\u0430\u0434", callback_data="stats:home")], [InlineKeyboardButton("\U0001F3E0 \u0413\u043b\u0430\u0432\u043d\u0430\u044F", callback_data="nav_home")]])
    await send_one(target_message, text=text, reply_markup=kb)

async def send_stats_genres(target_message):
    items = try_get_genres_top(limit=10) or []
    def fmtn(v):
        return f"{int(v):,}".replace(",", " ") if isinstance(v, int) else "?"
    lines = [f"• {esc(str(i.get('name')))} — <b>{fmtn(i.get('now'))}</b>" for i in items]
    text = (
        "<b>Most Played Genres</b>\n" + ("\n".join(lines) if lines else "\u041d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445") +
        "\n\n<i>Источник: fortnite.gg/player-count</i>"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("\u25C0\uFE0F \u041d\u0430\u0437\u0430\u0434", callback_data="stats:home")], [InlineKeyboardButton("\U0001F3E0 \u0413\u043b\u0430\u0432\u043d\u0430\u044F", callback_data="nav_home")]])
    await send_one(target_message, text=text, reply_markup=kb)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = chat_settings(update.effective_chat.id)
    text = (
        "\U0001F44B \u041f\u0440\u0438\u0432\u0435\u0442! \u042f \u0431\u043e\u0442 \u0434\u043b\u044f \u0441\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0438 fortnite.gg/creative.\n\n"
        f"Hide Epic: <b>{'ON' if s.get('hide_epic', True) else 'OFF'}</b>\n"
        "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0440\u0430\u0437\u0434\u0435\u043b:"
    )
    kb = build_home_kb_dynamic(update.effective_chat.id)
    await send_one(update.effective_message, text=text, reply_markup=kb, photo=get_banner_media())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_one(
        update.effective_message,
        text="\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435 \u043a\u043d\u043e\u043f\u043a\u0438 \u0434\u043b\u044f \u043d\u0430\u0432\u0438\u0433\u0430\u0446\u0438\u0438. \u041a\u043e\u043c\u0430\u043d\u0434\u044b: /top, /map, /creator, /settings.",
        reply_markup=home_kb(),
    )


async def version_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import sys, os, pathlib, platform, time, subprocess
    path = pathlib.Path(__file__).resolve()
    cwd = pathlib.Path().resolve()
    try:
        sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        sha = "n/a"
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    txt = (
        f"<b>Version</b>\n"
        f"file: <code>{path}</code>\n"
        f"cwd: <code>{cwd}</code>\n"
        f"py: {sys.version.split()[0]}  os: {platform.system()} {platform.release()}\n"
        f"git: {sha}  time: {ts}"
    )
    await update.effective_message.reply_text(txt, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        page_size = int(context.args[0]) if context.args else 10
    except Exception:
        page_size = 10
    await send_top(update.effective_chat.id, update.effective_message, 0, max(5, min(30, page_size)))


async def send_top(chat_id: int, target_message, offset: int, limit: int):
    s = chat_settings(chat_id)
    per_page = 28
    pages_needed = (offset + limit + per_page - 1) // per_page
    scraper = FortniteGGCreativeScraper()
    items = list(scraper.iter_creative_list(max_pages=pages_needed, hide_epic=bool(s.get("hide_epic", True))))
    slice_ = items[offset: offset + limit]
    header = (
        f"<b>Top {offset+1}\u2013{offset+len(slice_)}</b> of most played | "
        f"Hide Epic: <b>{'ON' if s.get('hide_epic', True) else 'OFF'}</b>\n"
    )
    body = "\n\n".join(format_list_items(slice_, limit=len(slice_))) if slice_ else "\n\u041d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445"
    text = header + body
    prev_off = max(0, offset - limit)
    next_off = offset + limit
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("\u25C0\uFE0F \u041d\u0430\u0437\u0430\u0434", callback_data=f"top:{prev_off}:{limit}"), InlineKeyboardButton("\u25B6\uFE0F \u0412\u043f\u0435\u0440\u0451\u0434", callback_data=f"top:{next_off}:{limit}")],
        [InlineKeyboardButton(("\U0001F513 \u041f\u043e\u043a\u0430\u0437\u0430\u0442\u044c Epic" if s.get("hide_epic", True) else "\U0001F512 \u0421\u043a\u0440\u044b\u0432\u0430\u0442\u044c Epic"), callback_data="toggle_hideepic")],
        [InlineKeyboardButton("\U0001F3E0 \u0413\u043b\u0430\u0432\u043d\u0430\u044f", callback_data="nav_home")],
    ])
    await send_one(target_message, text=text, reply_markup=kb)


async def map_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await send_one(update.effective_message, text="\u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u043a\u043e\u0434 \u0438\u043b\u0438 \u0441\u0441\u044b\u043b\u043a\u0443: 1234-5678-9012 \u0438\u043b\u0438 https://fortnite.gg/island?code=\u2026", reply_markup=home_kb())
        return
    await send_map_card(update.effective_message, context.args[0])


async def send_map_card(target_message, ident: str):
    s = FortniteGGCreativeScraper()
    det = s.fetch_island_details(ident)
    name = esc(det.name)
    code = det.code or ""
    pn_raw = det.players_now_text or ""
    p24_raw = det.peak_24h_text or ""
    ap_raw = det.all_time_peak_text or ""
    pn = esc(pn_raw)
    p24 = esc(p24_raw)
    ap = esc(ap_raw)
    tags = ", ".join(det.tags or [])
    # Try to display last updated and release date info from stats overview
    upd_text = None
    rel_text = None
    try:
        so = getattr(det, 'stats_overview', {}) or {}
        # Updated
        for k, v in so.items():
            if isinstance(k, str) and ('updated' in k.lower() or 'update' in k.lower()):
                upd_text = v
                break
        # Release Date
        for k, v in so.items():
            if isinstance(k, str) and 'release' in k.lower():
                rel_text = v
                break
    except Exception:
        upd_text = None
        rel_text = None

    url = f"https://fortnite.gg/island?code={code}" if code else "https://fortnite.gg/creative"
    lines = [
        f"<b><a href=\"{url}\">{name}</a></b>",
        f"<code>{esc(code)}</code>",
        f"\U0001F465 Онлайн: <b>{pn}</b>",
    ]
    if re.search(r"\d", p24_raw or ""):
        lines.append(f"\U0001F4C8 Пик 24ч: <b>{p24}</b>")
    lines.append(f"\U0001F3C6 Пик за всё время: <b>{ap}</b>")
    if tags:
        lines.append(f"\U0001F3F7\uFE0F Теги: {esc(tags)}")
    # Separate block: Updated / Release Date on a new line below
    if upd_text or rel_text:
        lines.append("")
        parts = []
        if upd_text:
            parts.append(f"\u23F2\uFE0F Обновлено: {esc(str(upd_text))}")
        if rel_text:
            parts.append(f"\U0001F4C5 Дата релиза: {esc(str(rel_text))}")
        lines.append("  |  ".join(parts))
    text = "\n".join(lines)
    
    qcode = up.quote(code, safe='')
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001F514 \u041e\u0442 75", callback_data=f"alert_map_fixed:{qcode}:75"), InlineKeyboardButton("\U0001F4C8 \u0420\u043e\u0441\u0442", callback_data=f"alert_map_growth:{qcode}")],
        [InlineKeyboardButton("\U0001F514 Уведомление: настроить порог", callback_data=f"alert_map_custom:{qcode}")],
        [InlineKeyboardButton("\u23F0 \u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u0442\u044c \u043a\u0430\u0436\u0434\u044b\u0435 4 \u0434\u043d\u044f", callback_data=f"updremind:{qcode}:4"), InlineKeyboardButton("\u2705 \u041e\u0431\u043d\u043e\u0432\u0438\u043b", callback_data=f"updmark:{qcode}")],
        [InlineKeyboardButton("\U0001F3E0 \u0413\u043b\u0430\u0432\u043d\u0430\u044F", callback_data="nav_home")],
    ])
    await send_one(target_message, text=text, reply_markup=kb, photo=getattr(det, 'image', None))


# override: improved map card (airier, RU labels, no tags)
async def send_map_card(target_message, ident: str):
    s = FortniteGGCreativeScraper()
    det = s.fetch_island_details(ident)

    name = esc(det.name)
    code = det.code or ""
    pn_raw = det.players_now_text or ""
    p24_raw = det.peak_24h_text or ""
    ap_raw = det.all_time_peak_text or ""
    pn = esc(pn_raw)

    # Updated / Release from stats overview
    upd_text = None
    rel_text = None
    try:
        so = getattr(det, 'stats_overview', {}) or {}
        for k, v in so.items():
            if isinstance(k, str) and ('updated' in k.lower() or 'update' in k.lower()):
                upd_text = v
                break
        for k, v in so.items():
            if isinstance(k, str) and 'release' in k.lower():
                rel_text = v
                break
    except Exception:
        pass

    # Extract readable date from all-time peak text
    ap_date = None
    try:
        m = re.search(r"([A-Za-z]{3,}\s+\d{1,2},\s+\d{4})", ap_raw or "")
        if not m:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", ap_raw or "")
        ap_date = m.group(1) if m else None
    except Exception:
        ap_date = None

    url = f"https://fortnite.gg/island?code={code}" if code else "https://fortnite.gg/creative"
    lines = [
        f"<b><a href=\"{url}\">{name}</a></b>",
        "",
        f"\U0001F465 \u041E\u043D\u043B\u0430\u0439\u043D: <b>{_toint(pn_raw) if _toint(pn_raw) is not None else pn}</b>",
    ]
    val24 = _toint(p24_raw)
    if val24 is not None:
        lines.append(f"\U0001F4C8 \u041F\u0438\u043A 24\u0447: <b>{val24}</b>")
    ap_val = _toint(ap_raw)
    if (ap_val is not None) or ap_date:
        prefix = f"<b>{ap_val}</b>" if ap_val is not None else ""
        suffix = f" \u2022 {esc(ap_date)}" if ap_date else ""
        lines.append(f"\U0001F3C6 \u041F\u0438\u043A \u0437\u0430 \u0432\u0441\u0451 \u0432\u0440\u0435\u043C\u044F: {prefix}{suffix}")

    if upd_text or rel_text:
        lines.append("")
        parts = []
        if upd_text:
            parts.append(f"\u23F2\uFE0F \u041E\u0431\u043D\u043E\u0432\u043B\u0435\u043D\u043E: {esc(str(upd_text))}")
        if rel_text:
            parts.append(f"\U0001F4C5 \u0414\u0430\u0442\u0430 \u0440\u0435\u043B\u0438\u0437\u0430: {esc(str(rel_text))}")
        lines.append("  |  ".join(parts))

    text = "\n".join(lines)

    qcode = up.quote(code, safe='')
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001F514 \u041E\u0442 75", callback_data=f"alert_map_fixed:{qcode}:75"), InlineKeyboardButton("\U0001F4C8 \u0420\u043E\u0441\u0442", callback_data=f"alert_map_growth:{qcode}")],
        [InlineKeyboardButton("\U0001F514 \u0423\u0432\u0435\u0434\u043E\u043C\u043B\u0435\u043D\u0438\u0435: \u043D\u0430\u0441\u0442\u0440\u043E\u0438\u0442\u044C \u043F\u043E\u0440\u043E\u0433", callback_data=f"alert_map_custom:{qcode}")],
        [InlineKeyboardButton("\u23F0 \u041D\u0430\u043F\u043E\u043C\u0438\u043D\u0430\u0442\u044C \u043A\u0430\u0436\u0434\u044B\u0435 4 \u0434\u043D\u044F", callback_data=f"updremind:{qcode}:4")],
        [InlineKeyboardButton("\U0001F3E0 \u0413\u043B\u0430\u0432\u043D\u0430\u044F", callback_data="nav_home")],
    ])
    await send_one(target_message, text=text, reply_markup=kb, photo=getattr(det, 'image', None))

async def creator_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await send_one(update.effective_message, text="\u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u0438\u043c\u044f \u0438\u043b\u0438 \u0441\u0441\u044b\u043b\u043a\u0443: https://fortnite.gg/creator?name=\u2026", reply_markup=home_kb())
        return
    await send_creator_card(update.effective_message, context.args[0])


async def send_creator_card(target_message, ident: str):
    s = FortniteGGCreativeScraper()
    stats = s.fetch_creator_stats(ident, max_pages=1)
    name = esc(stats.name)
    url = f"https://fortnite.gg/creator?name={name}"
    # Показываем компактный список карт с двойными переносами для воздуха
    def _fmt_item(it):
        title = esc(it.title)
        code_i = esc(it.code)
        now_i = it.players_now or 0
        peak_i = it.all_time_peak or 0
        href = esc(it.href)
        return (
            f"<a href='{href}'><b>{title}</b></a>\n"
            f"<code>{code_i or ''}</code>\n"
            f"\U0001F465 Онлайн: <b>{now_i}</b>  •  \U0001F3C6 Пик: <b>{peak_i}</b>"
        )
    items_block = "\n\n".join(_fmt_item(it) for it in stats.items[:8])
    text = (
        f"<b>Креатор: <a href='{url}'>{name}</a></b>\n"
        f"\U0001F465 Сейчас: <b>{stats.total_players_now}</b>  •  \U0001F3F7\uFE0F Карты: <b>{stats.total_maps}</b>\n\n"
        + items_block
    )
    qname = up.quote(stats.name, safe='')
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001F514 \u041e\u0442 75", callback_data=f"alert_creator_fixed:{qname}:75"), InlineKeyboardButton("\U0001F4C8 \u0420\u043e\u0441\u0442", callback_data=f"alert_creator_growth:{qname}")],
        [InlineKeyboardButton("\U0001F514 \u0423\u0432\u0435\u0434\u043e\u043c\u043b\u0435\u043d\u0438\u0435: \u043d\u0430\u0441\u0442\u0440\u043e\u0438\u0442\u044c \u043f\u043e\u0440\u043e\u0433", callback_data=f"alert_creator_custom:{qname}")],
        [InlineKeyboardButton("\U0001F3E0 \u0413\u043b\u0430\u0432\u043d\u0430\u044F", callback_data="nav_home")],
    ])
    await send_one(target_message, text=text, reply_markup=kb, photo=getattr(stats, 'avatar', None))


async def alerts_list_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    b = subs_bucket(update.effective_chat.id)
    lines = ["\u0412\u0430\u0448\u0438 \u043f\u043e\u0434\u043f\u0438\u0441\u043a\u0438:"]
    rows = []
    if b["maps"]:
        lines.append("\n\u041a\u0430\u0440\u0442\u044b:")
        for s in b["maps"]:
            code = s.get("code") or ""
            parts = []
            if s.get("threshold") is not None:
                parts.append(f"\u043f\u043e\u0440\u043e\u0433 {s.get('threshold')}")
            if s.get("growth_delta") is not None and s.get("growth_window") is not None:
                parts.append(f"\u0440\u043e\u0441\u0442 +{s.get('growth_delta')} / {s.get('growth_window')}\u043c")
            meta = ("; ".join(parts)) or "\u043d\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0435\u043d\u043e"
            lines.append(f"\u2022 {esc(code)} \u2014 {meta}")
            code_q = up.quote(code, safe='')
            rows.append([InlineKeyboardButton(text=f"\U0001F5FA\uFE0F {esc(code)}", callback_data=f"open_map:{code_q}")])
    if b["creators"]:
        lines.append("\n\u041a\u0440\u0435\u0430\u0442\u043e\u0440\u044b:")
        for s in b["creators"]:
            name = s.get("name") or ""
            parts = []
            if s.get("threshold") is not None:
                parts.append(f"\u043f\u043e\u0440\u043e\u0433 {s.get('threshold')}")
            if s.get("growth_delta") is not None and s.get("growth_window") is not None:
                parts.append(f"\u0440\u043e\u0441\u0442 +{s.get('growth_delta')} / {s.get('growth_window')}\u043c")
            meta = ("; ".join(parts)) or "\u043d\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0435\u043d\u043e"
            lines.append(f"\u2022 {esc(name)} \u2014 {meta}")
            name_q = up.quote(name, safe='')
            rows.append([InlineKeyboardButton(text=f"\U0001F464 {esc(name)}", callback_data=f"open_creator:{name_q}")])
    rows.append([InlineKeyboardButton("\U0001F3E0 \u0413\u043b\u0430\u0432\u043d\u0430\u044f", callback_data="nav_home")])
    kb = InlineKeyboardMarkup(rows)
    await send_one(update.effective_message, text="\n".join(lines), reply_markup=kb)


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or '').strip()
    # One-shot threshold input flow
    try:
        pend = chat_settings(update.effective_chat.id).get('await_thr')
        if isinstance(pend, dict) and t and re.fullmatch(r'\d{1,6}', t):
            thr = int(t)
            target = str(pend.get('target'))
            ident = str(pend.get('id'))
            if target == 'map':
                add_map_sub(update.effective_chat.id, ident, thr)
                await update.message.reply_text(
                    f"OK. Порог для карты <code>{esc(ident)}</code>: <b>{thr}</b>",
                    parse_mode=ParseMode.HTML,
                )
            elif target == 'creator':
                add_creator_sub(update.effective_chat.id, ident, thr)
                await update.message.reply_text(
                    f"OK. Порог для креатора <b>{esc(ident)}</b>: <b>{thr}</b>",
                    parse_mode=ParseMode.HTML,
                )
            pop_pending_threshold(update.effective_chat.id)
            return
    except Exception:
        pass

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()
    # One-shot threshold input: allow entering a number after pressing "Настроить порог"
    try:
        pend = chat_settings(update.effective_chat.id).get("await_thr")
        if isinstance(pend, dict) and t and re.fullmatch(r"\d{1,6}", t):
            thr = int(t)
            target = str(pend.get("target"))
            ident = str(pend.get("id"))
            if target == "map":
                add_map_sub(update.effective_chat.id, ident, thr)
                await update.message.reply_text(
                    f"OK. \u041F\u043E\u0440\u043E\u0433 \u0434\u043B\u044F \u043A\u0430\u0440\u0442\u044B <code>{esc(ident)}</code>: <b>{thr}</b>",
                    parse_mode=ParseMode.HTML,
                )
            elif target == "creator":
                add_creator_sub(update.effective_chat.id, ident, thr)
                await update.message.reply_text(
                    f"OK. \u041F\u043E\u0440\u043E\u0433 \u0434\u043B\u044F \u043A\u0440\u0435\u0430\u0442\u043E\u0440\u0430 <b>{esc(ident)}</b>: <b>{thr}</b>",
                    parse_mode=ParseMode.HTML,
                )
            s = chat_settings(update.effective_chat.id)
            s.pop("await_thr", None)
            save_json(STATE_PATH, STATE)
            return
    except Exception:
        pass
    if "fortnite.gg/island?code=" in t or (len(t) == 14 and t.count("-") == 2 and t.replace('-', '').isdigit()):
        await send_map_card(update.message, t)
        return
    if "fortnite.gg/creator?" in t:
        await send_creator_card(update.message, t)
        return
    if re.fullmatch(r"[A-Za-z0-9_.-]{2,32}", t):
        await send_creator_card(update.message, t)
        return


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.data:
        return
    data = q.data
    if data.startswith("top:"):
        _, off, lim = data.split(":", 2)
        await q.answer()
        await send_top(update.effective_chat.id, q.message, int(off), int(lim))
        return
    if data == "toggle_hideepic":
        s = chat_settings(update.effective_chat.id)
        set_setting(update.effective_chat.id, "hide_epic", not bool(s.get("hide_epic", True)))
        await q.answer("\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438 \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u044b")
        await send_top(update.effective_chat.id, q.message, 0, 10)
        return
    if data == "nav_home":
        kb = build_home_kb_dynamic(update.effective_chat.id)
        await send_one(q.message, text="\u0413\u043b\u0430\u0432\u043d\u043e\u0435 \u043c\u0435\u043d\u044e", reply_markup=kb, photo=get_banner_media())
        return
    if data == "stats:home":
        await q.answer()
        await send_stats_home(q.message)
        return
    if data == "stats:epicugc":
        await q.answer()
        await send_stats_epicugc(q.message)
        return
    if data == "stats:bz":
        await q.answer()
        await send_stats_build_zero(q.message)
        return
    if data == "stats:ranked":
        await q.answer()
        await send_stats_ranked(q.message)
        return
    if data == "stats:genres":
        await q.answer()
        await send_stats_genres(q.message)
        return
    if data.startswith("nav_top:"):
        _, lim = data.split(":", 1)
        await q.answer()
        await send_top(update.effective_chat.id, q.message, 0, int(lim))
        return
    if data == "start:map":
        await q.answer()
        await send_one(q.message, text="\u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u043a\u043e\u0434 1234-5678-9012 \u0438\u043b\u0438 \u0441\u0441\u044b\u043b\u043a\u0443 fortnite.gg/island?code=\u2026", reply_markup=home_kb())
        return
    if data == "start:creator":
        await q.answer()
        await send_one(q.message, text="\u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0438\u043c\u044f \u043a\u0440\u0435\u0430\u0442\u043e\u0440\u0430 \u0438\u043b\u0438 \u0441\u0441\u044b\u043b\u043a\u0443 fortnite.gg/creator?name=\u2026", reply_markup=home_kb())
        return
    if data == "start:settings":
        await q.answer()
        s = chat_settings(update.effective_chat.id)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(("\U0001F513 \u041f\u043e\u043a\u0430\u0437\u0430\u0442\u044c Epic" if s.get("hide_epic", True) else "\U0001F512 \u0421\u043a\u0440\u044b\u0432\u0430\u0442\u044c Epic"), callback_data="toggle_hideepic")],
            [InlineKeyboardButton("\U0001F3E0 \u0413\u043b\u0430\u0432\u043d\u0430\u044f", callback_data="nav_home")],
        ])
        await send_one(q.message, text=f"\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438:\n\u2022 Hide Epic: {'ON' if s.get('hide_epic', True) else 'OFF'}", reply_markup=kb)
        return
    if data == "start:help":
        await q.answer()
        await help_cmd(update, context)
        return
    if data == "start:alerts":
        await q.answer()
        await alerts_list_menu(update, context)
        return
    if data.startswith("open_map:"):
        code = up.unquote(data.split(":", 1)[1])
        await q.answer()
        await send_map_card(q.message, code)
        return
    if data.startswith("open_creator:"):
        name = up.unquote(data.split(":", 1)[1])
        await q.answer()
        await send_creator_card(q.message, name)
        return
    # Map alerts
    if data.startswith("alert_map:"):
        _, code_enc, thr = data.split(":", 2)
        code = up.unquote(code_enc)
        add_map_sub(update.effective_chat.id, code, int(thr))
        await q.answer(f"\u041f\u043e\u0434\u043f\u0438\u0441\u043a\u0430: {code} \u2265 {thr}")
        await send_map_card(q.message, code)
        return
    if data.startswith("alert_map_fixed:"):
        _, code_enc, thr = data.split(":", 2)
        code = up.unquote(code_enc)
        add_map_sub(update.effective_chat.id, code, int(thr))
        await q.answer(f"OK: {code} \u2265 {thr}")
        await send_map_card(q.message, code)
        return
    if data.startswith("alert_map_custom:"):
        _, code_enc = data.split(":", 1)
        code = up.unquote(code_enc)
        set_pending_threshold(update.effective_chat.id, "map", code)
        # Покажем всплывающую подсказку, не удаляя текущую карточку
        await q.answer(text=f"Введите команду:\n/alert_add {code} <порог>", show_alert=True)
        return
    # Creator alerts
    if data.startswith("alert_creator:"):
        _, name_enc, thr = data.split(":", 2)
        name = up.unquote(name_enc)
        add_creator_sub(update.effective_chat.id, name, int(thr))
        await q.answer(f"\u041f\u043e\u0434\u043f\u0438\u0441\u043a\u0430: {name} \u2265 {thr}")
        await send_creator_card(q.message, name)
        return
    if data.startswith("alert_creator_fixed:"):
        _, name_enc, thr = data.split(":", 2)
        name = up.unquote(name_enc)
        add_creator_sub(update.effective_chat.id, name, int(thr))
        await q.answer(f"OK: {name} \u2265 {thr}")
        await send_creator_card(q.message, name)
        return
    if data.startswith("alert_map_growth:"):
        _, code_enc = data.split(":", 1)
        code = up.unquote(code_enc)
        delta = int(os.getenv("BOT_GROWTH_DELTA", "25"))
        window_min = int(os.getenv("BOT_GROWTH_WINDOW", "15"))
        add_map_growth_sub(update.effective_chat.id, code, delta, window_min)
        await q.answer(f"OK: рост +{delta} за {window_min} мин")
        await send_map_card(q.message, code)
        return
    if data.startswith("alert_creator_growth:"):
        _, name_enc = data.split(":", 1)
        name = up.unquote(name_enc)
        delta = int(os.getenv("BOT_GROWTH_DELTA", "25"))
        window_min = int(os.getenv("BOT_GROWTH_WINDOW", "15"))
        add_creator_growth_sub(update.effective_chat.id, name, delta, window_min)
        await q.answer(f"OK: рост +{delta} за {window_min} мин")
        await send_creator_card(q.message, name)
        return
    if data.startswith("alert_creator_custom:"):
        _, name_enc = data.split(":", 1)
        name = up.unquote(name_enc)
        # Всплывающее окно с инструкцией, карточка остаётся на месте
        set_pending_threshold(update.effective_chat.id, "creator", name)
        await q.answer(text=f"Введите команду:\n/alertc_add {name} <порог>", show_alert=True)
        return

    # Map update reminder callbacks
    if data.startswith("updremind:"):
        _, code_enc, days = data.split(":", 2)
        code = up.unquote(code_enc)
        try:
            interval_days = int(days)
        except Exception:
            interval_days = 4
        # Create/enable reminder
        rec = set_map_update_reminder(update.effective_chat.id, code, interval_days)
        # Try to infer last update from fortnite.gg and backfill ts
        try:
            s = FortniteGGCreativeScraper()
            det2 = s.fetch_island_details(code)
            ts = _extract_updated_ts_from_details(det2)
            if ts:
                rec.update({"last_update_ts": ts, "last_notified_ts": None})
                save_json(SUBS_PATH, SUBS)
        except Exception:
            pass
        await q.answer("\u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435 \u0432\u043a\u043b\u044e\u0447\u0435\u043d\u043e")
        await send_map_card(q.message, code)
        return
    if data.startswith("updmark:"):
        _, code_enc = data.split(":", 1)
        code = up.unquote(code_enc)
        try:
            mark_map_updated_now(update.effective_chat.id, code)
        except NameError:
            pass
        await q.answer("OK")
        await send_one(q.message, text=f"\u2705 \u041e\u0442\u043c\u0435\u0447\u0435\u043d\u043e: {esc(code)} \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u043e. \u041d\u043e\u0432\u043e\u0435 \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435 \u0447\u0435\u0440\u0435\u0437 4 \u0434\u043d\u044f.", reply_markup=home_kb())
        

async def alert_add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await send_one(update.effective_message, text="\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u043d\u0438\u0435: /alert_add <\u043a\u043e\u0434|url> <\u043f\u043e\u0440\u043e\u0433>", reply_markup=home_kb())
        return
    ident, thr = context.args[0], int(context.args[1])
    code = ident
    if "code=" in ident:
        try:
            code = dict(up.parse_qsl(up.urlparse(ident).query)).get("code") or ident
        except Exception:
            pass
    add_map_sub(update.effective_chat.id, code, thr)
    await send_one(update.effective_message, text=f"OK. \u041f\u043e\u0434\u043f\u0438\u0441\u043a\u0430 \u043d\u0430 {code} \u043f\u0440\u0438 \u2265 {thr}")


async def alertc_add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await send_one(update.effective_message, text="\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u043d\u0438\u0435: /alertc_add <name|url> <\u043f\u043e\u0440\u043e\u0433>", reply_markup=home_kb())
        return
    name = context.args[0]
    if name.startswith("http") and "name=" in name:
        try:
            name = dict(up.parse_qsl(up.urlparse(name).query)).get("name") or name
        except Exception:
            pass
    thr = int(context.args[1])
    add_creator_sub(update.effective_chat.id, name, thr)
    await send_one(update.effective_message, text=f"OK. \u041f\u043e\u0434\u043f\u0438\u0441\u043a\u0430 \u043d\u0430 {name} \u043f\u0440\u0438 \u2265 {thr}")


async def alerts_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await alerts_list_menu(update, context)


# ==================== Map Update Reminders (every 4 days) ====================
import time as _time_mod
from datetime import datetime as _dt


def _now_ts() -> int:
    return int(_time_mod.time())


def reminders_bucket(chat_id: int):
    b = subs_bucket(chat_id)
    if "reminders" not in b:
        b["reminders"] = []
        save_json(SUBS_PATH, SUBS)
    return b["reminders"]  # type: ignore

def _parse_updated_text_to_ts(text: str) -> Optional[int]:
    if not text:
        return None
    t = text.strip()
    now = _now_ts()
    low = t.lower()
    # Relative like "3 days ago"
    m = re.search(r"(\d+)\s*(second|minute|hour|day|week|month|year)s?\s*ago", low)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        mult = {
            'second': 1,
            'minute': 60,
            'hour': 3600,
            'day': 86400,
            'week': 604800,
            'month': 2592000,  # approx 30 days
            'year': 31536000,
        }.get(unit, 0)
        if mult:
            return now - n * mult
    # Date formats
    for fmt in ["%Y-%m-%d", "%d.%m.%Y", "%b %d, %Y", "%d %b %Y", "%B %d, %Y"]:
        try:
            return int(_dt.strptime(t, fmt).timestamp())
        except Exception:
            pass
    m2 = re.search(r"(\d+)\s*day", low)
    if m2:
        return now - int(m2.group(1)) * 86400
    return None

def _extract_updated_ts_from_details(det) -> Optional[int]:
    try:
        so = getattr(det, 'stats_overview', {}) or {}
        for k in so.keys():
            if isinstance(k, str) and k.strip().lower() in ('updated', 'update', 'last update', 'last updated'):
                return _parse_updated_text_to_ts(str(so[k] or ''))
        for k, v in so.items():
            if isinstance(k, str) and any(s in k.lower() for s in ('updated','update')):
                ts = _parse_updated_text_to_ts(str(v or ''))
                if ts:
                    return ts
    except Exception:
        return None
    return None

def set_map_update_reminder(chat_id: int, code: str, days: int = 4) -> dict:
    b = reminders_bucket(chat_id)
    for r in b:
        if r.get("code") == code:
            r.update({
                "interval_days": max(1, int(days)),
                "active": True,
            })
            if not r.get("last_update_ts"):
                r["last_update_ts"] = _now_ts()
            save_json(SUBS_PATH, SUBS)
            return r
    rec = {
        "code": code,
        "interval_days": max(1, int(days)),
        "last_update_ts": _now_ts(),
        "last_notified_ts": None,
        "active": True,
    }
    b.append(rec)
    save_json(SUBS_PATH, SUBS)
    return rec


def mark_map_updated_now(chat_id: int, code: str) -> dict:
    b = reminders_bucket(chat_id)
    for r in b:
        if r.get("code") == code:
            r.update({
                "last_update_ts": _now_ts(),
                "last_notified_ts": None,
                "active": True,
            })
            save_json(SUBS_PATH, SUBS)
            return r
    return set_map_update_reminder(chat_id, code, 4)


def list_map_reminders(chat_id: int) -> list:
    return list(reminders_bucket(chat_id))


def _fmt_dt(ts: int) -> str:
    try:
        return _dt.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


async def remind_update_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await send_one(update.effective_message, text="\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u043d\u0438\u0435: /remind_update <\u043a\u043e\u0434|url> [\u0434\u043d\u0435\u0439=4]", reply_markup=home_kb())
        return
    ident = context.args[0]
    days = 4
    if len(context.args) >= 2:
        try:
            days = max(1, int(context.args[1]))
        except Exception:
            days = 4
    code = ident
    if "code=" in ident:
        try:
            code = dict(up.parse_qsl(up.urlparse(ident).query)).get("code") or ident
        except Exception:
            pass
    rec = set_map_update_reminder(update.effective_chat.id, code, days)
    # Fetch details to infer last update ts from fortnite.gg
    try:
        s = FortniteGGCreativeScraper()
        det = s.fetch_island_details(code)
        ts = _extract_updated_ts_from_details(det)
        if ts:
            rec.update({"last_update_ts": ts, "last_notified_ts": None})
            save_json(SUBS_PATH, SUBS)
    except Exception:
        pass
    next_ts = int(rec.get("last_update_ts", _now_ts())) + int(rec.get("interval_days", 4)) * 86400
    await send_one(update.effective_message, text=f"\u23F0 \u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435 \u0434\u043b\u044f {esc(code)} \u043a\u0430\u0436\u0434\u044b\u0435 {rec.get('interval_days')} \u0434\u043d\u044f. \u0421\u043b\u0435\u0434\u0443\u044e\u0449\u0435\u0435: <b>{_fmt_dt(next_ts)}</b>.")


async def mark_updated_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await send_one(update.effective_message, text="\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u043d\u0438\u0435: /mark_updated <\u043a\u043e\u0434|url>", reply_markup=home_kb())
        return
    ident = context.args[0]
    code = ident
    if "code=" in ident:
        try:
            code = dict(up.parse_qsl(up.urlparse(ident).query)).get("code") or ident
        except Exception:
            pass
    rec = mark_map_updated_now(update.effective_chat.id, code)
    next_ts = int(rec.get("last_update_ts", _now_ts())) + int(rec.get("interval_days", 4)) * 86400
    await send_one(update.effective_message, text=f"\u2705 {esc(code)}: \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u043e. \u041d\u043e\u0432\u043e\u0435 \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435: <b>{_fmt_dt(next_ts)}</b>.")


async def reminders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = list_map_reminders(update.effective_chat.id)
    if not items:
        await send_one(update.effective_message, text="\u041d\u0435\u0442 \u0430\u043a\u0442\u0438\u0432\u043d\u044b\u0445 \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0439. \u0414\u043e\u0431\u0430\u0432\u044c\u0442\u0435 /remind_update <\u043a\u043e\u0434>.")
        return
    lines = ["\u0412\u0430\u0448\u0438 \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u044f:"]
    now = _now_ts()
    for r in items:
        code = esc(str(r.get("code") or ""))
        iv = int(r.get("interval_days", 4))
        lu = int(r.get("last_update_ts") or now)
        next_ts = lu + iv * 86400
        overdue = now >= next_ts
        flag = "\u26A0\uFE0F" if overdue else "\u23F3"
        lines.append(f"{flag} <code>{code}</code> \u2014 {iv} \u0434\u043d. | \u0441 \u043e\u0431\u043d.: {_fmt_dt(lu)} | next: {_fmt_dt(next_ts)}")
    await send_one(update.effective_message, text="\n".join(lines))


async def check_reminders_job(context):
    try:
        now = _now_ts()
        for chat_id_str, data in list(SUBS.items()):
            try:
                chat_id = int(chat_id_str)
            except Exception:
                continue
            rems = (data or {}).get("reminders") or []
            changed = False
            for r in rems:
                if not r or not isinstance(r, dict):
                    continue
                if not r.get("active", True):
                    continue
                code = r.get("code")
                iv = int(r.get("interval_days", 4) or 4)
                lu = int(r.get("last_update_ts") or now)
                next_ts = lu + iv * 86400
                last_notified = r.get("last_notified_ts")
                if now >= next_ts and (not last_notified or int(last_notified) < next_ts):
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"\u23F0 \u041f\u043e\u0440\u0430 \u043e\u0431\u043d\u043e\u0432\u0438\u0442\u044c \u043a\u0430\u0440\u0442\u0443 <code>{esc(str(code))}</code>.\n"
                                f"/mark_updated {code} \u2014 \u043d\u0430\u0436\u043c\u0438\u0442\u0435 \u043f\u043e\u0441\u043b\u0435 \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u044f."
                            ),
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True,
                        )
                    except Exception:
                        pass
                    r["last_notified_ts"] = now
                    changed = True
            if changed:
                save_json(SUBS_PATH, SUBS)
    except Exception:
        pass

def main():
    load_dotenv(override=True)
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN is not set")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("version", version_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CommandHandler("map", map_cmd))
    app.add_handler(CommandHandler("creator", creator_cmd))
    app.add_handler(CommandHandler("alert_add", alert_add_cmd))
    app.add_handler(CommandHandler("alertc_add", alertc_add_cmd))
    app.add_handler(CommandHandler("alerts", alerts_list_cmd))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_router))
    app.add_handler(CallbackQueryHandler(callbacks))
    # Reminder commands
    try:
        app.add_handler(CommandHandler("remind_update", remind_update_cmd))
        app.add_handler(CommandHandler("mark_updated", mark_updated_cmd))
        app.add_handler(CommandHandler("reminders", reminders_cmd))
    except NameError:
        # Defined later in the file; safe in re-run environments
        pass

    cmds = [
        BotCommand("start", "\u0437\u0430\u043f\u0443\u0441\u0442\u0438\u0442\u044c \u0431\u043e\u0442\u0430"),
        BotCommand("help", "\u043f\u043e\u043c\u043e\u0449\u044c"),
        BotCommand("version", "\u0432\u0435\u0440\u0441\u0438\u044f/\u043e\u0442\u043a\u0443\u0434\u0430 \u0437\u0430\u043f\u0443\u0449\u0435\u043d"),
        BotCommand("top", "\u0442\u043e\u043f \u043a\u0430\u0440\u0442"),
        BotCommand("map", "\u043a\u0430\u0440\u0442\u0430 \u043f\u043e \u043a\u043e\u0434\u0443/\u0441\u0441\u044b\u043b\u043a\u0435"),
        BotCommand("creator", "\u0441\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430 \u043f\u043e \u043a\u0440\u0435\u0430\u0442\u043e\u0440\u0443"),
        BotCommand("alert_add", "\u043f\u043e\u0434\u043f\u0438\u0441\u043a\u0430 \u043d\u0430 \u043a\u0430\u0440\u0442\u0443"),
        BotCommand("alertc_add", "\u043f\u043e\u0434\u043f\u0438\u0441\u043a\u0430 \u043d\u0430 \u043a\u0440\u0435\u0430\u0442\u043e\u0440\u0430"),
        BotCommand("alerts", "\u043c\u043e\u0438 \u043f\u043e\u0434\u043f\u0438\u0441\u043a\u0438"),
        BotCommand("remind_update", "\u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435 \u043e\u0431 \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u0438 (4 \u0434\u043d\u044f)"),
        BotCommand("mark_updated", "\u043e\u0442\u043c\u0435\u0442\u0438\u0442\u044c \u043a\u0430\u0440\u0442\u0443 \u043a\u0430\u043a \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u043d\u0443\u044e"),
        BotCommand("reminders", "\u043c\u043e\u0438 \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u044f"),
    ]
    try:
        app.bot.set_my_commands(cmds)
    except Exception:
        pass

    # Start reminder check job hourly
    try:
        app.job_queue.run_repeating(check_reminders_job, interval=3600, first=60)
    except Exception:
        pass

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()





