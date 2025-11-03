from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .fortnite_gg import FortniteGGCreativeScraper

app = typer.Typer(help="Fortnite.GG Creative scraper")
console = Console()


@app.command()
def list(
    pages: int = typer.Option(1, "--pages", min=1, help="How many pages to fetch"),
    out: Optional[Path] = typer.Option(None, "--out", help="Save results to file (json or csv)"),
    format: str = typer.Option("table", "--format", help="Output format: table|json|csv"),
):
    """Fetch creative listing (Most Played) and print summary."""
    scraper = FortniteGGCreativeScraper()
    items = list(scraper.iter_creative_list(max_pages=pages))
    rows = [item.__dict__ for item in items]

    if out:
        if out.suffix.lower() == ".csv" or format == "csv":
            _write_csv(out, rows)
        else:
            out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"Saved {len(rows)} rows to {out}")
        return

    if format == "json":
        console.print_json(data=rows)
    elif format == "csv":
        # Print CSV to stdout
        import sys
        _write_csv(None, rows, file=sys.stdout)
    else:
        table = Table(show_lines=False)
        table.add_column("#", justify="right")
        table.add_column("Code")
        table.add_column("Title")
        table.add_column("Now")
        table.add_column("Peak")
        table.add_column("24h Plays")
        for r in rows[:100]:
            table.add_row(
                str(r.get("rank") or ""),
                r.get("code") or "",
                (r.get("title") or "")[:40],
                str(r.get("players_now") or ""),
                str(r.get("all_time_peak") or ""),
                r.get("plays_24h") or "",
            )
        console.print(table)


@app.command()
def scrape(
    pages: int = typer.Option(1, "--pages", min=1, help="How many pages to fetch"),
    out: Optional[Path] = typer.Option(None, "--out", help="Save results to JSON/CSV"),
    details: bool = typer.Option(True, "--details/--no-details", help="Follow each map page to get description, tags, etc."),
    format: str = typer.Option("json", "--format", help="Output format: json|csv"),
):
    """Scrape listing and optionally enrich with map details."""
    scraper = FortniteGGCreativeScraper()
    rows = scraper.scrape(max_pages=pages, with_details=details)
    if out:
        if out.suffix.lower() == ".csv" or format == "csv":
            _write_csv(out, rows)
        else:
            out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"Saved {len(rows)} rows to {out}")
        return
    console.print_json(data=rows)


def _write_csv(path: Optional[Path], rows, file=None):
    # Flatten nested dicts for CSV
    flat_rows = []
    for r in rows:
        flat = dict(r)
        stats = flat.pop("stats_overview", None)
        if isinstance(stats, dict):
            for k, v in stats.items():
                flat[f"stats_{k}"] = v
        flat_rows.append(flat)
    # Collect headers
    headers = sorted({k for row in flat_rows for k in row.keys()})
    if file is None and path is not None:
        f = path.open("w", newline="", encoding="utf-8")
        close = True
    else:
        f = file
        close = False
    writer = csv.DictWriter(f, fieldnames=headers)
    writer.writeheader()
    writer.writerows(flat_rows)
    if close:
        f.close()


def main():
    app()


if __name__ == "__main__":
    main()

