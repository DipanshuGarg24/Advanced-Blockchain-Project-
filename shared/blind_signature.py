"""
Chaumian Blind Signature Implementation (David Chaum, 1983)

This module implements RSA-based blind signatures as described in
"Blind Signatures for Untraceable Payments" by David Chaum.

Protocol:
1. Alice generates random serial S and blinding factor r
2. Alice computes B = S * r^e mod n (blinded message)
3. Mint signs: SIG_B = B^d mod n
4. Alice unblinds: SIG_S = SIG_B * r^(-1) mod n
5. Now (S, SIG_S) is a valid Mint signature that Mint has never seen

Privacy Property:
    The Mint signed B during issuance but receives S during settlement.
    It cannot link them because it never saw the unblinded S.
"""

import os
import hashlib
import json
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
from Crypto.Hash import SHA256


class BlindSignatureScheme:
    """
    RSA Blind Signature Scheme following Chaum's DigiCash protocol.
    
    The Mint holds the RSA private key and signs blinded messages.
    Users blind their tokens before sending to the Mint, then unblind
    the signature to obtain a valid signature the Mint cannot recognize.
    """

    def __init__(self, key_size=2048):
        """Initialize with a fresh RSA key pair for the Mint."""
        self.key = RSA.generate(key_size)
        self.n = self.key.n  # RSA modulus
        self.e = self.key.e  # Public exponent
        self.d = self.key.d  # Private exponent (Mint's secret)

    @classmethod
    def from_key_components(cls, n, e, d):
        """Reconstruct from existing key components."""
        instance = cls.__new__(cls)
        instance.n = n
        instance.e = e
        instance.d = d
        instance.key = RSA.construct((n, e, d))
        return instance

    def get_public_key(self):
        """Return the Mint's public key (n, e) for distribution to users."""
        return {"n": self.n, "e": self.e}

    # ---- User-side operations ----

    @staticmethod
    def generate_blinding_factor(n):
        """
        Generate a random blinding factor r that is coprime to n.
        This ensures r has a modular inverse mod n.
        """
        from math import gcd
        while True:
            r = int.from_bytes(os.urandom(256), 'big') % n
            if r > 1 and gcd(r, n) == 1:
                return r

    @staticmethod
    def hash_token_data(token_data: dict) -> int:
        """
        Hash token data to produce the message to be blind-signed.
        Uses SHA-256 and converts to integer for RSA operations.
        """
        # Canonical JSON serialization for deterministic hashing
        canonical = json.dumps(token_data, sort_keys=True, separators=(',', ':'))
        hash_bytes = hashlib.sha256(canonical.encode()).digest()
        return int.from_bytes(hash_bytes, 'big')

    @staticmethod
    def blind_message(message_int: int, r: int, e: int, n: int) -> int:
        """
        Blind the message: B = m * r^e mod n
        
        Args:
            message_int: The hash of the token data as an integer
            r: Random blinding factor
            e: Mint's public exponent
            n: Mint's modulus
        
        Returns:
            Blinded message B
        """
        r_e = pow(r, e, n)  # r^e mod n
        blinded = (message_int * r_e) % n
        return blinded

    @staticmethod
    def unblind_signature(blind_sig: int, r: int, n: int) -> int:
        """
        Unblind the signature: SIG = SIG_B * r^(-1) mod n
        
        After unblinding, we have a valid Mint signature on the
        original (unblinded) message that the Mint never saw.
        
        Args:
            blind_sig: Mint's signature on the blinded message
            r: The same blinding factor used during blinding
            n: Mint's modulus
        
        Returns:
            Unblinded signature (valid signature on original message)
        """
        r_inv = pow(r, -1, n)  # Modular inverse of r
        unblinded = (blind_sig * r_inv) % n
        return unblinded

    # ---- Mint-side operations ----

    def sign_blinded(self, blinded_message: int) -> int:
        """
        Mint signs the blinded message: SIG_B = B^d mod n
        
        The Mint does NOT know what message it is signing.
        This is the core privacy property of blind signatures.
        
        Args:
            blinded_message: The blinded message from the user
        
        Returns:
            Blind signature SIG_B
        """
        return pow(blinded_message, self.d, self.n)

    def verify_signature(self, message_int: int, signature: int) -> bool:
        """
        Verify a signature: check if SIG^e mod n == message
        
        Used during settlement to verify token authenticity.
        The Mint can verify the signature is valid but CANNOT
        link it back to the original blinding request.
        
        Args:
            message_int: Hash of the token data
            signature: The unblinded signature
        
        Returns:
            True if signature is valid
        """
        verification = pow(signature, self.e, self.n)
        return verification == message_int


