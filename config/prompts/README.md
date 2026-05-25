# config/prompts/

Per Design 9 §"What's tunable vs. baked-in", **all LLM prompts are tunable** and live here as YAML. Today most prompts are still inline in their Python modules (`kb/extraction/mentions.py`, `kb/query/rewriter.py`, etc.) — this directory exists as the migration target.

Files (created by the phase that moves the prompt here):

| File | Owner phase | Status |
|---|---|---|
| `extraction.yaml` | Phase 5 modules | TODO — inline today |
| `planner.yaml` | WA-10 (planner + Q-mode) | TODO |
| `generation.yaml` | Phase 8e | TODO — inline today |
| `conflict_detector.yaml` | WA-6 | TODO |
| `chat_context.yaml` | WA-12 (anaphora resolver) | TODO |
| `intent_classifier.yaml` | WA-9 | TODO |

Each YAML carries: `name`, `model`, `system_instruction`, `temperature`, `max_output_tokens`, `response_schema` (optional), `version`. `kb.layered_config.resolve_config("prompts.<name>")` returns the resolved spec.

Until a prompt is migrated, the resolver returns `None` for its key and modules fall back to inline strings.
