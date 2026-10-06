"""CommunicationAgent — drafts and persists every outbound message.

Deterministic short factual templates in EN/HI/MR for: family updates,
bystander instructions, the hospital ER pre-alert (EN only), and the
escalation notice (only when the incident was escalated to a human).

Every message is written to the ``communications`` table via
``persist_message`` AND appended to ``state.communications``. A real LLM
may draft the three family messages; non-empty replies are used, otherwise
the templates are. Comms never breaks the pipeline: always status=success.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app import models
from app.agents.agent_registry import register
from app.agents.base_agent import BaseAgent, utcnow
from app.schemas import AgentOutput, Communication, IncidentState


def persist_message(db: Session, incident_id: str, channel: str,
                    language: str, text: str) -> models.CommunicationMessage:
    """Backend tool: append one outbound message to the durable table."""
    msg = models.CommunicationMessage(
        incident_id=incident_id, channel=channel, language=language,
        text=text, ts=utcnow())
    db.add(msg)
    db.commit()
    return msg


_FAMILY_TEMPLATES = {
    "en": ("MedRelay update: Ambulance {amb} has been assigned for the "
           "{etype} emergency. Estimated arrival: {eta} minutes. A bed is "
           "reserved at {hosp}."),
    "hi": ("MedRelay सूचना: {etype} आपातकाल के लिए एम्बुलेंस {amb} भेज दी "
           "गई है। अनुमानित आगमन: {eta} मिनट। {hosp} में बेड आरक्षित है।"),
    "mr": ("MedRelay अपडेट: {etype} आपत्कालीन परिस्थितीसाठी रुग्णवाहिका "
           "{amb} पाठवली आहे. अंदाजे आगमन: {eta} मिनिटे. {hosp} येथे बेड "
           "राखीव आहे."),
}

_BYSTANDER_TEMPLATES = {
    "en": ("You are with the patient. Keep them still and do not move them "
           "unless in danger. Ambulance {amb} is on the way, ETA {eta} "
           "minutes. Keep their airway clear."),
    "hi": ("आप मरीज़ के साथ हैं। खतरा न हो तो उन्हें न हिलाएं। एम्बुलेंस "
           "{amb} रास्ते में है, ETA {eta} मिनट। उनकी सांस की नली खुली रखें।"),
    "mr": ("तुम्ही रुग्णासोबत आहात. धोका नसल्यास त्यांना हलवू नका. "
           "रुग्णवाहिका {amb} येत आहे, अंदाजे {eta} मिनिटे. त्यांचा "
           "श्वासमार्ग मोकळा ठेवा."),
}

_ER_TEMPLATE = ("ER pre-alert: {etype} inbound to {hosp} via {amb}, "
                "severity {sev}, ETA {eta} min. {pcount} patient(s).")

_ESCALATION_TEMPLATES = {
    "en": ("MedRelay update: your case has been escalated to a human "
           "dispatcher who is now coordinating the emergency response "
           "personally. Please stay available on this number."),
    "hi": ("MedRelay सूचना: आपका मामला मानव डिस्पैचर को सौंप दिया गया है, "
           "जो अब स्वयं आपातकालीन सहायता का समन्वय कर रहे हैं। कृपया इस "
           "नंबर पर उपलब्ध रहें।"),
    "mr": ("MedRelay अपडेट: तुमचे प्रकरण मानवी डिस्पॅचरकडे सोपवले आहे, जे "
           "आता स्वतः आपत्कालीन प्रतिसादाचे समन्वय करत आहेत. कृपया या "
           "क्रमांकावर उपलब्ध रहा."),
}

_LANG_NAMES = {"en": "English", "hi": "Hindi", "mr": "Marathi"}


@register
class CommunicationAgent(BaseAgent):
    """Notifier: family SMS, bystander instructions, ER pre-alert, and an
    escalation notice when a human dispatcher took over."""

    name = "CommunicationAgent"
    version = "2.0"

    def _family_message(self, lang: str, amb: str, etype: str,
                        eta: str, hosp: str) -> tuple[str, bool]:
        """Draft the family update; returns (text, llm_used).

        A real LLM may draft it; any empty/failed reply falls back to the
        deterministic template.
        """
        draft = self.llm_assist(
            f"Draft a short SMS family update in {_LANG_NAMES[lang]} for an "
            f"emergency: ambulance {amb} assigned for a {etype} emergency, "
            f"ETA {eta} minutes, bed reserved at {hosp}. Factual, under 200 "
            "characters, no medical advice.")
        if draft:
            return draft, True
        return _FAMILY_TEMPLATES[lang].format(
            amb=amb, etype=etype, eta=eta, hosp=hosp), False

    async def run(self, state: IncidentState) -> AgentOutput:
        etype = (state.incident_type or "unknown").replace("_", " ")
        amb = (state.selected_ambulance.id if state.selected_ambulance
               else "nearest available")
        hosp = (state.selected_hospital.name if state.selected_hospital
                else "nearest hospital")
        sev = state.severity if state.severity is not None else "?"
        eta = (str(state.selected_ambulance.eta_min)
               if state.selected_ambulance else "pending")
        pcount = state.patient_count

        # (state channel, db channel, language, text)
        outbox: list[tuple[str, str, str, str]] = []
        llm_used = False

        for lang in ("en", "hi", "mr"):
            text, used = self._family_message(lang, amb, etype, eta, hosp)
            llm_used = llm_used or used
            outbox.append((f"sms_family_{lang}", "family", lang, text))

        for lang in ("en", "hi", "mr"):
            text = _BYSTANDER_TEMPLATES[lang].format(
                amb=amb, eta=eta)
            outbox.append((f"bystander_{lang}", "bystander", lang, text))

        outbox.append(("er_alert", "hospital", "en",
                       _ER_TEMPLATE.format(etype=etype, hosp=hosp, amb=amb,
                                           sev=sev, eta=eta, pcount=pcount)))

        if state.escalation_status == "escalated":
            for lang in ("en", "hi", "mr"):
                outbox.append((f"escalation_notice_{lang}", "escalation",
                               lang, _ESCALATION_TEMPLATES[lang]))

        channels: list[str] = []
        for state_channel, db_channel, lang, text in outbox:
            persist_message(self.db, state.incident_id, db_channel, lang, text)
            state.communications.append(
                Communication(channel=state_channel, text=text, ts=utcnow()))
            channels.append(state_channel)

        decision = {
            "message_count": len(outbox),
            "channels": channels,
            "llm_used": llm_used,
        }
        return self.envelope(
            state, status="success", confidence=0.97, decision=decision,
            reasoning_summary=(f"Sent {len(outbox)} notification messages "
                               f"across {len(channels)} channels."),
            action="Notifications sent")
