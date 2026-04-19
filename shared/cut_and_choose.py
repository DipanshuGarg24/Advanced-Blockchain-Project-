"""
Cut-and-Choose Identity Reveal Protocol

Based on David Chaum's technique for conditional anonymity:
- Honest users (single spend) remain completely anonymous
- Double-spenders have their identity revealed

Protocol:
1. Alice embeds her identity into the token using XOR secret sharing
2. She splits her identity into two halves using random masks
3. Each time she spends the token, the receiver sends a random challenge bit
4. Alice reveals one half based on the challenge
5. Single spend: one half revealed -> identity NOT recoverable
6. Double spend: two different challenges -> both halves revealed -> identity recovered

This is the mathematical elegance that makes Chaum's scheme special:
privacy for honest users, exposure for cheaters, with NO trusted third party
needed for the reveal.
"""

import os
import hashlib
import json


class IdentityEmbedding:
    """
    Manages the embedding and reveal of user identity in tokens
    using Chaum's cut-and-choose protocol.
    """

    @staticmethod
    def create_identity_shares(user_id: str, num_pairs: int = 5) -> dict:
        """
        Create identity shares for embedding in a token.
        
        The user's identity is split into pairs of shares (a_i, b_i) such that:
            a_i XOR b_i = user_id_hash
        
        For each pair, a random mask is generated:
            a_i = random_mask_i
            b_i = random_mask_i XOR user_id_hash
        
        Knowing only a_i or only b_i reveals nothing about user_id.
        Knowing both a_i AND b_i reveals user_id_hash (and thus user_id).
        
        Args:
            user_id: The user's identity string
            num_pairs: Number of share pairs (more = higher detection probability)
        
        Returns:
            dict with identity_hash, pairs of (left_share, right_share)
        """
        # Hash the user ID to fixed-length bytes
        id_hash = hashlib.sha256(user_id.encode()).digest()
        id_hash_int = int.from_bytes(id_hash, 'big')

        pairs = []
        for i in range(num_pairs):
            # Generate random mask for this pair
            mask = int.from_bytes(os.urandom(32), 'big')

            left_share = mask                   # a_i = random
            right_share = mask ^ id_hash_int    # b_i = random XOR identity

            # Verify: left XOR right = identity
            assert left_share ^ right_share == id_hash_int

            pairs.append({
                "index": i,
                "left_share": left_share,    # Revealed if challenge bit = 0
                "right_share": right_share,  # Revealed if challenge bit = 1
            })

        return {
            "identity_hash": id_hash.hex(),
            "identity_hash_int": id_hash_int,
            "pairs": pairs,
            "num_pairs": num_pairs,
        }

    @staticmethod
    def generate_challenge(num_pairs: int = 5) -> list:
        """
        Receiver generates random challenge bits.
        Each bit determines which share of each pair is revealed.
        
        Args:
            num_pairs: Number of challenge bits to generate
        
        Returns:
            List of random bits (0 or 1)
        """
        challenge_byte = os.urandom(1)[0]
        bits = []
        for i in range(num_pairs):
            # Use random bytes for each bit to ensure independence
            bit = os.urandom(1)[0] & 1
            bits.append(bit)
        return bits

    @staticmethod
    def respond_to_challenge(identity_shares: dict, challenge_bits: list) -> list:
        """
        User reveals shares based on the challenge.
        
        For each pair:
            - If challenge bit = 0: reveal left_share
            - If challenge bit = 1: reveal right_share
        
        Args:
            identity_shares: The shares created by create_identity_shares
            challenge_bits: The challenge from the receiver
        
        Returns:
            List of revealed shares (one per pair)
        """
        responses = []
        for i, bit in enumerate(challenge_bits):
            pair = identity_shares["pairs"][i]
            if bit == 0:
                responses.append({
                    "index": i,
                    "challenge_bit": 0,
                    "revealed_share": pair["left_share"],
                })
            else:
                responses.append({
                    "index": i,
                    "challenge_bit": 1,
                    "revealed_share": pair["right_share"],
                })
        return responses

    @staticmethod
    def detect_double_spend(response1: list, response2: list) -> dict:
        """
        Given two sets of responses from spending the same token,
        attempt to recover the spender's identity.
        
        If the two challenges differ in at least one bit position,
        we have both shares for that position and can XOR them
        to recover the identity hash.
        
        Args:
            response1: Responses from first spending
            response2: Responses from second spending
        
        Returns:
            dict with detected (bool), recovered_identity_hash (str or None)
        """
        for r1, r2 in zip(response1, response2):
            if r1["index"] != r2["index"]:
                continue

            # If challenges differ, we have both shares
            if r1["challenge_bit"] != r2["challenge_bit"]:
                # One is left_share, other is right_share
                # XOR them to recover identity_hash
                recovered_hash_int = r1["revealed_share"] ^ r2["revealed_share"]
                recovered_hash = recovered_hash_int.to_bytes(32, 'big').hex()

                return {
                    "detected": True,
                    "recovered_identity_hash": recovered_hash,
                    "pair_index": r1["index"],
                    "detail": (
                        f"Double-spend detected at pair {r1['index']}! "
                        f"Challenge bits {r1['challenge_bit']} and {r2['challenge_bit']} "
                        f"revealed both shares, recovering the spender's identity."
                    ),
                }

        return {
            "detected": False,
            "recovered_identity_hash": None,
            "detail": (
                "Same challenge bits used in both spends - identity not revealed. "
                f"Probability of this: (1/2)^{len(response1)} = {1/(2**len(response1)):.6f}"
            ),
        }

    @staticmethod
    def match_identity(recovered_hash: str, candidate_user_id: str) -> bool:
        """
        Check if a recovered identity hash matches a known user.
        
        Args:
            recovered_hash: The hash recovered from double-spend detection
            candidate_user_id: The user ID to check against
        
        Returns:
            True if the recovered hash matches the candidate
        """
        candidate_hash = hashlib.sha256(candidate_user_id.encode()).hexdigest()
        return recovered_hash == candidate_hash


