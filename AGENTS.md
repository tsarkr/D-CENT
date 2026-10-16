# Agent instructions

D-CENT links March First Movement records (PostgreSQL/TEI) to a CIDOC-CRM-aligned Neo4j graph and answers
questions through a PHP web docent. See [README.md](README.md) for details.

## Where things are

- Data build: [pipeline/](pipeline/) — `python -m pipeline build|check|inspect`; one module per step, settings in [pipeline/config.py](pipeline/config.py)
- Web docent: [docent.php](docent.php), [includes/](includes/) (actions, prompts, security), [static/](static/)
- Evaluation: [evaluation/](evaluation/) — `python -m evaluation dataset|run|report|judge|expert|rate`; results in `evaluation/results/<run>/`
- Manuscript: [Latex/](Latex/)

## Rules

- Settings come from environment variables or `.streamlit/secrets.toml`; never print or commit secrets.
- `python -m pipeline build` drops and recreates PostgreSQL tables and the Neo4j graph. Ask before running it.
- Prefer read-only checks first: `python -m pipeline check`, `python -m pipeline inspect ...`, `php -l`.
- Keep the command-line surface small: add a setting or a constant rather than a new option, unless the user asks.
- The evaluation's shared conditions (in `evaluation/systems.py`) are the ones stated in the paper; change them only together with the paper.
- Keep changes minimal and summarize them for the commit message.
