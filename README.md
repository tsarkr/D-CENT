# D-CENT

A research repository that links the records of the March First Movement through PostgreSQL, TEI,
CIDOC-CRM, and Neo4j, and reconstructs for each question a dated narrative of incidents with citations
to the records. 한국어: [README_ko.md](./README_ko.md).

## Layout

| Path | Function | Run |
|---|---|---|
| [pipeline/](./pipeline/) | Data build: source files → PostgreSQL/TEI → Neo4j graph | `python -m pipeline` |
| [docent.php](./docent.php), [includes/](./includes/), [static/](./static/) | Web docent (query analysis, Cypher generation, narrative paths, two-stage generation) | PHP server |
| [evaluation/](./evaluation/) | Evaluation: benchmark, system comparison, expert judgment, report | `python -m evaluation` |
| [mcp_server.py](./mcp_server.py) | Read-only database tools for MCP clients | `python mcp_server.py` |
| [Latex/](./Latex/) | Manuscript | |

```text
pipeline/
  __main__.py   commands: build, check, inspect
  config.py     settings (environment → .streamlit/secrets.toml), paths, database connections
  text.py       Hanja readings (initial-sound law), TEI ruby helpers
  ingest.py     1  source files → raw_* tables, TEI per row
  entities.py   2  person-name tagging (Hanja readings as ruby, persName), entity list for review
  authority.py  3  links to the biographical dictionary (i815)
  cidoc.py      4  CIDOC-CRM mappings
  indexes.py    5  PostgreSQL search indexes
  graph.py      6  Neo4j graph
  thesaurus.py  7  Korean History Thesaurus
  names.py      8  name readings, romanized variants, labels of mention (Activity) nodes
  inspect.py    read-only diagnostics
  check.py      offline self-test
evaluation/
  __main__.py   commands: dataset, run, report, judge, expert, rate
  common.py     paths, statistics (bootstrap, permutation test, Holm, kappa), Excel workbooks
  benchmark.py  generation and validation of the benchmark
  systems.py    compared systems and shared conditions
  metrics.py    automatic measures, citation verification, paired comparison
  run.py        running and scoring, context-precision judge
  human.py      expert validation of the graph, blind rating of answers
  report.py     report (report.md, LaTeX tables, console summary)
  dataset/      questions.json (candidates), benchmark.json (validated), third_party/ (external questions)
  annotation/   workbooks for historians (expert/, rating/, rating_key/)
  results/      one folder per run; graph/ (graph size, expert validation)
```

  ## System flow

  ```mermaid
  flowchart LR
    %% Data pipeline
    subgraph DATA["1. Data build pipeline"]
      RAW["data/*.csv<br/>source data"]
      INGEST["pipeline ingest<br/>load raw_* tables"]
      ENTITY["pipeline entities<br/>create TEI and tag names"]
      AUTH["pipeline authority<br/>link i815 authority data"]
      CIDOC["pipeline cidoc<br/>CIDOC-CRM mappings"]
      INDEX["pipeline indexes<br/>PostgreSQL search indexes"]
      GRAPH["pipeline graph<br/>create Neo4j nodes and relations"]
      THESAURUS["pipeline thesaurus<br/>Korean History Thesaurus"]
      NAMES["pipeline names<br/>readings, romanized variants, namesIndex"]

      RAW --> INGEST --> ENTITY --> AUTH --> CIDOC --> INDEX
      INDEX --> GRAPH --> THESAURUS --> NAMES
    end

    PG[("PostgreSQL<br/>records, TEI, indexes")]
    NEO[("Neo4j<br/>CIDOC graph")]
    NAMESDB[("namesIndex<br/>name lookup index")]

    INGEST --> PG
    INDEX --> PG
    GRAPH --> NEO
    NAMES --> NAMESDB

    CHECK["pipeline check<br/>offline self-test"] -.-> DATA
    INSPECT["pipeline inspect<br/>database and graph diagnostics"] -.-> PG
    INSPECT -.-> NEO

    %% Web docent
    subgraph WEB["2. Web docent query flow"]
      USER["User"] --> BROWSER["Browser<br/>static/docent.js"]
      BROWSER --> ROUTER["docent.php<br/>POST, Origin, CSRF, rate limit"]
      ROUTER --> ACTION["includes/actions.php<br/>action dispatch"]

      ACTION --> ANALYZE["Analyze question<br/>language, focus, keywords"]
      ACTION --> NARRATIVE["Build narrative paths<br/>dated incidents and links"]
      ANALYZE --> GQUERY["Graph query<br/>Text-to-Cypher and cross-check"]
      NARRATIVE --> GQUERY
      GQUERY --> CONTEXT["Assemble context<br/>paths -> graph evidence -> records"]
      GQUERY --> FETCH["Fetch records<br/>record_refs and name search"]
      FETCH --> CONTEXT
      CONTEXT --> FACT["Gemini stage 1<br/>fact table"]
      FACT --> EXPLAIN["Gemini stage 2<br/>evidence-based explanation"]
      EXPLAIN --> RESPONSE["Graph, citations, and answer"]
      RESPONSE --> BROWSER
    end

    GQUERY --> NEO
    GQUERY --> NAMESDB
    FETCH --> PG
    NARRATIVE --> NEO
    ANALYZE --> GEMINI[("Gemini API")]
    FACT --> GEMINI
    EXPLAIN --> GEMINI

    %% Evaluation
    subgraph EVAL["3. Evaluation pipeline"]
      QUESTIONS["dataset/questions.json"] --> DATASET["evaluation dataset<br/>validate and create benchmark"]
      DATASET --> BENCH["benchmark.json"]
      BENCH --> RUN["evaluation run<br/>generate answers"]
      RUN --> SYSTEMS{"Compared systems"}
      SYSTEMS --> PASSAGE["passage<br/>string retrieval"]
      SYSTEMS --> HYBRID["hybrid<br/>BM25, dense, RRF"]
      SYSTEMS --> GRAPH_SYS["graph<br/>Neo4j retrieval"]
      SYSTEMS --> DCENT["dcent<br/>web docent over HTTP"]
      SYSTEMS --> ABLATIONS["controlled, no-paths,<br/>one-stage, hybrid-two-stage"]

      PASSAGE --> ANSWERS["answers.json"]
      HYBRID --> ANSWERS
      GRAPH_SYS --> ANSWERS
      DCENT --> ANSWERS
      ABLATIONS --> ANSWERS

      ANSWERS --> METRICS["metrics<br/>automatic scores"]
      METRICS --> SCORES["scores.csv"]
      ANSWERS --> JUDGE["evaluation judge<br/>context precision"]
      JUDGE --> JUDGE_RESULT["judge.json"]
      JUDGE --> OLLAMA[("Ollama")]

      ANSWERS --> RATE_EXPORT["rate export<br/>blind rating workbooks"]
      RATE_EXPORT --> HUMAN["human rating"]
      HUMAN --> RATING["rating.json"]

      GRAPH_SYS --> EXPERT_EXPORT["expert export<br/>annotation workbooks"]
      EXPERT_EXPORT --> EXPERT["expert score"]
      EXPERT --> EXPERT_RESULT["expert.json"]

      SCORES --> REPORT["evaluation report<br/>combine all results"]
      JUDGE_RESULT --> REPORT
      RATING --> REPORT
      EXPERT_RESULT --> REPORT
      REPORT --> REPORT_OUT["report.md, LaTeX tables, statistics"]
    end

    PASSAGE -.-> PG
    HYBRID -.-> PG
    GRAPH_SYS -.-> NEO
    DCENT -.-> ROUTER
    ABLATIONS -.-> ROUTER
  ```

