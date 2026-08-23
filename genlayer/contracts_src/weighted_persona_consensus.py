# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""WeightedPersonaConsensus - a reusable GenLayer consensus primitive.

A deploy-time-configurable multi-persona LLM consensus engine. The deployer
defines any set of analyst personas (name, weight, perspective frame), the
ordered outcome labels from most to least conservative, and the disagreement
policy. At runtime anyone can submit a JSON payload describing a subject; each
persona independently evaluates it through an Equivalence-Principle-secured
LLM call (leader produces the verdict, network validators check it against
the criteria), and this contract aggregates the verdicts deterministically.

Consensus design:
- Layer 1 (network): every persona runs via gl.eq_principle.prompt_non_comparative,
  so each individual verdict is agreed on by leader + validators.
- Layer 2 (contract): weighted voting across personas. Ties are broken toward
  the more conservative label by iterating labels in conservatism order and
  requiring a strictly greater score.
- Fail-closed: one malformed persona output reverts the entire call - no
  partial commit, history untouched.
- Disagreement override: if the winning score share drops below
  ``1 - disagreement_threshold`` of total weighted confidence, the result is
  downgraded to ``disagreement_fallback`` and confidence is penalized.

GenVM constraints honored:
- Calldata has no float: constructor config, evaluate payload, and all view
  outputs are JSON strings; floats travel inside JSON text.
- Storage uses GenVM types: DynArray, u256, @allow_storage dataclass.

The aggregation algorithm is mirrored in ``genlayer/consensus_logic.py``
(pure Python, unit-tested): ``parse_config``, ``normalize_vote`` (there named
without underscore, raising ``ValueError`` where this contract raises
``gl.UserError``), and ``aggregate_votes``. Keep the two in sync when editing
either side.

Example configs live in WEIGHTED_PERSONA_CONSENSUS.md next to this file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from genlayer import *

MAX_HISTORY = 10
TRADING_PRESET = {
    "personas": [
        {
            "name": "BULL_ANALYST",
            "weight": 1.0,
            "frame": (
                "You are the BULL ANALYST. Look for asymmetric upside and "
                "momentum continuation, but remain factual."
            ),
        },
        {
            "name": "BEAR_ANALYST",
            "weight": 1.35,
            "frame": (
                "You are the BEAR_ANALYST. Focus on downside, scam probability, "
                "manipulative order flow, and capital preservation."
            ),
        },
        {
            "name": "NEUTRAL_ANALYST",
            "weight": 1.15,
            "frame": (
                "You are the NEUTRAL ANALYST. Balance upside vs downside and "
                "favor patience when evidence conflicts."
            ),
        },
    ],
    "labels": ["SCAM", "SKIP", "WAIT", "BUY"],
    "disagreement_fallback": "WAIT",
    "disagreement_threshold": 0.45,
    "disagreement_confidence_penalty": 0.75,
    "max_history": MAX_HISTORY,
}


@allow_storage
@dataclass
class ConsensusRecord:
    subject: str
    final_label: str
    confidence: float
    disagreement: float


