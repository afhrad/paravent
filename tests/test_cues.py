"""Sound cues on the page (app/ui/cues.py): what a player hears, what the gamemaster hears,
and the markers on the elements that bring them."""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.gamestate import CheckCall, Role, User
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.auth import Identity
from app.services.i18n import UiCatalogue
from app.services.protocol_view import Shown
from app.services.safety import XCARD
from app.ui.check_pages import check_banner
from app.ui.context import Page
from app.ui.cues import cue
from app.ui.protocol_pages import toast_cards
from app.ui.shop_pages import sheet_shop_link


def _page(role: Role | None) -> Page:
    t = UiCatalogue().translator("en")
    if role is None:
        return Page(t)
    user = User(username="mara" if role is Role.PLAYER else "dm", password_hash="x", role=role)
    return Page(t, identity=Identity(user=user, session=None))  # type: ignore[arg-type]


PLAYER, GM = _page(Role.PLAYER), _page(Role.GAMEMASTER)


def test_who_hears_what() -> None:
    assert cue(PLAYER, "turn", "f1:2:0") == "turn|turn:f1:2:0"
    assert cue(PLAYER, "handout", "e1") == "handout|handout:e1"
    assert cue(PLAYER, "xcard", "e1") is None  # the X-card is the gamemaster's
    assert cue(GM, "turn", "f1:2:0") is None  # a sheet open on the gamemaster's screen
    assert cue(GM, "xcard", "e1") == "xcard|xcard:e1"
    assert cue(PLAYER, "fanfare", "x") is None
    assert cue(_page(None), "turn", "x") is None


def test_a_check_called_rings_until_rolled() -> None:
    call = CheckCall(kind="skill", subject="Perception", characters=["pc1"], by="dm")
    asked = str(check_banner(PLAYER, call, "pc1", None))
    assert f'data-cue="check|check:{call.id}:pc1"' in asked
    rolled = call.model_copy(update={"results": {"pc1": 14}})
    assert "data-cue" not in str(check_banner(PLAYER, rolled, "pc1", None))
    assert "data-cue" not in str(check_banner(GM, call, "pc1", None))


def test_a_handout_toast_and_the_x_card() -> None:
    handout = ProtocolEntry(
        kind=EntryKind.HANDOUT,
        what="h1",
        subject="character:pc1",
        subject_name="Lyra",
        scope=Scope(world="w1"),
        at=datetime.now(UTC),
    )
    assert f'data-cue="handout|handout:{handout.id}"' in str(toast_cards(PLAYER, [Shown(handout)]))
    xcard = ProtocolEntry(
        kind=EntryKind.CUSTOM, what=XCARD, visibility=Visibility.GM, scope=Scope(world="w1")
    )
    assert f'data-cue="xcard|xcard:{xcard.id}"' in str(toast_cards(GM, [Shown(xcard)]))
    assert "data-cue" not in str(toast_cards(PLAYER, [Shown(xcard)]))


def test_a_shop_opening_rings_once_per_shop() -> None:
    assert 'data-cue="bell|bell:shop:pc1:The Anvil"' in str(
        sheet_shop_link(PLAYER, "pc1", "The Anvil")
    )