## Installation and settings

- Python 3.11+, PostgreSQL, Neo4j 5.x, PHP and Composer
- For the evaluation only: Ollama (embedding `nomic-embed-text` for the hybrid baseline, judge `gemma4:26b`)

```bash
python3 -m venv .venv3.14
.venv3.14/bin/python -m pip install -r requirements.txt
composer install
```

Settings are read from environment variables, then from `.streamlit/secrets.toml`. Do not commit secrets.

```toml
PG_HOST = "localhost"
PG_PORT = "5432"
PG_DATABASE = "historical"
PG_USER = "postgres"
PG_PASSWORD = "change-me"
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "change-me"
GEMINI_API_KEY = "..."
GEMINI_MODEL = "gemini-flash-lite-latest"

# Evaluation (only when needed)
DOCENT_URL = "http://localhost:8000/docent.php"   # server called by the dcent system
EVAL_DELAY = "15"                                 # seconds between calls
```

Optional web-server settings: `DOCENT_CONTEXT_CHARS` (generation context budget, default 11000),
`DOCENT_EVAL_MODE=1` (allow evaluation mode), `GEMINI_TEMPERATURE`, `GEMINI_TOP_P`.

## Building the data

```bash
.venv3.14/bin/python -m pipeline build          # everything (PostgreSQL/TEI → Neo4j)
.venv3.14/bin/python -m pipeline build rdbms    # steps 1-5 only
.venv3.14/bin/python -m pipeline build graph    # steps 6-8 only (from existing PostgreSQL data)
.venv3.14/bin/python -m pipeline build --yes    # without the pause for the entity-list review
.venv3.14/bin/python -m pipeline check          # self-test without databases
```

