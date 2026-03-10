# Suzuki Cultus 2016 (Pakistan) — KWP2000 OBD Reader

Read live engine data from a Suzuki Cultus 2016 (Pakistan) over Bluetooth using a cheap ELM327 BLE dongle.
Works with both a CLI script (`cultus_obd.py`) and RealDash (`realdash_cultus_2016.xml`).

---

## Overview

| | |
|---|---|
| **Vehicle** | Suzuki Cultus 2016 (Pakistan / Alto K10 platform) |
| **Protocol** | ISO 14230-4 KWP2000 fast init over K-Line |
| **Adapter** | IOS-Vlink BLE (ELM327 v2.3) |
| **Service** | 0x21 local ID 0x00 — reads a 65-byte engine data block |
| **Data** | RPM, coolant temp, vehicle speed, TPS, IAT, baro pressure, battery voltage |

---

## Background

### Why standard OBD-II fails

The Cultus 2016 sold in Pakistan uses the **same engine ECU as the Suzuki Swift 1999**. That ECU predates CAN bus — it communicates exclusively over **K-Line (ISO 9141 / ISO 14230)**, not the ISO 15765-4 CAN that all modern OBD-II scanners expect. Plugging in a generic ELM327 and running standard OBD-II Mode 01 PIDs gives nothing.

The OBD-II port (J1962) is physically present, but only **pin 7 (K-Line)** is active. Pins 6 and 14 (CAN High/Low) are unconnected.

### How we reverse-engineered the protocol

The Android app **SZ Viewer** is a third-party ECU reader that supports these Suzuki vehicles. By decompiling the APK (`jadx`), we extracted:

- The exact ELM327 AT command init sequence (including flags that most guides omit)
- The KWP2000 header format: `ATSH 81 11 F1`
- The service/local ID that returns the full data block: `21 00`
- A full byte map (`Engine_KWP_00_Local$`) labelling every byte in the 65-byte response

No hardware sniffing was required.

---

## Hardware

### What you need

- **IOS-Vlink BLE ELM327 dongle** (the exact adapter matters — see UUIDs below)
- Any Mac / Linux / Windows machine with Bluetooth 4.0+ for the CLI tool
- Or an Android / iOS device running **RealDash** for the dashboard config

### OBD-II port pinout (J1962)

```
Pin  Signal   Used?
───  ───────  ─────
 4   Chassis GND     ✓
 5   Signal GND      ✓
 7   K-Line          ✓  ← the only data line that matters
16   Battery +12V    ✓
 6   CAN High        ✗  not connected
14   CAN Low         ✗  not connected
```

> **Tip**: If your ELM327 shows `BUS INIT ERROR` on a different car, it is almost certainly trying CAN or ISO 9141 on the wrong pin. On this ECU, only K-Line fast init works.

---

## Protocol Deep Dive

### Why `ATSH C1 11 F1` fails

Most KWP2000 guides tell you to use `ATSH C1 11 F1`. That sets the format byte to `0xC1`, which means "1 data byte, use address mode". The Cultus ECU rejects this.

The correct header is `ATSH 81 11 F1`:

```
Byte   Value  Meaning
──────────────────────────────────────────────────────
FMT    0x81   Format: 0x80 | length(1) — "1-byte length, no address field"
TGT    0x11   Target address: engine ECU
SRC    0xF1   Source address: external tester
```

The format byte formula is `0x80 | (number_of_data_bytes)`. For a 1-byte request (service ID only), that gives `0x81`. The `0xC1` variant uses a different addressing mode that this ECU does not support.

### Full ELM327 init sequence

```
Command     Why it's needed
─────────────────────────────────────────────────────────────────────────
ATZ         Full reset — clears any previous state
ATE0        Echo off — don't re-echo commands in responses
ATL0        Linefeeds off — simpler response parsing
ATS0        Spaces off — responses are dense hex without spaces (critical!)
ATH0        Headers off — we parse the raw data block, not framed headers
ATAL        Allow long messages (>7 bytes) — our response is 67 bytes
ATIB10      ISO baud 10400 — K-Line baud rate for this ECU
ATKW0       Keyword check off — skip KW1/KW2 validation on fast init
ATSW00      Wakeup message interval = 0 (use TesterPresent instead)
ATAT0       Adaptive timing off — use fixed timeout (ATST value)
ATCAF1      CAN auto-format on (harmless on KWP mode, good default)
ATCFC1      CAN flow control on (harmless here)
ATFCSM0     CAN flow control default mode
ATTP5       Select protocol 5 = ISO 14230-4 KWP fast init ← key step
ATSH 81 11 F1  Set header bytes (see above)
ATST 19     Response timeout = 0x19 × 4ms = 100ms
ATFI        Fast init — sends the 5-baud init pattern, wakes the ECU
ATKW        Read keyword bytes (confirms init succeeded)
3E          TesterPresent — starts the diagnostic session
```

