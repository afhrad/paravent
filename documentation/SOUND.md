# The sound desk

Music, ambience and effects at the table, played through a Bluetooth or AirPlay loudspeaker.
Decided with the user 2026-10-31.

## 1. Who plays

**A page plays sound only while it is open**, and Paravent loads a new page on every
navigation, so a player inside the page the gamemaster works on would fall silent at each
click. Hence two roles:

- **The speaker**, `/gm/sound`: left open on one device, the one connected to the loudspeaker.
  It alone makes sound. A tap on "Start" is required, because phones allow audio only after one.
- **The panel**, the slide-over behind the top bar's speaker button, on every gamemaster's
  page. It only says what should sound.

```txt
[iPad] pad pressed → POST /gm/sound/press/{id} → SoundDesk → event stream → [phone: speaker]
                                                                             fetches the file
```

Sound is never sent through the iPad: the files are on the server and the speaker fetches
them. The loudspeaker pairs with the speaker device, in that device's own settings; a web page
cannot choose an output. Expect AirPlay to lag by about two seconds, Bluetooth by a fraction of
one.

**The speaker must stay in front with its screen on.** iOS stops or refuses audio in a page in
the background, a home-screen app especially (WebKit bugs 198277, 229688, 291892), so it cannot
run behind the gamemaster's own page on one iPad. A second device, or a Mac in a second tab, it
is. The page asks for a wake lock; browsers offer one only over HTTPS, so on a plain `http://`
address it says to turn off auto-lock instead.

## 2. State

`SoundDesk` (`app/services/sound.py`) holds what should be sounding, in memory: the loops, the
music, the master volume. Every change sends **all of it** to speakers and panels, so a speaker
that reconnects catches up by itself, and a lost message costs nothing. It goes with a restart,
like the music.

An effect is an event, not a state: it travels in `shots`, numbered, dropped after five
seconds, so a reconnecting speaker does not fire what it missed. A speaker's first message only
sets the number.

The stream is open only while a panel is, so idle pages hold no connection; browsers allow six
per host. Speakers listen under a key of their own, `*sound-speaker`, whose subscriber count is
the panel's "no speaker connected".

## 3. Mixing

Web Audio on the speaker (`static/js/speaker.js`), one gain per voice under a master.

| Kind | Plays | Pad |
|---|---|---|
| **Effect** | once; effects overlap | fires |
| **Ambience** | loops; any number layer | on / off |
| **Music** | loops, one at a time, crossfading | on / off |

Effects and short loops are decoded into memory, which loops without a gap. **Music and any file
of 8 MB or more stream** through audio elements, so a long track does not take hundreds of
megabytes of a phone's memory; the server answers range requests. iOS lets an element play later
only if a tap started it, so "Start" starts a pool of six on silence.

A **scene** is a saved mix, one pad: its loops and music with their volumes. It replaces what is
on; what it shares keeps playing, the rest fades.

## 4. The library

`library/sounds/` beside `sounds.json` and `sound_scenes.json`. Shared by every world. Being in
the library it is in the backups, large files included; the user decided that, and the backup
code knows nothing of sounds.

Uploads at `/gm/sounds`: files or a whole folder, MP3, M4A, WAV or OGG, checked by content, no
size limit. In a folder what is not audio is counted and skipped. Older iPhones cannot play OGG.
`python -m app.cli sound import <folder> --kind ambience --credit "…"` does the same from the
command line. `credit` keeps who made a sound and under what licence.

## 5. Triggers

- **`{@sound Tavern}`** in any Paravent Markdown: a button that plays the scene or sound of that
  name, a scene first. Unknown names show as missing and the chapter check lists them.
- **A fight's sound**: chosen on the encounter, it starts when the fight is run, not on returning
  to a running one.

A trigger means "this now", so unlike a pad it never turns a loop off.

## 6. The samples

The library starts with 31 CC0 sounds from OpenGameArt, each with its credit: the *30 CC0 SFX
Loops* and *80 CC0 RPG SFX* packs, *Dark Ambiences*, the *Accessory SFX Library*, *Crowded Pub*,
and music by RandomMind, cynicmusic and iamoneabe.

## 7. Tested

Tried in desktop Chrome: speaker and panel in separate tabs, layers, streaming, crossfade, scenes.
The user tested the sound desk on their own devices on 2026-11-02.
