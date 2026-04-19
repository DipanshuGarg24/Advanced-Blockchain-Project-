"""
TEE Hardware Operation Logger

Logs every operation that happens inside the TEE boundary.
When migrating to real hardware (ARM TrustZone / ATECC608A),
these logs map 1:1 to actual secure world operations.

Log Format:
    [timestamp] [TEE:<user_id>] [OP:<operation>] <details>

Operations logged:
    KEY_GEN     - Key pair generated inside TEE
    KEY_EXPORT  - Public key exported (private key NEVER exported)
    TOKEN_BLIND - Token blinding factor generated, message blinded
    TOKEN_STORE - Unblinded token stored in secure vault
    TOKEN_DELETE- Token permanently deleted from vault (before payment)
    COUNTER_INC - Monotonic counter incremented
    PAYMENT_SIGN- Payment message signed with private key
    VERIFY      - Incoming payment verified
    TAMPER      - Tamper detection triggered
"""

import os
import time
import json
import threading


class TEELogger:
    """Thread-safe logger for TEE operations."""

    def __init__(self, user_id: str, log_dir: str = None):
        self.user_id = user_id
        self.log_dir = log_dir or os.path.expanduser(f"~/.trusted-bpi/{user_id}")
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_file = os.path.join(self.log_dir, "tee_hardware.log")
        self._lock = threading.Lock()

        # Write header on first creation
        if not os.path.exists(self.log_file):
            self._write_raw(
                f"# TEE Hardware Operation Log\n"
                f"# Device: {user_id}\n"
                f"# Created: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"# Format: [timestamp] [TEE:{user_id}] [OP:operation] details\n"
                f"# Note: In production, these operations execute inside ARM TrustZone\n"
                f"#       secure world or on ATECC608A secure element.\n"
                f"{'=' * 80}\n"
            )

    def log(self, operation: str, details: str, data: dict = None):
        """Log a TEE operation."""
        ts = time.strftime('%Y-%m-%d %H:%M:%S')
        line = f"[{ts}] [TEE:{self.user_id}] [OP:{operation}] {details}"
        if data:
            # Truncate large values for readability
            safe_data = {}
            for k, v in data.items():
                sv = str(v)
                safe_data[k] = sv[:64] + "..." if len(sv) > 64 else sv
            line += f" | data={json.dumps(safe_data)}"
        self._write_raw(line + "\n")

    def log_key_gen(self, public_key: str):
        self.log("KEY_GEN", "ECDSA key pair generated inside secure enclave (secp256k1)",
                 {"public_key": public_key, "private_key": "[SEALED_IN_TEE]"})

    def log_key_export(self, public_key: str):
        self.log("KEY_EXPORT", f"Public key exported: {public_key[:20]}...",
                 {"exported": public_key, "private_key_exported": "NEVER"})

    def log_token_blind(self, denomination: float, serial_short: str):
        self.log("TOKEN_BLIND",
                 f"Blinding token Rs.{denomination} (serial {serial_short}...) - "
                 f"blinding factor generated inside TEE, only blinded message exits",
                 {"denomination": denomination, "serial": serial_short,
                  "blinding_factor": "[INTERNAL_ONLY]"})

    def log_token_store(self, denomination: float, serial_short: str):
        self.log("TOKEN_STORE",
                 f"Token Rs.{denomination} ({serial_short}...) stored in secure vault - "
                 f"signature unblinded inside TEE",
                 {"denomination": denomination, "serial": serial_short,
                  "vault_location": "SECURE_FLASH"})

    def log_token_delete(self, denomination: float, serial_short: str):
        self.log("TOKEN_DELETE",
                 f"[!] Token Rs.{denomination} ({serial_short}...) PERMANENTLY DELETED "
                 f"from secure vault - BEFORE payment signature is released",
                 {"denomination": denomination, "serial": serial_short,
                  "recovery": "IMPOSSIBLE"})

    def log_counter_increment(self, old_val: int, new_val: int):
        self.log("COUNTER_INC",
                 f"Monotonic counter: {old_val} -> {new_val} "
                 f"(hardware fuse - cannot decrement)",
                 {"old": old_val, "new": new_val, "can_decrement": "NO"})

    def log_payment_sign(self, payment_id: str, amount: float, payee_short: str):
        self.log("PAYMENT_SIGN",
                 f"Payment {payment_id[:8]}... signed - Rs.{amount} -> {payee_short}... "
                 f"- private key used INSIDE TEE, never exposed",
                 {"payment_id": payment_id, "amount": amount,
                  "signing_location": "SECURE_ENCLAVE"})

    def log_verify(self, payment_id: str, result: str):
        self.log("VERIFY",
                 f"Incoming payment {payment_id[:8]}... verification: {result}",
                 {"payment_id": payment_id, "result": result})

    def log_tamper(self, reason: str):
        self.log("TAMPER",
                 f"!! TAMPER DETECTED: {reason} - ALL KEYS ZEROIZED",
                 {"action": "KEYS_DESTROYED", "device_locked": True})

    def get_log_contents(self) -> str:
        """Read the full log file."""
        if os.path.exists(self.log_file):
            with open(self.log_file, 'r') as f:
                return f.read()
        return ""

    def get_recent_entries(self, n: int = 20) -> list:
        """Get last n log entries."""
        if not os.path.exists(self.log_file):
            return []
        with open(self.log_file, 'r') as f:
            lines = f.readlines()
        # Filter out comments
        entries = [l.strip() for l in lines if l.strip() and not l.startswith('#') and not l.startswith('=')]
        return entries[-n:]

    def _write_raw(self, text: str):
        with self._lock:
            with open(self.log_file, 'a') as f:
                f.write(text)
