#!/usr/bin/env python3
"""
Suzuki Cultus 2016 (Pakistan) — K-Line KWP2000 live data reader
BLE adapter: IOS-Vlink (ELM327 v2.3)
Protocol: ISO 14230 fast init, ECU 0x11, service 0x21 local ID 0x00

Note: The Cultus 2016 sold in Pakistan shares its engine ECU platform with
the Suzuki Swift 1999. Standard OBD-II (ISO 15765 / CAN) does not work;
the ECU communicates over K-Line (ISO 14230-4 KWP2000) only.
"""

import asyncio
import re
import signal
import sys
from dataclasses import dataclass
from bleak import BleakClient, BleakScanner

# ── BLE config ────────────────────────────────────────────────────────────────
DEVICE_NAME  = "IOS-Vlink"
WRITE_UUID   = "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f"
NOTIFY_UUID  = "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f"

# ── ELM327 init sequence (from SZ Viewer source) ─────────────────────────────
ELM_INIT = [
    "ATZ", "ATE0", "ATL0", "ATS0", "ATH0",
    "ATAL", "ATIB10", "ATKW0", "ATSW00", "ATAT0",
    "ATCAF1", "ATCFC1", "ATFCSM0",
]
ECU_INIT = [
    "ATTP5",           # KWP2000 fast init protocol
    "ATSH 81 11 F1",   # header: fmt=0x81(len=1), ECU=0x11, tester=0xF1
    "ATST 19",         # timeout 100ms
]

# ── Engine data decoder (Engine_KWP_00_Local$ byte map) ──────────────────────
@dataclass
class EngineData:
    # Core
    rpm:           float   # b[20:21] × 0.25
    coolant_c:     int     # b[14] − 40
    speed_kmh:     int     # b[22]
    tps_pct:       float   # b[27] × 0.392
    intake_c:      int     # b[24] − 40
    baro_kpa:      float   # b[41] × 0.5
    batt_v:        float   # b[49] × 0.0784
    # Engine
    engine_load:   float   # b[13] × 0.392157
    ign_advance:   int     # b[23] − 64
    desired_idle:  int     # b[35] × 10  (approx RPM)
    # Fuel / injection
    fuel_pulse1:   int     # b[11] raw control byte
    fuel_pulse2:   int     # b[12] raw control byte
    inj_pw_ms:     float   # b[37:38] × 0.256  (injector pulse width ms)
    # Air
    airflow_raw:   int     # b[25] (MAF/airflow raw byte)
    # O2 / fuel trim
    o2_v:          float   # b[29] × 0.0196  (0–5 V range)
    stft_pct:      float   # b[30] × 0.78 − 100  (short-term fuel trim %)
    # Idle air control
    iac_pos:       float   # b[42] × 0.392


def decode_engine_frame(raw_hex: str) -> EngineData | None:
    """Parse 21 00 response. Strips 6100 header, decodes byte map."""
    clean = re.sub(r'[^0-9A-Fa-f]', '', raw_hex)
    if not clean.startswith("6100") or len(clean) < 140:
        return None
    b = bytes.fromhex(clean[4:])  # skip 61 00 response header
    if len(b) < 65:
        return None

    inj_pw_raw = (b[37] << 8) | b[38]  # big-endian 2-byte

    return EngineData(
        rpm          = ((b[20] << 8) | b[21]) * 0.25,
        coolant_c    = b[14] - 40,
        speed_kmh    = b[22],
        tps_pct      = b[27] * 0.392,
        intake_c     = b[24] - 40,
        baro_kpa     = b[41] * 0.5,
        batt_v       = b[49] * 0.0784,
        engine_load  = b[13] * 0.392157,
        ign_advance  = b[23] - 64,
        desired_idle = b[35] * 10,
        fuel_pulse1  = b[11],
        fuel_pulse2  = b[12],
        inj_pw_ms    = inj_pw_raw * 0.256,
        airflow_raw  = b[25],
        o2_v         = b[29] * 0.0196,
        stft_pct     = b[30] * 0.78 - 100,
        iac_pos      = b[42] * 0.392,
    )


