# BOX-3 firmware: "Hey Jarvis" on a tuned cutoff

`box3-jarvis.yaml` is the ESPHome config for the ESP32-S3-BOX-3 voice satellite
(HIMMEL-7777, tracker HIMMEL-7719). The wake word runs on the device, so the
cutoff is a firmware setting: the stock `hey_jarvis` cutoff (0.97) never fires on
this hardware. The cutoff is the `wake_cutoff` substitution at the top of the file.

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

Then add the device in Home Assistant (Settings, Devices, ESPHome) and set its
Assist pipeline to the Jarvis pipeline.

## Choosing the cutoff

Lower wakes more easily and also mis-wakes more. Test these candidates, one
flash each, in this order: 0.50, 0.60, 0.70, 0.80.

For each candidate:

Every wake logs `wake word detected: hey_jarvis`. Each phase below gets its own
log file, so the true wakes never land in the false-wake count. Stop the logger
with Ctrl-C when the phase ends.

1. **True wakes.** Say "Hey Jarvis" 20 times, about 2 m from the device, at normal
   volume. After each try wait for the reply to finish before the next one:
   wake detection restarts only after the device stops speaking. Say nothing else.

   ```
   esphome logs box3-jarvis.yaml --device box3-jarvis.local | tee true-wakes.log
   grep -c 'wake word detected' true-wakes.log
   ```

2. **False wakes.** Leave the device alone for 2 hours with a TV or room noise
   playing at normal volume, with nobody saying "Hey Jarvis".

   ```
   esphome logs box3-jarvis.yaml --device box3-jarvis.local | tee false-wakes.log
   grep -c 'wake word detected' false-wakes.log
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
