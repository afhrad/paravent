# ruff: noqa: E501 - captions are sentences, kept whole on their line
"""Every word the videos show, per language: titles, captions, labels.

A script names its texts by key; the take's language picks the set. Both languages have
the same keys (``test_film`` holds them to it).
"""

from __future__ import annotations

LABELS = {
    "en": {
        "gm": "Game master · laptop",
        "lyra": "Mara plays Lyra · phone",
        "brunhild": "Jonas plays Brunhild · phone",
    },
    "de": {
        "gm": "Spielleitung · Laptop",
        "lyra": "Mara spielt Lyra · Handy",
        "brunhild": "Jonas spielt Brunhild · Handy",
    },
}

EPISODES: dict[str, dict[str, dict[str, str]]] = {
    "fight": {
        "en": {
            "title": "A fight on two screens",
            "subtitle": "The game master's tracker and a player's phone, live",
            "intro": "The fight runs on the game master's screen. Every player follows it on their own phone.",
            "rolled": "Brunhild and Varis rolled at the table; their numbers are already in.",
            "lyra_rolls": "Lyra rolls initiative on her phone …",
            "arrives": "… and it arrives in the tracker by itself.",
            "monsters": "The goblins and their boss roll with one press each.",
            "begin": "Begin: the order is set, the first turn is marked.",
            "next": "Next turn moves on. Whose turn it is shows on every phone.",
            "foe": "On a goblin's turn its statblock sits beside the order.",
            "attack": "One press rolls the attack and its damage.",
            "target": "The game master picks the target and decides: hit.",
            "landed": "The damage lands on Lyra's sheet at once.",
            "your_turn": "Lyra's turn: her phone says so.",
            "fire_bolt": "She casts Fire Bolt from her sheet. The roll goes to the tracker.",
            "verdict": "The game master sees the roll and decides whether it hits.",
            "burned": "Hit: the damage is taken off the goblin, and the log keeps every roll.",
            "outro": "Initiative, attacks, damage and turns, shared live between the table's screens.",
            "end_title": "Paravent",
            "end_subtitle": "paravent.org",
        },
        "de": {
            "title": "Ein Kampf auf zwei Bildschirmen",
            "subtitle": "Der Kampf-Tracker der Spielleitung und das Handy eines Spielers, live",
            "intro": "Der Kampf läuft auf dem Bildschirm der Spielleitung. Jeder Spieler verfolgt ihn auf dem eigenen Handy.",
            "rolled": "Brunhild und Varis haben am Tisch gewürfelt; ihre Werte stehen schon drin.",
            "lyra_rolls": "Lyra würfelt die Initiative auf ihrem Handy …",
            "arrives": "… und sie landet von selbst im Tracker.",
            "monsters": "Die Goblins und ihr Anführer würfeln mit je einem Druck.",
            "begin": "Beginnen: Die Reihenfolge steht, der erste Zug ist markiert.",
            "next": "Nächster Zug geht weiter. Wer dran ist, zeigt jedes Handy.",
            "foe": "Ist ein Goblin am Zug, steht sein Wertekasten neben der Reihenfolge.",
            "attack": "Ein Druck würfelt den Angriff und seinen Schaden.",
            "target": "Die Spielleitung wählt das Ziel und entscheidet: Treffer.",
            "landed": "Der Schaden landet sofort auf Lyras Bogen.",
            "your_turn": "Lyra ist dran: Ihr Handy sagt es ihr.",
            "fire_bolt": "Sie wirkt Feuerpfeil von ihrem Bogen. Der Wurf geht an den Tracker.",
            "verdict": "Die Spielleitung sieht den Wurf und entscheidet, ob er trifft.",
            "burned": "Treffer: Der Schaden geht vom Goblin ab, und das Protokoll behält jeden Wurf.",
            "outro": "Initiative, Angriffe, Schaden und Züge, live geteilt zwischen den Bildschirmen am Tisch.",
            "end_title": "Paravent",
            "end_subtitle": "paravent.org",
        },
    },
}


