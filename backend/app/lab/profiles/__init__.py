"""The three profiles. Registry order is display order."""
from __future__ import annotations

from app.lab.profiles.base import Decision, Param, Profile, Variant
from app.lab.profiles.flambeur import Flambeur
from app.lab.profiles.matheux import Matheux
from app.lab.profiles.stratege import Stratege

PROFILES: dict[str, Profile] = {p.key: p for p in (Flambeur(), Matheux(), Stratege())}


def get_profile(key: str) -> Profile:
    try:
        return PROFILES[key]
    except KeyError:
        raise ValueError(f"profil inconnu : {key}") from None


__all__ = ["PROFILES", "get_profile", "Decision", "Param", "Profile", "Variant"]
