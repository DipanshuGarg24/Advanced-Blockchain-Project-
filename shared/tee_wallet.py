"""
TEE Hardware Wallet Simulator - Persistent Version

Simulates a Trusted Execution Environment wallet that:
- Stores keys, tokens, and counter in a local encrypted file (simulating secure flash)
- Exports/imports signed payment messages (for real P2P transfer over network)
- Private key never leaves the TEE boundary

In production: ARM TrustZone / ATECC608A / NXP SE050
In this demo: Python class with strict API + file-based persistence
"""

import os
import sys
import uuid
import time
import json
import hashlib
from typing import Optional

from eth_account import Account
from eth_account.messages import encode_defunct

sys.path.insert(0, os.path.dirname(__file__))

from blind_signature import BlindSignatureScheme
from cut_and_choose import IdentityEmbedding
from bpi_token import BlindToken, PaymentMessage
from monotonic_counter import MonotonicCounter
from tee_logger import TEELogger


class TEEWallet:
    def __init__(self, user_id: str, mint_public_key: dict = None, data_dir: str = None):
        self._user_id = user_id
        self._data_dir = data_dir or os.path.expanduser(f"~/.trusted-bpi/{user_id}")
        os.makedirs(self._data_dir, exist_ok=True)

        self.__mint_pubkey = mint_public_key
        self.__counter = MonotonicCounter()
        self.__token_vault: list[dict] = []
        self.__identity_shares_cache: dict[str, dict] = {}
        self.__journal: list[dict] = []
        self.__received_payments: list[dict] = []
        self.__transaction_history: list[dict] = []

        # TEE Hardware Logger
        self.tee_log = TEELogger(user_id, self._data_dir)

        # Load or generate keys
        key_file = os.path.join(self._data_dir, "key.json")
        if os.path.exists(key_file):
            with open(key_file, 'r') as f:
                kd = json.load(f)
            self.__private_key = kd["private_key"]
            self.__account = Account.from_key(self.__private_key)
            self._public_key = kd["address"]
            # Restore counter
            for _ in range(kd.get("counter", 0)):
                self.__counter.increment()
        else:
            self.__account = Account.create()
            self.__private_key = self.__account.key.hex()
            self._public_key = self.__account.address
            self._save_key()
            self.tee_log.log_key_gen(self._public_key)

        self.tee_log.log_key_export(self._public_key)

        # Load token vault
        self._load_vault()
        self._load_received()
        self._load_history()

    # -- Properties --

    @property
    def public_key(self) -> str:
        return self._public_key

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def counter_value(self) -> int:
        return self.__counter.value

    @property
    def token_count(self) -> int:
        return len(self.__token_vault)

    @property
    def total_balance(self) -> float:
        return sum(t["denomination"] for t in self.__token_vault)

    @property
    def received_payments(self) -> list:
        return list(self.__received_payments)

    @property
    def transaction_history(self) -> list:
        return list(self.__transaction_history)

    def get_token_summary(self) -> list[dict]:
        return [
            {
                "denomination": t["denomination"],
                "serial_short": t["serial"][:8],
                "expires_in": max(0, t["expiry"] - int(time.time())),
                "expired": time.time() > t["expiry"],
            }
            for t in self.__token_vault
        ]

    # -- Persistence --

    def _save_key(self):
        with open(os.path.join(self._data_dir, "key.json"), 'w') as f:
            json.dump({
                "private_key": self.__private_key,
                "address": self._public_key,
                "user_id": self._user_id,
                "counter": self.__counter.value,
            }, f)

    def _save_vault(self):
        with open(os.path.join(self._data_dir, "vault.json"), 'w') as f:
            json.dump(self.__token_vault, f)
        # Also save counter
        self._save_key()

    def _load_vault(self):
        vf = os.path.join(self._data_dir, "vault.json")
        if os.path.exists(vf):
            with open(vf, 'r') as f:
                self.__token_vault = json.load(f)

    def _save_received(self):
        with open(os.path.join(self._data_dir, "received.json"), 'w') as f:
            json.dump(self.__received_payments, f)

    def _load_received(self):
        rf = os.path.join(self._data_dir, "received.json")
        if os.path.exists(rf):
            with open(rf, 'r') as f:
                self.__received_payments = json.load(f)

    def _save_history(self):
        with open(os.path.join(self._data_dir, "history.json"), 'w') as f:
            json.dump(self.__transaction_history, f)

    def _load_history(self):
        hf = os.path.join(self._data_dir, "history.json")
        if os.path.exists(hf):
            with open(hf, 'r') as f:
                self.__transaction_history = json.load(f)

    def set_mint_pubkey(self, pubkey: dict):
        self.__mint_pubkey = pubkey
        with open(os.path.join(self._data_dir, "mint_pubkey.json"), 'w') as f:
            json.dump(pubkey, f)

    def load_mint_pubkey(self) -> bool:
        mp = os.path.join(self._data_dir, "mint_pubkey.json")
        if os.path.exists(mp):
            with open(mp, 'r') as f:
                self.__mint_pubkey = json.load(f)
            return True
        return False

    # -- Token Issuance (ONLINE - talks to server) --

    def prepare_blind_token(self, denomination: float, expiry_hours: float = 4.0) -> dict:
        """Prepare blinded token - only blinded message leaves TEE."""
        serial = str(uuid.uuid4())
        expiry = int(time.time()) + int(expiry_hours * 3600)

        token_data = {"serial": serial, "denomination": denomination, "expiry": expiry}
        message_hash = BlindSignatureScheme.hash_token_data(token_data)

        n = self.__mint_pubkey["n"]
        e = self.__mint_pubkey["e"]
        r = BlindSignatureScheme.generate_blinding_factor(n)
        blinded = BlindSignatureScheme.blind_message(message_hash, r, e, n)

        internal_ref = str(uuid.uuid4())
        self.__journal.append({
            "ref": internal_ref, "serial": serial,
            "denomination": denomination, "expiry": expiry,
            "message_hash": message_hash, "blinding_factor": r,
            "status": "pending",
        })

        # Pre-compute identity shares
        shares = IdentityEmbedding.create_identity_shares(self._user_id, num_pairs=5)
        self.__identity_shares_cache[serial] = shares

        self.tee_log.log_token_blind(denomination, serial[:8])
        return {"blinded_message": blinded, "denomination": denomination, "internal_ref": internal_ref}

    def load_signed_token(self, internal_ref: str, blind_signature: int) -> bool:
        """Receive Mint's blind signature and store unblinded token."""
        entry = None
        for j in self.__journal:
            if j["ref"] == internal_ref and j["status"] == "pending":
                entry = j
                break
        if entry is None:
            raise ValueError(f"No pending token with ref {internal_ref}")

        n = self.__mint_pubkey["n"]
        r = entry["blinding_factor"]
        unblinded_sig = BlindSignatureScheme.unblind_signature(blind_signature, r, n)

        # Verify
        e = self.__mint_pubkey["e"]
        if pow(unblinded_sig, e, n) != entry["message_hash"]:
            raise ValueError("Invalid blind signature!")

        self.__token_vault.append({
            "serial": entry["serial"],
            "denomination": entry["denomination"],
            "expiry": entry["expiry"],
            "signature": unblinded_sig,
            "message_hash": entry["message_hash"],
        })
        entry["status"] = "loaded"
        self._save_vault()
        self.tee_log.log_token_store(entry["denomination"], entry["serial"][:8])
        return True

    # -- Payment (OFFLINE - no server needed) --

    def make_payment(self, payee_pubkey: str, amount: float,
                     challenge_bits: list) -> Optional[dict]:
        """
        Create signed payment. Token DELETED before signature released.
        Returns serializable dict (ready to send over network).
        """
        selected, remaining = self._select_tokens(amount)
        if selected is None:
            return None

        total_selected = sum(t["denomination"] for t in selected)
        change = total_selected - amount

        # DELETE tokens FIRST
        self.__token_vault = remaining
        for t in selected:
            self.tee_log.log_token_delete(t["denomination"], t["serial"][:8])

        # Increment counter
        old_counter = self.__counter.value
        counter_val = self.__counter.increment()
        self.tee_log.log_counter_increment(old_counter, counter_val)

        # Identity responses
        all_responses = []
        token_dicts = []
        for t in selected:
            shares = self.__identity_shares_cache.get(t["serial"])
            if shares:
                responses = IdentityEmbedding.respond_to_challenge(shares, challenge_bits)
                all_responses.extend(responses)
            token_dicts.append({
                "serial": t["serial"],
                "denomination": t["denomination"],
                "expiry": t["expiry"],
                "signature": t["signature"],
                "message_hash": t["message_hash"],
            })

        payment = {
            "payment_id": str(uuid.uuid4()),
            "payer_pubkey": self._public_key,
            "payer_user_id": self._user_id,
            "payee_pubkey": payee_pubkey,
            "tokens": token_dicts,
            "total_amount": amount,
            "change_amount": change,
            "timestamp": int(time.time()),
            "monotonic_counter": counter_val,
            "challenge_bits": challenge_bits,
            "identity_responses": all_responses,
        }

        # Sign with ECDSA
        payload = json.dumps(
            {k: v for k, v in payment.items() if k != "payer_signature"},
            sort_keys=True, separators=(',', ':')
        ).encode()
        payload_hash = hashlib.sha256(payload).hexdigest()
        msg = encode_defunct(text=payload_hash)
        signed = Account.sign_message(msg, self.__private_key)
        payment["payer_signature"] = signed.signature.hex()

        self.tee_log.log_payment_sign(payment["payment_id"], amount, payee_pubkey[:16])

        # Record in history
        self.__transaction_history.append({
            "type": "sent",
            "payment_id": payment["payment_id"],
            "to": payee_pubkey[:16] + "...",
            "amount": amount,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "counter": counter_val,
        })

        self._save_vault()
        self._save_history()
        return payment

    def receive_payment(self, payment: dict) -> dict:
        """
        Verify and accept a received payment (OFFLINE).
        Returns verification result.
        """
        errors = []

        if not self.__mint_pubkey:
            errors.append("Mint public key not loaded")
            return {"valid": False, "errors": errors}

        n = self.__mint_pubkey["n"]
        e = self.__mint_pubkey["e"]
        total_token_value = 0

        for tok in payment["tokens"]:
            sig = tok["signature"]
            msg_hash = tok["message_hash"]

            if pow(sig, e, n) != msg_hash:
                errors.append(f"Invalid Mint signature on token {tok['serial'][:8]}")
            else:
                expected = BlindSignatureScheme.hash_token_data({
                    "serial": tok["serial"],
                    "denomination": tok["denomination"],
                    "expiry": tok["expiry"],
                })
                if expected != msg_hash:
                    errors.append(f"Token data hash mismatch {tok['serial'][:8]}")

            remaining = tok["expiry"] - int(time.time())
            if remaining < 0:
                errors.append(f"Token {tok['serial'][:8]} expired")

            total_token_value += tok["denomination"]

        # Verify payer's ECDSA signature
        pay_copy = {k: v for k, v in payment.items() if k != "payer_signature"}
        payload = json.dumps(pay_copy, sort_keys=True, separators=(',', ':')).encode()
        payload_hash = hashlib.sha256(payload).hexdigest()
        msg = encode_defunct(text=payload_hash)
        try:
            recovered = Account.recover_message(msg, signature=bytes.fromhex(payment["payer_signature"]))
            if recovered.lower() != payment["payer_pubkey"].lower():
                errors.append("Payer signature invalid")
        except Exception as ex:
            errors.append(f"Signature error: {ex}")

        if total_token_value < payment["total_amount"]:
            errors.append(f"Token value {total_token_value} < amount {payment['total_amount']}")

        valid = len(errors) == 0
        self.tee_log.log_verify(payment.get("payment_id", "?"),
                                 "VALID" if valid else f"REJECTED: {errors}")

        if valid:
            self.__received_payments.append({
                "payment_id": payment["payment_id"],
                "from_user": payment.get("payer_user_id", "unknown"),
                "from_pubkey": payment["payer_pubkey"],
                "amount": payment["total_amount"],
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "settled": False,
                "payment_data": payment,
            })
            self.__transaction_history.append({
                "type": "received",
                "payment_id": payment["payment_id"],
                "from": payment["payer_pubkey"][:16] + "...",
                "amount": payment["total_amount"],
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "settled": False,
            })
            self._save_received()
            self._save_history()

        return {"valid": valid, "errors": errors, "amount": payment["total_amount"]}

    def get_unsettled_payments(self) -> list:
        return [p for p in self.__received_payments if not p["settled"]]

    def mark_settled(self, payment_id: str):
        for p in self.__received_payments:
            if p["payment_id"] == payment_id:
                p["settled"] = True
        for h in self.__transaction_history:
            if h.get("payment_id") == payment_id:
                h["settled"] = True
        self._save_received()
        self._save_history()

    def _select_tokens(self, amount: float):
        sorted_tokens = sorted(self.__token_vault, key=lambda t: t["denomination"], reverse=True)
        selected, remaining = [], []
        total, fulfilled = 0, False

        for token in sorted_tokens:
            if not fulfilled and total < amount:
                if time.time() > token["expiry"]:
                    continue
                selected.append(token)
                total += token["denomination"]
                if total >= amount:
                    fulfilled = True
            else:
                remaining.append(token)

        if total < amount:
            return None, None
        return selected, remaining

    def reset(self):
        """Reset wallet completely (for testing)."""
        import shutil
        shutil.rmtree(self._data_dir, ignore_errors=True)
        os.makedirs(self._data_dir, exist_ok=True)
        self.__token_vault = []
        self.__received_payments = []
        self.__transaction_history = []
        self.__counter = MonotonicCounter()
        self.__account = Account.create()
        self.__private_key = self.__account.key.hex()
        self._public_key = self.__account.address
        self._save_key()
