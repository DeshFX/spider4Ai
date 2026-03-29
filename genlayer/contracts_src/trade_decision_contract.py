# { "Depends": "py-genlayer:latest" }
from genlayer import *

VALID_DECISIONS = ("BUY", "WAIT", "SKIP", "SCAM")
DISAGREEMENT_THRESHOLD = 0.45
MAX_HISTORY = 10


class SpiderTradeDecision(gl.Contract):

    symbol: str
    final_decision: str
    confidence: float
    disagreement: float
    reasoning: str
    bull_vote: str
    bear_vote: str
    neutral_vote: str
    evaluation_count: u256
    history: DynArray[str]

    def __init__(self) -> None:
        self.symbol = "NONE"
        self.final_decision = "WAIT"
        self.confidence = 0.0
        self.disagreement = 0.0
        self.reasoning = "No decision yet"
        self.bull_vote = "WAIT"
        self.bear_vote = "WAIT"
        self.neutral_vote = "WAIT"
        self.evaluation_count = u256(0)
        self.history = DynArray[str]()

    @gl.public.write
    def evaluate_trade(
        self,
        symbol: str,
        summary: str,
        signal_strength: int,
        risk_flags_count: int,
        market_context: str,
        recent_trend: str,
    ) -> None:
        normalized_symbol = str(symbol or "").strip().upper()
        if not normalized_symbol:
            raise gl.vm.UserError("symbol is required")
        if not (0 <= signal_strength <= 100):
            raise gl.vm.UserError("signal_strength must be between 0 and 100")
        if risk_flags_count < 0:
            raise gl.vm.UserError("risk_flags_count cannot be negative")

        scores = {"BUY": 0.0, "WAIT": 0.0, "SKIP": 0.0, "SCAM": 0.0}
        votes = {"BULL": "SKIP", "BEAR": "SKIP", "NEUTRAL": "SKIP"}

        for role in ("BULL", "BEAR", "NEUTRAL"):
            if role == "BULL":
                role_text = "You are a bullish crypto analyst. Look for upside momentum and buying opportunities."
            elif role == "BEAR":
                role_text = "You are a bearish crypto analyst. Focus on risks, scam detection, and capital preservation."
            else:
                role_text = "You are a neutral crypto analyst. Balance risk and reward objectively."

            prompt = role_text
            prompt += "\nEvaluate this token and return ONLY a JSON object."
            prompt += "\nFormat: {\"decision\": \"BUY or WAIT or SKIP or SCAM\", \"confidence\": 0.0}"
            prompt += "\nToken: " + normalized_symbol
            prompt += "\nSummary: " + summary
            prompt += "\nSignal Strength: " + str(signal_strength)
            prompt += "\nRisk Flags: " + str(risk_flags_count)
            prompt += "\nMarket Context: " + market_context
            prompt += "\nRecent Trend: " + recent_trend

            def leader_fn(p=prompt):
                return gl.nondet.exec_prompt(p, response_format="json")

            def validator_fn(leader_result):
                if not isinstance(leader_result, gl.vm.Return):
                    return False
                data = leader_result.calldata
                if not isinstance(data, dict):
                    return False
                decision = str(data.get("decision", "")).strip().upper()
                conf = data.get("confidence", None)
                return (
                    decision in VALID_DECISIONS
                    and conf is not None
                    and 0.0 <= float(conf) <= 1.0
                )

            result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
            decision = str(result.get("decision", "SKIP")).strip().upper()
            if decision not in VALID_DECISIONS:
                decision = "SKIP"
            conf = max(0.0, min(1.0, float(result.get("confidence", 0.0))))
            votes[role] = decision
            scores[decision] = scores[decision] + conf

        final_decision = max(scores, key=lambda k: scores[k])
        best_score = scores[final_decision]
        total_score = scores["BUY"] + scores["WAIT"] + scores["SKIP"] + scores["SCAM"]
        safe_total = total_score if total_score > 0.0 else 1.0
        disagreement = 1.0 - (best_score / safe_total)

        if disagreement >= DISAGREEMENT_THRESHOLD:
            final_decision = "WAIT"

        confidence_val = total_score / 3.0
        confidence_val = confidence_val - (risk_flags_count * 0.1)
        normalized_signal = max(0.0, min(1.0, signal_strength / 100.0))
        confidence_val = (confidence_val * 0.6) + (normalized_signal * 0.4)
        if disagreement >= DISAGREEMENT_THRESHOLD:
            confidence_val = confidence_val * 0.75
        confidence_val = max(0.0, min(1.0, confidence_val))

        reasoning = (
            "BULL=" + votes["BULL"]
            + " | BEAR=" + votes["BEAR"]
            + " | NEUTRAL=" + votes["NEUTRAL"]
            + " | disagreement=" + str(round(disagreement, 3))
            + " | risk_flags=" + str(risk_flags_count)
        )

        entry = (
            "{\"symbol\": \"" + normalized_symbol
            + "\", \"decision\": \"" + final_decision
            + "\", \"bull\": \"" + votes["BULL"]
            + "\", \"bear\": \"" + votes["BEAR"]
            + "\", \"neutral\": \"" + votes["NEUTRAL"]
            + "\", \"confidence\": " + str(round(confidence_val, 3))
            + ", \"disagreement\": " + str(round(disagreement, 3))
            + "}"
        )

        self.symbol = normalized_symbol
        self.final_decision = final_decision
        self.confidence = confidence_val
        self.disagreement = disagreement
        self.reasoning = reasoning
        self.bull_vote = votes["BULL"]
        self.bear_vote = votes["BEAR"]
        self.neutral_vote = votes["NEUTRAL"]
        self.evaluation_count = self.evaluation_count + u256(1)

        if len(self.history) >= MAX_HISTORY:
            self.history.pop(0)
        self.history.append(entry)

    @gl.public.view
    def get_last_decision(self) -> str:
        return self.final_decision

    @gl.public.view
    def get_symbol(self) -> str:
        return self.symbol

    @gl.public.view
    def get_reasoning(self) -> str:
        return self.reasoning

    @gl.public.view
    def get_votes(self) -> str:
        return "BULL=" + self.bull_vote + " | BEAR=" + self.bear_vote + " | NEUTRAL=" + self.neutral_vote

    @gl.public.view
    def get_confidence(self) -> str:
        return str(round(self.confidence, 3))

    @gl.public.view
    def get_disagreement(self) -> str:
        return str(round(self.disagreement, 3))

    @gl.public.view
    def get_evaluation_count(self) -> u256:
        return self.evaluation_count

    @gl.public.view
    def get_history(self) -> DynArray[str]:
        return self.history
