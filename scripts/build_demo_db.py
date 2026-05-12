#!/usr/bin/env python3
"""Build src/basalt/data/demo.db from examples/sample-vault/.

Release-time tool. Requires Ollama running with `nomic-embed-text` pulled.

Run when:
  - examples/sample-vault/ contents change
  - embedding model changes
  - buried-insight algorithm changes in a way that affects bundled output

Usage:
  python scripts/build_demo_db.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_VAULT = REPO_ROOT / "examples" / "sample-vault"
DEMO_DB = REPO_ROOT / "src" / "basalt" / "data" / "demo.db"

MIN_SIZE_BYTES = 50_000      # 50KB — sanity floor
MAX_SIZE_BYTES = 500_000     # 500KB — sanity ceiling

# Add src/ to sys.path so we can import basalt without installing it.
sys.path.insert(0, str(REPO_ROOT / "src"))

from basalt.vault import walk_vault
from basalt.index import open_db, upsert_note, replace_links, resolve_link_targets
from basalt.embed import ensure_embeddings
from basalt.buried import find_buried_insights


def main() -> int:
    if not SAMPLE_VAULT.is_dir():
        print(f"✗ sample vault not found at {SAMPLE_VAULT}", file=sys.stderr)
        return 1

    print(f"Building {DEMO_DB} from {SAMPLE_VAULT}…")
    if DEMO_DB.exists():
        DEMO_DB.unlink()
    DEMO_DB.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    conn = open_db(DEMO_DB)

    n_notes = 0
    for note in walk_vault(SAMPLE_VAULT):
        nid = upsert_note(conn, note)
        replace_links(conn, nid, note.wikilinks)
        n_notes += 1
    conn.commit()
    resolve_link_targets(conn)
    conn.commit()
    print(f"  ✓ indexed {n_notes} notes in {time.time()-t0:.1f}s")

    t1 = time.time()
    computed, skipped = ensure_embeddings(
        conn,
        model="nomic-embed-text",
        on_progress=lambda msg: print(f"  {msg}"),
    )
    print(f"  ✓ embedded {computed} (skipped {skipped}) in {time.time()-t1:.1f}s")

    # Validation: must produce at least one buried insight.
    results = find_buried_insights(conn, vault_aware=True, top_n=1)
    if not results:
        print("✗ validation failed: no buried insights surfaced", file=sys.stderr)
        return 2
    quote = results[0].quote.strip().replace("\n", " ")[:60]
    print(f"  ✓ top buried insight: {quote!r}")

    conn.close()

    # Validation: size bounds.
    size = DEMO_DB.stat().st_size
    if not (MIN_SIZE_BYTES <= size <= MAX_SIZE_BYTES):
        print(
            f"✗ validation failed: demo.db size {size} bytes is outside "
            f"[{MIN_SIZE_BYTES}, {MAX_SIZE_BYTES}]",
            file=sys.stderr,
        )
        return 3
    print(f"  ✓ size {size:,} bytes")
    print(f"✓ done · {DEMO_DB}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
