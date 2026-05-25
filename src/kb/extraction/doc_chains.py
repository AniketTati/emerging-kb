"""WA-3 / Design 3 — heuristic doc-chain detection.

Per Design 3 §"Detection — per chain type". Wave A ships **heuristic-only**
detectors — no LLM judge yet (Design 3 §"Pipeline integration" labels that
as "$0.001/doc Gemini Flash on borderline cases"; defer to a later phase).

Contract:

    DetectionInput holds everything a detector needs:
      - the file under inspection (file_id, name, mime_type,
        inferred_doc_type)
      - extracted page text snippets (first page, normalized title-like
        text, headers)
      - sibling files in the workspace (id, name, mime_type, doc_type,
        chain_key candidates, parse-time created_at)
      - email-specific headers when mime is message/rfc822
      - drawing-specific filename suffix when present

    detect_chain(input) -> ChainCandidate | None
      Returns None when no rule fires confidently.

Five per-type detectors run in priority order: email → contract → drawing
→ circular → patient. First confident match wins. All detectors are pure
functions of the DetectionInput; the worker stage is responsible for DB
reads (building DetectionInput) + DB writes (calling kb.domain.doc_chains).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SiblingFile:
    file_id: str
    name: str
    mime_type: str
    inferred_doc_type: str | None
    # Pre-parsed metadata that detectors look at.
    title_text: str | None = None
    project_id: str | None = None
    email_message_id: str | None = None
    email_references: tuple[str, ...] = ()
    email_subject: str | None = None
    email_sender: str | None = None
    email_recipients: tuple[str, ...] = ()
    email_date_iso: str | None = None
    chain_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class DetectionInput:
    file_id: str
    name: str
    mime_type: str
    inferred_doc_type: str | None
    title_text: str | None = None
    project_id: str | None = None
    email_message_id: str | None = None
    email_in_reply_to: str | None = None
    email_references: tuple[str, ...] = ()
    email_subject: str | None = None
    email_sender: str | None = None
    email_recipients: tuple[str, ...] = ()
    email_date_iso: str | None = None
    siblings: tuple[SiblingFile, ...] = ()


@dataclass(frozen=True)
class ChainCandidate:
    """Output of detect_chain. The worker turns this into doc_chains +
    doc_chain_members rows."""
    chain_type: str
    chain_key: str | None
    title: str | None
    role: str
    version_index: int
    confidence: float
    # Only set for email threads — the message that was replied to.
    parent_doc_id: str | None = None
    # Other members already in the chain (their file_ids); used to compute
    # version_index when joining an existing chain.
    sibling_member_ids: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SUBJECT_PREFIX_RE = re.compile(
    r"^(re|fwd|fw|aw|external|external\s*:?|sv|tr|antw)\s*:?\s*",
    re.IGNORECASE,
)

_AMENDMENT_PHRASES = (
    "amends", "amendment", "side letter", "supersedes", "this amendment",
    "amendment no", "amendment number", "addendum", "this addendum",
)

_REVISION_FILENAME_RE = re.compile(
    # base is non-greedy, MUST end at a separator before the rev marker
    r"^(?P<base>.+?)"
    r"[_\-\s.]+"
    # rev keyword (whole word; matched via the trailing rev token boundary)
    r"(?:rev|revision|version|r|v)"
    r"\s*"
    # rev token: 1-4 digits OR a single letter — and must be followed by a
    # separator / extension / end-of-string. Anchors away "contract.pdf"-
    # style false positives where 'r' inside the base name would otherwise
    # absorb subsequent letters as the rev token.
    r"(?P<rev>\d{1,4}|[A-Za-z])"
    r"(?=[_\-\s.]|$)",
    re.IGNORECASE,
)


def _normalize_subject(subject: str | None) -> str:
    """Strip Re:/Fwd:/[EXTERNAL] prefixes; collapse whitespace; lowercase."""
    if not subject:
        return ""
    s = subject
    for _ in range(5):  # repeated "Re: Re: Fwd: ..." prefixes
        new_s = _SUBJECT_PREFIX_RE.sub("", s.strip(), count=1)
        # strip [EXTERNAL]-style tags
        new_s = re.sub(r"^\[[^\]]+\]\s*", "", new_s).strip()
        if new_s == s.strip():
            break
        s = new_s
    return re.sub(r"\s+", " ", s).strip().lower()


# Small English stop-list — keeps title overlap from being diluted by
# function words ("this Amendment No 2 TO THE Vertex …").
_STOP_WORDS = frozenset({
    "a", "an", "the", "of", "to", "for", "and", "or", "in", "on", "by",
    "this", "that", "these", "those", "no", "is", "are", "be", "as",
    "with", "without", "between", "among",
})


def _normalize_contract_title(title: str | None) -> str:
    """Normalize for similarity matching — drop amendment markers + dates
    + version suffixes + stop-words."""
    if not title:
        return ""
    s = title.lower()
    s = re.sub(r"\bamendment\b[^\s]*", "", s)
    s = re.sub(r"\bside\s+letter\b[^\s]*", "", s)
    s = re.sub(r"\bv\d+\b", "", s)
    s = re.sub(r"\bversion\s+\d+\b", "", s)
    s = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", s)
    s = re.sub(r"\b\d{4}\b", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    tokens = [t for t in s.split() if t and t not in _STOP_WORDS]
    return " ".join(tokens)


def _jaccard_similarity(a: str, b: str) -> float:
    """Token-set Jaccard. Kept for use cases where the symmetric
    overlap matters (e.g., generic title diffing)."""
    if not a or not b:
        return 0.0
    aset = set(a.split())
    bset = set(b.split())
    if not aset or not bset:
        return 0.0
    return len(aset & bset) / len(aset | bset)


def _overlap_coefficient(a: str, b: str) -> float:
    """Symmetric overlap coefficient = |A ∩ B| / min(|A|, |B|).

    Better than Jaccard for the "amendment is a superset of the original
    title" case — "Vertex Supply Agreement" overlaps an amendment that
    contains all four tokens plus extras at 1.0 instead of 0.44.
    Design 3 §"Contract chains" + §"Government circulars" intends this
    flavor of similarity, not strict Jaccard."""
    if not a or not b:
        return 0.0
    aset = set(a.split())
    bset = set(b.split())
    if not aset or not bset:
        return 0.0
    return len(aset & bset) / min(len(aset), len(bset))


def _has_amendment_language(body: str | None) -> bool:
    if not body:
        return False
    low = body.lower()[:5000]  # only scan the first ~5K chars (opening clauses)
    return any(phrase in low for phrase in _AMENDMENT_PHRASES)


def _parse_revision_filename(name: str) -> tuple[str, str] | None:
    """Return (base_name, rev_token) if the filename matches a revision
    pattern; else None."""
    if not name:
        return None
    m = _REVISION_FILENAME_RE.search(name)
    if m is None:
        return None
    base = m.group("base").strip().lower()
    rev = m.group("rev").strip().upper()
    if not base or not rev:
        return None
    return base, rev


def _sender_recipient_overlap(
    sender_a: str | None,
    recipients_a: Iterable[str],
    sender_b: str | None,
    recipients_b: Iterable[str],
) -> float:
    """Fraction of (sender ∪ recipients) shared between two emails."""
    a = {(sender_a or "").lower()} | {r.lower() for r in recipients_a}
    b = {(sender_b or "").lower()} | {r.lower() for r in recipients_b}
    a.discard("")
    b.discard("")
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


def _detect_email_thread(input_: DetectionInput) -> ChainCandidate | None:
    """Email thread join. Per Design 3 §"Email threads":
       - In-Reply-To matches a known Message-ID → reply
       - Shared References ancestor → join the thread
       - Normalized subject + sender/recipient overlap ≥ 0.5 within 30d
         → join the thread (subject-similarity fallback)
       - role = reply / forward / original
    """
    if input_.mime_type != "message/rfc822":
        return None

    norm_subject = _normalize_subject(input_.email_subject)
    is_forward = bool(
        input_.email_subject
        and re.match(r"^(fwd|fw|tr)\s*:", input_.email_subject.strip(), re.IGNORECASE)
    )

    # Sibling matching: prefer In-Reply-To → References → subject+overlap.
    parent_doc_id: str | None = None
    confidence = 0.0
    matched_siblings: list[SiblingFile] = []

    for sib in input_.siblings:
        if sib.mime_type != "message/rfc822":
            continue
        # In-Reply-To exact match
        if input_.email_in_reply_to and sib.email_message_id == input_.email_in_reply_to:
            parent_doc_id = sib.file_id
            confidence = max(confidence, 0.98)
            matched_siblings.append(sib)
            continue
        # References ancestor overlap
        if input_.email_references and sib.email_message_id in input_.email_references:
            confidence = max(confidence, 0.92)
            matched_siblings.append(sib)
            continue
        # Reverse: this msg's id might appear in sib.email_references
        if input_.email_message_id and input_.email_message_id in sib.email_references:
            confidence = max(confidence, 0.92)
            matched_siblings.append(sib)
            continue
        # Subject + overlap fallback (within window — date check skipped
        # here to keep Wave A heuristic-cheap; sibling list is pre-filtered
        # by recency upstream).
        if norm_subject and _normalize_subject(sib.email_subject) == norm_subject:
            overlap = _sender_recipient_overlap(
                input_.email_sender, input_.email_recipients,
                sib.email_sender, sib.email_recipients,
            )
            if overlap >= 0.5:
                confidence = max(confidence, 0.75)
                matched_siblings.append(sib)
                continue

    if not matched_siblings:
        # First email in the thread — create a chain anchored on this
        # message's id. role=original, version_index=0.
        if input_.email_message_id:
            return ChainCandidate(
                chain_type="email_thread",
                chain_key=f"msgid:{input_.email_message_id}",
                title=norm_subject or input_.email_subject or input_.name,
                role="original",
                version_index=0,
                confidence=0.85,
            )
        return None  # no message-id → can't anchor a thread

    role = "forward" if is_forward else "reply"
    # chain_key: use the shared References root if known; else the first
    # matched sibling's message-id (so multiple replies converge).
    refs = input_.email_references
    chain_key_anchor = refs[0] if refs else matched_siblings[0].email_message_id
    return ChainCandidate(
        chain_type="email_thread",
        chain_key=f"msgid:{chain_key_anchor}" if chain_key_anchor else None,
        title=norm_subject,
        role=role,
        version_index=len(matched_siblings),  # rough chronological rank
        confidence=confidence,
        parent_doc_id=parent_doc_id,
        sibling_member_ids=tuple(s.file_id for s in matched_siblings),
    )


def _detect_contract_chain(input_: DetectionInput) -> ChainCandidate | None:
    """Contract amendment chain. Per Design 3 §"Contract chains":
       - Title similarity ≥ 0.7 (English-only Wave A; cross-lingual is
         Wave C per the design note)
       - Opening-clauses regex on amendment language
       - role = amendment / side_letter / original
    """
    if input_.inferred_doc_type and "contract" not in input_.inferred_doc_type.lower():
        return None

    norm_self = _normalize_contract_title(input_.title_text or input_.name)
    if not norm_self:
        return None

    has_amendment_lang = _has_amendment_language(input_.title_text)
    candidate_sibling: SiblingFile | None = None
    best_sim = 0.0

    for sib in input_.siblings:
        if sib.inferred_doc_type and "contract" not in (sib.inferred_doc_type or "").lower():
            continue
        if sib.file_id == input_.file_id:
            continue
        norm_sib = _normalize_contract_title(sib.title_text or sib.name)
        sim = _overlap_coefficient(norm_self, norm_sib)
        if sim > best_sim:
            best_sim = sim
            candidate_sibling = sib

    if candidate_sibling is None or best_sim < 0.7:
        return None

    # When the new doc has amendment language OR the filename hints "amendment",
    # treat as amendment; else as side_letter if it's not the original; else
    # original (this is the second file → still mark as original of a 2-file chain).
    name_low = (input_.name or "").lower()
    title_low = (input_.title_text or "").lower()
    is_side_letter = "side letter" in title_low or "side_letter" in name_low
    role = (
        "side_letter" if is_side_letter
        else ("amendment" if has_amendment_lang or "amendment" in name_low else "original")
    )

    return ChainCandidate(
        chain_type="contract_chain",
        chain_key=f"title:{norm_self}",
        title=norm_self,
        role=role,
        # Naive: incoming is appended; worker re-sorts by member added_at.
        version_index=1,
        confidence=min(1.0, best_sim),
        sibling_member_ids=(candidate_sibling.file_id,),
    )


def _detect_drawing_revision(input_: DetectionInput) -> ChainCandidate | None:
    """Drawing revision detection. Per Design 3 §"Drawing revisions":
       - Filename pattern (_RevA, _v2, _R03)
       - Same project_id (extracted at parse via L2)
       - role = revision
    """
    parsed = _parse_revision_filename(input_.name or "")
    if parsed is None:
        return None
    base, rev = parsed

    project_id = input_.project_id
    candidate_siblings: list[SiblingFile] = []
    for sib in input_.siblings:
        if sib.file_id == input_.file_id:
            continue
        sib_parsed = _parse_revision_filename(sib.name or "")
        if sib_parsed is None:
            continue
        sib_base, _ = sib_parsed
        if sib_base != base:
            continue
        # Optional project_id agreement strengthens confidence.
        if project_id and sib.project_id and sib.project_id != project_id:
            continue
        candidate_siblings.append(sib)

    # Even a singleton drawing creates a chain — future revisions join it.
    chain_key = f"drawing:{base}:{project_id or ''}"
    confidence = 0.95 if candidate_siblings else 0.80

    # Try to compute a numeric version_index from the rev token: R03 → 3,
    # V2 → 2, A → 1 (alpha mapping). Fall back to 0.
    def _rev_to_int(token: str) -> int:
        digits = re.findall(r"\d+", token)
        if digits:
            return int(digits[0])
        if token.isalpha() and len(token) == 1:
            return ord(token.upper()) - ord("A") + 1
        return 0

    version_index = _rev_to_int(rev)

    return ChainCandidate(
        chain_type="drawing_revisions",
        chain_key=chain_key,
        title=f"{base} (drawing)",
        role="revision",
        version_index=version_index,
        confidence=confidence,
        sibling_member_ids=tuple(s.file_id for s in candidate_siblings),
    )


def _detect_circular_corrigendum(input_: DetectionInput) -> ChainCandidate | None:
    """Circular + corrigendum. Per Design 3 §"Government circulars":
       - Title similarity ≥ 0.8
       - Corrigendum has explicit 'Corrigendum to ...' header
       - role = corrigendum (original keeps 'original')
    """
    title = input_.title_text or input_.name or ""
    if not title:
        return None
    low = title.lower()
    if not low.startswith("corrigendum to") and "corrigendum" not in low[:120]:
        return None

    # Strip "corrigendum to" prefix to compare with siblings.
    stripped = re.sub(r"^\s*corrigendum\s*to\s*", "", title, flags=re.IGNORECASE)
    norm_self = _normalize_contract_title(stripped)
    if not norm_self:
        return None

    best_sim = 0.0
    candidate: SiblingFile | None = None
    for sib in input_.siblings:
        if sib.file_id == input_.file_id:
            continue
        norm_sib = _normalize_contract_title(sib.title_text or sib.name)
        sim = _overlap_coefficient(norm_self, norm_sib)
        if sim > best_sim:
            best_sim = sim
            candidate = sib

    if candidate is None or best_sim < 0.8:
        return None

    return ChainCandidate(
        chain_type="circular_chain",
        chain_key=f"circular:{norm_self}",
        title=stripped.strip(),
        role="corrigendum",
        version_index=1,
        confidence=min(1.0, best_sim),
        sibling_member_ids=(candidate.file_id,),
    )


# ---------------------------------------------------------------------------
# Orchestration — first confident match wins
# ---------------------------------------------------------------------------


DETECTORS = (
    _detect_email_thread,
    _detect_contract_chain,
    _detect_drawing_revision,
    _detect_circular_corrigendum,
)


def detect_chain(input_: DetectionInput) -> ChainCandidate | None:
    """Run per-type detectors in priority order; return the first
    `ChainCandidate` with confidence ≥ 0.7. Returns None when no
    detector matches confidently."""
    for detector in DETECTORS:
        result = detector(input_)
        if result is not None and result.confidence >= 0.7:
            return result
    return None
