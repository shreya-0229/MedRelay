"""IntakeAgent — validates and normalizes the raw caller report.

Two paths:
- Free text (``report_text`` / ``voice_transcript`` present): deterministic
  keyword/regex extraction over the combined text. Nothing is invented —
  anything unresolvable lands in ``missing_information``.
- Structured (no free text): validates and normalizes the posted fields.

The only LLM use is classifying an *unknown* incident_type from free text;
the reply is validated against the known type keys and ignored otherwise.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.agents.agent_registry import register
from app.agents.base_agent import BaseAgent
from app.llm import LLMProvider
from app.schemas import AgentOutput, IncidentReport, IncidentState

_VALID_BREATHING = {"normal", "labored", "absent", "agonal"}
_VALID_BLEEDING = {"none", "minor", "severe"}
_VALID_LANGUAGES = {"en", "hi", "mr"}

# Deterministic free-text keyword maps. Checked top-down; the first type
# with any keyword hit wins.
_INCIDENT_KEYWORDS: dict[str, list[str]] = {
    "cardiac_arrest": ["cardiac arrest", "heart attack", "myocardial",
                       "no pulse", "heart stopped"],
    "road_accident": ["road accident", "car crash", "car accident",
                      "bike accident", "collision", "hit by", "accident"],
    "stroke": ["stroke", "paralysis", "face droop", "slurred speech"],
    "trauma_fall": ["fell", "fall from", "fracture", "broken bone",
                    "head injury"],
    "obstetric": ["labour", "labor", "pregnant", "pregnancy", "delivery",
                  "childbirth"],
    "chest_pain": ["chest pain", "chest discomfort", "angina"],
}

_SYMPTOM_KEYWORDS = ["bleeding", "unconscious", "not breathing", "breathless",
                     "chest pain", "fracture", "burn", "vomit", "seizure",
                     "dizzy"]

_UNCONSCIOUS_MARKERS = ["unconscious", "not responding", "unresponsive",
                        "fainted", "passed out"]
_CONSCIOUS_MARKERS = ["conscious", "awake", "responsive", "talking"]

_BREATHING_MARKERS: list[tuple[str, list[str]]] = [
    ("absent", ["not breathing", "stopped breathing"]),
    ("agonal", ["gasping", "agonal"]),
    ("labored", ["difficulty breathing", "breathless", "short of breath",
                 "labored"]),
    ("normal", ["breathing normally", "breathing fine"]),
]

_BLEEDING_MARKERS: list[tuple[str, list[str]]] = [
    ("severe", ["heavy bleeding", "severe bleeding", "bleeding heavily",
                "blood everywhere"]),
    ("none", ["no bleeding", "not bleeding"]),
    ("minor", ["bleeding", "blood", "cut"]),
]

_WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_PEOPLE_WORDS = r"people|persons|patients|victims|injured"
_DIGIT_COUNT_RE = re.compile(r"(\d+)\s*(?:" + _PEOPLE_WORDS + r")",
                             re.IGNORECASE)
_WORD_COUNT_RE = re.compile(
    r"\b(" + "|".join(_WORD_NUMBERS) + r")\s+(?:" + _PEOPLE_WORDS + r")",
    re.IGNORECASE)
_AMBIGUOUS_COUNT_RE = re.compile(r"\b(multiple|several)\b", re.IGNORECASE)
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def _extract_incident_type(text: str) -> str:
    lowered = text.lower()
    for itype, keywords in _INCIDENT_KEYWORDS.items():
        if any(k in lowered for k in keywords):
            return itype
    return "unknown"


def _extract_patient_count(text: str) -> tuple[int, bool]:
    """Return (count, ambiguous). Ambiguous means a vague quantifier was
    used — the caller must keep 1 and flag it, never invent a number."""
    m = _DIGIT_COUNT_RE.search(text)
    if m:
        return max(1, int(m.group(1))), False
    m = _WORD_COUNT_RE.search(text)
    if m:
        return _WORD_NUMBERS[m.group(1).lower()], False
    if _AMBIGUOUS_COUNT_RE.search(text):
        return 1, True
    return 1, False


def _extract_symptoms(text: str) -> list[str]:
    lowered = text.lower()
    found: list[str] = []
    for kw in _SYMPTOM_KEYWORDS:
        if kw in lowered:
            if kw == "bleeding" and "no bleeding" in lowered:
                continue  # negated — not a symptom
            found.append(kw)
    return found


def _extract_consciousness(text: str) -> str | None:
    lowered = text.lower()
    if any(m in lowered for m in _UNCONSCIOUS_MARKERS):
        return "unconscious"
    if any(m in lowered for m in _CONSCIOUS_MARKERS):
        return "conscious"
    return None


def _extract_breathing(text: str) -> str | None:
    lowered = text.lower()
    for value, markers in _BREATHING_MARKERS:
        if any(m in lowered for m in markers):
            return value
    return None


def _extract_bleeding(text: str) -> str | None:
    lowered = text.lower()
    for value, markers in _BLEEDING_MARKERS:
        if any(m in lowered for m in markers):
            return value
    return None


@register
class IntakeAgent(BaseAgent):
    """First agent: turns the raw report into normalized IncidentState fields."""

    name = "IntakeAgent"
    version = "2.0"

    def __init__(self, report: IncidentReport, db: Session,
                 llm: LLMProvider | None = None) -> None:
        super().__init__(db, llm)
        self.report = report

    # -- LLM assist (unknown incident_type only) ---------------------------
    def _llm_classify(self, text: str) -> str | None:
        """Ask a real LLM to classify free text into a known incident type.

        Returns the validated type key, or None (keep 'unknown').
        """
        prompt = (
            "You are an emergency intake classifier. Read this caller report "
            "and reply with exactly one incident-type key, nothing else.\n"
            f"Report: \"{text[:500]}\"\n"
            "Valid keys: cardiac_arrest, road_accident, stroke, trauma_fall, "
            "obstetric, chest_pain. If none fits, reply: unknown"
        )
        resp = self.llm_assist(prompt)
        if not resp:
            return None
        key = resp.strip().lower().replace(" ", "_").replace("-", "_")
        return key if key in _INCIDENT_KEYWORDS else None

    # -- language -----------------------------------------------------------
    def _resolve_language(self, text: str) -> str:
        lang = (self.report.language or "").strip().lower()
        if lang in _VALID_LANGUAGES:
            return lang
        if text and _DEVANAGARI_RE.search(text):
            return "hi"
        return "en"

    # -- coordinates ------------------------------------------------------
    # Pune city-centre fallback when the caller supplies no coordinates.
    _FALLBACK_LAT = 18.5204
    _FALLBACK_LON = 73.8567

    def _resolve_coords(self) -> tuple[float, float, bool]:
        """Return (lat, lon, precise): precise=False when falling back."""
        r = self.report
        if r.lat is not None and r.lon is not None:
            return r.lat, r.lon, True
        return self._FALLBACK_LAT, self._FALLBACK_LON, False

    def _apply_coords(self, state: IncidentState,
                      missing: list[str], warnings: list[str]) -> None:
        lat, lon, precise = self._resolve_coords()
        if not precise:
            missing.append("precise_location")
            warnings.append(
                "No coordinates supplied — using Pune city centre as a "
                "fallback; confirm the exact location with the caller.")
        state.location.lat = lat
        state.location.lon = lon

    # -- main ---------------------------------------------------------------
    async def run(self, state: IncidentState) -> AgentOutput:
        r = self.report

        # Hard validation failure: coordinates out of range (when supplied).
        # Everything else degrades gracefully into missing_information.
        issues: list[str] = []
        if r.lat is not None and not (-90.0 <= r.lat <= 90.0):
            issues.append(f"latitude {r.lat} out of range")
        if r.lon is not None and not (-180.0 <= r.lon <= 180.0):
            issues.append(f"longitude {r.lon} out of range")
        if r.patient_count < 1:
            issues.append("patient_count must be >= 1")
        if issues:
            return self.envelope(
                state, status="failed", confidence=0.0,
                decision={"issues": issues},
                reasoning_summary="Intake rejected: " + "; ".join(issues),
                warnings=issues,
                action="Intake validation failed",
            )

        free_text = " ".join(
            t for t in (r.report_text, r.voice_transcript) if t).strip()
        if free_text:
            return await self._run_free_text(state, free_text)
        return await self._run_structured(state)

    async def _run_free_text(self, state: IncidentState,
                             text: str) -> AgentOutput:
        warnings: list[str] = []
        missing: list[str] = []

        incident_type = _extract_incident_type(text)
        llm_used = False
        if incident_type == "unknown":
            llm_type = self._llm_classify(text)
            if llm_type:
                incident_type = llm_type
                llm_used = True
        if incident_type == "unknown":
            missing.append("incident_type")

        patient_count, ambiguous = _extract_patient_count(text)
        if ambiguous:
            missing.append("patient_count")
            warnings.append(
                "Patient count described vaguely ('multiple'/'several') — "
                "kept as 1; confirm the exact count with the caller.")

        symptoms = _extract_symptoms(text)
        if not symptoms:
            missing.append("symptoms")

        consciousness = _extract_consciousness(text)
        if consciousness is None:
            missing.append("consciousness")

        breathing = _extract_breathing(text)
        if breathing is None:
            missing.append("breathing_status")

        bleeding = _extract_bleeding(text)
        if bleeding is None:
            missing.append("bleeding_status")

        language = self._resolve_language(text)

        address = (r.address or "").strip() if (r := self.report) else ""
        if not address:
            missing.append("exact_address")

        # --- update shared state (safe defaults keep the state contract) ---
        state.incident_type = incident_type
        state.patient_count = max(1, patient_count)
        state.symptoms = symptoms
        state.breathing_status = breathing or "normal"
        state.bleeding_status = bleeding or "none"
        self._apply_coords(state, missing, warnings)
        state.location.address = address
        state.family_contact = (self.report.family_contact or "").strip()

        decision = {
            "incident_type": incident_type,
            "patient_count": patient_count,
            "symptoms": symptoms,
            "consciousness": consciousness,
            "breathing_status": breathing,
            "bleeding_status": bleeding,
            "language": language,
            "location": {"lat": self.report.lat, "lon": self.report.lon,
                         "address": address},
            "family_contact": state.family_contact,
            "image": {"received": bool(self.report.image_metadata),
                      "note": "stored, not analyzed in prototype"},
            "missing_information": missing,
            "llm_used": llm_used,
            "source": "free_text",
        }
        summary = (f"Parsed free-text report as {incident_type} "
                   f"({patient_count} patient(s)); {len(missing)} field(s) "
                   f"need follow-up.")
        return self.envelope(
            state, status="success", confidence=0.85, decision=decision,
            reasoning_summary=summary, warnings=warnings,
            action="Free-text report parsed")

    async def _run_structured(self, state: IncidentState) -> AgentOutput:
        r = self.report
        missing: list[str] = []
        warnings: list[str] = []

        itype = (r.incident_type or "unknown").strip().lower()
        itype = itype.replace(" ", "_").replace("-", "_") or "unknown"

        breathing_raw = (r.breathing_status or "").strip().lower()
        breathing = breathing_raw if breathing_raw in _VALID_BREATHING else None
        if breathing is None:
            missing.append("breathing_status")
            warnings.append(
                f"Unrecognized breathing_status {r.breathing_status!r} — "
                "defaulted to 'normal'.")

        bleeding_raw = (r.bleeding_status or "").strip().lower()
        bleeding = bleeding_raw if bleeding_raw in _VALID_BLEEDING else None
        if bleeding is None:
            missing.append("bleeding_status")
            warnings.append(
                f"Unrecognized bleeding_status {r.bleeding_status!r} — "
                "defaulted to 'none'.")

        address = (r.address or "").strip()
        if not address:
            missing.append("exact_address")

        state.incident_type = itype
        self._apply_coords(state, missing, warnings)
        state.location.address = address
        state.patient_count = max(1, r.patient_count)
        state.symptoms = [s.strip() for s in (r.symptoms or [])
                          if s and s.strip()]
        state.breathing_status = breathing or "normal"
        state.bleeding_status = bleeding or "none"
        state.family_contact = (r.family_contact or "").strip()

        decision = {
            "incident_type": itype,
            "patient_count": state.patient_count,
            "symptoms": state.symptoms,
            "consciousness": (r.consciousness or "").strip().lower() or None,
            "breathing_status": breathing,
            "bleeding_status": bleeding,
            "language": self._resolve_language(""),
            "location": {"lat": state.location.lat, "lon": state.location.lon,
                         "address": address},
            "family_contact": state.family_contact,
            "image": {"received": bool(r.image_metadata),
                      "note": "stored, not analyzed in prototype"},
            "missing_information": missing,
            "llm_used": False,
            "source": "structured",
        }
        summary = (f"Validated structured report: {itype}, "
                   f"{state.patient_count} patient(s) at "
                   f"({state.location.lat}, {state.location.lon}).")
        return self.envelope(
            state, status="success", confidence=0.98, decision=decision,
            reasoning_summary=summary, warnings=warnings,
            action="Incident parsed")
