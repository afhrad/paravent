"""The application's store instances and their lifecycle.

FastAPI's dependency injection is the access path — CLAUDE.md rules out global mutable
state, so nothing here is imported directly by a route. The registry is created once at
startup, attached to the app, and handed to routes through
:func:`app.dependencies.get_stores`.

The registry is also where the two-root rule (documentation/TECHSTACK.md §3) is **enforced** rather
than merely documented: see :meth:`StoreRegistry.assert_reference_readonly`.
"""

from __future__ import annotations

import logging
from functools import cached_property
from pathlib import Path
from typing import Any

from app import __version__
from app.config import Settings
from app.models.gamestate import Combat, Combatant
from app.models.protocol import EntryKind, ProtocolEntry
from app.services import media
from app.services.adventures import AdventureService
from app.services.auth import AuthService, Identity
from app.services.backlinks import BacklinkIndex
from app.services.backup import BackupService
from app.services.characters import CharacterService
from app.services.chargen.drafts import Drafts
from app.services.chargen.levelup import LevelUps
from app.services.chargen.prepare import Preparations
from app.services.combat import CombatService, SheetCharacters
from app.services.corpus_check import CorpusHealth, corpus_version
from app.services.corpus_import import CorpusImport, CorpusImports
from app.services.creature_builder import (
    HOMEBREW_SOURCE,
    CreatureMath,
    CreatureService,
    Wording,
)
from app.services.creature_pictures import CreaturePictures, design_ref, slug
from app.services.creature_sight import Sight, fluff_image, sight
from app.services.difficulty import DifficultyRules
from app.services.encounters import EncounterService
from app.services.events import COMBATS, PARTY, PROSE, PROTOCOL, ChangeHub, combat_key
from app.services.glossary import Glossary, LinkCounts
from app.services.handouts import HandoutService
from app.services.i18n import FALLBACK, UiCatalogue
from app.services.image_effects import ImageEffects
from app.services.item_names import ItemNames
from app.services.knowledge import KnowledgeService
from app.services.locations import LocationService
from app.services.machine_translation import MachineTranslator
from app.services.mcp_tokens import McpTokenService
from app.services.name_suggestions import ApiRunner, NameSuggestions
from app.services.naming import Naming
from app.services.naming_workshop import NamingWorkshop
from app.services.npcs import NpcService, TreasureService
from app.services.places import PlaceService
from app.services.player_view import Reader, needs_reader, reader_for
from app.services.players import PlayerService
from app.services.protocol import ProtocolService
from app.services.protocol_migration import copy_old_logs
from app.services.protocol_view import ProtocolReader, Viewer
from app.services.ratelimit import LoginThrottle
from app.services.references import ReferenceEntry, ReferenceService
from app.services.revisions import Revisions
from app.services.search import SearchService
from app.services.session_notes import SessionNoteService
from app.services.sheet import CONDITIONS
from app.services.sound import SoundDesk, SoundLibrary
from app.services.style_drafts import ApiAgent, StyleDrafts
from app.services.themes import ThemeLibrary, ThemeService
from app.services.tokens import Token, Tokens
from app.services.translation_coverage import TranslationCoverage, TranslationJobs
from app.services.translation_targets import FLUFF_OF, TranslationTargets, fluff_category
from app.services.translations import TranslationStore, record_key
from app.services.world_instructions import WorldInstructionService
from app.services.worlds import BookCatalog, WorldService
from app.store.compendium import CompendiumStore, LoadReport
from app.store.creature_tables import CreatureTables
from app.store.encounter_tables import EncounterTables
from app.store.errors import EntryNotFound
from app.store.fluff import FluffStore
from app.store.gamestate import GameStateStore
from app.store.lookups import LookupTables
from app.store.loot import LootTables
from app.store.maps import MapIndex
from app.store.npc_tables import NpcTables
from app.store.projections import MonsterProjection
from app.store.protocol import ProtocolStore, log_key
from app.store.published import PublishedAdventures, PublishedBooks, ReferenceScreens
from app.store.snapshot import Snapshot, fingerprint
from app.store.watcher import JsonWatcher

logger = logging.getLogger(__name__)


class ReferenceRootViolation(RuntimeError):
    """Raised when a store would write inside the read-only reference root.

    This is a programming error, not a user error: the reference corpus is somebody
    else's data, replaced wholesale on re-import. Anything that needs persisting is
    either library data or cache data.
    """


# Language codes the machine translator is told the name of; others go by their code.
LANGUAGE_NAMES = {"de": "German", "fr": "French", "es": "Spanish", "it": "Italian", "nl": "Dutch"}