MORE: dict[str, dict[str, dict[str, str]]] = {
    "phone": {
        "en": {
            "title": "A player's evening on the phone",
            "subtitle": "The character sheet in your pocket",
            "intro": "Each player keeps their character on their phone. Every number on it is worked out from the books.",
            "stats": "Abilities, saving throws and skills. A tap rolls.",
            "rolled": "The roll lands in the log with every bonus named. The game master sees it too.",
            "play": "The spells, with what each one does and the slots left.",
            "cast": "Cast spends the slot by itself.",
            "track": "Slots, hit dice, conditions and rests have their own page.",
            "rest": "A short rest brings back what a short rest brings back.",
            "log": "The log keeps every roll of the evening, the table's and your own.",
            "handout": "When the game master shares a handout, it pops up at once …",
            "journal": "… and stays in the journal, to read again later.",
            "outro": "Hit points, spells, rolls and handouts: the whole character, live, on the phone.",
            "end_title": "Paravent",
            "end_subtitle": "paravent.org",
        },
        "de": {
            "title": "Ein Spielabend auf dem Handy",
            "subtitle": "Der Charakterbogen in der Hosentasche",
            "intro": "Jeder Spieler hat seinen Charakter auf dem Handy. Jeder Wert darauf wird aus den Büchern berechnet.",
            "stats": "Attribute, Rettungswürfe und Fertigkeiten. Ein Tippen würfelt.",
            "rolled": "Der Wurf landet im Protokoll, jeder Bonus genannt. Die Spielleitung sieht ihn auch.",
            "play": "Die Zauber, mit ihrer Wirkung und den freien Plätzen.",
            "cast": "Wirken verbraucht den Zauberplatz von selbst.",
            "track": "Zauberplätze, Trefferwürfel, Zustände und Rasten haben eine eigene Seite.",
            "rest": "Eine kurze Rast bringt zurück, was eine kurze Rast zurückbringt.",
            "log": "Das Protokoll behält jeden Wurf des Abends, den des Tisches und die eigenen.",
            "handout": "Teilt die Spielleitung ein Handout, erscheint es sofort …",
            "journal": "… und bleibt im Journal, zum späteren Nachlesen.",
            "outro": "Trefferpunkte, Zauber, Würfe und Handouts: der ganze Charakter, live, auf dem Handy.",
            "end_title": "Paravent",
            "end_subtitle": "paravent.org",
        },
    },
    "adventure": {
        "en": {
            "title": "Writing an adventure",
            "subtitle": "Chapters in Markdown, references that resolve, handouts in one click",
            "intro": "A chapter is written in Markdown, with a preview beside it as you type.",
            "typing": "A read-aloud box is a callout; a creature is a reference in braces.",
            "new_text": "## The hut\n\nThe door hangs open. Inside, a {@creature Goblin} sleeps over the toll box.\n\n> [!read]\n> The hut smells of wet straw and old smoke. Something under the floor is breathing.\n",
            "preview": "The preview resolves the reference against the world's books: hover it, and its statblock shows.",
            "save": "Saved, the chapter reads as the game master will use it at the table.",
            "share": "Every read-aloud box can be shared with the players.",
            "choose": "Pick who gets it, give it a title, share.",
            "sent": "It arrives on the phone at once, and stays in the journal.",
            "handout_title": "The hut",
            "outro": "Write once, read at the table, hand out with one click.",
            "end_title": "Paravent",
            "end_subtitle": "paravent.org",
        },
        "de": {
            "title": "Ein Abenteuer schreiben",
            "subtitle": "Kapitel in Markdown, Verweise, die aufgehen, Handouts mit einem Klick",
            "intro": "Ein Kapitel wird in Markdown geschrieben, mit der Vorschau gleich daneben.",
            "typing": "Ein Vorlesetext ist ein Hinweiskasten; eine Kreatur ein Verweis in geschweiften Klammern.",
            "new_text": "## Die Hütte\n\nDie Tür hängt offen. Drinnen schläft ein {@creature Goblin} über der Mautkiste.\n\n> [!read]\n> Die Hütte riecht nach nassem Stroh und altem Rauch. Unter dem Boden atmet etwas.\n",
            "preview": "Die Vorschau löst den Verweis in den Büchern der Welt auf: darüberfahren, und der Wertekasten erscheint.",
            "save": "Gespeichert liest sich das Kapitel so, wie die Spielleitung es am Tisch braucht.",
            "share": "Jeder Vorlesetext lässt sich mit den Spielern teilen.",
            "choose": "Wer ihn bekommt, ein Titel, teilen.",
            "sent": "Er kommt sofort auf dem Handy an und bleibt im Journal.",
            "handout_title": "Die Hütte",
            "outro": "Einmal schreiben, am Tisch lesen, mit einem Klick austeilen.",
            "end_title": "Paravent",
            "end_subtitle": "paravent.org",
        },
    },
    "between": {
        "en": {
            "title": "Between the fights",
            "subtitle": "Time, the road, a shop and downtime",
            "intro": "The world keeps its own time. The game master moves it on from the party view.",
            "clock": "An hour passes; every sheet shows the new time.",
            "travel": "A day on the road: the miles, the weather, rations eaten, a check for encounters.",
            "travelled": "The journey stays on the panel and goes into the log.",
            "shop": "In town the game master opens a shop, for everyone or a few.",
            "shop_name": "The Gilded Quill",
            "browse": "The players find it on their sheet and buy what the books sell.",
            "bought": "The price comes out of the purse, with change.",
            "downtime": "Days off are granted to everyone at once.",
            "project": "Each player spends them on their own: crafting, training, research or one of the book's activities.",
            "research_label": "The drowned bell",
            "spent": "Days spent, the project moves on, and the log says so.",
            "outro": "Time, travel, shopping and downtime, kept by the table instead of on scraps of paper.",
            "end_title": "Paravent",
            "end_subtitle": "paravent.org",
        },
        "de": {
            "title": "Zwischen den Kämpfen",
            "subtitle": "Zeit, Reise, ein Laden und Auszeit",
            "intro": "Die Welt hat ihre eigene Zeit. Die Spielleitung lässt sie in der Gruppenansicht verstreichen.",
            "clock": "Eine Stunde vergeht; jeder Bogen zeigt die neue Uhrzeit.",
            "travel": "Ein Reisetag: die Meilen, das Wetter, gegessene Rationen, ein Wurf auf Begegnungen.",
            "travelled": "Die Reise bleibt im Panel stehen und geht ins Protokoll.",
            "shop": "In der Stadt öffnet die Spielleitung einen Laden, für alle oder für einige.",
            "shop_name": "Die Goldene Feder",
            "browse": "Die Spieler finden ihn auf ihrem Bogen und kaufen, was die Bücher verkaufen.",
            "bought": "Der Preis geht vom Geldbeutel ab, mit Wechselgeld.",
            "downtime": "Freie Tage werden allen auf einmal gewährt.",
            "project": "Jeder verbringt sie für sich: Handwerk, Ausbildung, Nachforschung oder eine der Tätigkeiten aus dem Buch.",
            "research_label": "Die ertrunkene Glocke",
            "spent": "Tage verbracht, das Vorhaben kommt voran, und das Protokoll hält es fest.",
            "outro": "Zeit, Reise, Einkauf und Auszeit, geführt vom Tisch statt auf Zetteln.",
            "end_title": "Paravent",
            "end_subtitle": "paravent.org",
        },
    },
    "start": {
        "en": {
            "title": "The first evening",
            "subtitle": "A world, its books, and a character made on the phone",
            "intro": "Everything starts with a world: its name, its language, and the books the table plays with.",
            "world_name": "The Sunken Marches",
            "world_text": "River country where the old dam broke.",
            "books": "The books decide which spell, which creature, which rule a reference means.",
            "created": "The world is there. Its players can now make their characters in it.",
            "wizard": "On her phone, Mara starts a character in the new world.",
            "species": "Step by step: first the species …",
            "class": "… then the class …",
            "abilities": "… then the ability scores, by the standard array, point buy, dice or by hand.",
            "more": "Background, skills, equipment and spells follow, each explained, to a finished sheet.",
            "sheet": "The finished character: every value derived, ready for the first session.",
            "outro": "A world, its books and its characters, set up in one evening.",
            "end_title": "Paravent",
            "end_subtitle": "paravent.org",
        },
        "de": {
            "title": "Der erste Abend",
            "subtitle": "Eine Welt, ihre Bücher und ein Charakter vom Handy",
            "intro": "Alles beginnt mit einer Welt: ihr Name, ihre Sprache und die Bücher, mit denen der Tisch spielt.",
            "world_name": "Die Versunkenen Marschen",
            "world_text": "Flussland, in dem der alte Damm brach.",
            "books": "Die Bücher entscheiden, welcher Zauber, welche Kreatur, welche Regel ein Verweis meint.",
            "created": "Die Welt steht. Ihre Spieler können jetzt ihre Charaktere darin erschaffen.",
            "wizard": "Auf ihrem Handy beginnt Mara einen Charakter in der neuen Welt.",
            "species": "Schritt für Schritt: erst das Volk …",
            "class": "… dann die Klasse …",
            "abilities": "… dann die Attributswerte, nach Standardreihe, Punktekauf, Würfeln oder von Hand.",
            "more": "Hintergrund, Fertigkeiten, Ausrüstung und Zauber folgen, jeder erklärt, bis zum fertigen Bogen.",
            "sheet": "Der fertige Charakter: jeder Wert abgeleitet, bereit für die erste Sitzung.",
            "outro": "Eine Welt, ihre Bücher und ihre Charaktere, an einem Abend eingerichtet.",
            "end_title": "Paravent",
            "end_subtitle": "paravent.org",
        },
    },
    "session": {
        "en": {
            "title": "A session at the table",
            "subtitle": "“The Drowned Bell”, played with a game master and two phones",
            "intro": "One game master, two players, a short adventure of our own. Everything below happens live.",
            "start": "The session starts: the clock, the log and the session note begin here.",
            "read": "The game master reads the opening aloud, and hands the text out.",
            "shared": "Both players have it on their phones at once.",
            "check": "Something waits in the reeds. The game master asks for a Perception check, the DC hidden.",
            "rolls": "Each player rolls on their own sheet …",
            "results": "… and the totals come back to the game master, pass or fail.",
            "fight": "Goblins. The encounter is run from the chapter, the party already in it.",
            "initiative": "Initiative from the phones, the goblins and their wolf with a press.",
            "goblin": "A goblin strikes at Brunhild; the game master calls it a hit.",
            "brunhild": "Brunhild answers with her longsword, from her phone.",
            "quick": "The rest goes quickly: hit points come off with a number and a press.",
            "end": "The fight is over. Its summary counts the experience …",
            "award": "… and the game master hands it out, once, to everyone who fought.",
            "treasure": "The toll box: the potion to Lyra, the coins split between the two.",
            "shared_out": "It lands in their packs and purses.",
            "close": "The session ends; its note writes itself from what the log saw.",
            "note": "The note: the fight, the experience, what each received. Ready to send to the players.",
            "outro": "A whole evening, with nothing kept on paper and every screen in step.",
            "end_title": "Paravent",
            "end_subtitle": "paravent.org",
        },
        "de": {
            "title": "Eine Sitzung am Tisch",
            "subtitle": "„Die ertrunkene Glocke“, gespielt mit einer Spielleitung und zwei Handys",
            "intro": "Eine Spielleitung, zwei Spieler, ein kurzes eigenes Abenteuer. Alles hier geschieht live.",
            "start": "Die Sitzung beginnt: Uhr, Protokoll und Sitzungsnotiz setzen hier an.",
            "read": "Die Spielleitung liest den Anfang vor und teilt den Text aus.",
            "shared": "Beide Spieler haben ihn sofort auf dem Handy.",
            "check": "Im Schilf lauert etwas. Die Spielleitung verlangt einen Wahrnehmungswurf, der SG verborgen.",
            "rolls": "Jeder Spieler würfelt auf seinem eigenen Bogen …",
            "results": "… und die Ergebnisse kommen bei der Spielleitung an, geschafft oder nicht.",
            "fight": "Goblins. Die Begegnung startet aus dem Kapitel, die Gruppe ist schon dabei.",
            "initiative": "Initiative von den Handys, Goblins und Wolf mit einem Druck.",
            "goblin": "Ein Goblin schlägt nach Brunhild; die Spielleitung wertet: Treffer.",
            "brunhild": "Brunhild antwortet mit dem Langschwert, vom Handy aus.",
            "quick": "Der Rest geht schnell: Trefferpunkte gehen mit einer Zahl und einem Druck ab.",
            "end": "Der Kampf ist vorbei. Seine Zusammenfassung zählt die Erfahrung …",
            "award": "… und die Spielleitung verteilt sie, einmal, an alle, die gekämpft haben.",
            "treasure": "Die Mautkiste: der Trank an Lyra, die Münzen geteilt zwischen beiden.",
            "shared_out": "Alles landet in Gepäck und Geldbeutel.",
            "close": "Die Sitzung endet; ihre Notiz schreibt sich aus dem, was das Protokoll gesehen hat.",
            "note": "Die Notiz: der Kampf, die Erfahrung, wer was bekam. Bereit für die Spieler.",
            "outro": "Ein ganzer Abend, nichts auf Papier, und jeder Bildschirm im Gleichschritt.",
            "end_title": "Paravent",
            "end_subtitle": "paravent.org",
        },
    },
}
EPISODES.update(MORE)

