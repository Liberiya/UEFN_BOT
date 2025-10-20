"""
UTF-8 safe Telegram bot for fortnite.gg data (single-post UI).
All user-facing strings use Unicode escapes to prevent mojibake in shells.
"""

import os
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
            f"\U0001F465 Now: <b>{now_}</b>   \U0001F53A Peak: <b>{peak}</b>   \u25B6\uFE0F 24h Plays: {esc(it.plays_24h)}"
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


def build_home_kb_dynamic(chat_id: int) -> InlineKeyboardMarkup:
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

    maps_count = len(SUBS.get(str(chat_id), {}).get("maps", []))
    creators_count = len(SUBS.get(str(chat_id), {}).get("creators", []))
    subs_count = maps_count + creators_count

    def fmt(n: Optional[int]) -> str:
        if n is None:
            return "?"
        return f"{int(n):,}".replace(",", " ")

    sub_label = f"\U0001F514 \u041f\u043e\u0434\u043f\u0438\u0441\u043a\u0438 ({subs_count})"
    total_label = f"\U0001F4C8 Fortnite: {fmt(total_global)}"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001F525 \u0422\u043e\u043f 10", callback_data="nav_top:10")],
        [InlineKeyboardButton("\U0001F50E \u041d\u0430\u0439\u0442\u0438 \u043a\u0430\u0440\u0442\u0443", callback_data="start:map"), InlineKeyboardButton("\U0001F464 \u041a\u0440\u0435\u0430\u0442\u043e\u0440", callback_data="start:creator")],
        [InlineKeyboardButton(sub_label, callback_data="start:alerts"), InlineKeyboardButton("\u2699\uFE0F \u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438", callback_data="start:settings")],
        [InlineKeyboardButton(total_label, url="https://fortnite.gg/player-count"), InlineKeyboardButton("\u2753 \u041f\u043e\u043c\u043e\u0449\u044c", callback_data="start:help")],
    ])


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
    header = f"<b>Top {offset+1}\u2013{offset+len(slice_)}</b> of most played | Hide Epic: <b>{'ON' if s.get('hide_epic', True) else 'OFF'}</b>\n"
    text = header + ("\n".join(format_list_items(slice_, limit=len(slice_))) if slice_ else "\n\u041d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445")
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
    lines = [f"<b>{name}</b>", f"<code>{esc(code)}</code>", f"\U0001F465 Players: {pn}"]
    if re.search(r"\d", p24_raw or ""):
        lines.append(f"\U0001F4C8 24h Peak: {p24}")
    lines.append(f"\U0001F3C6 All-time Peak: {ap}")
    lines.append(f"\U0001F3F7\uFE0F Tags: {esc(tags)}")
    text = "\n".join(lines)
    url = f"https://fortnite.gg/island?code={code}" if code else "https://fortnite.gg/creative"
    qcode = up.quote(code, safe='')
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001F310 \u041e\u0442\u043a\u0440\u044b\u0442\u044c \u043d\u0430 Fortnite.GG", url=url), InlineKeyboardButton("\U0001F4CB \u041a\u043e\u0434", callback_data=f"copy_code:{qcode}" if code else "noop")],
        [InlineKeyboardButton("\U0001F514 50", callback_data=f"alert_map:{qcode}:50"), InlineKeyboardButton("\U0001F514 100", callback_data=f"alert_map:{qcode}:100"), InlineKeyboardButton("\U0001F514 500", callback_data=f"alert_map:{qcode}:500"), InlineKeyboardButton("\U0001F514 1000", callback_data=f"alert_map:{qcode}:1000")],
        [InlineKeyboardButton("\u2699\uFE0F \u041d\u0430\u0441\u0442\u0440\u043e\u0438\u0442\u044c \u043f\u043e\u0440\u043e\u0433", callback_data=f"alert_map_custom:{qcode}")],
        [InlineKeyboardButton("\U0001F3E0 \u0413\u043b\u0430\u0432\u043d\u0430\u044f", callback_data="nav_home")],
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
    text = f"<b>Creator: {name}</b>\n\U0001F465 Now (sum): <b>{stats.total_players_now}</b> | Maps: <b>{stats.total_maps}</b>\n\n" + "\n".join(format_list_items(stats.items, limit=10))
    url = f"https://fortnite.gg/creator?name={name}"
    qname = up.quote(stats.name, safe='')
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001F310 \u041e\u0442\u043a\u0440\u044b\u0442\u044c \u043d\u0430 Fortnite.GG", url=url)],
        [InlineKeyboardButton("\U0001F514 50", callback_data=f"alert_creator:{qname}:50"), InlineKeyboardButton("\U0001F514 100", callback_data=f"alert_creator:{qname}:100"), InlineKeyboardButton("\U0001F514 500", callback_data=f"alert_creator:{qname}:500"), InlineKeyboardButton("\U0001F514 1000", callback_data=f"alert_creator:{qname}:1000")],
        [InlineKeyboardButton("\u2699\uFE0F \u041d\u0430\u0441\u0442\u0440\u043e\u0438\u0442\u044c \u043f\u043e\u0440\u043e\u0433", callback_data=f"alert_creator_custom:{qname}")],
        [InlineKeyboardButton("\U0001F3E0 \u0413\u043b\u0430\u0432\u043d\u0430\u044f", callback_data="nav_home")],
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
            thr = s.get("threshold")
            lines.append(f"\u2022 {esc(code)} \u2014 \u043f\u043e\u0440\u043e\u0433 {thr}")
            code_q = up.quote(code, safe='')
            rows.append([InlineKeyboardButton(text=f"\U0001F5FA\uFE0F {esc(code)}", callback_data=f"open_map:{code_q}")])
    if b["creators"]:
        lines.append("\n\u041a\u0440\u0435\u0430\u0442\u043e\u0440\u044b:")
        for s in b["creators"]:
            name = s.get("name") or ""
            thr = s.get("threshold")
            lines.append(f"\u2022 {esc(name)} \u2014 \u043f\u043e\u0440\u043e\u0433 {thr}")
            name_q = up.quote(name, safe='')
            rows.append([InlineKeyboardButton(text=f"\U0001F464 {esc(name)}", callback_data=f"open_creator:{name_q}")])
    rows.append([InlineKeyboardButton("\U0001F3E0 \u0413\u043b\u0430\u0432\u043d\u0430\u044f", callback_data="nav_home")])
    kb = InlineKeyboardMarkup(rows)
    await send_one(update.effective_message, text="\n".join(lines), reply_markup=kb)


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()
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
    if data.startswith("alert_map_custom:"):
        _, code_enc = data.split(":", 1)
        code = up.unquote(code_enc)
        await q.answer()
        await send_one(q.message, text=f"\u0412\u0432\u0435\u0434\u0438\u0442\u0435: /alert_add {code} <\u043f\u043e\u0440\u043e\u0433>", reply_markup=home_kb())
        return
    # Creator alerts
    if data.startswith("alert_creator:"):
        _, name_enc, thr = data.split(":", 2)
        name = up.unquote(name_enc)
        add_creator_sub(update.effective_chat.id, name, int(thr))
        await q.answer(f"\u041f\u043e\u0434\u043f\u0438\u0441\u043a\u0430: {name} \u2265 {thr}")
        await send_creator_card(q.message, name)
        return
    if data.startswith("alert_creator_custom:"):
        _, name_enc = data.split(":", 1)
        name = up.unquote(name_enc)
        await q.answer()
        await send_one(q.message, text=f"\u0412\u0432\u0435\u0434\u0438\u0442\u0435: /alertc_add {name} <\u043f\u043e\u0440\u043e\u0433>", reply_markup=home_kb())


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


