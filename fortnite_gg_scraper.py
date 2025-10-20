import argparse
import csv
import json
from dataclasses import asdict
from pathlib import Path

from uefn_scraper.fortnite_gg import FortniteGGCreativeScraper


def main():
    ap = argparse.ArgumentParser(description="Scrape fortnite.gg/creative maps")
    ap.add_argument("command", choices=["list", "scrape", "map"], help="Action")
    ap.add_argument("--pages", type=int, default=1, help="How many pages to fetch")
    ap.add_argument("--out", type=Path, default=None, help="Path to save (json)")
    ap.add_argument("--no-details", action="store_true", help="Do not fetch per-map details")
    ap.add_argument("--id", type=str, default=None, help="Island code (12 digits) or fortnite.gg island URL for 'map' command")
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


if __name__ == "__main__":
    main()
