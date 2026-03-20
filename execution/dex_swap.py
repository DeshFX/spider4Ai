"""Sepolia swap preview helper.

This module is intentionally isolated from SpiderAgent. It validates a proposed
ETH -> token swap on Sepolia and builds a preview transaction payload for
Uniswap V3 SwapRouter02, but it never signs or broadcasts the transaction.

Use the CLI `swap-test` command to inspect the preview output safely.
"""

from __future__ import annotations

from time import time
from typing import Any

from web3 import Web3

from config import settings
from execution.trade_manager import calculate_position_size

UNISWAP_V3_SWAP_ROUTER02 = Web3.to_checksum_address("0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45")
SEPOLIA_WETH = Web3.to_checksum_address("0xfFf9976782d46CC05630D1f6EBAb18b2324d6B14")
POOL_FEE_TIER = 3000
MIN_AMOUNT_OUT = 1
DEADLINE_SECONDS = 600
GAS_LIMIT = 350_000
GAS_BUFFER_WEI = Web3.to_wei(0.0002, "ether")
MIN_CONFIDENCE_TO_EXECUTE = 0.6

SWAP_ROUTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "address", "name": "recipient", "type": "address"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountOutMinimum", "type": "uint256"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "internalType": "struct IV3SwapRouter.ExactInputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "exactInputSingle",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    }
]


def _resolve_trade_size(confidence: float | None, *, enforce_threshold: bool = True) -> float | None:
    effective_confidence = 0.0 if confidence is None else float(confidence)
    size = settings.min_trade_size_eth if confidence is None else calculate_position_size(effective_confidence)
    print("[POSITION SIZING]")
    print(f"confidence={effective_confidence:.2f}")
    print(f"calculated_size={size:.5f} ETH")
    print("[FINAL SIZE AFTER CLAMP]")
    print(f"size={size:.5f} ETH")
    if enforce_threshold and confidence is not None and effective_confidence < MIN_CONFIDENCE_TO_EXECUTE:
        print("[SKIP] Confidence too low")
        return None
    return size


def build_swap_preview(token_address: str, confidence: float | None = None) -> dict[str, Any] | None:
    """Build and return a safe preview of an ETH -> token swap on Sepolia."""
    amount_eth = _resolve_trade_size(confidence, enforce_threshold=False)
    if amount_eth is None:
        return None
    if not settings.sepolia_rpc_url:
        raise ValueError("SPIDER4AI_SEPOLIA_RPC_URL is not configured")
    if not settings.wallet_private_key:
        raise ValueError("SPIDER4AI_WALLET_PRIVATE_KEY is not configured")

    token_out = Web3.to_checksum_address(token_address)
    amount_wei = Web3.to_wei(amount_eth, "ether")
    w3 = Web3(Web3.HTTPProvider(settings.sepolia_rpc_url))
    if not w3.is_connected():
        raise ConnectionError("[SWAP PREVIEW ERROR] RPC not connected")

    account = w3.eth.account.from_key(settings.wallet_private_key)
    wallet = account.address
    balance = w3.eth.get_balance(wallet)
    if balance < amount_wei + GAS_BUFFER_WEI:
        raise ValueError("[SWAP PREVIEW ERROR] Wallet balance too low for amount + gas buffer")

    router = w3.eth.contract(address=UNISWAP_V3_SWAP_ROUTER02, abi=SWAP_ROUTER_ABI)
    nonce = w3.eth.get_transaction_count(wallet)
    gas_price = w3.eth.gas_price
    deadline = int(time()) + DEADLINE_SECONDS

    params = (
        SEPOLIA_WETH,
        token_out,
        POOL_FEE_TIER,
        wallet,
        amount_wei,
        MIN_AMOUNT_OUT,
        0,
    )
    tx = router.functions.exactInputSingle(params).build_transaction(
        {
            "from": wallet,
            "value": amount_wei,
            "nonce": nonce,
            "gas": GAS_LIMIT,
            "gasPrice": gas_price,
            "chainId": settings.default_chain_id,
            "type": 0,
        }
    )

    return {
        "wallet": wallet,
        "token_address": token_out,
        "confidence": 0.0 if confidence is None else float(confidence),
        "amount_eth": amount_eth,
        "amount_wei": amount_wei,
        "chain_id": settings.default_chain_id,
        "router": UNISWAP_V3_SWAP_ROUTER02,
        "pool_fee": POOL_FEE_TIER,
        "min_amount_out": MIN_AMOUNT_OUT,
        "deadline": deadline,
        "balance_wei": balance,
        "gas_price_wei": gas_price,
        "transaction": tx,
    }


def swap_eth_to_token(token_address: str, confidence: float | None = None) -> None:
    """Print a preview of an ETH -> token swap without broadcasting it."""
    size = _resolve_trade_size(confidence)
    if size is None:
        return None
    if settings.dry_run:
        print("[SWAP PREVIEW] Dry run mode enabled; no transaction preview was broadcast")
        return None
    preview = build_swap_preview(token_address, confidence)
    if preview is None:
        return None
    print("[SWAP PREVIEW] Transaction prepared but not broadcast")
    print(f"wallet={preview['wallet']}")
    print(f"amount_eth={preview['amount_eth']}")
    print(f"token_address={preview['token_address']}")
    print(f"router={preview['router']}")
    print(f"nonce={preview['transaction']['nonce']}")
    print(f"gas={preview['transaction']['gas']}")
    print(f"gas_price={preview['gas_price_wei']}")
    print(f"value={preview['transaction']['value']}")
    print(f"data={preview['transaction']['data']}")
    return None
