# Abenteuer mit Claude schreiben

Paravent hat einen **MCP-Server**: Claude kann deine Welten, die Charaktere und die Bücher
lesen und ein neues Abenteuer in Paravent schreiben — Kapitel, NSC, ausgewogene Kämpfe und
Schätze — als **Entwurf**, den du prüfst, bevor er gespielt wird.

Diese Seite zeigt, wie du **Claude Desktop** und **Claude Code** verbindest, wie du um ein
Abenteuer bittest und was zu tun ist, wenn keine Verbindung zustande kommt.

---

## 1. Was Claude darf

- **Lesen**: jede Welt mit ihren Büchern, Abenteuern, NSC und Schätzen, die Charaktere,
  die Kreaturen, Zauber und Gegenstände der Bücher und was am Tisch geschah (Kämpfe,
  Erfahrung, Stufen).
- **Schreiben nur in Entwürfe, die es selbst angelegt hat.** Claude legt ein neues
  Abenteuer mit dem Status *Entwurf* an und füllt es. Alles andere ändert oder löscht es
  nie — weder deine Abenteuer noch deine NSC noch einen Entwurf, den du von Hand angelegt
  hast.
- **Monster kommen nur aus den Büchern der Welt**, und jeder Kampf wird mit Paravents
  eigener Schwierigkeitsrechnung für die Charaktere des Abenteuers bewertet.
- **Nimmst du einen Entwurf an**, wird er *Geplant*; ab dann kann Claude ihn nur noch
  lesen.

---

## 2. Einen Schlüssel erzeugen

1. Öffne **Zahnrad → Claude (MCP)**.
2. Gib dem Schlüssel einen Namen, der sagt, wo er benutzt wird („Claude Desktop, Laptop“),
   und klicke **Schlüssel erzeugen**.
3. Der Schlüssel wird **nur einmal** gezeigt, zusammen mit fertigen Einstellungen für
   Claude Code und Claude Desktop. Kopiere sie gleich; Paravent behält nur einen
   Fingerabdruck des Schlüssels.

Ein Schlüssel handelt als du, die Spielleitung. Erzeuge einen Schlüssel pro Rechner, dann
kannst du einen widerrufen, ohne die anderen. **Widerrufen** auf derselben Seite sperrt
ihn sofort.

---

## 3. Claude Desktop

Die **benutzerdefinierten Konnektoren** von Claude Desktop (*Einstellungen → Konnektoren →
Benutzerdefinierten Konnektor hinzufügen*) funktionieren für Paravent **nicht**: Sie
verbinden sich von Anthropics Servern im Internet aus, und die erreichen kein Paravent in
deinem Heimnetz.

Stattdessen startet Claude Desktop eine kleine **Brücke auf deinem Rechner**,
`mcp-remote`, die über dein Netz mit Paravent spricht und lokal mit Claude Desktop.

### Was du brauchst