Each step runs in its own process, and the build stops at the first failure. After step 2 it pauses so
that `data/output/Extracted_Historical_Entities.xlsx` can be reviewed; press Enter to continue.
The pipeline creates no embeddings.

- Step 1 drops and recreates the `raw_*` tables; check backups before running it on a production database.
- Steps 6-8 rebuild the Neo4j graph.
- The i815 dictionary index is cached in `data/i815_person_index.json`; delete it to download it again.
- The thesaurus CSV is the default file in `data/` or `THESAURUS_CSV`.

Diagnostics (read-only):

```bash
.venv3.14/bin/python -m pipeline inspect names "question"     # namesIndex lookup
.venv3.14/bin/python -m pipeline inspect family 유관순          # kinship data of a person
.venv3.14/bin/python -m pipeline inspect search 유관순 아우내   # record search timing and index use
.venv3.14/bin/python -m pipeline inspect counts                 # graph size → evaluation/results/graph/
.venv3.14/bin/python -m pipeline inspect sparsity               # Event-layer sparsity → evaluation/results/graph/
.venv3.14/bin/python -m pipeline inspect sources                # rows of the files in data/ and ID checks → evaluation/results/graph/
```

## Web docent

```bash
php -S 127.0.0.1:8000      # in the repository root
```

Open <http://127.0.0.1:8000/docent.php>. One question is answered through these requests:

| Action | Function |
|---|---|
| `narrative` | dated narrative paths from the persons and places in the question (in parallel with graph) |
| `analyze` | language, focus, and keywords of the question |
| `graph` | Text-to-Cypher (read-only guard) with a full-text cross-check; record identifiers of document nodes |
| `pg_prefetch` | record text by identifier, remaining slots by name |
| `explain` | context budget (paths → graph evidence → record text) → fact table → answer |
| `node_detail` | records behind a graph node |

Generated Cypher and step timings are written to the PHP error log with the prefix `[Docent]`. Incident dates
are properties of Event nodes; validity periods of relations are not modeled.

## Evaluation

```bash
.venv3.14/bin/python -m evaluation dataset              # validate questions → dataset/benchmark.json
.venv3.14/bin/python -m evaluation run --quick          # three questions, as a check
.venv3.14/bin/python -m evaluation run                  # full run → results/<date_time>/report.md
.venv3.14/bin/python -m evaluation run --resume         # continue an interrupted run
.venv3.14/bin/python -m evaluation report               # report of the latest run
.venv3.14/bin/python -m evaluation report RUN1 RUN2     # several runs combined
.venv3.14/bin/python -m evaluation judge                # context precision (secondary, Ollama)
```

`run` takes only `--quick`, `--limit N`, `--systems`, and `--resume`. The generation conditions are those of
the paper and fixed: temperature 0, top-p 0.8, 1,500 output tokens per answer, 11,000 characters of evidence
per question, retrieval with entities extracted once from the question (no system sees the gold entities),
and two retries of a failed generation.

| `--systems` key | System |
|---|---|
| `passage` | Passage baseline: bibliographic records containing an entity string |
| `hybrid` | Hybrid baseline: BM25 + dense → RRF → cross-encoder rerank |
| `graph` | Graph-only baseline: triples found by entity name |
| `dcent` | D-CENT end to end: the deployed system over HTTP |
| `controlled` | D-CENT with controlled evidence: name-matched triples and records, two-stage generation |
| `no-paths` | Ablation A: end to end without narrative paths |
| `one-stage` | Ablation B: controlled evidence answered in one call |
| `hybrid-two-stage` | Ablation C: hybrid evidence with the two-stage generation |

Choose `main` (the first four), `ablations` (the last four), `all` (default), or a comma-separated list.
`dcent` and `no-paths` call a running web docent: start the server in evaluation mode and set `DOCENT_URL`.

```bash
PHP_CLI_SERVER_WORKERS=4 DOCENT_EVAL_MODE=1 php -S localhost:8000     # in another terminal
```

In evaluation mode, `explain` returns the context and fact table it generated from, applies the shared
generation conditions, and asks for record citations; retrieval and context assembly are those of the
deployed system. `explain` allows five requests per minute, so keep `EVAL_DELAY` at 15 seconds or more.