LANGUAGES = ("en", "de")


# The adventure the demo session plays: our own words, creatures of the System Reference
# Document 5.1 only (goblins, a wolf, skeletons). Paravent Markdown, as a chapter is written.
ADVENTURE: dict[str, dict[str, object]] = {
    "en": {
        "title": "The Drowned Bell",
        "npc": "Hob Wade",
        "npc_notes": "The ferryman of Grey Reed: broad, slow-spoken, owes everyone a little money. He knows where the old chapel lies under the river.",
        "encounter": "Goblins in the Reeds",
        "goblins": ("Snik", "Grub", "Vexa"),
        "wolf": "Ashfang",
        "treasure": "The toll box",
        "handout": "Fog on the river",
        "chapters": [
            {
                "title": "The Ferry at Grey Reed",
                "body": """\
The ferry at Grey Reed has not crossed in three days. Its lantern hangs unlit on the post,
and the rope across the river is cut clean through on this side.

> [!read]
> Fog lies on the river like wet wool. Somewhere out on the water a bell tolls, slow and
> drowned, and the reeds along the bank go quiet all at once. On the landing a lantern
> swings, though there is no wind.

## The landing

Three goblins of the Rotfang band hold the ferryman's hut, with a wolf they call Ashfang.
They watch from the reeds; a DC 13 Wisdom (Perception) check spots the glint of a blade
before they strike.

{@encounter Goblins in the Reeds}

{@npc Hob Wade} lies tied up behind the hut. Freed, he ferries the party across at dawn.

{@treasure The toll box}
""",
            },
            {
                "title": "The Drowned Chapel",
                "body": """\
Across the river the old chapel tower rises from the water, the rest of it under the
flood since the dam broke. The bell still hangs in the tower.

> [!read]
> Water stands knee-deep in the nave. Pale shapes sit in the pews, as if waiting for a
> service that never came, and every one of them turns its head as you step inside.

Four skeletons rise from the pews when anyone reaches the altar. Whoever rings the bell
on purpose lays them to rest at once.

> [!secret]
> The bell rings itself at the new moon: the last priest is buried beneath it and wants
> the chapel's doors opened again.
""",
            },
        ],
    },
    "de": {
        "title": "Die ertrunkene Glocke",
        "npc": "Hob Wade",
        "npc_notes": "Der Fährmann von Grauried: breit, bedächtig, schuldet jedem ein wenig Geld. Er weiß, wo die alte Kapelle unter dem Fluss liegt.",
        "encounter": "Goblins im Schilf",
        "goblins": ("Snik", "Grub", "Vexa"),
        "wolf": "Aschezahn",
        "treasure": "Die Mautkiste",
        "handout": "Nebel auf dem Fluss",
        "chapters": [
            {
                "title": "Die Fähre bei Grauried",
                "body": """\
Die Fähre bei Grauried hat seit drei Tagen nicht übergesetzt. Ihre Laterne hängt kalt am
Pfahl, und das Seil über den Fluss ist auf dieser Seite glatt durchtrennt.

> [!read]
> Nebel liegt auf dem Fluss wie nasse Wolle. Irgendwo draußen auf dem Wasser schlägt eine
> Glocke, langsam und ertrunken, und das Schilf am Ufer verstummt auf einen Schlag. Am
> Anleger schwingt eine Laterne, obwohl kein Wind geht.

## Der Anleger

Drei Goblins der Faulzahn-Bande halten die Hütte des Fährmanns, mit einem Wolf, den sie
Aschezahn nennen. Sie lauern im Schilf; ein Wurf auf Weisheit (Wahrnehmung) gegen SG 13
erspäht das Blitzen einer Klinge, bevor sie zuschlagen.

{@encounter Goblins im Schilf}

{@npc Hob Wade} liegt gefesselt hinter der Hütte. Befreit, bringt er die Gruppe im
Morgengrauen hinüber.

{@treasure Die Mautkiste}
""",
            },
            {
                "title": "Die versunkene Kapelle",
                "body": """\
Jenseits des Flusses ragt der alte Kapellturm aus dem Wasser; der Rest liegt unter der
Flut, seit der Damm brach. Die Glocke hängt noch im Turm.

> [!read]
> Knietief steht das Wasser im Kirchenschiff. Bleiche Gestalten sitzen in den Bänken, als
> warteten sie auf einen Gottesdienst, der nie kam, und jede von ihnen dreht den Kopf, als
> ihr eintretet.

Vier Skelette erheben sich aus den Bänken, sobald jemand den Altar erreicht. Wer die
Glocke mit Absicht läutet, legt sie sofort zur Ruhe.

> [!secret]
> Die Glocke läutet sich bei Neumond selbst: Der letzte Priester liegt unter ihr begraben
> und will, dass die Türen der Kapelle wieder offen stehen.
""",
            },
        ],
    },
}
__all__ = ["ADVENTURE", "EPISODES", "LABELS", "LANGUAGES"]
