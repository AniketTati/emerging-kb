"""Phase 5c / PR8 — generic LLM-driven atomic-unit plugin.

The fallback plugin that handles the long tail of prose doc_types not
served by a structural plugin (rows, clauses, transactions,
email_messages). Examples: incident postmortems, performance reviews,
press releases, RFCs, bug reports, resumes, lab reports, EOBs, meeting
minutes, case studies, financial summaries…

Approach: one Gemini call per doc, asking it to identify the doc's
"natural atomic items" given its classified doc_type. The plugin
provides per-doc-type hints to steer the extraction (so a postmortem
gets timeline_entries / action_items, a resume gets experience_entries,
etc.) but the model is free to invent item_type names when the hint
table doesn't cover the classification.

`parameters` jsonb shape (per item):
  {
    "item_type": "action_item",   // snake_case label invented by the LLM
    "title": "Patch S3 IAM policy", // short label
    "summary": "Update the staging S3 IAM…", // 1-2 sentences (also the
                                              // source-resolver target)
    "date": "2026-03-15",          // ISO if mentioned
    "actor": "alice@northwind",    // person/role if relevant
    "category": "remediation",     // optional sub-label
    "value": 51840                 // optional numeric for $ amounts / counts
  }

Graceful degrade — no API key → returns empty list (file still moves
through the pipeline; the doc-detail accordion just stays empty).
"""

from __future__ import annotations

import os
from typing import Any

from kb.extraction.plugins import AtomicUnit, FileMeta


UNIT_TYPE = "item"  # Default fallback when the LLM omits item_type.


# Hints injected into the prompt to steer Gemini toward natural items
# for each doc_type family. Not exhaustive — the model invents item_type
# labels for unhinted classifications.
_DOC_TYPE_HINTS: dict[str, str] = {
    # Engineering / IT
    "incident_report": (
        "timeline_entry (chronological events during the incident), "
        "root_cause, action_item (remediation owners + due dates), "
        "impact_metric (downtime minutes, customers affected)"
    ),
    "incident_postmortem": (
        "timeline_entry, root_cause, action_item, "
        "lesson_learned, impact_metric"
    ),
    "postmortem": (
        "timeline_entry, root_cause, action_item, lesson_learned"
    ),
    "bug_report": (
        "symptom (observed misbehavior), reproduction_step, "
        "root_cause_hypothesis, fix_attempted"
    ),
    "rfc": (
        "design_choice, alternative_considered, open_question, "
        "constraint, decision"
    ),
    "request_for_comments": (
        "design_choice, alternative_considered, open_question, decision"
    ),
    "api_design": (
        "endpoint, request_field, response_field, design_choice"
    ),

    # HR / org
    "performance_review": (
        "achievement, goal, area_for_growth, rating_dimension, "
        "feedback_quote"
    ),
    "resume": (
        "experience_entry (one job), education_entry, skill_group, "
        "achievement, certification"
    ),
    "job_posting": (
        "responsibility, requirement, nice_to_have, qualification, "
        "benefit"
    ),
    "job_description": (
        "responsibility, requirement, nice_to_have, qualification"
    ),
    "offer_letter": (
        "compensation_item, benefit, condition, key_date"
    ),

    # Finance / commerce
    "financial_report": (
        "kpi (one per metric — revenue / margin / churn), "
        "highlight (narrative point), risk, forward_guidance"
    ),
    "quarterly_report": (
        "kpi, highlight, risk, forward_guidance"
    ),
    "earnings_summary": (
        "kpi, highlight, segment_result, risk"
    ),
    "invoice": (
        "line_item (one per billable line — description + qty + unit_price + amount), "
        "subtotal, tax, total, payment_term"
    ),
    "purchase_order": (
        "line_item, delivery_term, total"
    ),

    # Medical
    "lab_report": (
        "test_result (one per measured analyte — name, value, unit, "
        "reference_range, flag for abnormal), specimen, ordering_provider"
    ),
    "discharge_summary": (
        "diagnosis, procedure_performed, medication_at_discharge, "
        "follow_up_instruction, key_finding"
    ),
    "medical_report": (
        "finding, recommendation, diagnosis, procedure_performed"
    ),
    "explanation_of_benefits": (
        "claim_line (one per service — code, allowed_amount, "
        "patient_responsibility), total_billed, total_paid"
    ),

    # Marketing / communications
    "press_release": (
        "announcement (the headline news), supporting_quote, "
        "key_metric, executive_quote"
    ),
    "case_study": (
        "challenge, solution, outcome_metric, customer_quote, "
        "key_takeaway"
    ),
    "vendor_evaluation": (
        "criterion (scored dimension), pro, con, recommendation"
    ),

    # Meetings / general workflow
    "meeting_minutes": (
        "discussion_point, decision, action_item (with owner + due date), "
        "attendee"
    ),
    "meeting_notes": (
        "discussion_point, decision, action_item, attendee"
    ),
    "standup_notes": (
        "status_update (per person), blocker, action_item"
    ),
}


