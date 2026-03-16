"""Sepolia testnet transaction simulator via web3.py."""

from __future__ import annotations

from web3 import Web3

from config import settings


class SepoliaExecutor:
    """Connect and submit tiny self-transfer on Sepolia for testing only."""

    def __init__(self) -> None:
        self.rpc_url = settings.sepolia_rpc_url
        self.private_key = settings.wallet_private_key
        if not self.rpc_url:
            raise ValueError("SPIDER4AI_SEPOLIA_RPC_URL is not configured")
        if not self.private_key:
            raise ValueError("SPIDER4AI_WALLET_PRIVATE_KEY is not configured")
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        if not self.w3.is_connected():
            raise ConnectionError("Unable to connect to Sepolia RPC")

    def simulate_test_transaction(self) -> str:
        account = self.w3.eth.account.from_key(self.private_key)
        nonce = self.w3.eth.get_transaction_count(account.address)

        tx = {
            "nonce": nonce,
            "to": account.address,
            "value": self.w3.to_wei(0.000001, "ether"),
            "gas": 21_000,
            "gasPrice": self.w3.eth.gas_price,
            "chainId": settings.default_chain_id,
        }
        signed = account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        return tx_hash.hex()