### ECU address table

| Module | Address |
|--------|---------|
| Engine ECU | `0x11` |
| Automatic Transmission | `0x19` |
| ABS | `0x29` |
| Airbag (SRS) | `0x39` |
| Immobilizer | `0xD0` |

This project only uses the Engine ECU (`0x11`).

### Service 0x21, Local ID 0x00 — byte map

Request: `21 00` → Response header: `61 00` followed by 65 data bytes.

The table below shows offsets into the **data bytes only** (i.e. after stripping the `61 00` response header). The XML file uses **full-response offsets** (data offset + 2).

| Data offset | Full-resp offset | Parameter | Raw → Engineering |
|:-----------:|:----------------:|-----------|-------------------|
| b[13] | 15 | Engine Load | `V × 100/255 = %` |
| b[14] | 16 | Coolant Temperature | `V − 40 = °C` |
| b[20:21] | 22:23 | Engine RPM | `(B0 << 8 \| B1) × 0.25 = rpm` |
| b[22] | 24 | Vehicle Speed | `V = km/h` |
| b[23] | 25 | Ignition Advance | `V − 64 = °` |
| b[24] | 26 | Intake Air Temp | `V − 40 = °C` |
| b[27] | 29 | Throttle Position | `V × 0.392 = %` |
| b[41] | 43 | Barometric Pressure | `V × 0.5 = kPa` |
| b[49] | 51 | Battery Voltage | `V × 0.0784 = V` |

> **Note**: Offsets were confirmed by driving the car and correlating raw byte changes to known sensor inputs (revving engine, pressing accelerator, reading coolant temp cold vs warm).

---

## CLI Usage

### Install

```bash
pip install bleak
```

### Run

```bash
python3 cultus_obd.py
```

### Example output

```
Scanning for IOS-Vlink...
Found: IOS-Vlink [AA:BB:CC:DD:EE:FF]
Initializing ELM327...
Connecting to engine ECU (0x11)...
Connected.

      RPM    Coolant    Speed     TPS     IAT      Baro    Batt
  -----------------------------------------------------------------
      820      88°C     0km/h    0.0%    32°C    97.5kPa   14.11V
      825      88°C     0km/h    0.0%    32°C    97.5kPa   14.11V
     1640      89°C    40km/h    8.2%    33°C    97.0kPa   14.03V
```

Press `Ctrl+C` to stop.

---

## RealDash Usage

### Step-by-step setup

The XML file uses the **OBD2 format** (`<OBD2>` root), which is loaded into an **ELM327 (BLE)** connection — not "RealDash Custom". The init section handles all the KWP2000 AT commands automatically.

1. Install **RealDash** on Android or iOS.
2. Open RealDash → **Garage** → tap the instrument cluster.
3. On the **Connections** list, tap **Add** → choose **ELM327 (BLE)**.
4. Select device: **IOS-Vlink**.
5. After the connection is added, tap it in the list → tap **Select Vehicle**.
6. Scroll down to **Custom Channel Description File** and browse to `realdash_cultus_2016.xml`.
7. Tap **Done** — RealDash will reconnect using the custom init sequence and start polling engine data.

> If you see "obd2 property not found in file" you have the old XML. Re-download `realdash_cultus_2016.xml` from the repo.

### Fuel consumption note

Older revisions of this repo exposed a mis-scaled injector pulse-width channel to RealDash. That made RealDash prefer an incorrect fuel-consumption primitive and could produce implausible instant consumption.

The current XML intentionally does **not** export RealDash injector pulse width. Fuel consumption is expected to follow this fallback order instead:

- direct fuel flow, if a verified ECU primitive is ever found
- otherwise MAF (`targetId=30`)
- otherwise MAP (`targetId=31`) plus the existing engine metadata in the garage profile

This does **not** reproduce MotorData's hidden native fuel math exactly. It only aligns RealDash with the corrected SZ Viewer primitive map so RealDash can choose a more credible path.

