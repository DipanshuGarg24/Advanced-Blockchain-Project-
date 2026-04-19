"""
Token Data Structures for Trusted-BPI

Defines the BlindToken and Payment message formats used
throughout the system.
"""

import uuid
import time
import json
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class BlindToken:
    """
    A blind-signed token representing a unit of value.
    
    The token's serial was never seen by the Mint during signing,
    providing unconditional sender privacy (Chaum's property).
    """
    serial: str                          # Unique random serial (never seen by Mint)
    denomination: float                  # Token value (e.g., 100.0)
    expiry: int                         # Unix timestamp when token expires
    blind_signature: Optional[int] = None  # Mint's unblinded signature
    identity_shares: Optional[dict] = None  # Cut-and-choose shares

    @staticmethod
    def generate_serial() -> str:
        """Generate a cryptographically random serial."""
        return str(uuid.uuid4())

    def to_signable_data(self) -> dict:
        """Return the data that gets blind-signed by the Mint."""
        return {
            "serial": self.serial,
            "denomination": self.denomination,
            "expiry": self.expiry,
        }

    def is_expired(self) -> bool:
        """Check if this token has expired."""
        return time.time() > self.expiry

    def time_remaining(self) -> int:
        """Seconds remaining before expiry."""
        return max(0, self.expiry - int(time.time()))

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> 'BlindToken':
        """Deserialize from dictionary."""
        return cls(**data)


@dataclass
class PaymentMessage:
    """
    A signed payment message sent from payer to payee offline.
    
    Contains the blind-signed tokens being transferred, the
    cut-and-choose responses for identity reveal, and the
    payer's ECDSA signature over the entire message.
    """
    payment_id: str                      # Unique payment identifier
    payer_pubkey: str                     # Payer's ECDSA public key (hex)
    payee_pubkey: str                     # Payee's ECDSA public key (hex)
    tokens: list                         # List of BlindToken dicts being spent
    total_amount: float                  # Total payment amount
    change_amount: float = 0.0           # Change (if token > payment)
    timestamp: int = 0                   # Payment timestamp
    monotonic_counter: int = 0           # TEE counter value (anti-clone)
    challenge_bits: list = field(default_factory=list)   # Receiver's challenge
    identity_responses: list = field(default_factory=list) # Payer's identity reveals
    payer_signature: Optional[str] = None  # ECDSA signature over all fields

    def __post_init__(self):
        if self.timestamp == 0:
            self.timestamp = int(time.time())
        if not self.payment_id:
            self.payment_id = str(uuid.uuid4())

    def signable_payload(self) -> bytes:
        """
        The canonical byte representation signed by the payer.
        Includes ALL fields except the signature itself.
        """
        data = {
            "payment_id": self.payment_id,
            "payer_pubkey": self.payer_pubkey,
            "payee_pubkey": self.payee_pubkey,
            "tokens": self.tokens,
            "total_amount": self.total_amount,
            "change_amount": self.change_amount,
            "timestamp": self.timestamp,
            "monotonic_counter": self.monotonic_counter,
            "challenge_bits": self.challenge_bits,
            "identity_responses": self.identity_responses,
        }
        return json.dumps(data, sort_keys=True, separators=(',', ':')).encode()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'PaymentMessage':
        return cls(**data)


@dataclass
class SettlementRequest:
    """
    Submitted by the receiver to the Mint for on-chain settlement.
    """
    payment: PaymentMessage
    receiver_pubkey: str
    submitted_at: int = 0

    def __post_init__(self):
        if self.submitted_at == 0:
            self.submitted_at = int(time.time())
