"""Pure-Python reference implementation of the WeightedPersonaConsensus logic.

GenLayer contracts are deployed as a single self-contained file and cannot
import project modules at GenVM runtime, so the aggregation algorithm lives in
two places:

1. ``genlayer/contracts_src/weighted_persona_consensus.py`` - executable on-chain.
2. this module - importable off-chain, fully unit-tested.

They MUST stay behaviorally identical. When editing one side, edit the other
and run ``pytest tests -q``. The tests in ``tests/test_core.py``
(WeightedPersonaConsensusLogicTests) are the executable specification of the
consensus semantics: weighting math, conservative tie-break, fail-closed
normalization, disagreement downgrade, and config validation.
"""

from __future__ import annotations

import json

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

MODERATION_PRESET = {
    "personas": [
        {
            "name": "SAFETY_GUARD",
            "weight": 1.5,
            "frame": "You are the SAFETY GUARD. Protect users from harmful content first.",
        },
        {
            "name": "CONTEXT_REVIEWER",
            "weight": 1.2,
            "frame": "You are the CONTEXT REVIEWER. Judge intent, satire, and context fairly.",
        },
    ],
    "labels": ["BAN", "FLAG", "ALLOW"],
    "disagreement_fallback": "FLAG",
    "disagreement_threshold": 0.5,
    "disagreement_confidence_penalty": 0.7,
    "max_history": 20,
}


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
        or any(not isinstance(label, str) or not label.strip() for label in labels)
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


def normalize_vote(config: dict, persona: dict, vote: dict) -> dict:
    """Validate one persona verdict. Raises ValueError (contract: gl.UserError).

    Mirrors ``_normalize_vote`` inside the contract source exactly - this is
    the fail-closed gate: any malformed or out-of-domain verdict aborts the
    whole evaluation instead of being silently coerced.
    """
    if not isinstance(vote, dict):
        raise ValueError("Vote must be a dict")
    labels_upper = [str(l).upper() for l in config["labels"]]
    label = str(vote.get("label", "")).upper()
    if label not in labels_upper:
        raise ValueError(f"Invalid label: {label}")
    confidence = float(vote.get("confidence", 0))
    if confidence < 0 or confidence > 1:
        raise ValueError("confidence must be between 0 and 1")
    return {
        "persona": persona["name"],
        "label": label,
        "confidence": confidence,
        "reasoning": str(vote.get("reasoning", "")),
        "weight": float(persona["weight"]),
    }


def aggregate_votes(config: dict, payload: dict, votes: list[dict]) -> dict:
    """Deterministic weighted aggregation of persona votes.

    Mirrors ``aggregate_votes`` inside the contract source exactly.
    """
    labels = [str(l) for l in config["labels"]]  # index 0 = most conservative
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
        final_confidence *= float(config.get("disagreement_confidence_penalty", 0.75))
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
