"""文檔轉換 CLI（SPEC_INGEST，O9）。

    python -m matso_ai.ingest.cli convert <pdf> --doc-id GEN1 --out staging/
    python -m matso_ai.ingest.cli report  staging/<name>.md
    python -m matso_ai.ingest.cli promote staging/<name>.md --collection doctrine_general \
        --reviewer <name> --out ai/rag/corpus/
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from matso_ai.ingest.parse import extract_pages
from matso_ai.ingest.promote import promote_markdown
from matso_ai.ingest.structure import (
    build_sections,
    staging_confidence_report,
    to_staging_markdown,
)

_ANCHOR_RE = re.compile(r"^##\s*\[([A-Z0-9][A-Z0-9\-]*)\]", re.MULTILINE)


def cmd_convert(args: argparse.Namespace) -> int:
    pages = extract_pages(args.pdf, enable_ocr=not args.no_ocr)
    sections = build_sections(pages, args.doc_id)
    md = to_staging_markdown(sections, source=Path(args.pdf).name)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{args.doc_id.lower()}.md"
    dest.write_text(md, encoding="utf-8")
    report = staging_confidence_report(sections)
    print(f"convert → {dest}（{len(sections)} 節；{len(report)} 節需審核）")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    md = Path(args.staging).read_text(encoding="utf-8")
    anchors = _ANCHOR_RE.findall(md)
    flags = re.findall(r"<!-- INGEST-REVIEW: (.+?) -->", md)
    summary = {"anchors": len(anchors), "review_flags": flags}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    staging_md = Path(args.staging).read_text(encoding="utf-8")
    corpus_md = promote_markdown(staging_md, collection=args.collection, reviewer=args.reviewer)
    dest = Path(args.out) / args.collection / Path(args.staging).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(corpus_md, encoding="utf-8")
    print(f"promote → {dest}（reviewer={args.reviewer}）")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MATSO 文檔轉換（SPEC_INGEST）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("convert")
    c.add_argument("pdf")
    c.add_argument("--doc-id", required=True)
    c.add_argument("--out", default="staging")
    c.add_argument("--no-ocr", action="store_true")
    c.set_defaults(fn=cmd_convert)

    r = sub.add_parser("report")
    r.add_argument("staging")
    r.set_defaults(fn=cmd_report)

    p = sub.add_parser("promote")
    p.add_argument("staging")
    p.add_argument("--collection", required=True)
    p.add_argument("--reviewer", required=True)
    p.add_argument("--out", default="ai/rag/corpus")
    p.set_defaults(fn=cmd_promote)

    args = ap.parse_args(argv)
    rc: int = args.fn(args)
    return rc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
