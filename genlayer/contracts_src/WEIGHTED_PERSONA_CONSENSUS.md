# WeightedPersonaConsensus

**Standalone GenLayer intelligent contract primitive** — a deploy-time-configurable, multi-persona LLM consensus engine with on-chain deterministic aggregation.

- Source: [`weighted_persona_consensus.py`](./weighted_persona_consensus.py)
- Tested reference implementation: [`../consensus_logic.py`](../consensus_logic.py)
- Target network: GenLayer Testnet **Bradbury** (chain id 4221)
- Origin: extracted and generalized from the [Spider4AI](https://github.com/DeshFX/spider4Ai) trade-decision contract (deployed live: `0x54ba38e9D06cE4f99a3EA94A70101014C9ae261d`)
- Status: **algorithm verified off-chain (87 unit tests); parent trade contract verified live on Bradbury; this primitive itself pending first deployment**

## Purpose

"Have several AI personas vote on a decision, weight their opinions unequally, break ties conservatively, and refuse to decide when they disagree too much" — as a reusable primitive.

One deployed instance serves **any domain**, because everything domain-specific is configuration:

| Domain | Personas | Labels (conservative → permissive) |
|---|---|---|
| Trading risk | BULL / BEAR / NEUTRAL analysts | `SCAM > SKIP > WAIT > BUY` |
| Content moderation | SAFETY_GUARD / CONTEXT_REVIEWER | `BAN > FLAG > ALLOW` |
| Insurance claims | FRAUD_AUDIT / CLAIM_PROCESSOR | `REJECT > INVESTIGATE > APPROVE` |
| Oracle adjudication | SKEPTIC / VERIFIER | `INVALID > VALID` |

## How consensus is used (two layers)

### Layer 1 — network-level: Equivalence Principle per persona

Each persona runs an independent `gl.eq_principle.prompt_non_comparative` call. The leader model produces the verdict; network validators re-check it against explicit criteria (`label ∈ labels`, `confidence ∈ [0,1]`, reasoning present). A persona's verdict is therefore *agreed*, not just generated. Prompts embed payload keys in sorted order so leader/validator prompts are byte-identical — reproducibility is part of the design.

### Layer 2 — contract-level: weighted deterministic aggregation

```
score(label) = Σ weight(persona) × confidence(persona)   for votes on that label
disagreement = 1 − score(winner) / Σ score(all votes)
```

1. **Winner** = highest score; ties go to the **more conservative label** (labels are stored in conservatism order; iteration requires a strictly greater score to displace).
2. **Disagreement override**: if `disagreement ≥ threshold`, the result is downgraded to `disagreement_fallback` and confidence multiplied by `disagreement_confidence_penalty`.
3. **Fail-closed**: any malformed persona output raises → the whole transaction reverts. No partial commit, history untouched.
4. Optional caller-provided hooks: `signal_strength` blends 60/40 into final confidence; each `risk_flags` entry subtracts up to a capped penalty.

Rationale for asymmetric weights (trading preset): downside evidence gets 1.35× vs bullish 1.0× because in asymmetric-risk domains false negatives (missing a scam) cost more than false positives (skipping an opportunity). Deployers choose their own weights per domain.

## State design

| State | Type | Purpose |
|---|---|---|
| `config_json` | `str` | Immutable-after-deploy config (personas, weights, labels, thresholds) |
| `last_result` | `str` | Latest aggregate (full JSON incl. individual votes + reasoning) |
| `recent_evaluations` | `DynArray<ConsensusRecord>` | Ring buffer of last `max_history` results |
| `label_history` / `confidence_history` | `DynArray` | Flat history mirrors for cheap consumers |
| `label_counts_json` / `evaluation_count` | `str` / `u256` | Cumulative metrics |

GenVM constraints honored: no float calldata (JSON-string I/O), GenVM storage types only, single-file deployment (no cross-module imports).

## Config schema (constructor argument, JSON string)

```jsonc
{
  "personas": [
    {"name": "BEAR_ANALYST", "weight": 1.35, "frame": "You are the BEAR ANALYST..."},
    {"name": "NEUTRAL_ANALYST", "weight": 1.15, "frame": "..."},
    {"name": "BULL_ANALYST", "weight": 1.0, "frame": "..."}
  ],                       // ≥2 personas, unique names, weight ∈ (0,10]
  "labels": ["SCAM", "SKIP", "WAIT", "BUY"],  // index 0 = MOST conservative
  "disagreement_fallback": "WAIT",            // must be one of labels
  "disagreement_threshold": 0.45,             // ∈ [0,1]
  "disagreement_confidence_penalty": 0.75,    // ∈ [0,1]
  "max_history": 10                           // ∈ [1,100]
}
```

Invalid config aborts deployment (`parse_config` validation). Empty constructor arg defaults to the trading preset above.

## Usage

```bash
# Deploy (Python 3.13+ required by genlayer-py)
py -3.13 -c "
from genlayer.contracts import deploy_contract
print(deploy_contract('genlayer/contracts_src/weighted_persona_consensus.py'))
"

# Evaluate a subject (permissionless — submitter pays gas)
evaluate('{"subject": "PEPE", "summary": "...", "signal_strength": 0.8,
           "risk_flags": ["thin_liquidity"], "any_domain_key": "value"}')

# Views
get_last_result()      # aggregate + all persona votes + reasoning
get_recent_evaluations()
get_metrics()          # evaluation_count + cumulative per-label counts
get_config()
```

Payload keys are free-form: every key/value pair is rendered into each persona prompt, so the same contract ingests any domain context.

## Tests (executable specification)

```bash
pytest tests/test_core.py::WeightedPersonaConsensusLogicTests -v
```

Covers: config validation matrix, exact weighting math, conservative tie-break, disagreement downgrade + penalty, fail-closed normalization, optional signal/risk hooks, moderation preset validity. The reference module mirrors the contract algorithm line-for-line; both files carry sync notes.

## Live evidence (parent contract)

The generalized algorithm is the line-for-line superset of the already-deployed
`SpiderTradeDecision` contract, which is verified live on Bradbury:

- 3 personas (BULL/BEAR/NEUTRAL) each secured by the Equivalence Principle with full per-persona reasoning;
- weighted aggregation `WAIT @ 0.67`, disagreement `0.43`;
- network round: 5 validators — 3 AGREE, 1 DETERMINISTIC_VIOLATION, 1 TIMEOUT → result `AGREE`, status `ACCEPTED`, `FINISHED_WITH_RETURN`;
- example tx: `0xb5f72b1cf0a94d7d0310c3d15cb6d9349a33da6aff1302d79fcd5683d551f1ad`.

The primitive adds configurable personas/weights/labels on top of this proven
flow; deploy once to obtain its own live address.

## Known limitations (documented trade-offs)

- History ring buffer keeps last N evaluations; full auditability would require events (GenVM event API not used in this version).
- Aggregation happens at contract level; there is no cross-persona network consensus (by design — personas are perspectives, not validators of each other).
- `evaluate` is permissionless; anyone may submit payloads and pay gas.