class StoreRegistry:
    """Owns the compendium and library stores and keeps them in sync with disk."""

    def __init__(self, settings: Settings) -> None:
        """Build the stores described by ``settings`` without touching the disk yet."""
        self.settings = settings
        self.compendium = CompendiumStore(strict=settings.strict_validation)
        self.fluff = FluffStore(strict=settings.strict_validation)
        self.gamestate = GameStateStore(settings.library_dir)
        self.lookups = LookupTables()
        # Every character change, from any source, reaches the screens showing it.
        self.events = ChangeHub()
        self.gamestate.characters.subscribe(self._character_changed)
        self.gamestate.prose.subscribe(self._prose_changed)
        self._books: BookCatalog | None = None
        self._subclass_names: dict[tuple[str, str], str] | None = None
        self._watcher: JsonWatcher | None = None

    @cached_property
    def sounds(self) -> SoundLibrary:
        """The sound desk's files and scenes (documentation/SOUND.md)."""
        return SoundLibrary(
            self.gamestate.sounds,
            self.gamestate.sound_scenes,
            self.settings.library_dir / "sounds",
        )

    @cached_property
    def sound_desk(self) -> SoundDesk:
        """What should be sounding now."""
        return SoundDesk(self.sounds, self.events)

    @cached_property
    def auth(self) -> AuthService:
        """Logins and sessions over the library's user and session collections.

        Built on first use rather than in ``__init__``: it may generate the signing key,
        which writes into the library, and constructing a registry must not touch disk.
        """
        return AuthService(self.gamestate, self.settings)

    @cached_property
    def knowledge(self) -> KnowledgeService:
        """What each world's party learned about kinds of creatures (COMBAT.md §5)."""

        def combat(combat_id: str) -> Combat | None:
            return self.gamestate.combats.find(combat_id)

        def statblock(combatant: Combatant) -> dict[str, Any] | None:
            monster = self.combats.monster_of(combatant)
            if monster is None:
                return None
            data: dict[str, Any] = monster.entry.model_dump(by_alias=True, exclude_none=True)
            return data

        def entry(world_id: str | None, entry_id: str) -> ProtocolEntry | None:
            found = self.protocol.store.newest(log_key(world_id), 1, lambda e: e.id == entry_id)
            return found[0] if found else None

        return KnowledgeService(self.gamestate, combat, statblock, entry)

    @cached_property
    def corpus_health(self) -> CorpusHealth:
        """The corpus check (``/gm/corpus``), run against the stores loaded here."""
        reference = self.settings.reference_dir if self.settings.reference_configured else None
        return CorpusHealth(
            reference, self.settings.homebrew_dir, self.settings.reference_cache_dir, self
        )

    @cached_property
    def backups(self) -> BackupService:
        """Backups of the library into ``PARAVENT_BACKUP_DIR`` (never the reference)."""
        settings = self.settings
        return BackupService(
            settings.library_dir,
            settings.backup_dir if settings.backup_configured else None,
            settings.backup_keep,
            settings.backup_at,
        )

    @cached_property
    def handouts(self) -> HandoutService:
        """What the gamemaster handed to characters (documentation/JOURNAL.md)."""
        return HandoutService(
            self.gamestate,
            self.adventures,
            self.characters,
            self.protocol,
            self.settings.library_dir / "handouts",
        )

    @cached_property
    def image_effects(self) -> ImageEffects:
        """Pictures rendered with their effects, kept in the cache (ADVENTURES.md §5.5)."""
        return ImageEffects(self.settings.reference_cache_dir / "images")

    @cached_property
    def themes(self) -> ThemeService:
        """The table's theme and each reader's (documentation/THEMES.md)."""
        return ThemeService(self.gamestate, ThemeLibrary(self.settings.library_dir / "themes"))

    @cached_property
    def places(self) -> PlaceService:
        """Where each user left off, so logging in again carries on from there."""
        return PlaceService(self.gamestate, self.auth)

    @cached_property
    def locations(self) -> LocationService:
        """The campaign's places, ``{@place}`` (ADVENTURES.md §4)."""
        return LocationService(self.gamestate, self.adventures)

    @cached_property
    def instructions(self) -> WorldInstructionService:
        """What Claude is told about each world (MCP.md §5)."""
        return WorldInstructionService(self.gamestate)

    @cached_property
    def session_notes(self) -> SessionNoteService:
        """What happened at the table, day by day (ADVENTURES.md §4b)."""
        return SessionNoteService(self.gamestate)

    @cached_property
    def mcp_tokens(self) -> McpTokenService:
        """Keys Claude connects to the MCP server with (documentation/MCP.md §3)."""
        return McpTokenService(self.gamestate)

    @cached_property
    def login_throttle(self) -> LoginThrottle:
        """Failed logins slow down, per username and per address; kept in memory."""
        return LoginThrottle()

    @cached_property
    def glossary(self) -> Glossary:
        """Localized names for corpus entries, per language (TRANSLATION.md §4)."""
        return Glossary(self.settings.i18n_dir)

    @cached_property
    def creature_pictures(self) -> CreaturePictures:
        """The gamemaster's own pictures of creature kinds, per world."""
        return CreaturePictures(self.gamestate.prose, self.settings.media_dir)

    @cached_property
    def tokens(self) -> Tokens:
        """Round tokens for every element (documentation/TOKENS.md)."""
        return Tokens(
            self.gamestate.prose,
            self.settings.media_dir,
            lambda: self.settings.images_root,
            self._own_picture,
        )

    def _own_picture(self, kind: str, ident: str) -> Path | None:
        """An element's own picture, which its token is made from (documentation/TOKENS.md):
        an NPC's portrait, a place's picture, a builder creature's picture."""
        store = self.gamestate
        if kind == "npc":
            npc = store.npcs.find(ident)
            if npc is not None and npc.portrait:
                return media.resolve(store.prose, npc.scope.record_id, npc.portrait)
        elif kind == "place":
            place = store.locations.find(ident)
            if place is not None and place.picture:
                return media.resolve(store.prose, place.scope.record_id, place.picture)
        elif kind == "creature":
            design = store.creatures.find(ident)
            if design is not None:
                return self.creature_pictures.find(design.world_id, design_ref(design.name))
        return None

    def set_own_picture(self, kind: str, ident: str, filename: str, data: bytes) -> bool:
        """Make an upload the element's own picture (the token follows); False for a kind
        without one. Raises :class:`~app.services.media.MediaError`."""
        if kind == "npc":
            self.npcs.upload_portrait(ident, filename, data)
        elif kind == "place":
            self.locations.upload_picture(ident, filename, data)
        elif kind == "creature":
            design = self.creatures.get(ident)
            self.creature_pictures.set(design.world_id, design_ref(design.name), filename, data)
        else:
            return False
        return True

    def _combatant_token(self, combatant: Combatant, world_id: str | None) -> Token:
        """A combatant's token: its character's, its creature's, or a creature disc."""
        if combatant.character_id:
            pc = self.gamestate.characters.find(combatant.character_id)
            return self.tokens.of_record(
                "character", pc.world_id if pc else None, combatant.character_id
            )
        if combatant.ref:
            name, _, source = combatant.ref.partition("|")
            found = self.encounters.monster(name, source)
            extra = (getattr(found.entry, "model_extra", None) or {}) if found else {}
            token = extra.get("token")
            return self.monster_token(name, source, world_id, token)
        return Token("creature")

    def monster_token(
        self, name: str, source: str, world_id: str | None = None, token: object = None
    ) -> Token:
        """A creature's token: a builder creature's own upload, else 5eTools's, else the
        creature disc."""
        if source == HOMEBREW_SOURCE:
            design = self.creatures.named(name, world_id) or self.creatures.named(name)
            if design is not None:
                own = self.tokens.own(design.world_id, "creature", design.id)
                if own is not None:
                    return own
        else:
            # The table's own picture of a book creature, set in the tracker, is its token.
            ref = f"{name}|{source}"
            picture = self.creature_pictures.find(world_id, ref, shared=False)
            ident = f"book-{slug(ref)}"
            made = (
                self.tokens.from_picture(world_id, "creature", ident, picture)
                if picture is not None
                else None
            )
            if made is not None:
                return self.tokens.served(world_id, "creature", ident, made)
        return self.tokens.of_entry("monster", name, source, token)

    @cached_property
    def name_suggestions(self) -> NameSuggestions:
        """Suggested names for the glossary's open entries, through the Claude API with
        the machine translation's key; off without one."""
        settings = self.settings
        runner = (
            ApiRunner(
                api_key=settings.translate_api_key,
                model=settings.translate_model,
                effort=settings.translate_effort,
            )
            if settings.translate_api_key
            else None
        )
        english = self.ui.translator(FALLBACK)

        def kind_name(category: str) -> str:
            label = english(f"glossary.category.{category}")
            return category if label.startswith("glossary.") else label

        def source_of(category: str, name: str) -> str | None:
            found = self.compendium.find(category, name)
            source = getattr(found, "source", None) if found is not None else None
            return str(source) if source else None

        return NameSuggestions(
            self.settings.i18n_dir,
            self.glossary,
            runner,
            language_name=lambda code: LANGUAGE_NAMES.get(code, code),
            kind_name=kind_name,
            source_of=source_of,
        )

    @cached_property
    def translations(self) -> TranslationStore:
        """Translated corpus text, per language, over the English (TRANSLATION.md §5)."""
        return TranslationStore(self.settings.i18n_dir)

    @cached_property
    def machine_translator(self) -> MachineTranslator | None:
        """Machine-made drafts of translations (TRANSLATION.md §7); None until an API
        key is configured, since it sends corpus text to an external service."""
        if not self.settings.translate_api_key:
            return None
        return MachineTranslator(
            self.translations,
            self._glossary_terms,
            lambda code: LANGUAGE_NAMES.get(code, code),
            api_key=self.settings.translate_api_key,
            model=self.settings.translate_model,
            effort=self.settings.translate_effort,
        )

    def _glossary_terms(self, lang: str) -> dict[str, str]:
        """A language's names for corpus entries, English → localized, for the
        machine drafts: the glossary, plus conditions and skills from the UI catalogue."""
        terms = {
            english: localized
            for names in self.glossary.table(lang).values()
            for english, localized in names.values()
        }
        for key in CONDITIONS:
            own = self.ui.own(lang, f"condition.{key}")
            if isinstance(own, str):
                terms.setdefault(key.title(), own)
        return terms

    @cached_property
    def item_names(self) -> ItemNames:
        """Localized names for magic items made from variants ("Langschwert +1")."""
        return ItemNames(self.compendium)

    @cached_property
    def translation_coverage(self) -> TranslationCoverage:
        """How far each language covers the corpus (TRANSLATION.md §7)."""
        return TranslationCoverage(
            self.translations,
            self.translation_targets,
            self.compendium,
            self.link_counts,
            self.rulebooks,
            self.published,
            self.settings.i18n_dir,
            self.screens,
        )

    @cached_property
    def translation_jobs(self) -> TranslationJobs:
        """Machine drafts of many records, run in the background."""
        return TranslationJobs(
            self.machine_translator,
            self.translation_coverage,
            lambda: self.player_reader(None, as_player=True),
        )

    @cached_property
    def corpus_imports(self) -> CorpusImports:
        """Translated corpora imported from the translations page (TRANSLATION.md §7)."""
        return CorpusImports(
            lambda: CorpusImport(
                compendium=self.compendium,
                fluff=self.fluff,
                books=self.rulebooks,
                adventures=self.published,
                store=self.translations,
                glossary=self.glossary,
            ),
            reference_dir=self.settings.reference_dir
            if self.settings.reference_configured
            else None,
            library_dir=self.settings.library_dir,
        )

    @cached_property
    def translation_targets(self) -> TranslationTargets:
        """The English record behind each translatable page (TRANSLATION.md §7)."""
        return TranslationTargets(
            self.references,
            self.rulebooks,
            self.published,
            self.compendium,
            self.fluff,
            self.screens,
            self.creature_tables,
        )

    @cached_property
    def backlinks(self) -> BacklinkIndex:
        """What links to each entry: the corpus indexed once, the library read live."""
        from app.ui.tags import ENTITY_CATEGORY

        return BacklinkIndex(self.compendium, self.gamestate, ENTITY_CATEGORY, self.rulebooks)

    @cached_property
    def link_counts(self) -> LinkCounts:
        """How often the corpus links to each entry: what to name first."""
        from app.ui.tags import ENTITY_CATEGORY

        return LinkCounts(self.compendium, ENTITY_CATEGORY)

    @cached_property
    def ui(self) -> UiCatalogue:
        """UI string catalogues: shipped with the code, overridable from the library."""
        return UiCatalogue(self.settings.i18n_dir / "ui")

    @cached_property
    def protocol(self) -> ProtocolService:
        """The log (documentation/PROTOCOL.md): append only, one file per world. Every
        append reaches the open pages' log buttons and panels."""
        store = ProtocolStore(self.settings.library_dir / "protocol")
        store.subscribe(self._logged)
        return ProtocolService(store)

    def _logged(self, key: str, entry: ProtocolEntry) -> None:
        """Every append: the open pages' log buttons and panels hear of it; a character's
        party card shows its newest roll; an initiative rolled on a sheet lands in the
        character's running fight (COMBAT.md §4)."""
        self.events.publish(PROTOCOL, f"{key} {entry.id}")
        self.knowledge.observe(entry)
        if not entry.subject.startswith("character:"):
            return
        character_id = entry.subject.removeprefix("character:")
        if entry.roll is not None:
            self.events.publish(PARTY, f"{character_id} {entry.id}")
        if entry.kind is EntryKind.INITIATIVE and entry.origin == "sheet":
            self.combats.take_initiative(character_id)

    def _fight_of(self, character_id: str) -> tuple[str, int | None, str | None] | None:
        """The running fight a character is in, its round and world: a roll on the sheet
        belongs to it in the log."""
        fights = self.combats.joined_by([character_id])
        if not fights:
            return None
        return fights[0].id, fights[0].round or None, fights[0].world_id

    @cached_property
    def protocol_reader(self) -> ProtocolReader:
        """The log as each viewer may read it (PROTOCOL.md §4)."""
        return ProtocolReader(self.protocol.store)

    def copy_old_logs(self) -> int:
        """Bring the characters' and fights' old logs into the log, once (PROTOCOL.md §8);
        safe on every start, since only what is older than the log is copied."""
        return copy_old_logs(
            self.protocol.store,
            self.gamestate.characters.list(),
            self.gamestate.combats.list(),
        )

    def viewer(self, identity: Identity) -> Viewer:
        """Who reads the log: the gamemaster, or a player with their characters' worlds."""
        user = identity.user
        if identity.is_gamemaster:
            return Viewer(user.username, gamemaster=True)
        mine = self.characters.for_player(identity)
        return Viewer(
            user.username,
            gamemaster=False,
            characters=frozenset(pc.id for pc in mine),
            worlds=frozenset(pc.world_id for pc in mine if pc.world_id),
        )

    @cached_property
    def characters(self) -> CharacterService:
        """Characters for the screens: access rules, sheets and play."""
        return CharacterService(
            self.gamestate,
            self.compendium,
            self.settings.library_dir,
            self.protocol,
            fight_of=self._fight_of,
        )

    @cached_property
    def naming(self) -> Naming:
        """Naming styles: the books', the starters and the table's own."""
        return Naming(self.npc_tables, self.gamestate.naming_styles.list)

    def naming_for(self, world_id: str | None) -> Naming:
        """The styles a world rolls names from (NAMES.md §5b); every style for no world
        or a world that chose none."""
        world = self.gamestate.worlds.find(world_id) if world_id else None
        return self.naming.within(world.naming_styles) if world else self.naming

    @cached_property
    def naming_workshop(self) -> NamingWorkshop:
        """The table's own styles, and which style each world uses."""
        return NamingWorkshop(self.gamestate, self.naming)

    @cached_property
    def style_drafts(self) -> StyleDrafts:
        """Claude's drafts of naming styles for a world's peoples, through the Claude API
        with the machine translation's key; off without one."""
        settings = self.settings
        agent = (
            ApiAgent(api_key=settings.translate_api_key, model=settings.translate_model)
            if settings.translate_api_key
            else None
        )
        return StyleDrafts(self.naming_workshop, self.naming, agent)

    @cached_property
    def drafts(self) -> Drafts:
        """Characters being made in the wizard, over the world's books."""
        return Drafts(
            self.gamestate,
            self.compendium,
            self.lookups,
            self.characters,
            life=self.npc_tables,
            fluff=self.fluff,
            images=self.settings.images_root,
            naming=self.naming,
            workshop=self.naming_workshop,
        )

    @cached_property
    def levelups(self) -> LevelUps:
        """Levels the gamemaster grants and players take."""
        return LevelUps(self.drafts, self.gamestate)

    @cached_property
    def preparations(self) -> Preparations:
        """Spells prepared after a long rest."""
        return Preparations(self.drafts, self.gamestate)

    def _character_changed(self, character_id: str, revision: str | None) -> None:
        """Tell the character's own sheets, and the party overview, about a change."""
        self.events.publish(character_id, revision)
        self.events.publish(PARTY, f"{character_id} {revision or ''}")
        # A tracker shows the character's hit points and conditions too.
        for combat in self.gamestate.combats.list(
            lambda c: c.running and any(m.character_id == character_id for m in c.combatants)
        ):
            self.events.publish(combat.id, f"{combat.updated_at.isoformat()}|{revision or ''}")

    def _combat_changed(self, combat: Combat | None, combat_id: str) -> None:
        """Tell the trackers and player views of a combat, and the lists of combats."""
        revision = combat.updated_at.isoformat() if combat is not None else None
        self.events.publish(combat_id, revision)
        world = combat.world_id if combat is not None and combat.world_id else "-"
        self.events.publish(COMBATS, f"{world} {combat_id} {revision or ''}")
        # The party overview shows the running fights, each character's sheet its own.
        self.events.publish(PARTY, f"{COMBATS} {combat_id} {revision or ''}")
        for member in combat.combatants if combat is not None else ():
            if member.character_id is not None:
                self.events.publish(combat_key(member.character_id), revision)

    def _prose_changed(self, kind: str, record_id: str, revision: str | None) -> None:
        """Tell the pages showing a world, adventure or chapter, and the lists of them."""
        self.events.publish(record_id, revision)
        self.events.publish(PROSE, f"{kind} {record_id} {revision or ''}")

    @cached_property
    def references(self) -> ReferenceService:
        """Reference entries for the reference page."""
        return ReferenceService(
            self.compendium, self.gamestate, self.rulebooks, token=self._entry_token
        )

    def _entry_token(self, category: str, data: dict[str, Any], world_id: str | None) -> Token:
        """A reference entry's token: a creature's as the tracker shows it, else 5eTools's
        for a vehicle or object, else its kind's disc."""
        name, source = str(data.get("name", "")), str(data.get("source", ""))
        if category == "monster":
            return self.monster_token(name, source, world_id, data.get("token"))
        return self.tokens.of_entry(category, name, source, data.get("token"))

    @cached_property
    def search(self) -> SearchService:
        """One search over every category; follows compendium reloads on its own."""
        return SearchService(self.compendium, self.rulebooks, self.maps, self.published)

    @cached_property
    def encounters(self) -> EncounterService:
        """Encounters the gamemaster plans, rated against the compendium. The 2014
        guide's difficulty tables are read from the corpus (app/store/rulestables.py)."""
        reference = self.settings.reference_dir if self.settings.reference_configured else None
        return EncounterService(
            self.gamestate,
            self.compendium,
            DifficultyRules(reference=reference),
            renamed=lambda world_id, old, new, within: self.adventures.rewrite_references(
                world_id, "encounter", old, new, within
            ),
            unpublished=lambda name, source: (
                self.creatures.statblock_named(name) if source == HOMEBREW_SOURCE else None
            ),
        )

    @cached_property
    def combats(self) -> CombatService:
        """Fights run from encounters; characters changed through their sheets' rules."""
        return CombatService(
            self.gamestate,
            self.encounters.monster,
            SheetCharacters(self.characters),
            changed=self._combat_changed,
            legendary_group=self.references.legendary_group,
            rules=self.encounters.difficulty,
            protocol=self.protocol,
            sight=self._sight,
            known=lambda world_id, ref: self.knowledge.of(world_id, ref),
            spell=self._spell_data,
            token=self._combatant_token,
        )

    def _spell_data(
        self, name: str, source: str | None, world_id: str | None
    ) -> dict[str, Any] | None:
        """A spell's record as the world's books resolve it (TECHSTACK.md §6c)."""
        found = self.compendium.resolver().resolve_parts(
            "spell", name, source, self.references.source_set(world_id)
        )
        if found.entry is None:
            return None
        data: dict[str, Any] = found.entry.model_dump(by_alias=True, exclude_none=True)
        return data

    def _sight(self, monster: MonsterProjection, lang: str = "en") -> Sight:
        """What anyone sees of a creature: its statblock, fluff and picture — the fluff
        in ``lang`` where it is translated, passage by passage."""
        index = self.compendium.index

        def item_name(name: str) -> str | None:
            found = index.candidates("baseitem", name) or index.candidates("item", name)
            return str(found[0].name) if found else None

        data = monster.entry.model_dump(by_alias=True, exclude_none=True)
        fluff = self.fluff.for_entry("monster", monster.entry)
        if fluff is not None and lang != "en":
            shown = self.translations.overlay(
                lang,
                fluff_category("monster"),
                record_key(str(monster.entry.name), str(monster.entry.source)),
                {"entries": list(fluff.entries)},
                ("entries",),
            )
            fluff = fluff.model_copy(update={"entries": shown.data["entries"]})
        return sight(data, fluff, item_name, self.settings.images_root)

    def description(self, entry: ReferenceEntry) -> tuple[list[Any], str | None] | None:
        """What the books say about an entry and its picture, or None when they say
        nothing: the text in English, for the page to overlay; the picture's path."""
        fluff = self.fluff.get(
            FLUFF_OF.get(entry.category, entry.category), entry.name, entry.source
        )
        if fluff is None:
            return None
        image = fluff_image(fluff, self.settings.images_root)
        return (list(fluff.entries), image) if fluff.entries or image else None

    @cached_property
    def published(self) -> PublishedAdventures:
        """The corpus's published adventures: the index, and bodies read on demand."""
        reference = self.settings.reference_dir if self.settings.reference_configured else None
        return PublishedAdventures(reference)

    @cached_property
    def encounter_tables(self) -> EncounterTables:
        """The corpus's random-encounter tables, read on first use."""
        reference = self.settings.reference_dir if self.settings.reference_configured else None
        return EncounterTables(reference)

    @cached_property
    def npc_tables(self) -> NpcTables:
        """The corpus's NPC tables — names, the guides' NPC details, trinkets."""
        reference = self.settings.reference_dir if self.settings.reference_configured else None
        return NpcTables(reference)

    @cached_property
    def loot(self) -> LootTables:
        """The corpus's treasure tables, read on first use."""
        reference = self.settings.reference_dir if self.settings.reference_configured else None
        return LootTables(reference)

    @cached_property
    def screens(self) -> ReferenceScreens:
        """The DM screen and the players' quick reference, read on first use."""
        reference = self.settings.reference_dir if self.settings.reference_configured else None
        return ReferenceScreens(reference)

    @cached_property
    def maps(self) -> MapIndex:
        """The maps printed in each adventure and rulebook, read on first use."""
        reference = self.settings.reference_dir if self.settings.reference_configured else None
        return MapIndex(reference)

    @cached_property
    def rulebooks(self) -> PublishedBooks:
        """The corpus's rulebooks: the index, and bodies read on demand."""
        reference = self.settings.reference_dir if self.settings.reference_configured else None
        return PublishedBooks(reference)

    @cached_property
    def revisions(self) -> Revisions:
        """Accepted adventures revised on a draft copy (documentation/MCP.md §9)."""
        return Revisions(self.gamestate, self.adventures)

    @cached_property
    def adventures(self) -> AdventureService:
        """Adventures and chapters inside worlds, as Markdown files."""
        return AdventureService(self.gamestate, self.published)

    @cached_property
    def npcs(self) -> NpcService:
        """NPCs placed in worlds, adventures and chapters."""
        return NpcService(
            self.gamestate,
            self.adventures,
            lambda name, source: self.compendium.projections.monster(name, source),
        )

    @cached_property
    def treasure(self) -> TreasureService:
        """Treasure placed in worlds, adventures and chapters."""
        return TreasureService(
            self.gamestate,
            self.adventures,
            lambda name, source: self.compendium.projections.item(name, source),
        )

    @cached_property
    def creature_tables(self) -> CreatureTables:
        """The creature builder's tables: stats by CR, monster features, ready-made text."""
        reference = self.settings.reference_dir if self.settings.reference_configured else None
        return CreatureTables(reference)

    @cached_property
    def creatures(self) -> CreatureService:
        """Creature-builder designs, published to homebrew."""

        def monster(name: str, source: str) -> dict[str, Any] | None:
            try:
                entry = self.compendium.get("monster", f"{name}|{source}")
            except EntryNotFound:
                return None
            dumped: dict[str, Any] = entry.model_dump(by_alias=True, exclude_none=True)
            return dumped

        skills = {
            str(getattr(s, "name", "")).lower(): str(getattr(s, "ability", "wis"))
            for s in self.compendium.all("skill")
        }
        return CreatureService(
            self.gamestate,
            CreatureMath(self.creature_tables, skills),
            self.creature_tables,
            self.settings.homebrew_dir,
            monster,
            self.reload_compendium,
        )

    def creature_wording(self, world_id: str | None, fallback: str) -> Wording:
        """The language and units the builder writes a creature's text in: its world's,
        or ``fallback`` (the reader's language) for a creature of no world."""
        world = self.worlds.find(world_id) if world_id else None
        language = world.language if world else fallback
        return Wording(self.ui.translator(language, self.glossary, self.translations))

    @cached_property
    def worlds(self) -> WorldService:
        """Worlds and their declared books."""
        return WorldService(self.gamestate)

    @property
    def books(self) -> BookCatalog:
        """The sources with loaded content, titled; rebuilt after a corpus reload."""
        if self._books is None:
            loaded: set[str] = set()
            for category in self.compendium.categories():
                loaded |= set(self.compendium.sources(category))
            reference = self.settings.reference_dir if self.settings.reference_configured else None
            self._books = BookCatalog(reference, loaded)
        return self._books

    def player_reader(self, identity: Identity | None, *, as_player: bool = False) -> Reader | None:
        """What ``identity`` may read (documentation/PLAYER-VIEW.md); None for the
        gamemaster, who reads everything — unless ``as_player`` asks for the view of a
        player of every world, with no character of their own."""
        if not as_player and not needs_reader(identity):
            return None
        mine = (
            self.gamestate.characters.list(lambda pc: pc.belongs_to(identity.user.username))
            if identity is not None and not as_player
            else []
        )
        groups = {b.code.upper(): b.group for b in self.books.books}
        if self._subclass_names is None:
            names: dict[tuple[str, str], str] = {}
            for entry in self.compendium.all("subclass"):
                data = entry.model_dump(by_alias=True, exclude_none=True)
                name = str(data.get("name", ""))
                key = (str(data.get("className", "")).lower(), name.lower())
                names[key] = str(data.get("shortName") or name).lower()
            self._subclass_names = names
        return reader_for(mine, self.gamestate.worlds.list(), groups, self._subclass_names)

    @cached_property
    def players(self) -> PlayerService:
        """The gamemaster's player-account management."""
        return PlayerService(self.gamestate, self.auth, self.ui)

    # -- roots -------------------------------------------------------------------

    @property
    def compendium_roots(self) -> list[Path]:
        """Where compendium data is read from, in precedence order.

        The reference corpus first, then homebrew, so that a homebrew entry overrides an
        imported one with the same ``(name, source)``. A corpus that has not been
        configured is simply absent from the list — the tool still runs, with homebrew
        only, and reports the empty load.
        """
        roots: list[Path] = []
        if self.settings.reference_configured:
            roots.append(self.settings.reference_dir)
        roots.append(self.settings.homebrew_dir)
        return roots

    @property
    def write_roots(self) -> list[Path]:
        """Every directory any store may write to.

        Used by :meth:`assert_reference_readonly`. Keep this in step with the stores:
        a new writable location belongs here so the guard keeps covering it.
        """
        roots = [self.settings.library_dir, self.settings.reference_cache_dir]
        if self.settings.backup_configured:
            roots.append(self.settings.backup_dir)
        return roots

    # -- the two-root guarantee --------------------------------------------------

    def assert_reference_readonly(self) -> None:
        """Fail fast if any writable root lies inside the reference corpus.

        Pointing ``PARAVENT_LIBRARY_DIR`` at a subdirectory of the corpus would let a
        combat-tracker write land in somebody else's data — and be erased by the next
        re-import. Cheaper to refuse at startup than to debug later.
        """
        if not self.settings.reference_configured:
            return
        reference = self.settings.reference_dir.resolve()
        for root in self.write_roots:
            if _is_within(root, reference) or root.resolve() == reference:
                raise ReferenceRootViolation(
                    f"{root} lies inside the read-only reference root {reference}. "
                    "Library and cache data must live outside the corpus "
                    "(documentation/TECHSTACK.md section 3)."
                )

    # -- loading -----------------------------------------------------------------

    def load(self) -> LoadReport:
        """Load everything from disk."""
        self.assert_reference_readonly()
        if self.settings.reference_configured:
            version = corpus_version(self.settings.reference_dir)
            logger.info(
                "5eTools data %s",
                f"{version.version} ({version.date})" if version else "of unknown version",
            )
        report = self._load_corpus()
        self.lookups.load(*self.compendium_roots)
        self.gamestate.load_all()
        # Worlds and adventures from before they were Markdown files.
        migrated = self.gamestate.prose.adopt_json(self.settings.library_dir)
        if migrated:
            logger.info("Moved %d world/adventure record(s) into Markdown files", migrated)
        # Files from before ownership was a username name their player by account id.
        adopted = self.characters.adopt_legacy_owners()
        if adopted:
            logger.info("Rewrote the owner of %d character file(s) to a username", adopted)
        return report

    def reload_compendium(self, changed: set[Path] | None = None) -> LoadReport:
        """Re-read the compendium after a file change.

        The whole corpus is reloaded rather than just the changed file: entries copy
        from one another across files, so a single edit can invalidate resolutions
        elsewhere. A full reload takes about a second and is correct; a partial one is
        faster and wrong.
        """
        if changed:
            logger.info("Reloading compendium after %d change(s)", len(changed))
        self._books = None
        self._subclass_names = None
        if "published" in self.__dict__:
            self.published.clear()
        if "rulebooks" in self.__dict__:
            self.rulebooks.clear()
        if "screens" in self.__dict__:
            self.screens.clear()
        if "maps" in self.__dict__:
            self.maps.clear()
        if "loot" in self.__dict__:
            self.loot.clear()
        if "encounter_tables" in self.__dict__:
            self.encounter_tables.clear()
        if "npc_tables" in self.__dict__:
            self.npc_tables.clear()
        if "creature_tables" in self.__dict__:
            self.creature_tables.clear()
        self.lookups.load(*self.compendium_roots)
        return self._load_corpus()

    def _load_corpus(self) -> LoadReport:
        """The compendium and its fluff: from the snapshot when nothing they are read
        from has changed since it was made (app/store/snapshot.py), else read, and the
        snapshot made anew."""
        roots = self.compendium_roots
        snapshot = None
        if self.settings.reference_snapshot:
            snapshot = Snapshot(self.settings.reference_cache_dir, fingerprint(roots, __version__))
            state = snapshot.read()
            if isinstance(state, tuple) and len(state) == 2:
                report = self.compendium.restore(state[0])
                self.fluff.restore(state[1])
                return report
        report = self.compendium.load(*roots)
        self.fluff.load(*roots)
        if snapshot is not None:
            snapshot.write((self.compendium.state(), self.fluff.state()))
        return report

    def reload_gamestate(self, changed: set[Path] | None = None) -> None:
        """Re-read library records after an external edit.

        Character files are re-read one by one, so only the characters that changed
        notify their open sheets. Every other collection is small and reloaded whole.
        """
        characters_root = self.gamestate.characters.root.resolve()
        prose_root = self.gamestate.prose.root
        character_changes = (
            {p for p in changed if _is_within(p, characters_root)} if changed else set()
        )
        prose_changes = {p for p in changed if _is_within(p, prose_root)} if changed else set()
        if character_changes:
            self.gamestate.characters.reload_paths(character_changes)
        if prose_changes:
            self.gamestate.prose.reload_paths(prose_changes)
        rest = changed - character_changes - prose_changes if changed else None
        rest_json = {p for p in rest or () if p.suffix == ".json"}
        # Where users left off is written on almost every page view, what the party
        # learned on almost every hit; that alone must not reload everything and wake
        # every open tracker.
        quiet = {c.path.resolve(): c for c in (self.gamestate.places, self.gamestate.knowledge)}
        if changed is not None and rest_json and all(p.resolve() in quiet for p in rest_json):
            for path in {p.resolve() for p in rest_json}:
                quiet[path].load()
        elif changed is None or rest_json:
            for name, collection in self.gamestate.collections().items():
                if name not in ("characters", "prose"):
                    collection.load()
            # A hand edit of combats.json reaches the open trackers.
            for combat in self.gamestate.combats.list():
                self._combat_changed(combat, combat.id)
        ui_root = (self.settings.i18n_dir / "ui").resolve()
        # Only if it was ever built: a catalogue nobody has asked for needs no reload.
        if "ui" in self.__dict__ and (
            changed is None or any(_is_within(p, ui_root) for p in changed)
        ):
            self.ui.load()

    # -- watching ----------------------------------------------------------------

    def start_watching(self) -> None:
        """Watch the data directories and reload on change, if enabled."""
        if not self.settings.watch_files or self._watcher is not None:
            return
        self._watcher = JsonWatcher(
            [*self.compendium_roots, self.settings.library_dir],
            self._on_change,
        )
        self._watcher.start()

    async def stop_watching(self) -> None:
        """Stop the watcher."""
        if self._watcher is not None:
            await self._watcher.stop()
            self._watcher = None

    def _on_change(self, changed: set[Path]) -> None:
        """Route a change to whichever store owns the file that moved.

        Homebrew lives under the library root but belongs to the compendium, so it is
        split out before the remaining library changes are handed to the game-state
        store.
        """
        library_root = self.settings.library_dir.resolve()
        homebrew_root = self.settings.homebrew_dir.resolve()

        homebrew_changes = {p for p in changed if _is_within(p, homebrew_root)}
        library_changes = {p for p in changed - homebrew_changes if _is_within(p, library_root)}
        # Only JSON is compendium data; a README.md beside the corpus is not.
        compendium_changes = {
            p for p in (changed - library_changes) | homebrew_changes if p.suffix == ".json"
        }

        if library_changes:
            self.reload_gamestate(library_changes)
        if compendium_changes:
            self.reload_compendium(compendium_changes)


def _is_within(path: Path, root: Path) -> bool:
    """True when ``path`` lies inside ``root``."""
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True


__all__ = ["ReferenceRootViolation", "StoreRegistry"]