def demo_cut_and_choose():
    """Demonstrate the cut-and-choose identity reveal protocol."""
    print("=" * 60)
    print("CUT-AND-CHOOSE IDENTITY REVEAL DEMONSTRATION")
    print("=" * 60)

    alice_id = "alice_24M0755"
    num_pairs = 5

    # Step 1: Alice creates identity shares
    print(f"\n[1] Alice ({alice_id}) creates identity shares...")
    shares = IdentityEmbedding.create_identity_shares(alice_id, num_pairs)
    print(f"    Identity hash: {shares['identity_hash'][:32]}...")
    print(f"    Created {num_pairs} share pairs")

    # Step 2: First spending - Alice pays Bob
    print("\n[2] Alice pays Bob (first spend)...")
    challenge1 = IdentityEmbedding.generate_challenge(num_pairs)
    print(f"    Bob's challenge:  {challenge1}")
    response1 = IdentityEmbedding.respond_to_challenge(shares, challenge1)
    print(f"    Alice reveals {sum(1 for r in response1 if r['challenge_bit']==0)} left shares, "
          f"{sum(1 for r in response1 if r['challenge_bit']==1)} right shares")
    print(f"    -> Bob has HALF the information. Cannot identify Alice. [v]")

    # Step 3: Second spending - Alice pays Umang (DOUBLE SPEND!)
    print("\n[3] Alice pays Umang with SAME token (DOUBLE SPEND!)...")
    challenge2 = IdentityEmbedding.generate_challenge(num_pairs)
    print(f"    Umang's challenge: {challenge2}")
    response2 = IdentityEmbedding.respond_to_challenge(shares, challenge2)
    print(f"    Alice reveals {sum(1 for r in response2 if r['challenge_bit']==0)} left shares, "
          f"{sum(1 for r in response2 if r['challenge_bit']==1)} right shares")

    # Step 4: Detection during settlement
    print("\n[4] Mint receives both payments during settlement...")
    result = IdentityEmbedding.detect_double_spend(response1, response2)

    if result["detected"]:
        print(f"    !! {result['detail']}")
        print(f"    Recovered hash: {result['recovered_identity_hash'][:32]}...")

        # Verify identity
        match = IdentityEmbedding.match_identity(
            result["recovered_identity_hash"], alice_id
        )
        print(f"    Match against '{alice_id}': {match}")
        if match:
            print(f"    [v] CHEATER IDENTIFIED: {alice_id}")
    else:
        print(f"    {result['detail']}")
        print(f"    (Extremely unlikely - run again for different challenges)")

    return result["detected"]


if __name__ == "__main__":
    demo_cut_and_choose()
