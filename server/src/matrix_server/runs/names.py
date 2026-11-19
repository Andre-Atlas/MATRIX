"""Generated run names: `adjective-noun-N`, N = the run's ordinal in its project."""

from __future__ import annotations

import secrets

ADJECTIVES = (
    "amber", "ancient", "bold", "brave", "brisk", "calm", "clever", "cosmic", "crisp", "curious",
    "dapper", "daring", "dashing", "eager", "electric", "fancy", "fearless", "fiery", "gentle", "glad",
    "golden", "graceful", "happy", "hidden", "honest", "jolly", "keen", "lively", "lucky", "lunar",
    "mellow", "misty", "noble", "polar", "proud", "quiet", "rapid", "royal", "rustic", "serene",
    "silent", "silver", "snowy", "solar", "spry", "stellar", "sunny", "swift", "tidy", "vivid",
    "warm", "wild", "wise", "witty", "young", "zesty",
)
NOUNS = (
    "aurora", "badger", "bay", "breeze", "brook", "canyon", "cedar", "cloud", "comet", "coral",
    "crane", "dawn", "delta", "dune", "eagle", "ember", "falcon", "fern", "field", "fjord",
    "forest", "fox", "galaxy", "glacier", "grove", "harbor", "hawk", "heron", "hill", "lake",
    "lark", "leaf", "meadow", "mesa", "moon", "mountain", "nebula", "oak", "ocean", "orbit",
    "otter", "pine", "planet", "pond", "prairie", "rain", "river", "sea", "shadow", "sky",
    "snow", "sparrow", "star", "stone", "sun", "thunder", "tide", "valley", "wave", "willow",
    "wind", "wolf",
)


def generate_name(ordinal: int) -> str:
    return f"{secrets.choice(ADJECTIVES)}-{secrets.choice(NOUNS)}-{int(ordinal)}"
