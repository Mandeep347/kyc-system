import hashlib
import random

from faker import Faker


def stage_seed(seed: int, stage: str) -> int:
    """Same (seed, stage) always gives the same number, on every machine."""
    digest = hashlib.sha256(f"{seed}:{stage}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def stage_rng(seed: int, stage: str) -> tuple[random.Random, Faker]:
    """An independent random generator + Faker for one stage."""
    s = stage_seed(seed, stage)
    rng = random.Random(s)
    fake = Faker("en_IN")
    fake.seed_instance(s)
    return rng, fake