### RealDash targetId → gauge mapping

The XML maps data to standard RealDash channel IDs:

| targetId | Channel | Parameter |
|----------|---------|-----------|
| 37 | RPM | Engine RPM |
| 14 | Coolant Temp | Coolant Temperature |
| 64 | Speed | Vehicle Speed KPH |
| 42 | TPS | Throttle Position % |
| 30 | MAF | Mass Air Flow g/s |
| 31 | MAP | Manifold Absolute Pressure kPa |
| 27 | IAT | Intake Air Temp |
| 11 | Baro | Barometric Pressure kPa |
| 12 | Batt | Battery Voltage |
| 100 | Engine Load | Engine Load % |
| 38 | Ign Advance | Spark Advance deg |
| 254 | O2 Sensor | O2 Sensor Voltage Bank 1 Sensor 1 |
| 17 | STFT | Short-Term Fuel Trim Bank 1 % |

---

## Byte Map Reference

Full decode table for all known bytes in the `21 00` response (data bytes, 0-indexed):

| Byte(s) | Parameter | Formula | Unit |
|---------|-----------|---------|------|
| 13 | Engine Load | `V × 0.392157` | % |
| 14 | Coolant Temperature | `V − 40` | °C |
| 15 | Short-Term Fuel Trim Bank 1 | `V × 0.78125 − 100` | % |
| 16 | Long-Term Fuel Trim Bank 1 | `V × 0.78125 − 100` | % |
| 17 | Short-Term Fuel Trim Bank 2 | `V × 0.78125 − 100` | % |
| 18 | Long-Term Fuel Trim Bank 2 | `V × 0.78125 − 100` | % |
| 19 | Manifold Absolute Pressure | `V` | kPa |
| 20–21 | Engine RPM | `(B[20] << 8 \| B[21]) × 0.25` | rpm |
| 22 | Vehicle Speed | `V` | km/h |
| 23 | Ignition Advance | `V − 64` | ° BTDC |
| 24 | Intake Air Temperature | `V − 40` | °C |
| 25–26 | Mass Air Flow | `(B[25] << 8 \| B[26]) × 0.01` | g/s |
| 27 | Throttle Position Sensor | `V × 0.392` | % |
| 29 | O2 Bank 1 Sensor 1 | `V × 0.005` | V |
| 37–38 | Fuel Pulse Bank 1 | `(B[37] << 8 \| B[38]) × 0.001` | ms |
| 39–40 | Fuel Pulse Bank 2 | `(B[39] << 8 \| B[40]) × 0.001` | ms |
| 41 | Barometric Pressure | `V × 0.5` | kPa |
| 49 | Battery Voltage | `V × 0.0784` | V |

Bytes `11` and `12` are left as raw control bytes. They are not treated as injector pulse-width primitives.

MotorData source inspection in this repo suggests the app prefers fuel-rate/MAF-style primitives, but the exact Suzuki formula remains inside native code and has not been recovered.

---

## Troubleshooting

### `BUS INIT ERROR`

Most common causes:

| Cause | Fix |
|-------|-----|
| Wrong protocol selected | Ensure `ATTP5` (KWP fast init) is in the init sequence |
| Wrong header format byte | Use `ATSH 81 11 F1`, not `C1 11 F1` |
| Engine not running | Turn ignition to ON (not ACC) or start the engine |
| ECU address wrong | This ECU is `0x11`; confirm with `ATSH 81 11 F1` |
| Adapter already connected | Disconnect all other apps using the dongle |

### Session timeout / `NO DATA`

The ECU drops the KWP session after ~2 seconds without a `TesterPresent` (service `0x3E`). The CLI script sends `3E` before every `21 00` request. If you are building your own integration, send `3E` at least once per second.

### Reconnect after drop

The CLI script does not auto-reconnect. Restart the script if the session drops. RealDash handles reconnection automatically via the `writeOnlyOnce` init frame.

### IOS-Vlink disconnects after a few seconds

The IOS-Vlink BLE dongle has an aggressive auto-sleep. Keep issuing commands continuously — a long gap between commands will drop the BLE connection. The 300ms polling loop in the script is intentional.

---

## Contributing

If you map additional bytes in the `21 00` response, or find addresses for AT / ABS / airbag ECUs, please open a PR updating the byte map table.

## License

MIT