class WeightedPersonaConsensus(gl.Contract):
    """Multi-persona weighted-consensus evaluator, configurable at deploy."""

    last_result: str
    config_json: str
    label_counts_json: str
    label_history: DynArray[str]
    confidence_history: DynArray[float]
    recent_evaluations: DynArray[ConsensusRecord]
    evaluation_count: u256

    def __init__(self, config_json: str = "") -> None:
        config = parse_config(config_json or json.dumps(TRADING_PRESET))
        self.config_json = json.dumps(config)
        self.label_counts_json = json.dumps(
            {label: 0 for label in config["labels"]}
        )
        self.last_result = json.dumps(
            {
                "final_label": "",
                "confidence": 0.0,
                "votes": [],
                "reasoning": "No evaluation yet",
                "disagreement": 0.0,
            }
        )

    @gl.public.write
    def evaluate(self, payload_json: str) -> None:
        """Run all personas over a subject payload and store the aggregate.

        Payload must include ``subject`` (or ``token``/``symbol`` for trading
        compatibility). Any additional keys are rendered into every prompt so
        personas can reason over domain-specific context.
        """
        payload = json.loads(payload_json)
        subject = str(
            payload.get("subject")
            or payload.get("token")
            or payload.get("symbol")
            or ""
        ).upper()
        if not subject:
            raise gl.UserError("Payload must include subject")

        config = json.loads(self.config_json)
        votes: list[dict] = []
        for persona in config["personas"]:
            response = gl.eq_principle.prompt_non_comparative(
                lambda prompt=self._build_prompt(config, payload, persona): prompt,
                task=(
                    f"Return strict JSON with keys label, confidence, reasoning. "
                    f"label must be one of: {', '.join(config['labels'])}."
                ),
                criteria=self._build_criteria(config),
            )
            parsed = response if isinstance(response, dict) else json.loads(str(response))
            votes.append(self._normalize_vote(config, persona, parsed))

        aggregate = aggregate_votes(config, payload, votes)
        self.last_result = json.dumps(aggregate)
        self._store_history(subject, config, aggregate)

    @gl.public.view
    def get_last_result(self) -> str:
        return self.last_result

    @gl.public.view
    def get_config(self) -> str:
        return self.config_json

    @gl.public.view
    def get_recent_evaluations(self) -> str:
        records = [
            {
                "subject": record.subject,
                "final_label": record.final_label,
                "confidence": record.confidence,
                "disagreement": record.disagreement,
            }
            for record in self.recent_evaluations
        ]
        return json.dumps(records)

    @gl.public.view
    def get_metrics(self) -> str:
        return json.dumps(
            {
                "evaluation_count": int(self.evaluation_count),
                "label_counts": json.loads(self.label_counts_json),
            }
        )

    def _build_prompt(self, config: dict, payload: dict, persona: dict) -> str:
        allowed = ", ".join(config["labels"])
        lines = [
            persona["frame"],
            "Evaluate the subject and return strict JSON with keys "
            "label, confidence, reasoning.",
            f"Allowed labels: {allowed}.",
            "confidence must be a float between 0 and 1.",
        ]
        # Sorted keys keep prompts byte-identical across leader/validator runs.
        for key in sorted(payload.keys()):
            lines.append(f"{key}: {payload[key]}")
        return "\n".join(lines)

    def _build_criteria(self, config: dict) -> str:
        labels = ", ".join(config["labels"])
        return (
            f"label must be exactly one of: {labels}; confidence must be a float "
            "between 0 and 1; reasoning must explain the persona's perspective"
        )

    def _normalize_vote(self, config: dict, persona: dict, vote: dict) -> dict:
        if not isinstance(vote, dict):
            raise gl.UserError("Vote must be a dict")
        label = str(vote.get("label", "")).upper()
        if label not in [str(l).upper() for l in config["labels"]]:
            raise gl.UserError(f"Invalid label: {label}")
        confidence = float(vote.get("confidence", 0))
        if confidence < 0 or confidence > 1:
            raise gl.UserError("confidence must be between 0 and 1")
        return {
            "persona": persona["name"],
            "label": label,
            "confidence": confidence,
            "reasoning": str(vote.get("reasoning", "")),
            "weight": float(persona["weight"]),
        }

    def _store_history(self, subject: str, config: dict, aggregate: dict) -> None:
        label = str(aggregate.get("final_label", ""))
        confidence = float(aggregate.get("confidence", 0))
        disagreement = float(aggregate.get("disagreement", 0))
        max_history = int(config.get("max_history", MAX_HISTORY))
        self.label_history.append(label)
        self.confidence_history.append(confidence)
        self.recent_evaluations.append(
            ConsensusRecord(
                subject=subject,
                final_label=label,
                confidence=confidence,
                disagreement=disagreement,
            )
        )
        self.label_history = self.label_history[-max_history:]
        self.confidence_history = self.confidence_history[-max_history:]
        self.recent_evaluations = self.recent_evaluations[-max_history:]

        counts = json.loads(self.label_counts_json)
        if label in counts:
            counts[label] += 1
        else:
            counts[label] = 1
        self.label_counts_json = json.dumps(counts)
        self.evaluation_count = u256(int(self.evaluation_count) + 1)


