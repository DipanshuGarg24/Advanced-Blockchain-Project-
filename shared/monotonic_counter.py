"""
Monotonic Counter Simulation

In real TEE hardware (ARM TrustZone, ATECC608A), the monotonic counter
is backed by hardware fuses or secure non-volatile storage that can
ONLY increment, never decrement or reset.

This simulation enforces the same invariant in software:
- Counter starts at 0
- Can only be incremented by 1
- Cannot be decremented, reset, or set to arbitrary values
- Counter value is included in every signed transaction
- Two transactions with the same counter value = proof of cloning

In production: STM32 TrustZone's secure counter or ATECC608A's 
monotonic counter (limited to 2^21 increments) would provide this.
"""


class MonotonicCounter:
    """
    A monotonic counter that can only go up.
    Simulates hardware-backed counter in TEE.
    """

    def __init__(self):
        self.__value = 0  # Private: no external access
        self.__locked = False

    @property
    def value(self) -> int:
        """Read the current counter value."""
        return self.__value

    def increment(self) -> int:
        """
        Atomically increment the counter by 1.
        Returns the NEW counter value.
        
        In real hardware, this would be a single atomic
        operation that persists across power cycles.
        """
        if self.__locked:
            raise RuntimeError("Counter is locked (device tamper detected)")
        self.__value += 1
        return self.__value

    def lock(self):
        """
        Permanently lock the counter (tamper response).
        In real hardware, this would zeroize all keys.
        """
        self.__locked = True

    def __repr__(self):
        status = "LOCKED" if self.__locked else "active"
        return f"MonotonicCounter(value={self.__value}, status={status})"

    # Prevent serialization of internal state
    def __getstate__(self):
        raise TypeError("MonotonicCounter cannot be serialized (simulates hardware binding)")

    def __setstate__(self, state):
        raise TypeError("MonotonicCounter cannot be deserialized")
