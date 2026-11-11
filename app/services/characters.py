"""Characters as the screens use them: access, sheets, and the gamemaster's list.

Access rule, enforced here so no route can forget it: a **player** sees and plays only
their own characters; the **gamemaster** sees and plays all of them. Asking for a
character you may not see is indistinguishable from asking for one that does not exist,
so a player cannot probe for other players' characters.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models.gamestate import Role, SourceSet
from app.models.pc import DraftStatus, PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry
from app.services.auth import Identity
from app.services.experience import award
from app.services.play import Actor, PlayError, PlayService
from app.services.protocol import ProtocolService, for_character, roll_record
from app.services.sheet import Sheet, build_sheet, heal_at
from app.store.gamestate import GameStateStore


class CharacterNotFound(LookupError):
    """No such character — or not one this user may see."""


@dataclass(frozen=True)
class CharacterSummary:
    """A row in a character list."""

    character: PlayerCharacter
    owner: str | None
    file_error: str | None


@dataclass(frozen=True)
class PartyMember:
    """A character as the gamemaster's overview shows it."""

    sheet: Sheet
    owner: str | None
    world: str | None


def _actor(identity: Identity) -> Actor:
    """Who pressed the button, as the log names them."""
    user = identity.user
    return Actor(user.display_name or user.username, username=user.username)