- **Node.js 18 oder neuer** auf dem Rechner mit Claude Desktop
  ([nodejs.org](https://nodejs.org), die LTS-Version). Prüfen im Terminal:
  `node --version`.
- Paravent muss von diesem Rechner erreichbar sein — öffne
  `http://<dein-server>:8000` in seinem Browser.

### Einrichten

1. Öffne in Claude Desktop **Einstellungen → Entwickler → Konfiguration bearbeiten**. Das
   öffnet `claude_desktop_config.json`:
   - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`
2. Trage Paravent unter `mcpServers` ein — die Schlüsselseite zeigt diesen Block mit
   deiner Adresse und deinem Schlüssel schon ausgefüllt:

   ```json
   {
     "mcpServers": {
       "paravent": {
         "command": "npx",
         "args": [
           "-y",
           "mcp-remote",
           "http://192.168.1.20:8000/mcp",
           "--allow-http",
           "--header",
           "Authorization:${PARAVENT_KEY}"
         ],
         "env": {
           "PARAVENT_KEY": "Bearer pv_dein-schluessel"
         }
       }
     }
   }
   ```

   Stehen schon andere Server in der Datei, setze `"paravent": {…}` daneben in dasselbe
   `mcpServers`.
3. **Beende Claude Desktop vollständig und starte es neu.**
4. In einem neuen Chat zeigt das Werkzeug-Menü unter dem Eingabefeld jetzt **paravent**
   mit seinen Werkzeugen. *Einstellungen → Entwickler* zeigt ihn als laufend.

Zu den Einstellungen:

- `--allow-http` ist nötig, weil Paravent in deinem Netz einfaches `http` spricht. Ohne
  verweigert die Brücke jede Adresse, die nicht `https` oder `localhost` ist.
- Der Schlüssel steht in `env`, und die Kopfzeile ist absichtlich ohne Leerzeichen nach dem
  Doppelpunkt geschrieben: Claude Desktop unter Windows zerlegt Argumente mit Leerzeichen.
- `-y` lässt `npx` beim ersten Mal `mcp-remote` holen, ohne zu fragen.

---

## 4. Claude Code

Ein Befehl im Terminal auf dem Rechner mit Claude Code (die Schlüsselseite zeigt ihn
ausgefüllt):

```bash
claude mcp add --transport http paravent http://192.168.1.20:8000/mcp \
  --header "Authorization: Bearer pv_dein-schluessel"
```

Prüfen mit `claude mcp list`; in einer Sitzung zeigt `/mcp` den Server und seine
Werkzeuge.

---

## 5. Um ein Abenteuer bitten

Am einfachsten beginnst du mit Paravents Vorlage **new_adventure**:

- **Claude Code:** tippe `/mcp__paravent__new_adventure` und gib Welt, Charaktere, Zahl der
  Spielabende und eine Idee an.
- **Claude Desktop:** Vorlagen verbundener Server stehen im **+**-Menü unter dem
  Eingabefeld — oder du schreibst die Bitte einfach selbst.

Eine selbst geschriebene Bitte funktioniert genauso:

> Schreib ein Abenteuer für zwei Abende in Azeroth für Tenewaja und Varis Thornwood:
> Folk-Horror im Moor, eine unschuldig Angeklagte, eine Vettel dahinter. Knüpf an den
> letzten Spielabend an.

Was Claude der Reihe nach tut: Es liest die Welt, die Charaktere und was bisher am Tisch
geschah — zuerst deine **Sitzungsnotizen** (Seite der Welt → *Sitzungsnotizen*: ein paar
Zeilen pro Spielabend dazu, was geschah, wem die Gruppe begegnet ist, was noch offen ist); plant die Geschichte und wie viel Kampf die Gruppe verträgt; legt den Entwurf
mit seinen NSC, Kämpfen (jeden angepasst, bis seine Schwierigkeit passt) und Schätzen an;
schreibt die Kapitel; prüft jedes Kapitel auf Verweise, die ins Leere führen, und bessert
sie aus; und schließt mit einem kurzen Bericht.

**Sprache:** Claude schreibt in der **Sprache der Welt** — einzustellen auf der Seite der
Welt (*Bearbeiten*). Namen von Kreaturen, Zaubern und Gegenständen bleiben in den Tags
englisch; Paravent zeigt sie übersetzt, wo das Glossar einen Namen hat.

Du kannst zusehen: Öffne die Welt in Paravent, während Claude arbeitet — der Entwurf und
seine Kapitel erscheinen und aktualisieren sich live.

---

## 5b. Claude von deiner Welt erzählen

Auf der Seite der Welt hält **Anweisungen für Claude** fest, was Claude liest, bevor es
schreibt:

- **Das Setting** — welche Welt das ist und wann: Länder, Städte, Völker, Fraktionen und
  wie ihre Völker und Kreaturen auf 5e übertragen werden. „Diese Welt ist Azeroth aus
  World of Warcraft, zur Zeit des ursprünglichen Spiels …" genügt, damit Claude nutzt, was
  es darüber weiß.
- **Ein Text pro Art** — Abenteuer, Kapitel, NSC, Begegnungen, Schätze, Orte: wie lang,
  wie schwer, wie benannt, was vermieden werden soll.

Kreaturen, die das Setting hat, aber 5e nicht, werden zu **Reskins**: eine Buchkreatur
unter dem Namen des Settings — „Murloc" mit den Werten eines Kuo-toa. Du kannst das auch
selbst auf jeder Zeile einer Begegnung setzen (*Angezeigt als*).

## 6. Den Entwurf prüfen

Ein Entwurf öffnet sich mit einem lila Kasten oben:

- der Schlüssel, der ihn schrieb, und die Charaktere, für die er ist;
- jedes Kapitel mit seiner Prüfung — *alle Verweise führen ans Ziel* oder die Zahl der
  Probleme;
- jeder Kampf mit seiner Schwierigkeit und die Erfahrung aller Kämpfe zusammen gegen den
  Abenteuertag der Gruppe, mit einer Warnung, wenn es mehr als ein Tag ist;
- wie viele NSC und Schätze er enthält.

Lies die Kapitel, ändere, was du willst (Claude kann deine Änderungen nicht
überschreiben — eine Änderung an einem Text, den du bearbeitet hast, wird abgewiesen, und
es bekommt deine Fassung zurück), dann:

- **Entwurf annehmen** — das Abenteuer ist *Geplant*; Claude liest es ab jetzt nur noch;
- **Entwurf verwerfen** — es kommt in den Papierkorb wie jedes gelöschte Abenteuer.

Solange es ein Entwurf ist, kannst du Claude auch um Änderungen bitten: „mach den zweiten
Kampf leichter“, „gib dem Schultheiß eine Tochter“.

---

## 7. Wenn es nicht klappt

| Was du siehst | Was es heißt | Was zu tun ist |
| --- | --- | --- |
| Claude Desktop zeigt *paravent* nicht | Die Konfiguration wurde nicht gelesen, oder die Brücke startete nicht | JSON prüfen (ein fehlendes Komma macht die ganze Datei ungültig), Claude Desktop beenden und neu starten. *Einstellungen → Entwickler* zeigt den Zustand des Servers und sein Protokoll |
| „npx: command not found“ / der Server bricht sofort ab | Node.js fehlt, oder Claude Desktop findet es nicht | Node.js 18+ installieren. Liegt es an ungewöhnlicher Stelle, den vollen Pfad zu `npx` bei `"command"` eintragen |
| Die Brücke verweigert die Adresse, weil sie nicht HTTPS ist | `--allow-http` fehlt | In `args` ergänzen |
| 401 / „a Paravent MCP token is needed“ | Der Schlüssel ist falsch, widerrufen oder die Kopfzeile kaputt | Neuen Schlüssel erzeugen; auf `Bearer ` davor achten |
| 429 / „too many failed attempts“ | Zu viele falsche Schlüssel von diesem Rechner | Ein paar Minuten warten, dann den Schlüssel richten |
| Verbindung abgelehnt / Zeitüberschreitung | Paravent ist unter der Adresse nicht erreichbar | Dieselbe Adresse im Browser öffnen; Host, Port und die Firewall des Servers prüfen |
| „… is not a draft written through Paravent's MCP server“ | Claude wollte etwas ändern, das nicht sein Entwurf ist | So gewollt — bitte um ein neues Abenteuer oder eine Änderung am eigenen Entwurf |
| Kapitel kommen auf Englisch | Die Sprache der Welt ist Englisch | Sprache der Welt einstellen und erneut bitten |

---

## 8. Sicherheit

- In deinem Netz geht der Schlüssel als einfaches `http` über die Leitung. Wer diesen
  Verkehr mitlesen kann, kann den Schlüssel lesen — betreibe Paravent in einem Netz, dem du
  vertraust, oder hinter HTTPS.
- Ein Schlüssel kann, was der MCP-Server erlaubt, und nicht mehr: lesen und in seine
  eigenen Entwürfe schreiben. Er kann sich nicht an Paravents Seiten anmelden, keine
  Spieler ändern und nichts von deiner Arbeit löschen.
- Widerrufe Schlüssel, die du nicht mehr brauchst — auf der Schlüsselseite, *Widerrufen*.
