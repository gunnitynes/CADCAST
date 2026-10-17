# CBACK — CCAST in reverse: students' audio to the teacher's desk

**Page:** https://gunnitynes.github.io/CBack/ · sibling of [CCAST](https://github.com/gunnitynes/ClassCast)

Students send what their DAW is playing to your Mac so you can listen to their work
without AirDrop. Nothing to install for them. One Python file, a Dock app, works offline.
Stereo, 48 kHz Opus at 320 kb/s with no speech processing (CCAST's recipe), about 50 ms delay.

## Every class
1. Click **CBACK** in the Dock. Chrome opens **the desk**. (First time only: allow Python to accept
   incoming connections, and allow the microphone once. The desk uses that permission to name your
   output devices. It records nothing.)
2. **⛶ Projector** shows the student link + QR: `https://<this-mac>:8091`.
3. Students open it, type their name, press **Join**. They pick **BlackHole 2ch** under *Send from*
   (it is picked for them when present).
   - First time only, Chrome says *"Your connection is not private"*: **Advanced → Proceed**. It's this
     Mac's own certificate, the same one CCAST's Ultra link uses, so anyone who accepted it there is not asked.
4. Every student appears as a tile, **muted (🔇) until they press Start broadcast**. Students broadcast only when they
   want to be heard. While they broadcast, the tile shows **live meters**.

On the desk:
- **Click** a student to listen; click again to stop. **⇧-click** adds students to listen to several at once.
- **1–9** solo the student in that position (tiles sort alphabetically) · **0** / **Esc** stop.
- **✋ Raised hand**: students press *Ask teacher to listen*; the tile glows amber. **H** jumps to the next raised hand.
  Listening lowers it.
- **Output** picker: listen on your interface's headphones while CCAST keeps playing elsewhere. **VOL** sets the level.

**Start / Stop broadcast** is the student's mute button, and it is **off every time the page opens**. While it's off,
nothing leaves their laptop, not even the meter, even if you click them (the tile says *Waiting for them to broadcast*;
their page says *Teacher wants to hear you*). Raising a hand starts the broadcast.
The student sees a red **TEACHER LISTENING** light only while they broadcast *and* you listen. Audio leaves their
machine only then; otherwise at most a meter does, so 25 students on Wi‑Fi cost almost nothing.

## Student setup (BlackHole)
Chrome captures **channels 1–2 of the device the student picks**, so the page should get BlackHole itself:
- DAW output → a device that includes BlackHole: a **Multi-Output Device** (headphones/interface + BlackHole 2ch,
  drift correction on BlackHole) mirrors the master into both, so the student still hears themselves.
  An **Aggregate** works too if the master goes to BlackHole's channels.
- On the CBACK page, *Send from* = **BlackHole 2ch** (not the Aggregate: its channels 1–2 are usually the
  interface, and the page warns about this).
- The page warns "No signal" after 6 s of silence.

## Why https for students
Browsers only allow audio capture on secure pages, so the student link is https with this Mac's
self-signed certificate. The desk itself runs on `http://localhost:8090/desk` and only opens on this Mac.

## Runs next to CCAST
Different ports (CBACK 8090/8091, CCAST 8080/8443), so both can run in the same class.

## If something's off
- A student stays "Waiting for the teacher": is the desk open? Same Wi‑Fi? (School networks that isolate
  clients block CCAST too; a travel router / hotspot fixes both.)
- A student is "On the desk" but silent: their meters on your tile show it. Usually the DAW isn't playing into BlackHole.
- Only one desk tab: opening a second one makes the older one step down.
- Log: `cback.log` · Terminal: `python3 ~/CBack/cback.py`

## Files
- `CBACK.app`: launcher (keep it here, or leave the folder at ~/CBack) · click again while running to open/stop
- `cback.py`: server + both pages · `qrcode.min.js`: QR library (MIT)
