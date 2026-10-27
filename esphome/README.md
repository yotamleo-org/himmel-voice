# BOX-3 firmware: three wake words, a status screen and mascots

`box3-jarvis.yaml` is the ESPHome config for the ESP32-S3-BOX-3 voice satellite
(HIMMEL-7777, tracker HIMMEL-7719). The wake word runs on the device, so the
cutoff is a firmware setting: the stock `hey_jarvis` cutoff (0.97) never fires on
this hardware. The cutoff is the `wake_cutoff` substitution at the top of the file.

The screen (HIMMEL-7815) shows the agent the wake word called and its pixel-art
mascot, the voice state (Ready, Listening, Thinking, Speaking, Error, Muted,
Offline), the reply text or the error, and an amber "Local only" banner while the
HIMMEL-7780 daily cap holds.

| Wake word   | Agent   | Home Assistant slot |
| ----------- | ------- | ------------------- |
| Hey Jarvis  | Claude  | 1                   |
| Hey Mycroft | Codex   | 2                   |
| Hey Grok    | Grokbot | 2 (internal model)  |

Home Assistant gives a satellite two wake word slots, each with its own pipeline.
"Hey Grok" is a device-only model that Home Assistant does not list: it shows
Grokbot and runs the second slot's pipeline. The `hey_grok` model is from
[TaterTotterson/Tater-Wake-Words](https://github.com/TaterTotterson/Tater-Wake-Words) (MIT).

- **BOOT** (the left top button) or a tap on the screen talks to the first slot
  without a wake word; pressed while the assistant runs, it stops it, reply
  included.
- **Talk now** (a button in Home Assistant) does the same: use it to test the
  microphone through speech-to-text and the reply without the wake word.
- **Mute** (a switch in Home Assistant) silences the microphone and stops
  listening until it is turned off.
- **Volume** is the device's media player in Home Assistant.

## Secrets

```
cp secrets.example.yaml secrets.yaml   # then fill in your own values
```

`secrets.yaml` is untracked (`esphome/.gitignore`). Never commit it.

## Build

Use a container or a VM; do not install the ESPHome toolchain on your workstation.
The config compiles with ESPHome 2026.10.0 (`pip install esphome==2026.10.0` in a
venv); the first compile downloads the ESP-IDF toolchain and the `hey_jarvis` model.

```
esphome compile box3-jarvis.yaml                         # the committed cutoff
esphome -s wake_cutoff 0.50 compile box3-jarvis.yaml     # a candidate cutoff
```

## Flash

First flash is over USB with the BOX-3 in download mode (hold BOOT, tap RESET).
Later flashes go over the air.

```
esphome -s wake_cutoff 0.50 run box3-jarvis.yaml --device /dev/ttyACM0   # first flash, USB
esphome -s wake_cutoff 0.50 run box3-jarvis.yaml --device box3-jarvis.local   # later, OTA
```

Then add the device in Home Assistant (Settings, Devices, ESPHome) and set, on the
device page:

- Wake word: Hey Jarvis; Assistant: the Claude pipeline.
- Wake word 2: Hey Mycroft; Assistant 2: the Codex pipeline.

The "Local only" banner reads the first Assistant select, named by the
`pipeline_select` substitution (`select.jarvis_box_3_assistant` by default). Check
the entity ID on the device page and pass `-s pipeline_select <entity id>` if it
differs.

## Mascots

`assets/make_mascots.py` draws the three mascots on a 32x32 grid in one palette
whose colours are exact in RGB565, and writes `assets/<mascot>.gif` (8 frames:
idle, listen, think, speak, two each), `assets/palette.gpl` and the preview sheet
`assets/mascots.png`. After a change, regenerate them (needs Pillow):

```
python3 -I assets/make_mascots.py
```

## Choosing the cutoff

Lower wakes more easily and also mis-wakes more. Test these candidates, one
flash each, in this order: 0.50, 0.60, 0.70, 0.80.

For each candidate:

Every wake logs `wake word detected: <phrase>`, for example
`wake word detected: Hey Jarvis`. Each phase below gets its own
log file, so the true wakes never land in the false-wake count. Stop the logger
with Ctrl-C when the phase ends.

1. **True wakes.** Say "Hey Jarvis" 20 times, about 2 m from the device, at normal
   volume. After each try wait for the reply to finish before the next one:
   wake detection restarts only after the device stops speaking. Say nothing else.

   ```
   esphome logs box3-jarvis.yaml --device box3-jarvis.local | tee true-wakes.log
   grep -c 'wake word detected: Hey Jarvis' true-wakes.log
   ```

2. **False wakes.** Leave the device alone for 2 hours with a TV or room noise
   playing at normal volume, with nobody saying "Hey Jarvis".

   ```
   esphome logs box3-jarvis.yaml --device box3-jarvis.local | tee false-wakes.log
   grep -c 'wake word detected: Hey Jarvis' false-wakes.log
   ```

Record:

| cutoff | true wakes (of 20) | false wakes (2 h) |
| ------ | ------------------ | ----------------- |
| 0.50   |                    |                   |
| 0.60   |                    |                   |
| 0.70   |                    |                   |
| 0.80   |                    |                   |

Pick the cutoff with the fewest false wakes among those with at least 18 of 20
true wakes. The target is at most 1 false wake in 2 hours. If no candidate
qualifies, add candidates between the best two and repeat. Set the winner as the
`wake_cutoff` default in `box3-jarvis.yaml`.
