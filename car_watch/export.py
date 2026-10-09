"""Produce a single HTML snapshot usable without a server."""
import argparse
import json
from pathlib import Path
from collector import DEFAULT_DATA, load

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "data" / "dashboard.html")
    args = parser.parse_args()
    data = json.dumps(load(args.data), ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    html = (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html.replace("/*INITIAL_DATA*/ null", data), encoding="utf-8")
    print(args.output)