A new build writes `data/output/build_manifest.json`. For an existing database with
no manifest, set `EVAL_DATA_BUILD_ID` to an identifier for its frozen snapshot.
For end-to-end evaluation, the PHP server reports the build in the same manifest; set its
`DOCENT_DATA_BUILD_ID` only for a database without a manifest, to the same identifier. Change the identifier whenever either database changes; keep both
databases fixed during an evaluation. `--resume` rejects changed code, dataset,
packages, model, server configuration, or build identifiers; old runs without a
fingerprint must be restarted. Checkpoints are written atomically after each answer.
Evaluation mode disables model fallback and records the actual server model versions.
The numeric shared generation conditions are unchanged. Corrected metrics and evidence
item boundaries require fresh runs; do not combine old and new results.

### Run results

`results/<date_time>/` holds `config.json` (conditions, model, code version), `answers.json` (answers and
evidence), `scores.csv` (scores per question and system), `report.md`, `table_main.tex`,
`table_by_category.tex`, `case_study.md` (one question, D-CENT next to the best baseline), and
`error_analysis.xlsx` (20 D-CENT answers to code for the four failure modes; rerun `report` to tally them).

The report contains per-system measures (mean and 95% bootstrap CI), the planned comparisons (RQ1: D-CENT
against the baselines; RQ2: contributions of the narrative paths, query generation, two-stage generation,
and evidence; paired permutation tests, Holm correction per measure), results by category and by question
source, citation verification, context precision, expert judgments, graph size, and items to check by hand
(failed generations, sentences flagged as trap endorsements). Combined runs are averaged per question before
testing, and run-to-run variation is shown.

### Benchmark

`dataset` validates `dataset/questions.json` and writes the questions that pass (with particle corrections)
to `dataset/benchmark.json`; the validation workbook with a 30% review sample and the composition table `benchmark_table.tex` go to
`dataset/validation/`. Reference facts of template questions state only what the event register records (persons,
dates, places, links between related incidents, and the order given by the dates).
`dataset --generate` first regenerates the template questions from the event register (curated and
third-party questions are kept) and merges external questions from `dataset/third_party/*.json` (with
`author`, `question`, `category`, and `reference_facts` filled in).

### Expert judgments

```bash
.venv3.14/bin/python -m evaluation expert export       # one workbook per annotator in annotation/expert/ (150 records)
.venv3.14/bin/python -m evaluation expert score        # → results/graph/expert.json
.venv3.14/bin/python -m evaluation rate export [RUN] [--n 40]   # one workbook per rater in annotation/rating/
.venv3.14/bin/python -m evaluation rate score          # → results/<RUN>/rating.json
```

Rating covers the answers of the four RQ1 systems (passage, hybrid, graph, dcent), shuffled and without system
names. Give raters only `annotation/rating/`; the blinding key in `annotation/rating_key/` stays with you.

## MCP server

[mcp_server.py](./mcp_server.py) provides `query_postgres` (SELECT/WITH), `get_postgres_schema`, `query_neo4j`,
and `get_neo4j_schema`. Adjust the paths in [mcp_client_config.json](./mcp_client_config.json) and keep secrets in
environment variables or secrets.toml, not in the client configuration.

MCP requires separate, least-privilege accounts configured as `MCP_PG_USER` /
`MCP_PG_PASSWORD` and `MCP_NEO4J_USER` / `MCP_NEO4J_PASSWORD`. Grant SELECT on the
required PostgreSQL tables and read-only access to the Neo4j database; do not grant
ownership, write/schema privileges, or unsafe procedure/function execution. Account
provisioning is an administrator task. The server refuses missing or reused build
usernames, uses read-only PostgreSQL transactions and Neo4j read access with a
read-only plan check, and limits query execution to 15 seconds and 500 returned rows.
User-supplied Cypher procedures, multi-statement queries, and comments are rejected.

## Checks

```bash
.venv3.14/bin/python -m pipeline check
php -l docent.php && php -l includes/actions.php
.venv3.14/bin/python -B -m unittest discover -s tests -v
php -n tests/gemini_safety.php
# With jsdom@26.1.0 installed in an isolated test directory:
NODE_PATH=/path/to/test/node_modules node tests/rendering_safety.cjs
```

## Operational notes

- `pipeline build` recreates PostgreSQL tables and the Neo4j graph. Confirm the target and backups.
- Source records, TEI, reference facts, and retrieved context are different layers of evidence. Do not adjust
  reference facts to model answers; check the records and the graph first.
- Keep API keys, database passwords, and remote connection details out of logs and commits.

## License

See [LICENSE](./LICENSE).
