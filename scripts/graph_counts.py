#!/usr/bin/env python3
"""Count nodes per label and relationships per type in Neo4j (read-only).

Writes two CSV files used for the graph-size tables of the paper:
  evaluation/results/graph_node_counts.csv          label,count
  evaluation/results/graph_relationship_counts.csv  type,count
A node with several labels (e.g. Person and 인물) is counted under each label;
the total line counts distinct nodes.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.config import get_neo4j_driver

OUT_DIR = ROOT / "evaluation" / "results"


def main() -> int:
    driver = get_neo4j_driver(notifications_min_severity="OFF")
    with driver.session() as session:
        labels = [r["label"] for r in session.run("CALL db.labels() YIELD label RETURN label")]
        nodes = []
        for label in labels:
            n = session.run(f"MATCH (n:`{label}`) RETURN count(n) AS n").single()["n"]
            nodes.append((label, n))
        nodes.sort(key=lambda x: -x[1])
        total_nodes = session.run("MATCH (n) RETURN count(n) AS n").single()["n"]
        rels = [(r["type"], r["n"]) for r in session.run(
            "MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS n ORDER BY n DESC")]
        total_rels = sum(n for _, n in rels)
    driver.close()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, rows, total in (("graph_node_counts.csv", nodes, total_nodes),
                              ("graph_relationship_counts.csv", rels, total_rels)):
        with (OUT_DIR / name).open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["label" if "node" in name else "type", "count"])
            w.writerows(rows)
            w.writerow(["TOTAL", total])
        print(f"{OUT_DIR / name}")
    print(f"nodes {total_nodes:,}, relationships {total_rels:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
