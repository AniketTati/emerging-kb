"""B9 / WA-16 + WA-17 — Dataset loader + Eval harness.

Three primitives:

  - kb.eval.loader   — ingest a directory tree of files via POST /files
                       (uses the existing HTTP ingest pipeline so the
                       full parse → chunk → embed → extract chain fires).
  - kb.eval.runner   — drive a question set through POST /chat,
                       collecting answers + citations + verdict metadata.
  - kb.eval.scorer   — pure-function metrics (lexical overlap, refusal
                       correctness, citation count) + CSV writer.

Golden questions live in `golden_questions.yaml`: 45 questions across
9 strata (architecture §"9-stratum golden set"). The set is templated
for the demo corpus (CUAD + Enron + SEC); operators replace question
text + expectations to match their workspace data.

CLI: `python -m kb.eval.cli` — see scripts/run_eval.sh for the
fetch-corpus → ingest → wait-for-ready → run-eval orchestration.
"""

from kb.eval.loader import (
    IngestionReport,
    IngestionResult,
    ingest_directory,
    ingest_file,
)
from kb.eval.runner import (
    EvalResult,
    GoldenQuestion,
    load_golden_questions,
    run_eval,
)
from kb.eval.scorer import (
    ScoreReport,
    score_results,
    write_results_csv,
)


__all__ = [
    "EvalResult",
    "GoldenQuestion",
    "IngestionReport",
    "IngestionResult",
    "ScoreReport",
    "ingest_directory",
    "ingest_file",
    "load_golden_questions",
    "run_eval",
    "score_results",
    "write_results_csv",
]