class CharacterService:
    """Reading, playing and listing characters on behalf of a logged-in user."""

    def __init__(
        self,
        store: GameStateStore,
        compendium: Any | None,
        library_dir: Path,
        protocol: ProtocolService | None = None,
        fight_of: Callable[[str], tuple[str, int | None, str | None] | None] | None = None,
    ) -> None:
        """Bind to the library, the loaded compendium, the library root and the log;
        ``fight_of(character_id)`` names the running fight a character is in."""
        self._store = store
        self._compendium = compendium
        self._library_dir = library_dir
        self._protocol = protocol
        self.play = PlayService(store.characters, protocol=protocol, fight_of=fight_of)

    # -- access ------------------------------------------------------------------

    def get_for(self, identity: Identity, character_id: str) -> PlayerCharacter:
        """The character, if ``identity`` may see it; else :class:`CharacterNotFound`."""
        character = self._store.characters.find(character_id)
        if character is None:
            raise CharacterNotFound(character_id)
        if identity.role is Role.GAMEMASTER or character.belongs_to(identity.user.username):
            return character
        raise CharacterNotFound(character_id)

    def get_any(self, character_id: str) -> PlayerCharacter:
        """The character regardless of who asks — for gamemaster-only routes."""
        character = self._store.characters.find(character_id)
        if character is None:
            raise CharacterNotFound(character_id)
        return character

    def for_player(self, identity: Identity) -> list[PlayerCharacter]:
        """A player's own characters, most recently changed first."""
        mine = self._store.characters.list(lambda pc: pc.belongs_to(identity.user.username))
        return sorted(mine, key=lambda pc: pc.updated_at, reverse=True)

    # -- sheets ------------------------------------------------------------------

    def source_set(self, character: PlayerCharacter) -> SourceSet | None:
        """The books in play: the character's world's declaration, if it has a world."""
        if character.world_id is None:
            return None
        world = self._store.worlds.find(character.world_id)
        return world.source_set if world is not None else None

    def resolver(self) -> Any:
        """A resolver over the loaded compendium, for rendering a character's text."""
        return self._compendium.resolver() if self._compendium is not None else None

    def sheet(self, character: PlayerCharacter) -> Sheet:
        """The derived sheet, with the file's revision and any error reading it."""
        store = self._store.characters
        sheet = build_sheet(
            character,
            self._compendium,
            source_set=self.source_set(character),
            revision=store.revision(character.id),
            file_error=store.error(character.id),
        )
        if self._protocol is not None:
            sheet.last_roll = self._protocol.newest(
                f"character:{character.id}", character.world_id, rolled=True
            )
        return sheet

    def record(self, entry: ProtocolEntry) -> None:
        """Put an entry in the log (XP, a level, the wizard's rolls); none without one."""
        if self._protocol is not None:
            self._protocol.append([entry])

    def newest_roll(
        self,
        character: PlayerCharacter,
        kinds: Iterable[EntryKind] | None = None,
        *,
        since: datetime | None = None,
    ) -> ProtocolEntry | None:
        """The character's newest roll made on its sheet, of these kinds, after ``since``
        — the tracker takes an initiative, a player's save or attack from it."""
        if self._protocol is None:
            return None
        return self._protocol.newest(
            f"character:{character.id}",
            character.world_id,
            kinds=kinds,
            origin="sheet",
            since=since,
            rolled=True,
        )

    def rolled_since(self, character: PlayerCharacter, moment: datetime) -> list[ProtocolEntry]:
        """The rolls logged about the character after ``moment``, newest first — what
        an action just rolled, for its sound."""
        if self._protocol is None:
            return []
        found = self._protocol.since(f"character:{character.id}", character.world_id, moment)
        return [e for e in found if e.roll is not None]

    def act(
        self, identity: Identity, character_id: str, action: str, params: dict[str, str]
    ) -> PlayerCharacter:
        """Apply a sheet action as ``identity``. Rules refusals raise ``PlayError``."""
        character = self.get_for(identity, character_id)
        actor = _actor(identity)
        return self.play.act(character_id, action, params, self.sheet(character), actor)

    def file_label(self, character_id: str) -> str | None:
        """Where the file lives, relative to the library — for the gamemaster."""
        path = self._store.characters.path(character_id)
        if path is None:
            return None
        try:
            return str(path.resolve().relative_to(self._library_dir.resolve()))
        except ValueError:
            return str(path)

    # -- the gamemaster's party overview -----------------------------------------

    def party(self, world_id: str | None = None) -> list[PartyMember]:
        """Every character (in ``world_id``, if given) with its sheet, by name."""
        users = {
            u.username.casefold(): (u.display_name or u.username) for u in self._store.users.list()
        }
        worlds = {w.id: w.name for w in self._store.worlds.list()}
        members = []
        for pc in self._store.characters.list():
            if world_id is not None and pc.world_id != world_id:
                continue
            members.append(
                PartyMember(
                    self.sheet(pc),
                    self._owner_label(pc.player, users),
                    worlds.get(pc.world_id) if pc.world_id else None,
                )
            )
        return sorted(members, key=lambda m: m.sheet.character.name.casefold())

    def in_world(self, world_id: str) -> list[PlayerCharacter]:
        """The characters placed in ``world_id``, drafts too, by name."""
        found = [pc for pc in self._store.characters.list() if pc.world_id == world_id]
        return sorted(found, key=lambda pc: pc.name.casefold())

    def named(self, world_id: str, name: str) -> PlayerCharacter | None:
        """The character in ``world_id`` called ``name``, case aside: ``{@character}``."""
        wanted = name.strip().casefold()
        return next((pc for pc in self.in_world(world_id) if pc.name.casefold() == wanted), None)

    def party_member(self, character_id: str) -> PartyMember:
        """One character's card data, or :class:`CharacterNotFound`."""
        pc = self._store.characters.find(character_id)
        if pc is None:
            raise CharacterNotFound(character_id)
        users = {
            u.username.casefold(): (u.display_name or u.username) for u in self._store.users.list()
        }
        world = self._store.worlds.find(pc.world_id) if pc.world_id else None
        return PartyMember(
            self.sheet(pc), self._owner_label(pc.player, users), world.name if world else None
        )

    # -- the gamemaster's list ---------------------------------------------------

    def summaries(self) -> list[CharacterSummary]:
        """Every character with its owner's name, by name."""
        users = {
            u.username.casefold(): (u.display_name or u.username) for u in self._store.users.list()
        }
        store = self._store.characters
        rows = [
            CharacterSummary(pc, self._owner_label(pc.player, users), store.error(pc.id))
            for pc in store.list()
        ]
        return sorted(rows, key=lambda row: row.character.name.casefold())

    @property
    def unreadable_files(self) -> dict[str, str]:
        """Character files that could not be read at all, keyed by filename stem."""
        known = {pc.id for pc in self._store.characters.list()}
        return {k: v for k, v in self._store.characters.errors.items() if k not in known}

    @staticmethod
    def _owner_label(player: str | None, users: dict[str, str]) -> str | None:
        if not player:
            return None
        # A name with no account yet is shown as written, so the gamemaster sees whom
        # the file expects.
        return users.get(player.casefold(), f"@{player}")

    def adopt_legacy_owners(self) -> int:
        """Rewrite owners stored as account ids (format 1's ``player_id``) to usernames.

        Returns how many files were rewritten. Run on load, where accounts are known.
        """
        users = self._store.users.list()
        by_id = {u.id: u.username for u in users}
        names = {u.username.casefold() for u in users}
        changed = 0
        for pc in self._store.characters.list():
            if pc.player and pc.player.casefold() not in names and pc.player in by_id:
                self._store.characters.update(pc.id, player=by_id[pc.player])
                changed += 1
        return changed

    def heal_with_spell(
        self,
        identity: Identity,
        caster_id: str,
        reference: str,
        level: int,
        target_ids: Sequence[str],
        *,
        free: bool = False,
    ) -> int:
        """Cast a healing spell on characters of the caster's world — one, or up to the
        spell's number (Mass Cure Wounds: six): the slot is spent on the caster, the dice
        rolled once, each target heals that much. Returns the hit points."""
        caster = self.get_for(identity, caster_id)
        sheet = self.sheet(caster)
        allowed = {cid for cid, _ in self.allies(caster)}
        spell = sheet.spell(reference)
        chosen = list(dict.fromkeys(target_ids))
        targets = [self._store.characters.find(t) for t in chosen]
        if (
            spell is None
            or not spell.heal
            or not chosen
            or len(chosen) > spell.heal_targets
            or any(t is None or t.id not in allowed for t in targets)
        ):
            raise PlayError("play.error.bad_input")
        name = identity.user.display_name or identity.user.username
        self.play.act(
            caster_id,
            "cast",
            {"spell": reference, "level": "free" if free else str(level)},
            sheet,
            _actor(identity),
        )
        expression = heal_at(spell, level or spell.level, sheet.modifier_for(spell)) or spell.heal
        rolled = self.play.roll(expression)
        for target in (t for t in targets if t is not None):
            self.play.act(
                target.id,
                "hp",
                {"mode": "heal", "amount": str(max(0, rolled.total))},
                self.sheet(target),
                _actor(identity),
            )
        names = ", ".join((t.name or "?") for t in targets if t is not None)
        self.record(
            for_character(
                caster,
                EntryKind.HEAL,
                actor=identity.user.username,
                actor_name=name,
                origin="sheet",
                what=f"heal:{spell.name}",
                target_name=names,
                roll=roll_record(rolled, rest="modifier"),
            )
        )
        return rolled.total

    def allies(self, character: PlayerCharacter) -> list[tuple[str, str]]:
        """Who a healing spell may reach: the characters of the caster's world (or, with no
        world, the same player's), the caster first."""

        def same(pc: PlayerCharacter) -> bool:
            if pc.status is DraftStatus.DRAFT and pc.wizard_step is not None:
                return False  # a character still in the wizard is no one to heal yet
            if character.world_id:
                return pc.world_id == character.world_id
            return pc.player is not None and pc.player == character.player

        others = sorted(
            (pc for pc in self._store.characters.list(same) if pc.id != character.id),
            key=lambda pc: pc.name.lower(),
        )
        return [(character.id, character.name or "?"), *((pc.id, pc.name or "?") for pc in others)]

    def award_xp(self, character_id: str, xp: int, reason: str, by: str) -> PlayerCharacter:
        """Add experience points (documentation/COMBAT.md §6); untracked XP starts now.
        The award goes in the log, with its reason as written."""

        def change(record: PlayerCharacter) -> None:
            award(record, xp)

        changed = self._store.characters.mutate(character_id, change)
        self.record(
            for_character(
                changed, EntryKind.EXPERIENCE, actor_name=by, amount=max(0, xp), note=reason
            )
        )
        return changed

    def create(self, name: str, player: str | None) -> PlayerCharacter:
        """A new, empty character file, ready to be filled in by hand or by the wizard."""
        character = PlayerCharacter(
            name=name.strip(),
            player=self._player_or_none(player),
            status=DraftStatus.DRAFT,
        )
        return self._store.characters.create(character)

    def assign(self, character_id: str, player: str | None) -> PlayerCharacter:
        """Hand a character to a player, or take it back (``None``)."""
        if character_id not in self._store.characters:
            raise CharacterNotFound(character_id)
        return self._store.characters.update(character_id, player=self._player_or_none(player))

    def delete_for(self, identity: Identity, character_id: str) -> str:
        """Delete a character ``identity`` may see — a player their own, the gamemaster
        any — and return the name it had. Its file is removed; there is no undo."""
        character = self.get_for(identity, character_id)
        self._store.characters.delete(character_id)
        return character.name or ""

    def _player_or_none(self, username: str | None) -> str | None:
        """The player's username as the account spells it, or None if not a player."""
        if not username:
            return None
        wanted = username.strip().casefold()
        for user in self._store.users.list():
            if user.username.casefold() == wanted and user.role is Role.PLAYER:
                return user.username
        return None


__all__ = ["CharacterNotFound", "CharacterService", "CharacterSummary", "PartyMember"]
