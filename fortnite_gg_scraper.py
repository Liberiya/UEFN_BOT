import argparse
import csv
import json
from dataclasses import asdict
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from uefn_scraper.fortnite_gg import FortniteGGCreativeScraper


def main():
    ap = argparse.ArgumentParser(description="Scrape fortnite.gg/creative maps")
    ap.add_argument("command", choices=["list", "scrape", "map"], help="Action")
    ap.add_argument("--pages", type=int, default=1, help="How many pages to fetch")
    ap.add_argument("--out", type=Path, default=None, help="Path to save (json)")
    ap.add_argument("--no-details", action="store_true", help="Do not fetch per-map details")
    ap.add_argument("--id", type=str, default=None, help="Island code (12 digits) or fortnite.gg island URL for 'map' command")
    ap.add_argument(
        "--format",
        choices=["auto", "table", "json"],
        default="auto",
        help="Console output style when --out not set (auto=table)")
    args = ap.parse_args()

    s = FortniteGGCreativeScraper()
    if args.command == "list":
        rows = [i.__dict__ for i in s.iter_creative_list(max_pages=args.pages)]

        if args.out and args.out.suffix.lower() == ".csv":
            _write_csv(args.out, rows)
            print(f"Saved {len(rows)} rows -> {args.out}")
            return

        data = json.dumps(rows, ensure_ascii=False, indent=2)
        if args.out:
            args.out.write_text(data, encoding="utf-8")
            print(f"Saved {len(rows)} rows -> {args.out}")
        else:
            if args.format in ("auto", "table"):
                _print_creative_table(rows)
            else:
                Path("_output.json").write_text(data, encoding="utf-8")
                print(f"Wrote JSON to _output.json ({len(rows)} rows)")

    elif args.command == "scrape":
        rows = s.scrape(max_pages=args.pages, with_details=not args.no_details)

        if args.out and args.out.suffix.lower() == ".csv":
            _write_csv(args.out, rows)
            print(f"Saved {len(rows)} rows -> {args.out}")
            return

        data = json.dumps(rows, ensure_ascii=False, indent=2)
        if args.out:
            args.out.write_text(data, encoding="utf-8")
            print(f"Saved {len(rows)} rows -> {args.out}")
        else:
            if args.format in ("auto", "table"):
                _print_creative_table(rows)
            else:
                Path("_output.json").write_text(data, encoding="utf-8")
                print(f"Wrote JSON to _output.json ({len(rows)} rows)")

    elif args.command == "map":
        if not args.id:
            ap.error("--id is required for 'map' command (code or fortnite.gg URL)")
        det = s.fetch_island_details(args.id)
        row = asdict(det)

        if args.out and args.out.suffix.lower() == ".csv":
            _write_csv(args.out, [row])
            print(f"Saved 1 row -> {args.out}")
            return

        data = json.dumps(row, ensure_ascii=False, indent=2)
        if args.out:
            args.out.write_text(data, encoding="utf-8")
            print(f"Saved 1 row -> {args.out}")
        else:
            if args.format in ("auto", "table"):
                _print_map_panel(row)
            else:
                Path("_output.json").write_text(data, encoding="utf-8")
                print("Wrote JSON to _output.json (1 row)")


def _write_csv(path: Path, rows):
    # Flatten nested dicts if present
    flat_rows = []
    for r in rows:
        flat = dict(r)
        stats = flat.pop("stats_overview", None)
        if isinstance(stats, dict):
            for k, v in stats.items():
                flat[f"stats_{k}"] = v
        flat_rows.append(flat)
    headers = sorted({k for row in flat_rows for k in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(flat_rows)


# ---------------- Visual console helpers -----------------
_console = Console()


def _short(s: str, limit: int = 40) -> str:
    if not s:
        return ""
    s = str(s)
    return (s[: limit - 1] + "…") if len(s) > limit else s


def _print_creative_table(rows):
    table = Table(
        title="Fortnite.GG Creative — Most Played",
        title_style="bold",
        box=box.ROUNDED,
        expand=False,
        show_lines=False,
        padding=(0, 1),
    )
    table.add_column("#", justify="right", style="cyan", no_wrap=True)
    table.add_column("Title", style="bold")
    table.add_column("Code", style="magenta", no_wrap=True)
    table.add_column("Now", justify="right", style="green", no_wrap=True)
    table.add_column("Peak", justify="right", style="yellow", no_wrap=True)
    table.add_column("24h Plays", justify="right", style="dim", no_wrap=True)

    for it in rows:
        rank = it.get("rank") or ""
        title = _short(it.get("title") or "")
        code = it.get("code") or ""
        now_ = it.get("players_now") or ""
        peak = it.get("all_time_peak") or ""
        p24 = it.get("plays_24h") or it.get("players_24h") or ""
        table.add_row(str(rank), str(title), str(code), str(now_), str(peak), str(p24))

    _console.print(table)


def _print_map_panel(det: dict):
    title = det.get("name") or det.get("code") or "Map"
    code = det.get("code") or ""
    now = det.get("players_now_text") or ""
    p24 = det.get("peak_24h_text") or ""
    ap = det.get("all_time_peak_text") or ""
    tags = ", ".join(det.get("tags") or [])
    body = Text()
    if code:
        body.append(f"Code: {code}\n", style="magenta")
    if now:
        body.append(f"Now: {now}\n", style="green")
    if p24:
        body.append(f"24h Peak: {p24}\n", style="yellow")
    if ap:
        body.append(f"All-time Peak: {ap}\n", style="yellow")
    if tags:
        body.append(f"Tags: {tags}\n", style="dim")

    panel = Panel(body, title=str(title), title_align="left", border_style="blue", box=box.ROUNDED)
    _console.print(panel)


if __name__ == "__main__":
    main()
