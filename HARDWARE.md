# Hardware

What a himmel-voice build needs, part by part, and what it costs to run on the station. Each figure names its source. A figure marked *estimate* was not measured. A part marked *coming in #n* belongs to an open PR and is not on `main` yet.

Four parts make the PA:

- **The ESP32-S3-BOX-3** is the satellite. It listens for the wake word on the device, streams the request to Home Assistant and plays the reply.
- **The station** runs the speech services in Docker: speech-to-text (faster-whisper) and text-to-speech (Piper), and the Codex proxy once #9 lands.
- **The Home Assistant host** runs the Assist pipelines and talks to both.
- **The network** joins them on one LAN.

## ESP32-S3-BOX-3

| Part | Role | Minimum spec | Source |
|---|---|---|---|
| Board | The voice satellite: wake word on the device, mic and speaker for Assist | ESP32-S3-BOX-3. Other ESP32-S3-BOX models are untested | `esphome/box3-jarvis.yaml` (`board: esp32s3box`) |
| Flash | Firmware and the `hey_jarvis` wake word model | 16 MB | `esphome/box3-jarvis.yaml` (`flash_size: 16MB`) |
| PSRAM | Working memory for the wake word model and the audio buffers | Octal PSRAM at 80 MHz | `esphome/box3-jarvis.yaml` (`psram:`) |
| Microphone | Wake word detection and the spoken request, 16 kHz 16-bit | The on-board mics through the ES7210 ADC | `esphome/box3-jarvis.yaml` (`audio_adc`, `microphone`) |
| Speaker | Plays the reply, 16 kHz 16-bit mono | The on-board speaker through the ES8311 DAC, amplifier gated by GPIO46 | `esphome/box3-jarvis.yaml` (`audio_dac`, `speaker`, `switch`) |
| Screen | Unused on `main`. An animated mascot is coming in #8 (HIMMEL-7815) | The on-board screen | `esphome/box3-jarvis.yaml` (no `display:`) |
| USB-C cable | The first flash, in download mode (hold BOOT, tap RESET). Later flashes go over the air | A USB-C data cable, not a charge-only one | `esphome/README.md` (Flash) |
| Power | Always on, since the wake word runs on the device | A USB-C 5 V supply. Power draw not measured | *estimate* |
| Build machine | Compiles and flashes the firmware | A container or VM with ESPHome 2026.10.0. The first compile downloads the ESP-IDF toolchain | `esphome/README.md` (Build) |

The firmware's compile RAM and flash figures are not recorded in the repo.

## Station

