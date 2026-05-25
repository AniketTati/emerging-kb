"""B4b — Q-mode (structured-query) planner package.

Architecture §6 step 3 mode "Q"; gaps_design.md Design 1.

A Q-plan is the planner's structured aggregation intent. The pipeline:

    raw plan dict
       ↓
    grammar.parse_plan   → typed QPlan dataclass  (parse-time enums)
       ↓
    validator.validate   → ValidatedQPlan         (catalog whitelist)
       ↓
    compiler.compile     → (sql, params)          (parameter-only)
       ↓
    executor.execute     → QExecutionResult       (txn read_only + timeout + row cap)
       ↓
    artifact.persist     → audit_queries row + CSV in MinIO

The 10 defense layers (Design 1):
  1. Field-name whitelist          → validator (catalog.py)
  2. Operator enum                 → grammar.parse_plan
  3. Aggregation enum              → grammar.parse_plan
  4. Set-op enum                   → grammar.parse_plan (Wave A: not used)
  5. Parameter-only values         → compiler (only $N placeholders)
  6. No raw-SQL escape hatch       → compiler (grammar disallows raw strings)
  7. Read-only PG role             → executor (SET LOCAL transaction_read_only=on)
  8. statement_timeout = 30s       → executor (SET LOCAL statement_timeout)
  9. Result row cap                → executor (LIMIT cap clamped before exec)
 10. Audit log row + artifact      → artifact.persist (audit_queries)
"""

from kb.q_planner.grammar import (
    ALLOWED_AGGREGATIONS,
    ALLOWED_OPERATORS,
    ALLOWED_SET_OPS,
    Filter,
    QPlan,
    Aggregation,
    parse_plan,
)
from kb.q_planner.validator import QPlanValidationError, ValidatedQPlan, validate
from kb.q_planner.compiler import compile_plan
from kb.q_planner.executor import (
    DEFAULT_ROW_CAP,
    DEFAULT_TIMEOUT_MS,
    QExecutionError,
    QExecutionResult,
    execute,
)


__all__ = [
    "ALLOWED_AGGREGATIONS",
    "ALLOWED_OPERATORS",
    "ALLOWED_SET_OPS",
    "Aggregation",
    "DEFAULT_ROW_CAP",
    "DEFAULT_TIMEOUT_MS",
    "Filter",
    "QExecutionError",
    "QExecutionResult",
    "QPlan",
    "QPlanValidationError",
    "ValidatedQPlan",
    "compile_plan",
    "execute",
    "parse_plan",
    "validate",
]
