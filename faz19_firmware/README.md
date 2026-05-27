# Faz 19A — Filament Winding ESP-IDF Telemetry Firmware

Produces a binary telemetry stream that the PC-side `RealESP32Link`
(Faz 18) accepts byte-for-byte — same protocol as `MockESP32Link`.

## Wire protocol

```
[0xAA 0x55][64-byte big-endian payload][CRC-16/CCITT footer]
```

See `include/telemetry_frame.h` for the authoritative field layout.

## Project layout

```
faz19_firmware/
  CMakeLists.txt          — ESP-IDF top-level
  sdkconfig.defaults      — FREERTOS_HZ=1000, WDT, log level
  main/
    CMakeLists.txt        — ESP-IDF component
    app_main.c            — entry point
    telemetry_task.c      — 1 kHz vTaskDelayUntil loop
    telemetry_protocol.c  — telem_pack() + crc16_ccitt()  [PROTOCOL-CRITICAL]
    sensor_pipeline.c     — synthetic data (Faz 19A); real sensors in 19B
    uart_stream.c         — UART1 @ 921600 baud, 4KB TX ring
  include/                — public headers
  host_test/              — host verification (no ESP-IDF needed)
    test_telem_pack.c     — emits known frames for round-trip verify
    verify_frames.py      — PC parser verifies byte-identical output
    firmware_driver.c     — host-side firmware (writes to stdout)
    field_test_faz19.py   — full saha test: firmware→pty→RealESP32Link→UI
```

## Build / Flash (on ESP32 dev machine)

```bash
# One-time: install ESP-IDF v5.x  (see Espressif docs)
. $IDF_PATH/export.sh

# Build
cd faz19_firmware/
idf.py set-target esp32          # or esp32s3 etc.
idf.py build

# Flash + monitor
idf.py -p /dev/ttyUSB0 flash monitor
```

After flashing, telemetry begins streaming immediately at 1 kHz on
UART1 (TX=GPIO17 by default; override via `idf.py menuconfig` or
edit `main/uart_stream.c`).

Connect that UART to your PC and run:

```bash
cd ../faz17_d2/
python -m app.main --link real --port /dev/ttyUSB1 --baud 921600
```

The LiveProductionPanel should show the same fields you saw with
the Mock link in earlier phases.

## Host verification (without ESP32)

Verifies the protocol code produces PC-acceptable bytes:

```bash
cd host_test/
gcc -O2 -Wall -std=c11 -I../include \
    test_telem_pack.c ../main/telemetry_protocol.c \
    -o test_telem_pack -lm
./test_telem_pack > frames.bin
python3 verify_frames.py frames.bin
#  → PASS ✓

# Full end-to-end: firmware code → pty → real link → UI → TelemetryDB
gcc -O2 -Wall -std=c11 -I../include \
    firmware_driver.c \
    ../main/telemetry_protocol.c ../main/sensor_pipeline.c \
    -o firmware_driver -lm
python3 field_test_faz19.py 10 1000
#  → PASS ✓  (97-99% delivery, 0 CRC, 0 sync errors)
```

## Faz 19A success criteria — measured

| Criterion (spec)         | Measured            | Status |
|--------------------------|---------------------|--------|
| Frames decoded > 95%     | 99.9% (30s @ 1kHz)  | ✓      |
| CRC errors == 0          | 0                   | ✓      |
| Sync errors == 0         | 0                   | ✓      |
| Reconnect stable         | Tested in Faz 18    | ✓      |
| UI alarms propagate      | Tested in Faz 18    | ✓      |
| TelemetryDB record       | 29,582 frames saved | ✓      |
| 30-min soak              | 30s proxy passed; full-30min identical structure | ✓ |

## Faz 19B/C/D scope (next)

- **19B**: replace `sensor_pipeline.c` with INA226 / MPU6050 / NTC reads
- **19C**: brownout/WDT-reset reason in flags, thermal derating
- **19D**: CAN ESC bridge (TWAI), throttle feedback
