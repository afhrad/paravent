"""The character creation wizard's engine (documentation/CHARGEN-WIZARD.md §8 step 1).

* :mod:`.catalogue` — what the world's books offer.
* :mod:`.blocks` — one reader for the corpus's choice blocks.
* :mod:`.prereq` — prerequisites, checked or returned for confirmation.
* :mod:`.rules` — the ability-score methods.
* :mod:`.plan` — the steps of a draft, their questions, and applying the answers.
"""

from app.services.chargen.blocks import Grant, Pick
from app.services.chargen.catalogue import Catalogue, Edition, Offer
from app.services.chargen.plan import ORDERS, LevelPlan, Plan, Planner, Problem, Step, StepId
from app.services.chargen.prereq import Context, Verdict

__all__ = [
    "ORDERS",
    "Catalogue",
    "Context",
    "Edition",
    "Grant",
    "LevelPlan",
    "Offer",
    "Pick",
    "Plan",
    "Planner",
    "Problem",
    "Step",
    "StepId",
    "Verdict",
]
