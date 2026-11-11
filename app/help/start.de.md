# Erste Schritte

Von einem leeren Paravent bis zum ersten Abend am Tisch, in der Reihenfolge, in der du es
brauchst.

Paravent steht neben eurem Spiel, nicht an seiner Stelle. Du erzählst weiter die
Geschichte, würfelst, womit du magst, und entscheidest, was passiert. Paravent hält Zahlen,
Notizen und Handouts in Ordnung, damit dir mehr Aufmerksamkeit für die Leute am Tisch
bleibt.

---

## 1. Die Daten, die du mitbringst

Paravent liefert keine D&D-Inhalte mit. Es liest die 5eTools-Daten aus einem Ordner, auf
den du es zeigen lässt (`PARAVENT_REFERENCE_DIR`), und schreibt dort nie etwas hinein.

Öffne das Zahnrad → *5eTools-Daten* und starte die Prüfung. Sie nennt jede Datei, die
sich nicht lesen ließ, und alles, was unterwegs verloren ging. Ist der Bericht sauber,
sind Monster, Zauber, Gegenstände und Regelbücher da.

## 2. Eine Welt und ihre Bücher

*Welten* → **Neue Welt**. Eine Welt ist deine Kampagne: ihr Name, ihre Sprache und vor
allem **die Bücher, mit denen ihr spielt**.

- **Hake die Bücher an, die euer Tisch nutzt.** *PHB* (2014) oder *XPHB* (2024)
  entscheidet die Edition. Jeder Verweis findet dann die richtige Fassung, und einer in ein
  Buch, das niemand besitzt, zeigt „nicht in deinen Büchern“, statt sich dort zu bedienen.
- Hakst du nichts an, zählt alles, was du eingelesen hast. Für den ersten Blick reicht das.
- **Spieler** neben einem Buch heißt: Deine Spieler dürfen es lesen. Handbücher und
  Ergänzungen sind schon angehakt, Monsterbücher und Abenteuer nicht.

## 3. Deine Spieler

Zahnrad → *Spieler* → **Neuer Spieler**: ein Name zum Anmelden, ein Passwort und eine
Sprache. Gib jeder Person ihre Anmeldung.

Die Spieler öffnen dieselbe Adresse auf ihren Handys. Auf dem iPhone macht *Teilen* →
*Zum Home-Bildschirm* daraus eine App mit eigenem Symbol. Alle im selben Netz, nichts in
der Cloud.

## 4. Charaktere

Die Spieler bauen ihre Charaktere selbst: Benutzermenü → **Neuer Charakter**. Der
Assistent führt durch Volk, Klasse, Attribute, Hintergrund, Ausrüstung und Zauber, nur aus
den Büchern ihrer Welt, und erklärt jede Zahl, die er zeigt.

Gibt es den Charakter schon auf Papier? *Charaktere* → **Neuer Charakter** legt eine leere
Datei an, die du von Hand füllst; was die Regeln nicht ausrechnen können (ein selbst
erfundener Ring), kommt als Überschreibung hinein.

*Spieltisch* ist deine Sicht auf die Gruppe: Trefferpunkte, Rüstungsklasse, Zustände, der
letzte Wurf, live, während gespielt wird. **Alle eine Stufe höher** vergibt eine Stufe;
jede Person beantwortet dann nur, was die neue Stufe mitbringt.

## 5. Das erste Kapitel

Öffne deine Welt → **Abenteuer anlegen** → **Kapitel anlegen**. Ein Kapitel ist
einfaches Markdown: Schreib es wie eine Notiz, den Rest macht Paravent.

- `{@creature goblin}` oder `{@spell fireball}` wird ein Link mit Vorschau. Tippst du
  `{@`, schlägt der Editor die Namen vor.
- `> [!read]` beginnt einen Kasten zum Vorlesen, `> [!secret]` einen, den nur du siehst.
- `{@npc Hob Wade}`, `{@place Grauried}` oder `{@encounter Hinterhalt an der Fähre}`
  nennt etwas Eigenes; die Prüfung neben der Vorschau bietet an, es anzulegen.
- Leitest du ein gekauftes Abenteuer? **Mit dem Buch beginnen** legt für jeden Abschnitt
  ein Kapitel an, und deine Notizen kommen dazwischen.

Ein Kasten oder Bild in einem Kapitel hat **Mit Spielern teilen**: Es landet in ihrem
*Journal* und bleibt dort.

## 6. Der erste Kampf

*Begegnungen* → **Neue Begegnung**, oder leg sie aus dem Kapitel heraus an. Füge Monster
über die Suche hinzu und sieh, wie sich die Schwierigkeit für eure tatsächliche Gruppe
ändert.

**Starten** beginnt den Kampf. Die Spieler würfeln Initiative auf dem Handy, der Tracker
hält Reihenfolge, Trefferpunkte und Zustände fest, und der Wertekasten öffnet sich daneben.
Ob ein Angriff trifft, entscheidest immer du. Den ganzen Rundgang findest du unter *So
geht's* im Tracker.

## 7. Schön zu haben

- **Klang:** Zahnrad → *Klangbibliothek* für Regen, Tavernen und Kampfmusik. Öffne die
  Seite **Lautsprecher** auf dem Gerät am Lautsprecher und spiel vom Klangpult in der
  oberen Leiste.
- **Namen:** Zahnrad → *Namen* für Namensstile je Volk, damit jeder Wirt einen Namen
  bekommt, der in deine Welt passt.
- **Aussehen:** *Themes*. Tome sieht aus wie die gedruckten Bücher, tagsüber Pergament,
  abends Leder.
- **Sicherungen:** Alles, was du machst, liegt in einem Ordner, der Bibliothek. Setz einmal
  `PARAVENT_BACKUP_DIR` (und `PARAVENT_BACKUP_AT` für die Uhrzeit), dann packt Paravent sie
  jede Nacht in ein Zip; Zahnrad → *Sicherungen* zeigt die Kopien und sichert sofort.
- **Claude als Mitautor:** Zahnrad → *Claude (MCP)* gibt deiner eigenen Claude-App einen
  Schlüssel zu deinen Welten, damit sie ein Abenteuer für eure Gruppe aus euren Büchern
  entwerfen kann. Es bleibt ein Entwurf, bis du es gelesen und angenommen hast.
- **Übersetzung:** Mit `PARAVENT_TRANSLATE_API_KEY` (ein Claude-API-Schlüssel, bezahlt
  nach Nutzung) entwirft Paravent deutsche Texte und Namen, die du bestätigst. Ohne ihn
  ist das einfach aus.

## Wenn etwas nicht stimmt

| Du siehst | Das heißt |
|---|---|
| Keine Monster oder Zauber | Der Datenordner stimmt nicht; Zahnrad → *5eTools-Daten* sagt, welcher |
| Ein Link zeigt „nicht in deinen Büchern“ | Die Welt nennt dieses Buch nicht; hak es an oder lass ihn ruhen |
| Spieler sehen zu wenig | Hak *Spieler* neben dem Buch in der Welt an |
| Handys verbinden sich nicht | Paravent lief ohne `--lan`, oder eine Firewall sperrt den Port |