class BlindTokenProtocol:
    """
    High-level protocol for blind token issuance and verification.
    Wraps the raw blind signature math into a usable token workflow.
    """

    def __init__(self, scheme: BlindSignatureScheme):
        self.scheme = scheme

    def prepare_token_for_blinding(self, serial: str, denomination: float,
                                     expiry_timestamp: int) -> dict:
        """
        User prepares token data and blinds it for signing.
        
        Returns:
            dict with blinded_message, blinding_factor, token_data, message_hash
        """
        token_data = {
            "serial": serial,
            "denomination": denomination,
            "expiry": expiry_timestamp,
        }

        message_hash = BlindSignatureScheme.hash_token_data(token_data)
        r = BlindSignatureScheme.generate_blinding_factor(self.scheme.n)
        blinded = BlindSignatureScheme.blind_message(
            message_hash, r, self.scheme.e, self.scheme.n
        )

        return {
            "blinded_message": blinded,
            "blinding_factor": r,
            "token_data": token_data,
            "message_hash": message_hash,
        }

    def mint_sign(self, blinded_message: int) -> int:
        """Mint signs the blinded token (doesn't see the content)."""
        return self.scheme.sign_blinded(blinded_message)

    def user_unblind(self, blind_signature: int, blinding_factor: int) -> int:
        """User unblinds to get a valid signature Mint can't recognize."""
        return BlindSignatureScheme.unblind_signature(
            blind_signature, blinding_factor, self.scheme.n
        )

    def verify_token(self, token_data: dict, signature: int) -> bool:
        """
        Verify a token's blind signature.
        Used by receivers and the Mint during settlement.
        """
        message_hash = BlindSignatureScheme.hash_token_data(token_data)
        return self.scheme.verify_signature(message_hash, signature)


# ---- Convenience functions for direct usage ----

def demo_blind_signature():
    """Demonstrate the complete blind signature protocol."""
    print("=" * 60)
    print("CHAUMIAN BLIND SIGNATURE DEMONSTRATION")
    print("=" * 60)

    # Step 1: Mint generates RSA keys
    print("\n[1] Mint generates RSA key pair...")
    scheme = BlindSignatureScheme(key_size=2048)
    pub = scheme.get_public_key()
    print(f"    Public key (n): {str(pub['n'])[:40]}...")
    print(f"    Public exponent (e): {pub['e']}")

    # Step 2: Alice prepares a token
    print("\n[2] Alice prepares token data...")
    protocol = BlindTokenProtocol(scheme)
    import uuid, time
    prep = protocol.prepare_token_for_blinding(
        serial=str(uuid.uuid4()),
        denomination=100.0,
        expiry_timestamp=int(time.time()) + 14400,  # 4 hours
    )
    print(f"    Token serial: {prep['token_data']['serial']}")
    print(f"    Denomination: Rs.{prep['token_data']['denomination']}")
    print(f"    Message hash: {str(prep['message_hash'])[:40]}...")
    print(f"    Blinded message: {str(prep['blinded_message'])[:40]}...")
    print(f"    (Mint CANNOT see the serial or denomination)")

    # Step 3: Mint signs blindly
    print("\n[3] Mint blind-signs the token...")
    blind_sig = protocol.mint_sign(prep["blinded_message"])
    print(f"    Blind signature: {str(blind_sig)[:40]}...")

    # Step 4: Alice unblinds
    print("\n[4] Alice unblinds the signature...")
    real_sig = protocol.user_unblind(blind_sig, prep["blinding_factor"])
    print(f"    Unblinded signature: {str(real_sig)[:40]}...")

    # Step 5: Verify
    print("\n[5] Verification (by Bob or Mint during settlement)...")
    valid = protocol.verify_token(prep["token_data"], real_sig)
    print(f"    Signature valid: {valid}")
    print(f"    [v] Mint signed this token but NEVER saw its contents!")

    return valid


if __name__ == "__main__":
    demo_blind_signature()
