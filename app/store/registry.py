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
import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from functools import cached_property
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

from app import __version__
from app.config import Settings
from app.models.compendium import Deity, score_of
from app.models.gamestate import (
    CheckCall,
    Combat,
    Combatant,
    CreatureDesign,
    Faction,
    FactionRank,
    World,
    WorldClock,
)
from app.models.pc import PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry
from app.services import features, media, polymorph
from app.services.activities import Activities, Offered, rarity_of
from app.services.adventures import AdventureService
from app.services.afflictions import AfflictionService
from app.services.auth import AuthService, Identity
from app.services.backlinks import BacklinkIndex
from app.services.backup import BackupService
from app.services.bastion_events import EventTools, item_ref
from app.services.bastion_orders import OrderOption, OrderYields, options_of, tool_choice
from app.services.bastions import BastionEvent, BastionService, event_rows
from app.services.book_tables import Rows, copper, days, rolled, row_for, rows_of
from app.services.calendar import CALENDARS, Calendar, calendar_from
from app.services.character import ability_modifier
from app.services.characters import CharacterService
from app.services.chargen.catalogue import Catalogue, Edition
from app.services.chargen.drafts import Drafts
from app.services.chargen.levelup import LevelUps
from app.services.chargen.prepare import Preparations
from app.services.chase import ChaseService, Runner
from app.services.checks import CheckService
from app.services.clock import DAY, ClockService
from app.services.coins import coins_for, worth
from app.services.combat import CombatService, SheetCharacters
from app.services.contacts import ContactService
from app.services.corpus_check import CorpusHealth, corpus_version
from app.services.corpus_import import CorpusImport, CorpusImports
from app.services.creature_builder import (
    HOMEBREW_SOURCE,
    CreatureMath,
    CreatureService,
    Wording,
)
from app.services.creature_pictures import CreaturePictures, design_ref, slug
from app.services.creature_sight import Sight, fluff_image, image_file, sight
from app.services.delve import DelveService
from app.services.dice import Advantage, Dice
from app.services.difficulty import DifficultyRules
from app.services.downtime import DowntimeService
from app.services.encounter_roll import Resolve
from app.services.encounters import EncounterService
from app.services.events import (
    COMBATS,
    PARTY,
    PROSE,
    PROTOCOL,
    TABLE,
    ChangeHub,
    combat_key,
)
from app.services.factions import FactionService, Reward, ranks_from, rewards_of
from app.services.followers import FollowerService, Statblock, hit_die
from app.services.gifts import GiftService
from app.services.glossary import Glossary, LinkCounts
from app.services.handouts import HandoutService
from app.services.holdings import Holdings
from app.services.i18n import FALLBACK, UiCatalogue
from app.services.image_effects import ImageEffects
from app.services.item_charges import Charges, ItemCharges
from app.services.item_names import ItemNames
from app.services.knowledge import KnowledgeService
from app.services.lifestyle import LifestyleService, costs_2014, costs_2024
from app.services.locations import LocationService
from app.services.machine_translation import MachineTranslator
from app.services.magic_crafting import CraftRule, Formulas, rule_from
from app.services.mcp_tokens import McpTokenService
from app.services.media_tags import MediaTags
from app.services.media_uses import MediaUses
from app.services.name_suggestions import ApiRunner, NameSuggestions
from app.services.naming import Naming
from app.services.naming_workshop import NamingWorkshop
from app.services.news import NewsFeed
from app.services.npcs import NpcService, TreasureService
from app.services.party_notes import PartyNotesService
from app.services.places import PlaceService
from app.services.play import Actor, PlayError
from app.services.play_session import PlaySessionService
from app.services.player_view import Reader, needs_reader, reader_for
from app.services.players import PlayerService
from app.services.protocol import ProtocolService
from app.services.protocol_migration import copy_old_logs
from app.services.protocol_view import ProtocolReader, Viewer
from app.services.quests import QuestService
from app.services.ratelimit import LoginThrottle
from app.services.references import ReferenceEntry, ReferenceService
from app.services.revisions import Revisions
from app.services.safety import SafetyService
from app.services.search import SearchService
from app.services.sentient import Guide, SentientService, guide_for
from app.services.session_notes import SessionNoteService
from app.services.sheet import CONDITIONS
from app.services.shop import ShopService
from app.services.sound import SoundDesk, SoundLibrary
from app.services.spellbook import SpellbookService
from app.services.story import StoryService
from app.services.style_drafts import ApiAgent, StyleDrafts
from app.services.summons import Summoning, summoning
from app.services.table import TableService
from app.services.text import strip_tags
from app.services.themes import ThemeLibrary, ThemeService
from app.services.title_images import TitleImages
from app.services.tokens import Token, Tokens
from app.services.translation_coverage import TranslationCoverage, TranslationJobs
from app.services.translation_targets import FLUFF_OF, TranslationTargets, fluff_category
from app.services.translations import TranslationStore, record_key
from app.services.traps import TrapService
from app.services.trash import Trash
from app.services.travel import PACES, TERRAINS, Terrain, TravelService, Vessel
from app.services.treasure_roll import roller_for
from app.services.upkeep import BOOKS, UpkeepService
from app.services.upkeep import TABLE as UPKEEP_TABLE
from app.services.upkeep import costs as upkeep_costs
from app.services.wild_magic import TABLE as WILD_TABLE
from app.services.wild_magic import WildMagicService
from app.services.world_copy import WorldCopy
from app.services.world_instructions import WorldInstructionService
from app.services.world_transfer import WorldTransfer
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
from app.store.projections import MonsterProjection, coerce_cr
from app.store.protocol import ProtocolStore, log_key
from app.store.published import PublishedAdventures, PublishedBooks, ReferenceScreens
from app.store.snapshot import Snapshot, fingerprint
from app.store.watcher import JsonWatcher

