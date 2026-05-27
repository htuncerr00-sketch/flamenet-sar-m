"""
fmea_engine.py — FMEA + Commissioning SOP + FAT/SAT Checklist
==============================================================
FMEA: Failure Mode and Effects Analysis (IEC 60812)
RPN = Severity × Occurrence × Detection  (1-10 scale each)
RPN > 100 → kritik, derhal aksiyon gerekli

FAT: Factory Acceptance Test (üretici tesisinde)
SAT: Site Acceptance Test (kurulum yerinde)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List
import numpy as np

# ── FMEA ─────────────────────────────────────────────────────────

@dataclass(slots=True)
class FMEAEntry:
    item:           str
    failure_mode:   str
    effect:         str
    cause:          str
    severity:       int    # 1-10
    occurrence:     int    # 1-10
    detection:      int    # 1-10
    controls:       str    # Current controls
    action:         str    # Recommended action
    owner:          str

    @property
    def rpn(self) -> int: return self.severity * self.occurrence * self.detection
    @property
    def critical(self) -> bool: return self.rpn > 100

    def row(self) -> str:
        flag = "⚡" if self.critical else " "
        return (f"  {flag} RPN={self.rpn:4d} [{self.severity}/{self.occurrence}/{self.detection}]  "
                f"{self.item:<22} {self.failure_mode:<24} {self.effect[:30]}")


FMEA_TABLE: List[FMEAEntry] = [
    FMEAEntry("Fiber tension","Tension loss","Fiber kopma, part reject","Bobbin jam, fiber break",
        10,3,2,"Load cell + fiber break detector","Dual fiber sensor, auto-stop","Safety"),
    FMEAEntry("Fiber tension","Tension spike","Fiber kırılma, winding stop","Carriage jerk, overspeed",
        8,4,3,"Tension PID + soft limits","Jerk-limited motion profile","Controls"),
    FMEAEntry("Spindle sync","Phase slip","Angle error → coverage fail","Missed steps, encoder loss",
        7,3,3,"Encoder feedback + CUSUM","Closed-loop sync, alert","Controls"),
    FMEAEntry("Resin system","Pot life exceed","Gelation in bath, stoppage","Temp too high, slow winding",
        9,4,2,"Pot life timer + temp monitor","Resin temp alarm, batch control","Process"),
    FMEAEntry("Resin system","Wrong ratio","Poor cure, delamination","Weighing error",
        10,2,3,"Scale with checkweigh","Barcode+scale interlock","Quality"),
    FMEAEntry("E-stop chain","E-stop fails","Operator injury, equipment damage","Relay weld, wiring fault",
        10,1,1,"Daily test + safety relay monitoring","Monthly relay test SOP","Safety"),
    FMEAEntry("STO relay","STO fails active","Motor energized at E-stop","Coil failure",
        10,2,1,"Dual-channel safety relay","Pilz PNOZ, annual cert.","Safety"),
    FMEAEntry("Thermal runaway","Exotherm kaçması","Fire, part destruction","Poor temp control",
        9,2,2,"Thermal safety monitor (Faz 13)","Thermal runaway detector","Safety"),
    FMEAEntry("Power supply","48V PSU failure","Motion stop, session interrupt","Overload, overcurrent",
        7,3,4,"Overcurrent protection","UPS for 24V, hot-standby PSU","Reliability"),
    FMEAEntry("Brownout","MCU brownout","Firmware crash, position loss","Low AC voltage",
        7,4,3,"BOR (brown-out reset) + snapshot","Crash-safe resume + UPS","Firmware"),
    FMEAEntry("Encoder","Encoder loss","Position unknown, miss steps","Cable damage, noise",
        8,3,3,"CRC check + timeout","Shielded cable, EMI hardening","Hardware"),
    FMEAEntry("Void content","High void %","Structural failure","Poor compaction, air entrapment",
        9,3,4,"Vacuum bag + compaction pressure","Vf/void SPC monitoring","Quality"),
    FMEAEntry("Mandrel","Mandrel seizure","Demolding impossible","Inadequate release",
        8,2,6,"Release agent SOP","Triple coat Frekote, demolding force limit","Process"),
    FMEAEntry("Lead screw","Backlash increase","Position error grows","Wear, no lubrication",
        6,4,5,"Encoder feedback correction","Monthly backlash check, lubrication","Maintenance"),
    FMEAEntry("CAN bus","CAN timeout","Communication loss","EMI, wiring",
        7,3,3,"Watchdog + auto-reconnect","CAN termination, cable shielding","Hardware"),
]


def rpn_summary() -> str:
    sorted_fmea = sorted(FMEA_TABLE, key=lambda x: -x.rpn)
    lines = [
        "  FMEA TABLOSU (RPN sıralı, ⚡=kritik RPN>100):",
        f"  {'':2} {'RPN [S/O/D]':>15}  {'Item':<22} {'Failure Mode':<24} {'Effect':<30}",
        "  " + "─"*90,
    ]
    for e in sorted_fmea:
        lines.append(e.row())
    crits = [e for e in sorted_fmea if e.critical]
    lines += ["  " + "─"*90,
              f"  Toplam: {len(FMEA_TABLE)} entry  Kritik (RPN>100): {len(crits)}"]
    return "\n".join(lines)


# ── Commissioning SOP ──────────────────────────────────────────────

FAT_CHECKLIST = [
    # (item, pass_criterion, test_method)
    ("E-stop function",        "All motion stops <100ms",        "Press E-stop during motion, measure"),
    ("STO relay",              "Drive disabled, no torque",       "Multimeter on drive enable"),
    ("X axis travel",          "390mm ±0.5mm",                   "Dial indicator on carriage"),
    ("X axis repeatability",   "±0.05mm over 10 runs",           "Indicator, 10× home→midpoint"),
    ("A axis rotation",        "360° ±0.1°",                     "Protractor + encoder readout"),
    ("A axis RPM",             "0-60 RPM smooth",                "Stroboscope or Hall sensor"),
    ("Encoder X resolution",   "3.125µm (4x, 80step/mm)",        "Jog 1mm, count encoder pulses"),
    ("Encoder A resolution",   "0.045°/pulse (16ms, 5:1)",       "Rotate 360°, count pulses"),
    ("Tension zero",           "0.0 ±0.1N (tared)",              "Load cell readout, no fiber"),
    ("Tension calibration",    "5N/10N/15N ±2%",                 "Calibrated weights"),
    ("Spindle sync",           "Phase error <1° @30RPM",          "Scope: step/dir timing"),
    ("Soft limits X",          "Stop at X=-5 and X=395",         "Command beyond limits"),
    ("Hard limits X",          "Stop at limit switch",           "Manually actuate switch"),
    ("CAN bus heartbeat",      "100% TX @10ms",                  "CAN analyzer, 10s capture"),
    ("Telemetry binary",       "CRC-16 error rate <0.1%",        "1000 frame capture + count"),
    ("Thermal safety",         "Halt at dT/dt > 10°C/s",         "Simulate via software inject"),
    ("Brownout recovery",      "Resume after 80% AC→restore",    "Variac AC reduction test"),
    ("OTA firmware",           "Flash + verify + boot",           "Flash test binary"),
    ("Dry-run winding",        "No physical contact, motion OK", "Pattern w/o fiber"),
    ("First wet winding",      "Fiber tension 15N ±1N, no break", "Load cell log, 5 circuits"),
]

SAT_CHECKLIST = [
    ("Installation vertical", "Mandrel axis horizontal ±0.1°",   "Spirit level"),
    ("Electrical safety",    "PE continuity <0.1Ω",             "Earth bond tester"),
    ("EMC compliance",       "No encoder noise at full speed",   "Scope on encoder at 100mm/s"),
    ("Temperature check",    "Cabinet <45°C at 100% duty",      "30min full speed, thermal camera"),
    ("Vibration baseline",   "Vib RMS <0.5g at mandrel",        "IMU during winding"),
    ("Production winding",   "8 layers, Vf>0.50, void<3%",      "Ultrasonik C-scan of part"),
    ("Data recording",       "100Hz telemetry, 60min session",  "Play back session file"),
    ("Operator training",    "SOP acknowledged, signed",         "Training record"),
]


@dataclass(slots=True)
class ChecklistResult:
    item:   str
    passed: bool
    note:   str = ""

    def row(self) -> str:
        icon = "✓" if self.passed else "✗"
        return f"    {icon} {self.item:<40} {self.note}"


class CommissioningReport:
    """FAT + SAT execution and scoring."""
    def __init__(self):
        self._fat: List[ChecklistResult] = []
        self._sat: List[ChecklistResult] = []

    def run_fat_mock(self) -> int:
        """Simüle FAT — tüm maddeler PASS (gerçek testte ölçülür)."""
        self._fat = [ChecklistResult(item, True, "Mock PASS") for item,_,_ in FAT_CHECKLIST]
        # Mark a few as potentially failing for realism
        self._fat[19] = ChecklistResult(FAT_CHECKLIST[19][0], True, "5 circuit tension OK")
        return len([r for r in self._fat if r.passed])

    def run_sat_mock(self) -> int:
        self._sat = [ChecklistResult(item, True, "Mock PASS") for item,_,_ in SAT_CHECKLIST]
        return len([r for r in self._sat if r.passed])

    def fat_score(self) -> float:
        if not self._fat: return 0.0
        return sum(1 for r in self._fat if r.passed) / len(self._fat) * 100

    def sat_score(self) -> float:
        if not self._sat: return 0.0
        return sum(1 for r in self._sat if r.passed) / len(self._sat) * 100

    def fat_report(self) -> str:
        lines = [f"  FAT Report ({len(self._fat)} items):"]
        for r in self._fat: lines.append(r.row())
        lines.append(f"  FAT Score: {self.fat_score():.1f}/100")
        return "\n".join(lines)

    def sat_report(self) -> str:
        lines = [f"  SAT Report ({len(self._sat)} items):"]
        for r in self._sat: lines.append(r.row())
        lines.append(f"  SAT Score: {self.sat_score():.1f}/100")
        return "\n".join(lines)


# ── Maintenance Intervals ─────────────────────────────────────────

MAINTENANCE = [
    ("Daily",    "E-stop test",          "Press and verify all motion stops"),
    ("Daily",    "Resin bath clean",     "Acetone flush of resin tank and rollers"),
    ("Daily",    "Fiber guide check",    "Visual: ceramic guide wear, alignment"),
    ("Weekly",   "Lead screw lubrication","Molykote DX grease, 0.5g per axis"),
    ("Weekly",   "Encoder check",        "Jog 100mm, compare encoder vs command"),
    ("Weekly",   "Tension calibration",  "5/10/15N check with calibrated weights"),
    ("Monthly",  "Backlash measurement", "Indicator: 1mm reversal error check"),
    ("Monthly",  "Safety relay test",    "Simulate fault, verify relay drop-out"),
    ("Monthly",  "Cable inspection",     "Visual: chafe, connector tightness"),
    ("Quarterly","Full calibration",     "All axes, all sensors, NIST traceable"),
    ("Annually", "Safety certification","Third-party E-stop and STO functional test"),
    ("As needed","Bearing replacement",  "Vibration signature degradation >0.3g"),
]

SPARE_PARTS = [
    ("TMC2209 driver",        2, "A-axis driver replacement"),
    ("TMC5160 driver",        1, "X-axis driver replacement"),
    ("HX711 load cell",       1, "Tension sensor backup"),
    ("US Digital E6 encoder", 1, "Encoder replacement"),
    ("ESP32-WROOM-32D",       1, "MCU replacement"),
    ("Pilz PNOZ X3.1 relay",  1, "Safety relay spare"),
    ("NEMA34 stepper motor",  1, "X-axis motor"),
    ("Frekote 700-NC 400ml",  3, "Mandrel release agent"),
    ("Peel ply 200m roll",    1, "Vacuum process consumable"),
    ("Vacuum bag film 100m",  1, "Per-cure consumable"),
]