# ── ELM327 communication ─────────────────────────────────────────────────────
class ELM327:
    def __init__(self, client: BleakClient):
        self.client = client
        self._buf = ""
        self._event = asyncio.Event()

    def _on_notify(self, _, data: bytes):
        self._buf += data.decode("utf-8", errors="replace")
        if ">" in self._buf:
            self._event.set()

    async def start(self):
        await self.client.start_notify(NOTIFY_UUID, self._on_notify)

    async def send(self, cmd: str, timeout: float = 8.0) -> str:
        self._buf = ""
        self._event.clear()
        await self.client.write_gatt_char(WRITE_UUID, (cmd + "\r").encode(), response=True)
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        return self._buf

    async def init_elm(self):
        for cmd in ELM_INIT:
            await self.send(cmd)

    async def init_ecu(self) -> bool:
        for cmd in ECU_INIT:
            await self.send(cmd)
        for attempt in range(3):
            if attempt:
                print(f"  retrying in 5s (attempt {attempt + 1}/3)...")
                await asyncio.sleep(5)
            r = await self.send("ATFI", timeout=12)
            if "ERROR" not in r.upper():
                await self.send("ATKW")
                return True
        return False


# ── Main loop ─────────────────────────────────────────────────────────────────
async def run():
    print(f"Scanning for {DEVICE_NAME}...")
    device = await BleakScanner.find_device_by_filter(
        lambda d, _: d.name and DEVICE_NAME.lower() in d.name.lower(),
        timeout=15,
    )
    if not device:
        print("Device not found. Check dongle is powered and not connected elsewhere.")
        return

    print(f"Found: {device.name} [{device.address}]")

    async with BleakClient(device) as client:
        elm = ELM327(client)
        await elm.start()

        print("Initializing ELM327...")
        await elm.init_elm()

        print("Connecting to engine ECU (0x11)...")
        if not await elm.init_ecu():
            print("ECU init failed.")
            return

        print("Connected.\n")
        hdr = (f"  {'RPM':>7}  {'Cool':>6}  {'Spd':>5}  {'TPS':>5}  "
               f"{'IAT':>5}  {'Load':>5}  {'Ign':>5}  "
               f"{'InjPW':>6}  {'O2':>5}  {'STFT':>6}  "
               f"{'Baro':>7}  {'Batt':>5}  {'IAC':>5}")
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))

        while True:
            await elm.send("3E", timeout=3)       # TesterPresent keepalive
            raw = await elm.send("21 00", timeout=5)
            d = decode_engine_frame(raw)
            if d:
                print(
                    f"  {d.rpm:>7.0f}"
                    f"  {d.coolant_c:>4}°C"
                    f"  {d.speed_kmh:>3}kph"
                    f"  {d.tps_pct:>4.1f}%"
                    f"  {d.intake_c:>3}°C"
                    f"  {d.engine_load:>4.1f}%"
                    f"  {d.ign_advance:>3}°"
                    f"  {d.inj_pw_ms:>5.2f}ms"
                    f"  {d.o2_v:>4.2f}V"
                    f"  {d.stft_pct:>+5.1f}%"
                    f"  {d.baro_kpa:>5.1f}kPa"
                    f"  {d.batt_v:>4.2f}V"
                    f"  {d.iac_pos:>4.1f}%"
                )
            else:
                print(f"  [bad frame] {raw[:40]}")
            await asyncio.sleep(0.3)

def main():
    loop = asyncio.new_event_loop()
    task = loop.create_task(run())

    def _stop(*_):
        task.cancel()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    try:
        loop.run_until_complete(task)
    except (asyncio.CancelledError, KeyboardInterrupt):
        print("\nStopped.")
    finally:
        loop.close()

if __name__ == "__main__":
    main()