def _build_user_prompt(doc_type: str, doc_text: str) -> str:
    hint = _DOC_TYPE_HINTS.get(doc_type.lower())
    if hint:
        hint_clause = (
            f"This is a {doc_type}. Look for items like: {hint}."
        )
    else:
        hint_clause = (
            f"This is a {doc_type}. Identify the doc's natural atomic "
            f"items — discrete units of meaning the user could ask "
            f"about later. Invent a snake_case item_type label for each."
        )

    return (
        "Extract atomic items from this document.\n\n"
        f"{hint_clause}\n\n"
        "For each item return: item_type (required, snake_case), "
        "title (short label), summary (1-2 sentences — the user's "
        "answer surface), and any of {date, actor, category, value, "
        "unit} when stated. Omit fields you cannot determine.\n\n"
        f"<doc>\n{doc_text}\n</doc>\n\n"
        'Return JSON exactly: {"items": [{"item_type": "...", '
        '"title": "...", "summary": "...", "date": "...", '
        '"actor": "...", "category": "...", "value": 0}]}'
    )


_SYSTEM_PROMPT = (
    "You extract atomic items from documents — each item is a discrete "
    "unit of meaning the user could ask about. Return JSON only. Skip "
    "filler / prose. Items should be specific (a single action_item, a "
    "single kpi, a single test_result), not summaries of the whole "
    "document."
)


_MAX_OUTPUT_TOKENS = 8000


def _parse_items(raw: str) -> list[dict[str, Any]]:
    from kb.extraction.json_recovery import parse_tolerant_array_in_object

    raw_list, truncated = parse_tolerant_array_in_object(raw, "items")
    if truncated:
        import logging
        logging.getLogger(__name__).warning(
            "generic_items response was truncated; recovered %d items",
            len(raw_list),
        )
    out: list[dict[str, Any]] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        item_type = item.get("item_type")
        if not isinstance(item_type, str) or not item_type.strip():
            # Tolerate missing item_type by falling back to the generic
            # UNIT_TYPE label — better than dropping the whole item.
            item_type = UNIT_TYPE
        clean: dict[str, Any] = {
            "item_type": item_type.strip().lower().replace(" ", "_")[:50],
        }
        for k in ("title", "summary", "date", "actor",
                  "category", "value", "unit"):
            v = item.get(k)
            if v is not None and v != "":
                clean[k] = v
        out.append(clean)
    return out


# Doc-type classifications that ARE prose but don't benefit from items
# extraction — they're handled by other plugins or have no natural
# atomic structure. Listed so the generic plugin can early-return
# instead of burning Gemini tokens.
_SKIP_DOC_TYPES = {
    "unknown",
    "document",
    "other",
    # Handled by structural plugins
    "bank_statement", "credit_card_statement",
    "price_sheet", "spreadsheet",
    # Handled by clauses plugin
    "legal_contract", "contract", "agreement", "nda",
    "employment_letter", "service_agreement",
    "license_agreement", "lease",
    # Handled by email plugin
    "email_thread", "email",
}


class GenericItemsPlugin:
    """Fallback plugin — runs LAST in the dispatcher chain. Matches any
    classified prose doc that no specific plugin handles. Skips xlsx /
    email mimes (the structural plugins already covered them)."""

    # Reported up to the worker for logging. The actual `unit_type` per
    # row is whatever item_type the LLM returns (e.g. "action_item",
    # "kpi") — finer-grained than this top-level label, but the JIT
    # anomaly scorer keys on unit_type so we need ONE consistent label.
    UNIT_TYPE = UNIT_TYPE

    def matches(self, file_meta: FileMeta) -> bool:
        if not file_meta.inferred_doc_type:
            return False
        dt = file_meta.inferred_doc_type.lower()
        if dt in _SKIP_DOC_TYPES:
            return False

        # Skip mimes already owned by structural plugins.
        mime = (file_meta.mime_type or "").lower()
        if mime in (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
            "application/x-xlsx",
            "message/rfc822",
        ):
            return False
        # Likewise — the substring guard against contract-like types is
        # delegated to the clauses plugin earlier in the dispatcher chain.
        # If we got here, that plugin already declined.
        return True

    async def extract(
        self,
        *,
        file_meta: FileMeta,
        doc_text: str,
        raw_pages: list[tuple[int, str, dict]],
    ) -> list[AtomicUnit]:
        api_key = os.environ.get("KB_GEMINI_API_KEY")
        if not api_key:
            return []

        # Skip empty docs.
        text = (doc_text or "").strip()
        if not text:
            return []

        model = (
            os.environ.get("KB_ATOMIC_UNIT_MODEL")
            or "gemini-2.5-flash"
        )

        try:
            from google.genai import Client, types
            client = Client(api_key=api_key)
            config = types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            )
            user_prompt = _build_user_prompt(
                file_meta.inferred_doc_type or "document",
                text[:16000],
            )
            response = await client.aio.models.generate_content(
                model=model,
                contents=user_prompt,
                config=config,
            )
        except Exception:
            return []

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return []
        raw_text = ""
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            t = getattr(part, "text", None)
            if t:
                raw_text = t
                break

        items = _parse_items(raw_text)
        units: list[AtomicUnit] = []
        for it in items:
            # Promote item_type to the row's unit_type — gives the per-
            # corpus anomaly scorer a tight grouping (e.g. score all
            # "action_item" rows against each other across the corpus).
            ut = str(it.pop("item_type", UNIT_TYPE))[:50]
            units.append(AtomicUnit(unit_type=ut, parameters=it))
        return units


PLUGIN = GenericItemsPlugin()