| Part | Role | Minimum spec | Source |
|---|---|---|---|
| CPU | Speech-to-text and text-to-speech on the CPU, the default | A 64-bit CPU. Measured use is below 2 % of one core on average, in bursts during a request (*estimate*: any recent desktop CPU will do) | measured 2026-10-27, on a 32-thread station |
| RAM | The containers | About 2 GB at peak, 1.3 GB idle. See [Running cost](#running-cost-on-the-station) | measured 2026-10-27 |
| Disk | Model caches and voices in `wyoming/data/`, and the images | 5.0 GB for `wyoming/data/` (it includes the GPU benchmark models), plus 2 to 3 GB of CPU images (*estimate*) | measured 2026-10-27 (`du`); image size *estimate* |
| GPU (optional, coming in #11) | Speech-to-text on the GPU, through the opt-in compose profile `gpu` | An NVIDIA GPU with CUDA (measured on an RTX 4090), an NVIDIA driver and nvidia-container-toolkit, amd64. 1.5 to 3 GB of VRAM (*estimate*), plus 4 to 6 GB of disk for the CUDA image (*estimate*) | PR #11 (HIMMEL-7834); HIMMEL-7820 comment |
| Docker | Runs the services from `wyoming/compose.yaml`, images pinned in `wyoming/images.lock` | Docker with the compose plugin | `wyoming/compose.yaml`, `README.md` |
| Firewall | Lets only the Home Assistant host reach the speech ports | iptables and systemd, for `docker-user-rule.sh` and `wyoming-docker-user.service` | `README.md` (wyoming/) |
| Admin access | Setup: the `data/` ownership, the firewall rule, `docker compose up` | sudo | `README.md` (wyoming/) |

## Home Assistant host

| Part | Role | Minimum spec | Source |
|---|---|---|---|
| Home Assistant | Runs the Assist pipelines, the ESPHome device and the Wyoming integrations; `ha/assist.py` configures it over the websocket API | Home Assistant 2026.8.0 or later, for the `litellm` integration that Codex uses (coming in #9) | HIMMEL-7820 |
| Host | Runs Home Assistant. The speech models run on the station, so this build adds only the pipelines, the integrations and the `cap` counter and automations | What Home Assistant's own installation guide asks for (*estimate*: this build adds no measurable load) | *estimate* |
| Address | The station's firewall rule allows only this address (`HA_HOST`) | A fixed LAN address, for example a DHCP reservation | `README.md` (wyoming/) |
| Internet | The cloud conversation agent (Anthropic) | Outbound HTTPS | `README.md` (ha/, `cap`) |

## Network

| Part | Role | Minimum spec | Source |
|---|---|---|---|
| LAN | Joins the BOX-3, the station and the Home Assistant host | One LAN you trust: Home Assistant reaches Wyoming over plain TCP, unencrypted | `README.md` (wyoming/) |
| Wi-Fi | The BOX-3's link | 2.4 GHz: the ESP32-S3 has no 5 GHz radio | `esphome/box3-jarvis.yaml` (`wifi:`); ESP32-S3 datasheet |
| Station address | `BIND_ADDR`, the address the speech ports listen on | A fixed LAN address | `README.md` (wyoming/) |
| Port 10300/tcp | Wyoming speech-to-text on the station. Only the Home Assistant host may connect | Open from the Home Assistant host | `wyoming/compose.yaml`, `wyoming/docker-user-rule.sh` |
| Port 10200/tcp | Wyoming text-to-speech on the station. Only the Home Assistant host may connect | Open from the Home Assistant host | `wyoming/compose.yaml`, `wyoming/docker-user-rule.sh` |
| ESPHome API | Home Assistant connects to the BOX-3, encrypted with the API key | Home Assistant reaches the BOX-3 on ESPHome's API port (6053/tcp by default) | `esphome/box3-jarvis.yaml` (`api:`) |
| OTA and mDNS | Later flashes and the `box3-jarvis.local` name | The build machine reaches the BOX-3; mDNS works on the LAN | `esphome/README.md` (Flash) |
| Port 4000/tcp (coming in #9) | The Codex LiteLLM proxy. Only the Home Assistant host may connect. Its only way out is squid, on 443 to the OpenAI hosts | Open from the Home Assistant host; outbound HTTPS from the station | PR #9 (HIMMEL-7733) |
| Port 8740/tcp (coming in #10) | The voice gateway into the luna inbox. Only the Home Assistant host may connect | Open from the Home Assistant host | PR #10 (HIMMEL-7734) |

## Running cost on the station

Measured 2026-10-27 on the station from each container's cgroup (`memory.current`, `memory.swap.current`, `memory.peak`, `cpu.stat`) and `du` (HIMMEL-7820 comment). The LiteLLM row is the deployment from #9, which is not on `main` yet; its squid container was not measured.

| Service | RAM resident + swap | RAM peak | CPU used |
|---|---|---|---|
| faster-whisper speech-to-text (CPU, `small-int8`) | 11 MB + 466 MB | 759 MB | 27 s over 47 min up |
| Piper text-to-speech (`en_US-lessac-medium`) | 19 MB + 338 MB | 567 MB | 301 s over 4.9 h up |
| LiteLLM, the Codex proxy (coming in #9) | 9 MB + 516 MB | 646 MB | 13 s over 36 min up |

- **RAM:** about 2 GB at peak and 1.3 GB idle, most of it swapped out. On a host without swap, expect the idle figure to stay resident (*estimate*).
- **CPU:** below 2 % of one core on average, with bursts only during a request. Speech-to-text takes about 1 s on the CPU. A Codex question takes about 6 s end to end, mostly remote.
- **Disk:** `wyoming/data/` is 5.0 GB, including the GPU benchmark models from HIMMEL-7834. The images were not measured: 2 to 3 GB for the CPU images, plus 4 to 6 GB for the CUDA image (*estimate*).
- **GPU (optional, coming in #11):** none is used today. GPU speech-to-text keeps its model in VRAM, 1.5 to 3 GB on the RTX 4090, plus a few watts at idle (*estimate*). It cuts speech-to-text from 0.80 to 0.99 s on the CPU to 0.07 s (PR #11 benchmark).
- **Codex (coming in #9):** it runs remote on the ChatGPT subscription, two calls per voice question, so there is no per-token bill.
- **Power:** 1 to 3 W extra on average on an always-on station (*estimate*).
- **No caps:** `wyoming/compose.yaml` sets no CPU or memory limit on any container.