# The code that reads the corpus, part of the snapshot's key (app/store/snapshot.py).
CORPUS_CODE = (Path(__file__).resolve().parent.parent / "models", Path(__file__).resolve().parent)

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
        # A character plays in the table's world, not one of its own (CURRENT-WORLD.md §2).
        self.gamestate.characters.world = lambda: self.table.world_id()
        self.gamestate.prose.subscribe(self._prose_changed)
        self._books: BookCatalog | None = None
        self._subclass_names: dict[tuple[str, str], str] | None = None
        self._tables: dict[str, dict[str, Rows]] | None = None
        self._vessels: dict[str, list[Vessel]] = {}
        self._summonings: tuple[int, dict[str, Summoning]] | None = None
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
        an NPC's portrait, a builder creature's picture."""
        store = self.gamestate
        if kind == "npc":
            npc = store.npcs.find(ident)
            if npc is not None and npc.portrait:
                return media.resolve(store.prose, npc.scope.record_id, npc.portrait)
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
            return self.monster_token(name, source, world_id, self._token_field(name, source))
        return Token("creature")

    def _token_field(self, name: str, source: str) -> object:
        """A creature record's own ``token`` field (it may name another creature's)."""
        found = self.encounters.monster(name, source)
        extra = (getattr(found.entry, "model_extra", None) or {}) if found else {}
        return extra.get("token")

    def record_token(self, kind: str, world_id: str | None, ident: str) -> Token:
        """An element's token as pages draw it (``Page.tokens``); a builder creature's
        through :meth:`design_token`."""
        if kind == "creature":
            design = self.gamestate.creatures.find(ident)
            if design is not None:
                return self.design_token(design)
        return self.tokens.of_record(kind, world_id, ident)

    def design_token(self, design: CreatureDesign) -> Token:
        """A builder creature's token: made from its own picture, else the creature disc."""
        return self.tokens.own(design.world_id, "creature", design.id) or Token("creature")

    def copy_base_picture(self, design: CreatureDesign) -> bool:
        """Give a design begun from a book creature (``based_on``) a copy of that
        creature's picture as its own, and so its token: the table's picture of it, else
        the books' picture, else its 5eTools token. A copy, so the design goes its own way
        from then on. False when there is nothing to copy or it has a picture already."""
        if not design.based_on or self.tokens.picture("creature", design.id) is not None:
            return False
        found = self._creature_picture(design.world_id, design.based_on)
        if found is None:
            return False
        try:
            return self.set_own_picture("creature", design.id, found.name, found.read_bytes())
        except (OSError, media.MediaError, FileNotFoundError):
            return False

    def copy_statblock_picture(self, npc_id: str) -> bool:
        """Give an NPC without a portrait a copy of its statblock creature's picture as
        its portrait, and so its token, as :meth:`copy_base_picture` does for a builder
        creature. False when it has a portrait, no statblock or nothing to copy."""
        npc = self.gamestate.npcs.find(npc_id)
        if npc is None or npc.portrait or not npc.statblock:
            return False
        found = self._creature_picture(npc.scope.world_id, npc.statblock)
        if found is None:
            return False
        try:
            self.npcs.upload_portrait(npc_id, found.name, found.read_bytes())
        except (OSError, media.MediaError):
            return False
        return True

    def _creature_picture(self, world_id: str | None, ref: str) -> Path | None:
        """The best picture of a book creature (``Name|SOURCE``) to copy: the table's
        picture of it, else the books' picture, else its 5eTools token; None without any."""
        name, _, source = ref.partition("|")
        found = self.creature_pictures.find(world_id, ref)
        images = self.settings.images_root
        if found is None and images is not None:
            fluff = self.fluff.get("monster", name, source)
            path = fluff_image(fluff, images)
            found = image_file(images, path) if path else None
        if found is None:
            found = self.tokens.path_of(
                self.tokens.of_entry("monster", name, source, self._token_field(name, source))
            )
        return found

    def monster_token(
        self, name: str, source: str, world_id: str | None = None, token: object = None
    ) -> Token:
        """A creature's token: a builder creature's own upload, else 5eTools's, else the
        creature disc."""
        if source == HOMEBREW_SOURCE:
            design = self.creatures.named(name, world_id) or self.creatures.named(name)
            if design is not None:
                return self.design_token(design)
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
            # A caster's lost concentration on Polymorph ends the shapes in the fights too.
            on_released=lambda caster, spell, by: self.combats.release_forms(caster, spell, by),
            on_shape_ended=lambda caster, spell, by: self.combats.end_concentration_of(
                caster, spell, by
            ),
            # A check rolled on a sheet answers the gamemaster's open calls for it.
            on_roll=lambda character, kind, subject, total: self.checks.answer(
                character, kind, subject, total
            ),
            on_timed=self._timed,
            on_rested=lambda character, by: self._rested(character, by.username, by.name),
            # A slot spent in a wild magic zone may surge.
            on_slot=lambda pc: self.wild_magic.slot_spent(pc.world_id, pc.id, pc.name or "?"),
        )

    def _rested(self, character_id: str, by: str, by_name: str) -> None:
        """A long rest ended: afflictions roll their saves, a magic item regains the
        charges a rest brings back."""
        self.afflictions.rested(character_id, by, by_name)
        self.item_charges.rested(character_id, by, by_name)

    def _timed(
        self, pc: PlayerCharacter, kind: str, reference: str, minutes: int, concentration: bool
    ) -> None:
        """A light lit or a spell with a duration cast on a sheet runs on its world's
        clock; a character in no world has none."""
        if pc.world_id and minutes > 0:
            self.clock.start(
                pc.world_id,
                kind,
                reference,
                minutes,
                character_id=pc.id,
                concentration=concentration,
            )

    @cached_property
    def clock(self) -> ClockService:
        """Each world's time in the game and its timers (app/services/clock.py)."""
        return ClockService(
            self.gamestate,
            self.protocol,
            concentrating=self._concentrating,
            end_concentration=self._end_concentration,
            changed=self._clock_changed,
            days_passed=self._days_passed,
            moved=self._clock_moved,
        )

    def _clock_moved(
        self, world: str, before: int, now: int, by: str, by_name: str, why: str = ""
    ) -> None:
        """The world's clock moved: afflictions run their course, magic items regain
        their charges at the dawns, dusks and midnights passed, and a party exploring a
        dungeon meets its encounter checks (the road rolls its own)."""
        if why != "travel" and self.feature(world, "dungeon"):
            self.delve.passed(world, before, now, by, by_name)
        self.afflictions.passed(world, before, now, by, by_name)
        self.item_charges.passed(world, before, now, by, by_name)
        self.gifts.passed(world, before, now, by, by_name)

    @cached_property
    def holdings(self) -> Holdings:
        """The world's ready-made objects an adventure uses (app/services/holdings.py)."""
        return Holdings(self.gamestate)

    @cached_property
    def media_tags(self) -> MediaTags:
        """Tags on a world's pictures, for the image browser (app/services/media_tags.py)."""
        return MediaTags(self.gamestate.prose)

    @cached_property
    def media_uses(self) -> MediaUses:
        """Where a world's pictures are used, to rename or delete them (media_uses.py)."""
        return MediaUses(self.gamestate, self.media_tags)

    @cached_property
    def item_charges(self) -> ItemCharges:
        """Magic items' charges coming back (app/services/item_charges.py)."""

        def charges(pc: PlayerCharacter, index: int) -> Charges | None:
            item = next((i for i in self.characters.sheet(pc).inventory if i.index == index), None)
            return item.charges if item is not None else None

        return ItemCharges(self.gamestate, self.protocol, charges=charges)

    def _days_passed(self, world_id: str, days: int, why: str, by: str, by_name: str) -> None:
        """Days passed off the road cost each character its lifestyle; on the road the
        party eats its rations instead (app/services/lifestyle.py). Hirelings are paid
        either way (app/services/followers.py)."""
        if why != "travel" and self.feature(world_id, "lifestyle"):
            self.lifestyle.charge(world_id, days, self.lifestyle_costs(world_id), by, by_name)
        # Hirelings are paid for every day, on the road too; so is property's upkeep.
        if self.feature(world_id, "followers"):
            self.followers.pay_wages(world_id, days, by, by_name)
        if self.feature(world_id, "upkeep"):
            self.upkeep.charge(world_id, days, by, by_name)

    def calendars(self, world_id: str | None) -> list[Calendar]:
        """The setting calendars the world's books print, each once."""
        out: list[Calendar] = []
        for caption, books, month_days in CALENDARS:
            for book in books:
                rows = self.book_table(world_id, caption, [book])
                if not rows:
                    continue
                found = calendar_from(caption, book, rows, month_days)
                names = [m.name for m in found.months]
                if found.months and all([m.name for m in c.months] != names for c in out):
                    out.append(found)
                break
        return out

    def calendar_of(self, world_id: str | None) -> tuple[Calendar, int, str] | None:
        """The calendar a world's clock dates its days by, with the calendar's day of
        the clock's first day and the era; None for plain day numbers."""
        if not world_id:
            return None
        clock = self.gamestate.clocks.find(world_id)
        if clock is None or not clock.calendar:
            return None
        found = next((c for c in self.calendars(world_id) if c.key == clock.calendar), None)
        if found is None:
            return None
        if not clock.calendar_leaps:
            # Set before leap days were counted: the same date, counted with them.
            epoch = found.from_flat(clock.calendar_epoch + clock.minute // DAY)
            epoch -= clock.minute // DAY
            clock = self.clock.dated(world_id, clock.calendar, epoch, clock.calendar_era)
        return found, clock.calendar_epoch, clock.calendar_era

    def lifestyle_costs(self, world_id: str | None) -> dict[str, int]:
        """Copper a day by lifestyle, by the world's books: the 2014 handbook's table,
        or the 2024 handbook's inn stay and meals; empty without either."""
        old = costs_2014(self.book_table(world_id, "Lifestyle Expenses", ["PHB"]))
        new = costs_2024(self.book_table(world_id, "Food, Drink, and Lodging", ["XPHB"]))
        first, second = (new, old) if self.rules_of(world_id) == "one" else (old, new)
        return first or second

    def spellbook_for(self, by: Actor) -> SpellbookService:
        """Scrolls cast and spells copied into spellbooks, as ``by``
        (app/services/spellbook.py)."""
        characters = self.characters

        def roll(character_id: str, kind: str, subject: str) -> int:
            return characters.roll_for(character_id, kind, subject, by, private=False) or 0

        def act(character_id: str, action: str, params: dict[str, str]) -> None:
            characters.act_for(character_id, action, params, by)

        return SpellbookService(
            # The wizard's catalogue: it carries the spell lists.
            catalogue=lambda pc: self.drafts.planner(pc).catalogue,
            sheet=characters.sheet,
            roll=roll,
            act=act,
        )

    @cached_property
    def followers(self) -> FollowerService:
        """Characters' hirelings, companions, mounts and sidekicks
        (app/services/followers.py)."""
        characters = self.characters
        by = Actor("Paravent", username="")

        def purse(character_id: str) -> int:
            pc = self.gamestate.characters.find(character_id)
            return worth(pc.currency) if pc is not None else 0

        def pay(character_id: str, amount: int) -> None:
            characters.act_for(
                character_id,
                "spend",
                {"amount": str(amount), "for": "wages", "quiet": "1"},
                by,
            )

        return FollowerService(
            self.gamestate,
            self.protocol,
            wages=self.wages,
            statblock=self.follower_statblock,
            sidekick_classes=self.sidekick_classes,
            purse=purse,
            pay=pay,
        )

    def sidekick_classes(self, world_id: str | None) -> list[tuple[str, str]]:
        """The sidekick classes of the world's books, ``(ref, name)``."""
        world = self.gamestate.worlds.find(world_id) if world_id else None
        out = []
        for cls in self.compendium.all("class"):
            data = cls.model_dump(by_alias=True, exclude_none=True)
            source = str(data.get("source", ""))
            if not data.get("isSidekick"):
                continue
            if (
                world is not None
                and not world.source_set.is_unrestricted
                and (source.lower() not in world.source_set.normalized)
            ):
                continue
            out.append((f"{data['name']}|{source}", str(data["name"])))
        return sorted(out, key=lambda x: x[1])

    def sidekick_progress(
        self, ref: str, level: int
    ) -> tuple[list[tuple[int, str]], dict[str, int]]:
        """A sidekick class's features up to ``level`` (``(level, name)``) and its table
        at that level (cantrips and spells known, by column)."""
        name, _, source = ref.partition("|")
        try:
            cls = self.compendium.find("class", name, source)
        except EntryNotFound:
            return [], {}
        if cls is None:
            return [], {}
        data = cls.model_dump(by_alias=True, exclude_none=True)
        features = []
        for item in data.get("classFeatures") or []:
            text = item.get("classFeature") if isinstance(item, dict) else item
            parts = str(text).split("|")
            at = int(parts[-1]) if parts[-1].isdigit() else 0
            if parts and 0 < at <= level:
                features.append((at, parts[0]))
        table: dict[str, int] = {}
        for group in data.get("classTableGroups") or []:
            rows = group.get("rows") or group.get("rowsSpellProgression") or []
            if not 0 < level <= len(rows):
                continue
            for label, value in zip(group.get("colLabels") or [], rows[level - 1], strict=False):
                if isinstance(value, int):
                    table[strip_tags(str(label))] = value
        return features, table

    def wages(self, world_id: str | None) -> dict[str, int]:
        """Copper a day for a skilled and an untrained hireling, by the world's books:
        the 2024 handbook's *Hirelings* table, or the 2014 *Services* table."""
        found: dict[str, int] = {}
        tables = [("Hirelings", "XPHB"), ("Services", "PHB")]
        if self.rules_of(world_id) != "one":
            tables.reverse()
        for caption, book in tables:
            for row in self.book_table(world_id, caption, [book]) or []:
                if len(row) < 2:
                    continue
                label = strip_tags(row[0]).lower()
                kind = (
                    "untrained" if "untrained" in label else "skilled" if "skilled" in label else ""
                )
                if kind and kind not in found and copper(row[1]):
                    found[kind] = copper(row[1])
            if found:
                return found
        return found

    def follower_statblock(self, world_id: str | None, name: str) -> Statblock | None:
        """A creature of the world's books as a follower needs it."""
        creature, _, source = name.partition("|")
        if not creature.strip():
            return None
        world = self.gamestate.worlds.find(world_id) if world_id else None
        found = (
            self.references.resolver()
            .resolve_parts(
                "monster",
                creature.strip(),
                source.strip() or None,
                world.source_set if world else None,
            )
            .entry
        )
        if found is None:
            return None
        data = found.model_dump(by_alias=True, exclude_none=True)
        hp = data.get("hp") or {}
        average = hp.get("average") if isinstance(hp, dict) else None
        return Statblock(
            name=str(getattr(found, "name", creature)),
            reference=f"{getattr(found, 'name', creature)}|{getattr(found, 'source', '')}",
            hp=int(average) if isinstance(average, int) else 1,
            die=hit_die(str(hp.get("formula", "")) if isinstance(hp, dict) else ""),
            con=ability_modifier(score_of(data.get("con"))),
            cr=cr.value if (cr := coerce_cr(data.get("cr"))) is not None else None,
        )

    @cached_property
    def lifestyle(self) -> LifestyleService:
        """What living costs the characters each day (app/services/lifestyle.py)."""
        characters = self.characters
        by = Actor("Paravent", username="")

        def party(world_id: str) -> list[PlayerCharacter]:
            return self.gamestate.characters.of_world(world_id)

        def purse(character_id: str) -> int:
            pc = self.gamestate.characters.find(character_id)
            return worth(pc.currency) if pc is not None else 0

        def pay(character_id: str, amount: int) -> None:
            characters.act_for(
                character_id,
                "spend",
                {"amount": str(amount), "for": "lifestyle", "quiet": "1"},
                by,
            )

        return LifestyleService(self.protocol, party=party, purse=purse, pay=pay)

    @cached_property
    def upkeep(self) -> UpkeepService:
        """What the characters' property costs to run each day (app/services/upkeep.py)."""
        characters = self.characters
        by = Actor("Paravent", username="")

        def purse(character_id: str) -> int:
            pc = self.gamestate.characters.find(character_id)
            return worth(pc.currency) if pc is not None else 0

        def pay(character_id: str, amount: int) -> None:
            characters.act_for(
                character_id,
                "spend",
                {"amount": str(amount), "for": "upkeep", "quiet": "1"},
                by,
            )

        return UpkeepService(self.gamestate, self.protocol, purse=purse, pay=pay)

    def upkeep_costs(self, world_id: str | None) -> list[tuple[str, int]]:
        """The *Maintenance Costs* of the world's guides: the one of its rules first (the
        2024 guide prints only fortifications), then what the other prices besides."""
        books = list(BOOKS) if self.rules_of(world_id) != "one" else list(reversed(BOOKS))
        first = upkeep_costs(self.book_table(world_id, UPKEEP_TABLE, books[:1]))
        named = {name.lower() for name, _ in first}
        more = upkeep_costs(self.book_table(world_id, UPKEEP_TABLE, books[1:]))
        return first + [(name, price) for name, price in more if name.lower() not in named]

    def _concentrating(self, character_id: str) -> str | None:
        pc = self.gamestate.characters.find(character_id)
        held = pc.state.concentration if pc is not None else None
        return held.reference if held is not None else None

    def _end_concentration(self, character_id: str, spell: str, by: str) -> None:
        """A spell's duration ran out on the clock: its caster stops concentrating."""
        characters = self.characters
        pc = self.gamestate.characters.find(character_id)
        if pc is not None:
            characters.play.act(
                character_id, "concentration", {}, characters.sheet(pc), Actor(by, username=by)
            )

    def _clock_changed(self, clock: WorldClock) -> None:
        """The party view and every sheet of the world show the time."""
        state = f"clock-{clock.updated_at.isoformat()}"
        for pc in self.gamestate.characters.of_world(clock.id):
            self.events.publish(pc.id, state)
        self.events.publish(PARTY, f"*clock {clock.id}")

    @cached_property
    def bastions(self) -> BastionService:
        """Characters' bastions (app/services/bastions.py)."""
        characters = self.characters
        by = Actor("Paravent", username="")

        def pay(character_id: str, amount: int, what: str) -> bool:
            try:
                characters.act_for(character_id, "spend", {"amount": str(amount), "for": what}, by)
            except PlayError:
                return False
            return True

        def earn(character_id: str, amount: int) -> None:
            characters.receive(character_id, [], coins_for(amount), by)

        def give(character_id: str, ref: str, count: int) -> None:
            characters.receive(character_id, [(ref, count)], {}, by)

        def price(ref: str) -> int:
            name, _, source = ref.partition("|")
            for category in ("baseitem", "item"):
                try:
                    found = self.compendium.find(category, name, source)
                except EntryNotFound:
                    continue
                if found is not None:
                    value = found.model_dump(by_alias=True, exclude_none=True).get("value")
                    return int(value) if isinstance(value, int | float) else 0
            return 0

        yields = OrderYields(
            options=self.facility_options, pay=pay, earn=earn, give=give, price=price
        )
        dice = Dice()

        def table(caption: str) -> Rows:
            try:
                found = self.compendium.find("table", caption, "XDMG")
            except EntryNotFound:
                return []
            return rows_of(found) if found is not None else []

        def art(value: int) -> str:
            found = self.loot.art("XDMG", value) or self.loot.art("DMG", value)
            pieces = [item_ref(x) for x in (found or {}).get("table") or []]
            pieces = [x for x in pieces if x]
            return pieces[dice.die(len(pieces)) - 1] if pieces else ""

        def magic(character_id: str, rarity: str) -> str:
            pc = self.gamestate.characters.find(character_id)
            drawn = self.magic_roll(pc.world_id if pc else None, rarity, 1, dice)
            return drawn[0][0] if drawn else ""

        tools = EventTools(earn=earn, pay=pay, give=give, table=table, art=art, magic=magic)
        return BastionService(
            self.gamestate,
            self.protocol,
            events=self._bastion_events,
            yields=yields,
            pay=pay,
            tools=tools,
        )

    def discoveries(self, world_id: str | None) -> list[tuple[str, str]]:
        """The uncommon potions and scrolls of the world's books, ``(ref, name)``: what a
        bastion's Magical Discovery may make."""
        world = self.gamestate.worlds.find(world_id) if world_id else None
        source_set = world.source_set if world is not None else None
        resolver = self.compendium.resolver()
        out = []
        for item in self.compendium.all("item"):
            data = item.model_dump(by_alias=True, exclude_none=True)
            code = str(data.get("type", "")).split("|")[0]
            if code not in ("P", "SC") or rarity_of(data.get("rarity")) != "uncommon":
                continue
            name, source = str(data.get("name", "")), str(data.get("source", ""))
            found = resolver.resolve_parts("item", name, None, source_set).entry
            if found is not None and str(getattr(found, "source", "")) == source:
                out.append((f"{name}|{source}", name))
        return sorted(out, key=lambda x: x[1])

    def facility_options(self, ref: str) -> dict[str, list[OrderOption]]:
        """A special facility's options per order, read from its text."""
        name, _, source = ref.partition("|")
        try:
            found = self.compendium.find("facility", name, source)
        except EntryNotFound:
            return {}
        if found is None:
            return {}
        return options_of(found.model_dump(by_alias=True, exclude_none=True))

    def facility_tools(self, ref: str) -> tuple[int, tuple[str, ...]]:
        """The tools a special facility is equipped with when added: how many to choose,
        and from which (app/services/bastion_orders.py)."""
        name, _, source = ref.partition("|")
        try:
            found = self.compendium.find("facility", name, source)
        except EntryNotFound:
            return 0, ()
        if found is None:
            return 0, ()
        return tool_choice(found.model_dump(by_alias=True, exclude_none=True))

    def basic_costs(self, world_id: str | None) -> tuple[dict[str, tuple[int, int]], ...]:
        """Adding and enlarging a basic facility, by space: (copper, days) — from the
        guide's two tables, empty without them."""
        out = []
        for caption in ("Adding Basic Facilities", "Enlarging Basic Facilities"):
            costs = {}
            for row in self.book_table(world_id, caption, ["XDMG"]) or []:
                if len(row) >= 3:
                    space = row[0].strip().lower().split(" to ")[0]
                    costs[space] = (copper(row[1]), days(row[2]))
            out.append(costs)
        return tuple(out)

    def _bastion_events(self) -> list[BastionEvent]:
        """The 2024 guide's Bastion Events table, when the corpus has it."""
        try:
            table = self.compendium.find("table", "Bastion Events", "XDMG")
        except EntryNotFound:
            return []
        return event_rows(getattr(table, "rows", None) or []) if table is not None else []

    def catalogue(self, world_id: str | None) -> Catalogue:
        """What a world's books offer, as the wizard reads them (its shop, languages,
        tools, the classes' spell lists): the shop, downtime and Divine Intervention use
        it."""
        world = self.gamestate.worlds.find(world_id) if world_id else None
        return Catalogue(self.compendium, world.source_set if world else None, self.lookups)

    def rules_of(self, world_id: str | None) -> Edition:
        """``one`` when a world's books include the 2024 rules (as supersession reads
        them), else ``classic``."""
        world = self.gamestate.worlds.find(world_id) if world_id else None
        return "one" if world is not None and "one" in world.source_set.editions else "classic"

    def pantheons(self, world_id: str | None) -> list[str]:
        """The pantheons whose deities the world's books print, by name."""
        return sorted({str(d.pantheon) for d in self._book_deities(world_id) if d.pantheon})

    def deities(self, world_id: str | None) -> list[Deity]:
        """The deities the characters may worship: those of the world's books, of the
        pantheons it plays with (every one where it names none), by pantheon and name."""
        world = self.gamestate.worlds.find(world_id) if world_id else None
        found = self._book_deities(world_id)
        chosen = {p.lower() for p in world.pantheons} if world is not None else set()
        if chosen:
            found = [d for d in found if str(d.pantheon or "").lower() in chosen]
        return sorted(found, key=lambda d: (str(d.pantheon or ""), str(d.name).casefold()))

    def _book_deities(self, world_id: str | None) -> list[Deity]:
        world = self.gamestate.worlds.find(world_id) if world_id else None
        found = [d for d in self.compendium.all("deity") if isinstance(d, Deity)]
        if world is not None and not world.source_set.is_unrestricted:
            books = set(world.source_set.normalized)
            found = [d for d in found if str(d.source).lower() in books]
        return found

    def feature(self, world_id: str | None, key: str) -> bool:
        """Whether the world plays with a feature (app/services/features.py); no world,
        or one from before the switches, plays with every one."""
        world = self.gamestate.worlds.find(world_id) if world_id else None
        return features.on(world, key)

    def features_changed(self, world_id: str, by: str, by_name: str) -> None:
        """A world's features were saved: what runs of one switched off ends (an open
        shop closes, a chase ends, the party leaves the dungeon); its records stay."""
        if not self.feature(world_id, "shops") and self.shops.of(world_id).open:
            self.shops.close(world_id, by, by_name)
        if not self.feature(world_id, "chases") and self.chases.of(world_id) is not None:
            self.chases.end(world_id, "stopped", by, by_name)
        if not self.feature(world_id, "dungeon") and self.delve.of(world_id) is not None:
            self.delve.leave(world_id, by, by_name)
        if not self.feature(world_id, "hazards") and self.wild_magic.zone(world_id):
            self.wild_magic.enter(world_id, False, by, by_name)

    @cached_property
    def wild_magic(self) -> WildMagicService:
        """A world's wild magic zone and its surges (app/services/wild_magic.py)."""

        def table(world_id: str) -> Rows | None:
            books = ["XPHB", "PHB"] if self.rules_of(world_id) == "one" else ["PHB", "XPHB"]
            return self.book_table(world_id, WILD_TABLE, books)

        return WildMagicService(self.clock, self.protocol, table=table)

    @cached_property
    def delve(self) -> DelveService:
        """A party exploring a dungeon, its encounter checks (app/services/delve.py)."""
        return DelveService(
            self.clock,
            self.protocol,
            party=lambda world_id: self.gamestate.characters.of_world(world_id),
            table=self.encounter_tables.get,
            resolver=self.monster_resolve,
        )

    def monster_resolve(self, world_id: str) -> Resolve:
        """Resolves a creature by name (and book) in a world's books."""
        world = self.gamestate.worlds.find(world_id)
        resolver = self.references.resolver()

        def resolve(name: str, source: str | None) -> tuple[str, str] | None:
            found = resolver.resolve_parts(
                "monster", name, source, world.source_set if world else None
            ).entry
            return (str(found.name), str(found.source)) if found is not None else None

        return resolve

    @cached_property
    def travel(self) -> TravelService:
        """The party on the road (app/services/travel.py)."""
        characters = self.characters

        def party(world_id: str) -> list[PlayerCharacter]:
            return self.gamestate.characters.of_world(world_id)

        def eat(pc: PlayerCharacter) -> bool:
            try:
                characters.act_for(pc.id, "eat", {}, Actor("Paravent", username=""))
            except PlayError:
                return False
            return True

        def tire(pc: PlayerCharacter, cause: str) -> None:
            characters.act_for(
                pc.id, "exhaustion", {"delta": "1", "cause": cause}, Actor("Paravent", username="")
            )

        def quench(pc: PlayerCharacter) -> None:
            fresh = self.gamestate.characters.find(pc.id)
            if fresh is not None and fresh.state.thirst_levels:
                characters.act_for(pc.id, "fed", {"what": "thirst"}, Actor("Paravent", username=""))

        def con_mod(pc: PlayerCharacter) -> int:
            return characters.sheet(pc).ability("con").modifier

        def con_save(pc: PlayerCharacter) -> int:
            return characters.sheet(pc).ability("con").save.value or 0

        def rest(world_id: str) -> None:
            # A night on the road; under gritty realism only a short rest.
            world = self.gamestate.worlds.find(world_id)
            gritty = world is not None and "rest_gritty" in world.character_rules
            characters.rest_party(world_id, "short" if gritty else "long", Actor("Paravent"))

        def sheltered(pc: PlayerCharacter, climate: str) -> bool:
            sheet = characters.sheet(pc)
            kind = "cold" if climate == "cold" else "fire"
            if kind in sheet.defended("resist") or kind in sheet.defended("immune"):
                return True
            return climate == "cold" and any(
                "cold weather" in i.item.name.lower() for i in pc.inventory
            )

        def skill(pc: PlayerCharacter, key: str) -> int:
            found = (s.value.value for s in characters.sheet(pc).skills if s.key == key)
            return next(found, None) or 0

        def wis_mod(pc: PlayerCharacter) -> int:
            return characters.sheet(pc).ability("wis").modifier

        def passive(pc: PlayerCharacter) -> int:
            return characters.sheet(pc).passive_perception.value or 10

        def lurk(name: str, source: str) -> tuple[int, int]:
            # The creature as a chase reads it: its Stealth and passive Perception.
            found = self.chase_runner(None, f"{name}|{source}", "pursuer")
            return (found[0].stealth, found[0].passive) if found else (0, 10)

        return TravelService(
            self.clock,
            self.protocol,
            party=party,
            eat=eat,
            tire=tire,
            con_mod=con_mod,
            con_save=con_save,
            rest=rest,
            quench=quench,
            sheltered=sheltered,
            skill=skill,
            wis_mod=wis_mod,
            passive=passive,
            lurk=lurk,
        )

    def terrains(self, world_id: str | None) -> list[str]:
        """The terrains the world's guide gives travel rules for (TERRAINS' names):
        every row of the 2024 *Travel Terrain* table, else those the 2014 *Wilderness
        Navigation* table names; none when the world has neither guide."""
        return [name for name in TERRAINS if self.terrain(world_id, name, "limited")]

    def foraging_by_abundance(self, world_id: str | None) -> bool:
        """Whether foraging's DC is chosen by how much food the land holds (the 2014
        guide's *Foraging DCs*) rather than read from the terrain (2024)."""
        return self.rules_of(world_id) != "one" and bool(
            self.book_table(world_id, "Foraging DCs", ["DMG"])
        )

    def terrain(self, world_id: str | None, name: str, abundance: str = "") -> Terrain | None:
        """What the world's guide says of travelling through ``name``: under the 2024
        rules the *Travel Terrain* table's row; under the 2014 rules the *Wilderness
        Navigation* DC of the row naming it and the *Foraging DCs* row of ``abundance``
        (abundant, limited, scarce), with the 2024 table's encounter distance where the
        world has that guide too. None when the books say nothing of it."""
        if name not in TERRAINS:
            return None
        both = self.book_table(world_id, "Travel Terrain", ["XDMG"]) or []
        row = next((r for r in both if strip_tags(r[0]).strip(" *†").lower() == name.lower()), None)
        distance = ""
        if row is not None and len(row) > 4:
            found = re.search(r"\{@dice ([^}|]+)", row[2])
            distance = found[1].strip() if found else ""
        if self.rules_of(world_id) == "one":
            if row is None or len(row) < 5:
                return None
            pace = strip_tags(row[1]).strip(" *†").lower()
            return Terrain(
                name,
                navigation_dc=_number(row[4]),
                foraging_dc=_number(row[3]),
                distance=distance,
                max_pace=pace if pace in PACES else "",
            )
        navigating = self.book_table(world_id, "Wilderness Navigation", ["DMG"]) or []
        nav = next((r for r in navigating if re.search(rf"\b{name.lower()}", r[0].lower())), None)
        foraging = self.book_table(world_id, "Foraging DCs", ["DMG"]) or []
        first = {"abundant": "abundant", "limited": "limited", "scarce": "very"}.get(abundance, "")
        forage = next((r for r in foraging if first and r[0].lower().startswith(first)), None)
        if nav is None and not distance:
            return None
        return Terrain(
            name,
            navigation_dc=_number(nav[-1]) if nav else None,
            foraging_dc=_number(forage[-1]) if forage else None,
            distance=distance,
        )

    def hazard_links(self, world_id: str | None) -> dict[str, str]:
        """The books' road hazards the world has, by English name → their page."""
        world = self.gamestate.worlds.find(world_id) if world_id else None
        books = world.source_set if world else None
        resolver = self.references.resolver()
        out = {}
        for name in (
            "Extreme Cold",
            "Extreme Heat",
            "High Altitude",
            "Strong Wind",
            "Heavy Precipitation",
        ):
            entry = resolver.resolve_parts("hazard", name, None, books).entry
            if entry is not None:
                query = f"?world={world_id}" if world_id else ""
                out[name] = f"/ref/hazard/{getattr(entry, 'source', '')}/{quote(name)}{query}"
        return out

    def book_table(self, world_id: str | None, caption: str, sources: Sequence[str]) -> Rows | None:
        """The rows of a table the books print (``caption``), from the first of
        ``sources`` the world's books include (app/services/book_tables.py)."""
        world = self.gamestate.worlds.find(world_id) if world_id else None
        held = (
            [s for s in sources if s.lower() in world.source_set.normalized]
            if world is not None and not world.source_set.is_unrestricted
            else list(sources)
        )
        if not held:
            return None
        found = self._table_index().get(caption.lower(), {})
        return next((found[s.lower()] for s in held if s.lower() in found), None)

    def _table_index(self) -> dict[str, dict[str, Rows]]:
        """The books' tables by caption (and by each tail after a "<section>; "), then by
        source, as ``find_in`` reads them: built once per corpus load, so a page asking
        for a few small tables does not walk all of them each time."""
        if self._tables is None:
            index: dict[str, dict[str, Rows]] = {}
            for table in self.compendium.all("table"):
                name = str(getattr(table, "name", "")).lower()
                source = str(getattr(table, "source", "")).lower()
                rows = None
                parts = name.split("; ")
                for key in {"; ".join(parts[n:]) for n in range(len(parts))}:
                    by_source = index.setdefault(key, {})
                    if source not in by_source:
                        rows = rows if rows is not None else rows_of(table)
                        by_source[source] = rows
            self._tables = index
        return self._tables

    def weather(self, world_id: str | None) -> Callable[[], str] | None:
        """Rolls a day's weather on the world's guide ("temperature|wind|
        precipitation"), or None when its books print no weather table."""
        dice = Dice()
        both = self.book_table(world_id, "Weather", ["XDMG"])
        if both is not None:

            def roll_2024() -> str:
                cells = [both_row(both, dice.die(20))[i] for i in (1, 2, 3)]
                return "|".join(rolled(c, dice) for c in cells)

            return roll_2024
        parts = [
            self.book_table(world_id, caption, ["DMG"])
            for caption in ("Weather", "Weather; Wind", "Weather; Precipitation")
        ]
        if any(p is None for p in parts):
            return None

        def roll_2014() -> str:
            return "|".join(rolled(both_row(p or [], dice.die(20))[1], dice) for p in parts)

        return roll_2014

    def vessels(self, world_id: str | None) -> list[Vessel]:
        """The ships and airships of the world's books, with their speed; kept per set of
        books until the corpus reloads (the party view asks on every refresh)."""
        world = self.gamestate.worlds.find(world_id) if world_id else None
        key = world.source_set.identity if world is not None else "*"
        held = self._vessels.get(key)
        if held is None:
            held = self._vessels[key] = self._read_vessels(world_id)
        return list(held)

    def _read_vessels(self, world_id: str | None) -> list[Vessel]:
        seen: dict[str, Vessel] = {}
        for offer in self.catalogue(world_id).rows("baseitem") + self.catalogue(world_id).rows(
            "item"
        ):
            speed = offer.data.get("vehSpeed")
            code = str(offer.data.get("type", "")).split("|")[0]
            if code in ("SHP", "AIR") and isinstance(speed, int | float) and speed > 0:
                seen.setdefault(offer.name.lower(), Vessel(offer.name, float(speed)))
        return sorted(seen.values(), key=lambda v: v.name.lower())

    @cached_property
    def downtime(self) -> DowntimeService:
        """Characters' downtime days and projects (app/services/downtime.py)."""
        characters = self.characters
        by = Actor("Paravent", username="")

        def pay(character_id: str, copper: int, what: str) -> None:
            characters.act_for(character_id, "spend", {"amount": str(copper), "for": what}, by)

        def give(character_id: str, ref: str) -> None:
            # A scribed scroll comes as "<scroll>;<spell>", holding its spell.
            item, _, spell = ref.partition(";")
            characters.receive(character_id, [(item, 1)], {}, by, spell=spell)

        def learn(character_id: str, kind: str, ref: str) -> None:
            characters.act_for(character_id, "learn", {"kind": kind, "ref": ref}, by)

        def recuperate(character_id: str) -> None:
            characters.act_for(character_id, "roll", {"kind": "save", "subject": "con"}, by)

        return DowntimeService(
            self.gamestate,
            self.protocol,
            pay=pay,
            give=give,
            learn=learn,
            recuperate=recuperate,
        )

    @cached_property
    def activities(self) -> Activities:
        """The downtime activities of *Xanathar's Guide* (app/services/activities.py)."""
        characters = self.characters
        by = Actor("Paravent", username="")

        def check(pc: PlayerCharacter, kind: str, subject: str) -> int:
            return characters.roll_for(pc.id, kind, subject, by, private=False) or 0

        def proficiency(pc: PlayerCharacter) -> int:
            return characters.sheet(pc).proficiency.value or 0

        def hit_die(pc: PlayerCharacter) -> int:
            dice = [h.faces for h in characters.sheet(pc).hit_dice] or [8]
            return max(dice)

        def pay(character_id: str, amount: int, what: str) -> None:
            characters.act_for(character_id, "spend", {"amount": str(amount), "for": what}, by)

        def earn(character_id: str, amount: int, _what: str) -> None:
            characters.receive(character_id, [], coins_for(amount), by)

        def purse(character_id: str) -> int:
            pc = self.gamestate.characters.find(character_id)
            return worth(pc.currency) if pc is not None else 0

        def give(character_id: str, ref: str) -> None:
            characters.receive(character_id, [(ref, 1)], {}, by)

        def sell(character_id: str, ref: str, price: int) -> None:
            pc = self.gamestate.characters.find(character_id)
            index = next(
                (
                    n
                    for n, item in enumerate(pc.inventory if pc else [])
                    if item.item.reference.lower() == ref.lower() and not item.equipped
                ),
                None,
            )
            if index is None:
                raise PlayError("play.error.bad_input")
            characters.act_for(
                character_id, "sell", {"index": str(index), "count": "1", "price": str(price)}, by
            )

        def magic_items(table: str, times: int) -> list[Offered]:
            roller = roller_for(self.compendium, self.loot, None, "DMG")
            out = []
            for name, source in roller.magic_items(table, "DMG", times):
                try:
                    item = self.compendium.find("item", name, source)
                except EntryNotFound:
                    continue
                data = item.model_dump(by_alias=True, exclude_none=True) if item else {}
                rarity = rarity_of(data.get("rarity"))
                if rarity:
                    kind = str(data.get("type", "")).split("|")[0]
                    out.append(Offered(name, source, rarity, kind in ("P", "SC")))
            return out

        return Activities(
            self.downtime,
            check=check,
            proficiency=proficiency,
            hit_die=hit_die,
            pay=pay,
            earn=earn,
            purse=purse,
            magic_items=magic_items,
            give=give,
            sell=sell,
        )

    def has_book(self, world_id: str | None, source: str) -> bool:
        """Whether a world may read ``source``: declared, or nothing declared and the
        corpus holds it."""
        world = self.gamestate.worlds.find(world_id) if world_id else None
        if world is not None and not world.source_set.is_unrestricted:
            return source.lower() in world.source_set.normalized
        held = {
            s.lower()
            for category in ("table", "item", "spell")
            for s in self.compendium.sources(category)
        }
        return source.lower() in held

    def training_rule(self, world_id: str | None) -> str:
        """Which book's training rule a world follows: "xge", "phb" or "table" (the
        2024 books have none: the gamemaster's days)."""
        if self.has_book(world_id, "XGE"):
            return "xge"
        return "phb" if self.has_book(world_id, "PHB") else "table"

    def scroll_costs(self, world_id: str | None) -> dict[int, tuple[int, int, str]]:
        """Scribing a scroll by spell level: (days, copper, the scroll's reference), from
        the world's Spell Scroll Costs table; empty without one."""
        if self.rules_of(world_id) == "one" and self.has_book(world_id, "XPHB"):
            rows = self.book_table(world_id, "Scribing Spell Scrolls; Spell Scroll Costs", ["XPHB"])
            scroll = "XDMG" if self.has_book(world_id, "XDMG") else "DMG"
        else:
            rows = self.book_table(world_id, "Resources; Spell Scroll Costs", ["XGE"])
            scroll = "DMG" if self.has_book(world_id, "DMG") else "XDMG"
        out: dict[int, tuple[int, int, str]] = {}
        for row in rows or []:
            if len(row) < 3:
                continue
            cell = strip_tags(row[0]).strip().lower()
            level = 0 if cell.startswith("cantrip") else int(re.sub(r"\D", "", cell) or -1)
            if 0 <= level <= 9:
                out[level] = (days(row[1]), copper(row[2]), _scroll_ref(level, scroll))
        return out

    def magic_craft_rule(self, world_id: str | None) -> CraftRule | None:
        """How a world crafts magic items (app/services/magic_crafting.py): the 2024
        guide where the world plays the 2024 rules, else *Xanathar's*, else the 2014
        guide's rule, else the 2024 guide; None without any of them."""
        new_rules = self.rules_of(world_id) == "one"
        order = ["XDMG", "XGE", "DMG"] if new_rules else ["XGE", "DMG", "XDMG"]
        for book in order:
            if not self.has_book(world_id, book):
                continue
            if book == "XDMG":
                rule = rule_from(
                    "XDMG",
                    self.book_table(world_id, "Magic Item Crafting Time and Cost", ["XDMG"]),
                    self.book_table(world_id, "Magic Item Tools", ["XDMG"]),
                )
            elif book == "XGE":
                rule = rule_from(
                    "XGE",
                    self.book_table(world_id, "Magic Item Crafting Time and Cost", ["XGE"]),
                    None,
                    self.book_table(world_id, "Magic Item Ingredients", ["XGE"]),
                )
            else:
                rule = rule_from("DMG", self._dmg_crafting_rows())
            if rule is not None:
                return rule
        return None

    def _dmg_crafting_rows(self) -> Rows | None:
        """The 2014 guide's *Crafting Magic Items* table, printed in its downtime rule."""
        try:
            entry = self.compendium.find(
                "variantrule", "Downtime Activity: Crafting a Magic Item", "DMG"
            )
        except EntryNotFound:
            return None
        data = entry.model_dump(by_alias=True, exclude_none=True) if entry else {}
        for node in data.get("entries") or []:
            if isinstance(node, dict) and node.get("caption") == "Crafting Magic Items":
                return rows_of(SimpleNamespace(rows=node.get("rows")))
        return None

    def rarity_values(self, world_id: str | None) -> dict[str, int]:
        """A magic item's value by rarity in copper, from the world's guide: the 2024
        table, else *Xanathar's* base prices, else the top of the 2014 guide's range."""
        for caption, source in (
            ("Magic Item Values by Rarity; Magic Item Rarities and Values", "XDMG"),
            ("Selling a Magic Item; Magic Item Base Prices", "XGE"),
            ("Magic Item Rarity", "DMG"),
        ):
            rows = self.book_table(world_id, caption, [source])
            if not rows:
                continue
            out = {}
            for row in rows:
                if len(row) < 2:
                    continue
                cell = strip_tags(row[-1]).replace(",", "")
                numbers = [int(n) for n in re.findall(r"\d+", cell)]
                if numbers:
                    out[row[0].strip().lower()] = max(numbers) * 100
            if out:
                return out
        return {}

    def magic_price(self, world_id: str | None, name: str, source: str) -> int:
        """What a magic item is worth, in copper: its own price if it has one, else its
        rarity's value (halved for a consumable other than a spell scroll; a scroll
        twice what scribing it costs), with the price of the item it is made from."""
        try:
            item = self.compendium.find("item", name, source)
        except EntryNotFound:
            return 0
        if item is None:
            return 0
        data = item.model_dump(by_alias=True, exclude_none=True)
        if isinstance(data.get("value"), int | float) and data["value"] > 0:
            return int(data["value"])
        if name.lower().startswith("spell scroll"):
            level = 0 if "cantrip" in name.lower() else int(re.sub(r"\D", "", name) or 0)
            costs = self.scroll_costs(world_id)
            if level in costs:
                return costs[level][1] * 2
        rarity = rarity_of(data.get("rarity"))
        value = self.rarity_values(world_id).get(rarity, 0)
        code = str(data.get("type", "")).split("|")[0]
        if code in ("P", "SC", "A"):
            value //= 2
        base = data.get("baseItem")
        if isinstance(base, str) and "|" in base:
            base_name, _, base_source = base.partition("|")
            offer = self.catalogue(world_id).item(f"{base_name}|{base_source}")
            if offer is not None:
                value += int(offer.data.get("value") or 0)
        return value

    def magic_roll(
        self, world_id: str | None, rarity: str, count: int, dice: Dice | None = None
    ) -> list[tuple[str, int]]:
        """``count`` magic items of ``rarity`` drawn from the world's books, with their
        prices: ``(reference, copper)``."""
        dice = dice or Dice()
        world = self.gamestate.worlds.find(world_id) if world_id else None
        source_set = world.source_set if world is not None else None
        resolver = self.compendium.resolver()
        pool = []
        for item in self.compendium.all("item"):
            data = item.model_dump(by_alias=True, exclude_none=True)
            if rarity_of(data.get("rarity")) != rarity or data.get("sentient"):
                continue
            name, source = str(data.get("name", "")), str(data.get("source", ""))
            found = resolver.resolve_parts("item", name, None, source_set).entry
            if found is None or str(getattr(found, "source", "")) != source:
                continue
            pool.append((name, source))
        out = []
        for _ in range(min(count, len(pool))):
            name, source = pool.pop(dice.die(len(pool)) - 1)
            out.append((f"{name}|{source}", self.magic_price(world_id, name, source)))
        return out

    @cached_property
    def shops(self) -> ShopService:
        """The shop each world's gamemaster opens (app/services/shop.py)."""
        return ShopService(self.gamestate, self.protocol)

    @cached_property
    def chases(self) -> ChaseService:
        """The chase each world's gamemaster runs (app/services/chase.py)."""
        characters = self.characters
        by = Actor("Paravent", username="")

        def sheet_roll(character_id: str, kind: str, subject: str, edge: Advantage) -> int:
            advantage = {Advantage.ADVANTAGE: 1, Advantage.DISADVANTAGE: -1}.get(edge, 0)
            total = characters.roll_for(
                character_id, kind, subject, by, private=False, advantage=advantage
            )
            return total or 0

        def tire(character_id: str) -> int:
            return characters.act_for(
                character_id, "exhaustion", {"delta": "1"}, by
            ).state.exhaustion

        def changed(world_id: str) -> None:
            # Every sheet of the world: a runner's shows the chase, and one that ran
            # loses it when the chase ends.
            chase = self.gamestate.chases.find(world_id)
            state = f"chase-{chase.updated_at.isoformat() if chase else 'none'}"
            for pc in self.gamestate.characters.of_world(world_id):
                self.events.publish(pc.id, state)
            self.events.publish(PARTY, f"*chase {world_id}")

        return ChaseService(
            self.gamestate, self.protocol, sheet_roll=sheet_roll, tire=tire, changed=changed
        )

    def chase_complications(self, world_id: str | None, environment: str) -> tuple[Rows, int]:
        """The world's chase complications for an environment and the die they are
        rolled with: the 2024 guide's (d12) or the 2014 guide's (d20), the world's own
        rules first; no rows without either."""
        caption = f"{environment.title()} Chase Complications"
        books = [("XDMG", 12), ("DMG", 20)]
        if self.rules_of(world_id) != "one":
            books.reverse()
        for book, die in books:
            rows = self.book_table(world_id, caption, [book])
            if rows:
                return rows, die
        return [], 20

    def chase_translations(self, world_id: str | None, environment: str) -> dict[str, Rows]:
        """The world's chase complications for an environment in every language that
        translates the table (TRANSLATION.md), by language."""
        rows, die = self.chase_complications(world_id, environment)
        if not rows:
            return {}
        caption = f"{environment.title()} Chase Complications".lower()
        source = "xdmg" if die == 12 else "dmg"
        table = next(
            (
                t
                for t in self.compendium.all("table")
                if str(getattr(t, "source", "")).lower() == source
                and (
                    str(getattr(t, "name", "")).lower() == caption
                    or str(getattr(t, "name", "")).lower().endswith(f"; {caption}")
                )
            ),
            None,
        )
        if table is None:
            return {}
        data = table.model_dump(by_alias=True, exclude_none=True)
        key = record_key(str(getattr(table, "name", "")), str(getattr(table, "source", "")))
        out: dict[str, Rows] = {}
        for lang in self.translations.languages():
            if lang == "en":
                continue
            shown = self.translations.overlay(lang, "table", key, data, ("rows",))
            if shown.coverage.translated:
                out[lang] = rows_of(SimpleNamespace(rows=shown.data.get("rows")))
        return out

    def chase_runner(self, world_id: str | None, line: str, side: str) -> list[Runner]:
        """The creatures a line of the chase form names ("2 Thug", "Thug|MM"), from the
        world's books, with what a chase needs of their statblock; unknown names run with
        plain numbers."""
        count_text, _, rest = line.strip().partition(" ")
        count = int(count_text) if count_text.isdigit() and rest else 1
        name, _, source = (rest if count_text.isdigit() and rest else line).strip().partition("|")
        if not name:
            return []
        world = self.gamestate.worlds.find(world_id) if world_id else None
        found = (
            self.references.resolver()
            .resolve_parts("monster", name, source or None, world.source_set if world else None)
            .entry
        )
        stats = Runner(name=name, side=side)
        if found is not None:
            data = found.model_dump(by_alias=True, exclude_none=True)
            dex = ability_modifier(score_of(data.get("dex")))
            wis = ability_modifier(score_of(data.get("wis")))
            stealth = str((data.get("skill") or {}).get("stealth", "")).strip()
            passive = data.get("passive")
            stats = Runner(
                name=str(found.name),
                side=side,
                monster=f"{found.name}|{found.source}",
                initiative_bonus=dex,
                con_mod=ability_modifier(score_of(data.get("con"))),
                stealth=int(stealth) if stealth.lstrip("+-").isdigit() else dex,
                passive=passive if isinstance(passive, int) else 10 + wis,
            )
        count = max(1, min(count, 20))
        if count == 1:
            return [stats]
        return [replace(stats, name=f"{stats.name} {n}") for n in range(1, count + 1)]

    @cached_property
    def play_sessions(self) -> PlaySessionService:
        """Each world's day of play: the briefing and the closing note
        (app/services/play_session.py)."""
        return PlaySessionService(
            self.gamestate,
            self.protocol,
            minute_of=lambda world_id: self.clock.of(world_id).minute,
            timers_of=self.clock.active,
        )

    @cached_property
    def story(self) -> StoryService:
        """Each character's story (app/services/story.py)."""
        return StoryService(
            self.gamestate,
            quests=self.quests.shown_to,
            standing=self.factions.standing,
            log=self.protocol.since,
        )

    @cached_property
    def quests(self) -> QuestService:
        """Each world's quests (app/services/quests.py)."""
        return QuestService(self.gamestate, self.protocol)

    @cached_property
    def factions(self) -> FactionService:
        """Each world's factions and renown (app/services/factions.py)."""
        return FactionService(self.gamestate, self.protocol)

    def renown_tables(self, world_id: str | None) -> list[tuple[str, str]]:
        """The renown tables the world's books print, ``(name, source)``: a faction's
        rewards by renown score."""
        world = self.gamestate.worlds.find(world_id) if world_id else None
        out = []
        for table in self.compendium.all("table"):
            data = table.model_dump(by_alias=True, exclude_none=True)
            labels = [str(c) for c in data.get("colLabels") or []]
            source = str(data.get("source", ""))
            if (
                str(data.get("name", "")).endswith(" Renown")
                and labels[:2] == ["Renown Score", "Reward"]
                and (
                    world is None
                    or world.source_set.is_unrestricted
                    or source.lower() in world.source_set.normalized
                )
            ):
                out.append((str(data["name"]), source))
        return sorted(out)

    def faction_ranks(self, world_id: str | None) -> dict[str, list[FactionRank]]:
        """The 2014 guide's example factions and their titles, where the world has it."""
        world = self.gamestate.worlds.find(world_id) if world_id else None
        if (
            world is not None
            and not world.source_set.is_unrestricted
            and "dmg" not in world.source_set.normalized
        ):
            return {}
        for table in self.compendium.all("table"):
            if (
                str(getattr(table, "name", "")) == "Examples of Faction Ranks"
                and str(getattr(table, "source", "")) == "DMG"
            ):
                data = table.model_dump(by_alias=True, exclude_none=True)
                labels = [str(c) for c in data.get("colLabels") or []]
                return ranks_from(labels, rows_of(table))
        return {}

    def renown_rewards(self, world_id: str | None, faction: Faction) -> list[Reward]:
        """A faction's rewards, from its book table."""
        name, _, source = faction.table.partition("|")
        if not name:
            return []
        rows = self.book_table(world_id, name, [source]) if source else None
        return rewards_of(rows or [])

    def spell_data(self, world_id: str | None) -> Callable[[str, str], dict[str, Any] | None]:
        """A finder of spells in the world's books, as plain data (a trap that works as
        one, app/services/traps.py)."""
        world = self.gamestate.worlds.find(world_id) if world_id else None
        books = world.source_set if world else None
        resolver = self.references.resolver()

        def find(name: str, source: str) -> dict[str, Any] | None:
            entry = resolver.resolve_parts("spell", name, source or None, books).entry
            return entry.model_dump(by_alias=True, exclude_none=True) if entry else None

        return find

    @cached_property
    def contacts(self) -> ContactService:
        """Whom each character knows (app/services/contacts.py)."""
        return ContactService(self.gamestate, self.protocol)

    @cached_property
    def traps(self) -> TrapService:
        """Traps and hazards sprung on characters (app/services/traps.py)."""
        characters = self.characters
        by = Actor("Paravent", username="")

        def save(character_id: str, ability: str) -> int:
            return characters.roll_for(character_id, "save", ability, by, private=False) or 0

        def check(character_id: str, skill: str) -> int:
            return characters.roll_for(character_id, "skill", skill, by, private=False) or 0

        def hurt(character_id: str, amount: int) -> None:
            characters.act_for(character_id, "hp", {"mode": "damage", "amount": str(amount)}, by)

        def afflict(character_id: str, condition: str) -> None:
            pc = self.gamestate.characters.find(character_id)
            if pc is not None and condition in CONDITIONS and condition not in pc.state.conditions:
                characters.act_for(character_id, "condition", {"key": condition}, by)

        return TrapService(self.protocol, save=save, hurt=hurt, afflict=afflict, check=check)

    @cached_property
    def afflictions(self) -> AfflictionService:
        """Diseases, poisons and curses on characters (app/services/afflictions.py)."""
        characters = self.characters
        by = Actor("Paravent", username="")

        def save(character_id: str, ability: str) -> int:
            return characters.roll_for(character_id, "save", ability, by, private=False) or 0

        def hurt(character_id: str, amount: int) -> None:
            characters.act_for(character_id, "hp", {"mode": "damage", "amount": str(amount)}, by)

        def now(world_id: str) -> int:
            return self.clock.of(world_id).minute

        return AfflictionService(
            self.gamestate,
            self.protocol,
            now=now,
            save=save,
            hurt=hurt,
            conditions=frozenset(CONDITIONS),
        )

    @cached_property
    def sentient(self) -> SentientService:
        """Conflicts between sentient items and their bearers (app/services/sentient.py)."""
        characters = self.characters
        by = Actor("Paravent", username="")

        def roll(character_id: str, kind: str, ability: str) -> int:
            return characters.roll_for(character_id, kind, ability, by, private=False) or 0

        return SentientService(self.protocol, self.afflictions, roll=roll)

    def sentient_guide(self, world_id: str | None) -> Guide:
        """The guide a world makes sentient items and artifacts by: its rules' guide first."""

        def find(caption: str, sources: Sequence[str]) -> Rows | None:
            return self.book_table(world_id, caption, sources)

        return guide_for(find, self.rules_of(world_id) == "one")

    @cached_property
    def formulas(self) -> Formulas:
        """Magic item formulas the gamemaster gives (app/services/magic_crafting.py)."""
        return Formulas(self.gamestate, self.protocol)

    @cached_property
    def gifts(self) -> GiftService:
        """Blessings, charms and boons given to characters (app/services/gifts.py)."""
        return GiftService(
            self.gamestate, self.protocol, now=lambda world_id: self.clock.of(world_id).minute
        )

    @cached_property
    def title_images(self) -> TitleImages:
        """Title images of places, encounters, quests and factions."""
        return TitleImages(self.gamestate, self.locations)

    @cached_property
    def safety(self) -> SafetyService:
        """The X-card and each world's lines and veils (app/services/safety.py)."""
        return SafetyService(self.gamestate, self.protocol)

    @cached_property
    def party_notes(self) -> PartyNotesService:
        """The notes each world's party keeps together (the players' journal)."""
        return PartyNotesService(self.gamestate)

    @cached_property
    def checks(self) -> CheckService:
        """Checks the gamemaster calls for outside a fight (app/services/checks.py)."""
        return CheckService(
            self.gamestate,
            roll=lambda character, kind, subject, by, _secret: self.characters.roll_for(
                character, kind, subject, by
            ),
            changed=self._check_changed,
        )

    def _check_changed(self, call: CheckCall) -> None:
        """A call opened, answered or closed: the asked characters' sheets and the
        party view follow."""
        state = f"check-{call.id}-{len(call.results)}-{int(call.open)}"
        for character_id in call.characters:
            self.events.publish(character_id, state)
        self.events.publish(PARTY, f"*checks {call.id}")

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
            shapes=lambda world_id, spell, cr: polymorph.shapes(
                self.compendium, self.references.source_set(world_id), spell, cr
            ),
            table=self.book_table,
            injure=lambda *args: self.afflictions.injure(*args),
            objects_of=lambda world_id: [o.data for o in self.catalogue(world_id).rows("object")],
        )

    def summoning_for(self, spell: str) -> Summoning | None:
        """What a spell (``"Summon Beast|XPHB"``) summons, read once per corpus load."""
        generation = self.compendium.generation
        if self._summonings is None or self._summonings[0] != generation:
            found: dict[str, Summoning] = {}
            for monster in self.compendium.monsters():
                data = monster.entry.model_dump(by_alias=True, exclude_none=True)
                made = summoning(data)
                if made is not None:
                    found.setdefault(made.spell.lower(), made)
            self._summonings = (generation, found)
        return self._summonings[1].get(spell.lower())

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

    @cached_property
    def news(self) -> NewsFeed:
        """What changed in Paravent lately (``app/news.json``, from the commits)."""
        return NewsFeed()

    @cached_property
    def trash(self) -> Trash:
        """Deleted NPCs, places, treasure and encounters, kept a while to restore."""
        return Trash(self.gamestate)

    @cached_property
    def world_copy(self) -> WorldCopy:
        """One piece of a world copied or moved into another (app/services/world_copy.py)."""

        def delete(kind: str, record_id: str) -> None:
            if kind == "adventure":
                self.adventures.delete(record_id, contents=True)
            elif kind == "npc":
                self.npcs.delete(record_id)
            elif kind == "place":
                self.locations.delete(record_id)
            elif kind == "treasure":
                self.treasure.delete(record_id)
            elif kind == "encounter":
                self.encounters.delete(record_id)

        return WorldCopy(self.gamestate, self.tokens.folder, delete)

    @cached_property
    def world_transfer(self) -> WorldTransfer:
        """A world exported, imported or duplicated (documentation/CURRENT-WORLD.md §9)."""
        return WorldTransfer(
            self.gamestate,
            self.settings.library_dir / "protocol",
            self.settings.library_dir / "handouts",
        )

    @cached_property
    def table(self) -> TableService:
        """The world the whole table plays in (documentation/CURRENT-WORLD.md)."""
        return TableService(self.gamestate, self._table_world_changed)

    def _table_world_changed(self, world: World | None) -> None:
        """Tell every open page the world the table now plays in."""
        self.events.publish(TABLE, f"{world.id} {world.name}" if world is not None else "")

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
        self._tables = None
        self._vessels = {}
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
            key = fingerprint(roots, __version__, CORPUS_CODE)
            snapshot = Snapshot(self.settings.reference_cache_dir, key)
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


def _scroll_ref(level: int, book: str) -> str:
    """The spell scroll item of a spell level, in the 2014 or the 2024 guide."""
    if level == 0:
        return f"Spell Scroll (Cantrip)|{book}"
    if book == "XDMG":
        return f"Spell Scroll (Level {level})|XDMG"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(level, "th")
    return f"Spell Scroll ({level}{suffix} Level)|DMG"


def both_row(rows: Rows, roll: int) -> list[str]:
    """The row a d20 lands on, padded so every column reads."""
    row = row_for(rows, roll) or (rows[0] if rows else [])
    return [*row, "", "", "", ""]


def _number(text: str) -> int | None:
    """The first whole number in a table's cell ("15", "DC 10"); None for none."""
    found = re.search(r"\d+", strip_tags(text))
    return int(found[0]) if found else None


__all__ = ["ReferenceRootViolation", "StoreRegistry"]