def parse_config(config_json: str) -> dict:
    """Validate a deploy-time config. Raises ValueError on any violation."""
    try:
        config = json.loads(config_json)
    except Exception as exc:
        raise ValueError(f"config_json is not valid JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")

    personas = config.get("personas")
    if not isinstance(personas, list) or len(personas) < 2:
        raise ValueError("config needs at least 2 personas")
    names_seen: list[str] = []
    for persona in personas:
        if not isinstance(persona, dict):
            raise ValueError("each persona must be an object")
        name = str(persona.get("name", "")).strip()
        weight = float(persona.get("weight", 0))
        frame = str(persona.get("frame", "")).strip()
        if not name or not frame:
            raise ValueError("persona name and frame are required")
        if name in names_seen:
            raise ValueError(f"duplicate persona name: {name}")
        if weight <= 0 or weight > 10:
            raise ValueError("persona weight must be in (0, 10]")
        names_seen.append(name)

    labels = config.get("labels")
    if (
        not isinstance(labels, list)
        or len(labels) < 2
        or any(not isinstance(l, str) or not l.strip() for l in labels)
    ):
        raise ValueError("labels must be a list of at least 2 non-empty strings")
    if len(set(labels)) != len(labels):
        raise ValueError("labels must be unique")

    fallback = str(config.get("disagreement_fallback", ""))
    if fallback not in labels:
        raise ValueError("disagreement_fallback must be one of the labels")

    threshold = float(config.get("disagreement_threshold", 0.45))
    if threshold < 0 or threshold > 1:
        raise ValueError("disagreement_threshold must be between 0 and 1")

    penalty = float(config.get("disagreement_confidence_penalty", 0.75))
    if penalty < 0 or penalty > 1:
        raise ValueError("disagreement_confidence_penalty must be between 0 and 1")

    max_history = int(config.get("max_history", MAX_HISTORY))
    if max_history < 1 or max_history > 100:
        raise ValueError("max_history must be between 1 and 100")

    normalized = dict(config)
    normalized["disagreement_threshold"] = threshold
    normalized["disagreement_confidence_penalty"] = penalty
    normalized["max_history"] = max_history
    return normalized


def aggregate_votes(config: dict, payload: dict, votes: list[dict]) -> dict:
    """Deterministic weighted aggregation. Mirrored inside the contract."""
    labels = list(config["labels"])  # index 0 = most conservative
    weighted_scores = {label: 0.0 for label in labels}
    weighted_confidence_sum = 0.0
    total_weight = 0.0
    for vote in votes:
        vote_weight = float(vote["weight"])
        vote_score = vote_weight * float(vote["confidence"])
        weighted_scores[vote["label"]] += vote_score
        weighted_confidence_sum += vote_score
        total_weight += vote_weight

    # Iterating in conservatism order means equal scores keep the earlier
    # (more conservative) label - deterministic tie-break without extra state.
    winning_label = labels[0]
    winning_score = -1.0
    for label in labels:
        if weighted_scores[label] > winning_score:
            winning_label = label
            winning_score = weighted_scores[label]

    disagreement = 1 - (winning_score / max(weighted_confidence_sum, 1e-9))
    threshold = float(config.get("disagreement_threshold", 0.45))
    downgraded = disagreement >= threshold
    if downgraded:
        winning_label = str(config["disagreement_fallback"])

    ai_confidence = weighted_confidence_sum / max(total_weight, 1e-9)
    final_confidence = ai_confidence
    # Optional domain hooks: blend with caller signal strength when present,
    # penalize declared risk flags when present.
    if "signal_strength" in payload:
        signal_strength = float(payload.get("signal_strength", 0) or 0)
        final_confidence = (ai_confidence * 0.6) + (signal_strength * 0.4)
    risk_flags = payload.get("risk_flags") or []
    if risk_flags:
        risk_penalty = min(len(risk_flags) * 0.08, 0.4)
        final_confidence -= risk_penalty
    if downgraded:
        final_confidence *= float(
            config.get("disagreement_confidence_penalty", 0.75)
        )
    final_confidence = max(0.0, min(1.0, final_confidence))

    return {
        "final_label": winning_label,
        "confidence": final_confidence,
        "votes": votes,
        "reasoning": (
            f"Weighted decision {winning_label} with disagreement {disagreement:.4f}"
        ),
        "disagreement": disagreement,
    }