def main():
    load_dotenv(override=True)
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN is not set")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CommandHandler("map", map_cmd))
    app.add_handler(CommandHandler("creator", creator_cmd))
    app.add_handler(CommandHandler("alert_add", alert_add_cmd))
    app.add_handler(CommandHandler("alertc_add", alertc_add_cmd))
    app.add_handler(CommandHandler("alerts", alerts_list_cmd))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_router))
    app.add_handler(CallbackQueryHandler(callbacks))

    cmds = [
        BotCommand("start", "\u0437\u0430\u043f\u0443\u0441\u0442\u0438\u0442\u044c \u0431\u043e\u0442\u0430"),
        BotCommand("help", "\u043f\u043e\u043c\u043e\u0449\u044c"),
        BotCommand("top", "\u0442\u043e\u043f \u043a\u0430\u0440\u0442"),
        BotCommand("map", "\u043a\u0430\u0440\u0442\u0430 \u043f\u043e \u043a\u043e\u0434\u0443/\u0441\u0441\u044b\u043b\u043a\u0435"),
        BotCommand("creator", "\u0441\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430 \u043f\u043e \u043a\u0440\u0435\u0430\u0442\u043e\u0440\u0443"),
        BotCommand("alert_add", "\u043f\u043e\u0434\u043f\u0438\u0441\u043a\u0430 \u043d\u0430 \u043a\u0430\u0440\u0442\u0443"),
        BotCommand("alertc_add", "\u043f\u043e\u0434\u043f\u0438\u0441\u043a\u0430 \u043d\u0430 \u043a\u0440\u0435\u0430\u0442\u043e\u0440\u0430"),
        BotCommand("alerts", "\u043c\u043e\u0438 \u043f\u043e\u0434\u043f\u0438\u0441\u043a\u0438"),
    ]
    try:
        app.bot.set_my_commands(cmds)
    except Exception:
        pass

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()




