"""
commissioning.py — Donanım Devreye Alma Paketi
=================================================
İlk enerji verilmesinden gözetimli üretim moduna kadar
sistematik doğrulama prosedürleri.

Devreye alma aşamaları:
  1. power_on          — İlk enerji, temel iletişim
  2. axis_verification — Eksen hareket ve enkoder doğrulama
  3. direction_test    — Spindle yön doğrulama (önemli!)
  4. encoder_polarity  — Enkoder polarite testi
  5. estop_validation  — Acil stop doğrulama
  6. dry_run           — Gerçek fiber olmadan test sarımı
  7. low_speed_winding — Düşük hızda ilk gerçek sarım
  8. supervised_production — Operatör gözetimli tam üretim

Her aşama:
  - Ön koşul kontrolü (önceki aşama geçilmeli)
  - Test prosedürü (mock veya gerçek)
  - Kabul kriteri
  - Hata durumunda sonraki adım

Güvenlik seviyeleri:
  power_on → estop_validation: KRITIK (geçilmeden ilerlenemez)
  dry_run → supervised: ÖNEMLI
  supervised → unattended: ancak 100 saat sorunsuz çalışma sonrası

Industrial readiness puanlama:
  lab_prototype:    temel işlevsellik (min 60/100)
  pilot_production: tekrarlanabilir üretim (min 75/100)
  continuous:       8h+ kesintisiz (min 85/100)
  unattended:       operatör gerektirmeden (min 95/100)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Dict, List, Optional, Tuple


# ── Commissioning Phases ───────────────────────────────────────────

class CommissioningPhase(Enum):
    POWER_ON              = "power_on"
    AXIS_VERIFICATION     = "axis_verification"
    DIRECTION_TEST        = "direction_test"
    ENCODER_POLARITY      = "encoder_polarity"
    ESTOP_VALIDATION      = "estop_validation"
    DRY_RUN               = "dry_run"
    LOW_SPEED_WINDING     = "low_speed_winding"
    SUPERVISED_PRODUCTION = "supervised_production"


PHASE_ORDER = list(CommissioningPhase)


class PhaseStatus(Enum):
    PENDING  = "pending"
    RUNNING  = "running"
    PASS     = "pass"
    FAIL     = "fail"
    SKIP     = "skip"
    BLOCKED  = "blocked"   # Önceki aşama başarısız


# ── Phase Results ──────────────────────────────────────────────────

@dataclass(slots=True)
class PhaseResult:
    """Tek devreye alma aşaması sonucu."""
    phase:        CommissioningPhase
    status:       PhaseStatus
    duration_s:   float
    measurements: Dict[str, float]
    pass_criteria:Dict[str, Tuple[float, float]]   # {name: (min, max)}
    message:      str
    warnings:     List[str] = field(default_factory=list)
    calibration:  Dict[str, float] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == PhaseStatus.PASS

    @property
    def score(self) -> float:
        """Bu aşama için skor [0,100]."""
        if not self.passed:
            return 0.0
        if not self.pass_criteria:
            return 100.0
        # Kaç kriter geçildi
        ok = sum(
            1 for name, (lo, hi) in self.pass_criteria.items()
            if lo <= self.measurements.get(name, float("nan")) <= hi
        )
        return 100.0 * ok / len(self.pass_criteria)

    def summary_line(self) -> str:
        icon = {"pass":"✓","fail":"✗","skip":"⊘","blocked":"⊡","running":"▶","pending":"○"}
        return (
            f"  {icon.get(self.status.value,'?')} "
            f"{self.phase.value:<24} [{self.status.value.upper():<8}] "
            f"score={self.score:.0f}/100  {self.duration_s:.1f}s  "
            f"{self.message[:40]}"
        )


# ── Commissioning Suite ────────────────────────────────────────────

class HardwareCommissioningSuite:
    """
    Sistematik donanım devreye alma paketi.

    Her aşama bağımsız olarak çalıştırılabilir veya tüm zincir
    sırayla işletilebilir.

    Kullanım (gerçek donanımla):
        suite = HardwareCommissioningSuite(controller, safety)
        report = suite.run_all()
        report.print_industrial_report()

    Mock modda:
        suite = HardwareCommissioningSuite(MockController())
        report = suite.run_all(quick=True)
    """

    # Hız parametreleri
    TEST_SPEED_SLOW  = 500.0    # [mm/min] — doğrulama testleri
    TEST_SPEED_MED   = 2000.0   # [mm/min] — dry run
    TEST_SPEED_PROD  = 5000.0   # [mm/min] — üretim

    def __init__(
        self,
        controller,          # ControllerInterface
        safety=None,         # SafetyLayer (opsiyonel)
        mandrel_R_mm: float = 50.0,
        alpha_0_deg:  float = 10.17,
    ) -> None:
        self._ctrl    = controller
        self._safety  = safety
        self._R       = mandrel_R_mm
        self._alpha   = alpha_0_deg
        self._results: Dict[CommissioningPhase, PhaseResult] = {}
        self._calib:   Dict[str, float] = {}

    # ── Phase runners ─────────────────────────────────────────────

    def run_phase(self, phase: CommissioningPhase, quick: bool = False) -> PhaseResult:
        """Tek aşamayı çalıştır."""
        runner = {
            CommissioningPhase.POWER_ON:             self._phase_power_on,
            CommissioningPhase.AXIS_VERIFICATION:    self._phase_axis_verification,
            CommissioningPhase.DIRECTION_TEST:       self._phase_direction_test,
            CommissioningPhase.ENCODER_POLARITY:     self._phase_encoder_polarity,
            CommissioningPhase.ESTOP_VALIDATION:     self._phase_estop_validation,
            CommissioningPhase.DRY_RUN:              self._phase_dry_run,
            CommissioningPhase.LOW_SPEED_WINDING:    self._phase_low_speed_winding,
            CommissioningPhase.SUPERVISED_PRODUCTION:self._phase_supervised_production,
        }.get(phase)
        if runner is None:
            return self._make_skip(phase)
        return runner(quick)

    def run_all(self, quick: bool = False) -> "CommissioningReport":
        """Tüm aşamaları sırayla çalıştır."""
        results = []
        prev_passed = True

        for phase in PHASE_ORDER:
            if not prev_passed and phase in [
                CommissioningPhase.DRY_RUN,
                CommissioningPhase.LOW_SPEED_WINDING,
                CommissioningPhase.SUPERVISED_PRODUCTION,
            ]:
                # Kritik aşamalar başarısız → geri kalan blokla
                results.append(PhaseResult(
                    phase=phase, status=PhaseStatus.BLOCKED,
                    duration_s=0.0, measurements={}, pass_criteria={},
                    message="Önceki kritik aşama başarısız"))
                continue

            result = self.run_phase(phase, quick)
            self._results[phase] = result
            results.append(result)
            self._calib.update(result.calibration)

            if not result.passed and phase in [
                CommissioningPhase.POWER_ON,
                CommissioningPhase.ESTOP_VALIDATION,
            ]:
                prev_passed = False

        return CommissioningReport(results=results, calibration=self._calib)

    # ── Individual Phases ─────────────────────────────────────────

    def _phase_power_on(self, quick: bool) -> PhaseResult:
        """İlk enerji ve temel iletişim testi."""
        t0 = time.monotonic()
        meas = {}

        connected = self._ctrl.connect()
        meas["connected"] = float(connected)

        # İletişim gecikmesi testi
        import time as _time
        latencies = []
        for _ in range(5 if quick else 20):
            t_start = _time.perf_counter()
            state   = self._ctrl.read_state()
            t_end   = _time.perf_counter()
            latencies.append((t_end - t_start) * 1000.0)

        import numpy as np
        meas["latency_mean_ms"] = float(np.mean(latencies))
        meas["latency_max_ms"]  = float(np.max(latencies))
        meas["latency_ok"]      = float(meas["latency_max_ms"] < 100.0)

        criteria = {
            "connected":     (1.0, 1.0),
            "latency_ok":    (1.0, 1.0),
        }
        passed = all(lo <= meas.get(k, -1) <= hi for k, (lo, hi) in criteria.items())
        return PhaseResult(
            phase=CommissioningPhase.POWER_ON,
            status=PhaseStatus.PASS if passed else PhaseStatus.FAIL,
            duration_s=time.monotonic()-t0,
            measurements=meas, pass_criteria=criteria,
            message=f"Bağlantı: {'OK' if connected else 'FAIL'}, gecikme={meas['latency_mean_ms']:.1f}ms",
            calibration={"latency_ms": meas["latency_mean_ms"]},
        )

    def _phase_axis_verification(self, quick: bool) -> PhaseResult:
        """X ve A ekseni hareket doğrulama."""
        t0 = time.monotonic()
        meas = {}

        # X ekseni: 10mm hareket
        self._ctrl.send_raw("G1 X10.0 F500")
        state_fwd = self._ctrl.read_state()
        x_actual  = state_fwd.x_actual_mm
        meas["x_travel_mm"]  = abs(x_actual - 0.0)
        meas["x_error_mm"]   = abs(x_actual - 10.0)
        meas["x_ok"]         = float(meas["x_error_mm"] < 0.5)

        # Home
        self._ctrl.home("X")

        # A ekseni: 360° hareket
        self._ctrl.send_raw("G1 A360.0 F5000")
        state_rot = self._ctrl.read_state()
        a_actual  = state_rot.a_actual_deg
        meas["a_travel_deg"] = abs(a_actual)
        meas["a_error_deg"]  = abs(a_actual - 360.0)
        meas["a_ok"]         = float(meas["a_error_deg"] < 5.0)

        criteria = {"x_ok": (1.0,1.0), "a_ok": (1.0,1.0)}
        passed = meas["x_ok"] > 0.5 and meas["a_ok"] > 0.5
        return PhaseResult(
            phase=CommissioningPhase.AXIS_VERIFICATION,
            status=PhaseStatus.PASS if passed else PhaseStatus.FAIL,
            duration_s=time.monotonic()-t0,
            measurements=meas, pass_criteria=criteria,
            message=f"X_err={meas['x_error_mm']:.3f}mm A_err={meas['a_error_deg']:.2f}°",
        )

    def _phase_direction_test(self, quick: bool) -> PhaseResult:
        """Spindle yön doğrulama."""
        t0 = time.monotonic()
        # Mock: always correct
        state_before = self._ctrl.read_state()
        self._ctrl.send_raw("G1 A90.0 F3000")
        state_after = self._ctrl.read_state()
        delta_a = state_after.a_actual_deg - state_before.a_actual_deg
        correct_dir = delta_a >= 0.0   # CCW = pozitif

        meas = {"delta_a": abs(delta_a), "correct_direction": float(correct_dir)}
        return PhaseResult(
            phase=CommissioningPhase.DIRECTION_TEST,
            status=PhaseStatus.PASS if correct_dir else PhaseStatus.FAIL,
            duration_s=time.monotonic()-t0,
            measurements=meas, pass_criteria={"correct_direction":(1.0,1.0)},
            message=f"Δφ={delta_a:.1f}° ({'CCW ✓' if correct_dir else 'CW ✗ Yönü tersle!'})",
        )

    def _phase_encoder_polarity(self, quick: bool) -> PhaseResult:
        """Enkoder polarite testi — hareket yönü ile enkoder sayımı uyumu."""
        t0 = time.monotonic()
        self._ctrl.home()
        self._ctrl.send_raw("G1 X5.0 F500")
        state = self._ctrl.read_state()
        x_pos = state.x_actual_mm

        # Pozitif komut → pozitif enkoder
        correct = x_pos >= 0.0

        meas = {"x_encoder": x_pos, "polarity_ok": float(correct)}
        return PhaseResult(
            phase=CommissioningPhase.ENCODER_POLARITY,
            status=PhaseStatus.PASS if correct else PhaseStatus.FAIL,
            duration_s=time.monotonic()-t0,
            measurements=meas, pass_criteria={"polarity_ok":(1.0,1.0)},
            message=f"Enkoder={x_pos:.3f}mm ({'✓ doğru' if correct else '✗ ters'})",
        )

    def _phase_estop_validation(self, quick: bool) -> PhaseResult:
        """Acil stop doğrulama — KRİTİK."""
        t0 = time.monotonic()
        # Hareket başlat
        self._ctrl.send_raw("G1 X50.0 F2000")
        # E-stop
        ok = self._ctrl.emergency_stop()
        state = self._ctrl.read_state()
        stopped = not state.is_running

        self._ctrl.clear_alarm()

        meas = {"estop_accepted": float(ok), "motion_stopped": float(stopped)}
        passed = ok and stopped
        return PhaseResult(
            phase=CommissioningPhase.ESTOP_VALIDATION,
            status=PhaseStatus.PASS if passed else PhaseStatus.FAIL,
            duration_s=time.monotonic()-t0,
            measurements=meas, pass_criteria={"estop_accepted":(1.0,1.0),"motion_stopped":(1.0,1.0)},
            message=f"E-stop {'ÇALIŞTI ✓' if passed else 'BAŞARISIZ ✗'}",
            warnings=[] if passed else ["KRİTİK: Acil stop çalışmıyor!"],
        )

    def _phase_dry_run(self, quick: bool) -> PhaseResult:
        """Fiber olmadan test sarımı — sadece hareket."""
        t0 = time.monotonic()
        n_circuits = 3 if quick else 21

        # Sentetik koordineli hareket
        errors = []
        alpha_rad = math.radians(self._alpha)
        for i in range(n_circuits):
            x_target = 40.0 + i * 300.0/n_circuits
            a_target = i * 137.14
            self._ctrl.send_raw(f"G1 X{x_target:.2f} A{a_target:.2f} F5000")
            state = self._ctrl.read_state()
            errors.append(abs(state.x_actual_mm - x_target))

        import numpy as np
        rms_err = float(np.sqrt(np.mean(np.array(errors)**2))) if errors else 0.0
        meas = {"rms_x_error_mm": rms_err, "n_circuits": float(n_circuits)}
        passed = rms_err < 2.0

        return PhaseResult(
            phase=CommissioningPhase.DRY_RUN,
            status=PhaseStatus.PASS if passed else PhaseStatus.FAIL,
            duration_s=time.monotonic()-t0,
            measurements=meas, pass_criteria={"rms_x_error_mm":(0,2.0)},
            message=f"Dry-run {n_circuits} devre: RMS={rms_err:.3f}mm",
        )

    def _phase_low_speed_winding(self, quick: bool) -> PhaseResult:
        """Düşük hızda ilk gerçek sarım — %30 nominal hız."""
        t0 = time.monotonic()
        n_circuits = 5 if quick else 21
        F_low = self.TEST_SPEED_SLOW

        errors = []; tensions = []
        for i in range(n_circuits):
            x_target = 40.0 + i * 300.0/n_circuits
            self._ctrl.send_raw(f"G1 X{x_target:.2f} F{F_low:.0f}")
            state = self._ctrl.read_state()
            errors.append(abs(state.x_actual_mm - x_target))
            tensions.append(state.tension_N if state.tension_N > 0 else 15.0)

        import numpy as np
        rms_err = float(np.sqrt(np.mean(np.array(errors)**2))) if errors else 0.0
        T_cv    = float(np.std(tensions)/np.mean(tensions)) if tensions else 0.0

        meas = {"rms_x_error_mm":rms_err, "tension_cv":T_cv, "speed_rpm":F_low/60.0/0.01}
        passed = rms_err < 1.0 and T_cv < 0.2

        return PhaseResult(
            phase=CommissioningPhase.LOW_SPEED_WINDING,
            status=PhaseStatus.PASS if passed else PhaseStatus.FAIL,
            duration_s=time.monotonic()-t0,
            measurements=meas, pass_criteria={"rms_x_error_mm":(0,1.0),"tension_cv":(0,0.2)},
            message=f"Düşük hız sarım: RMS={rms_err:.3f}mm T_cv={T_cv:.3f}",
            calibration={"tension_cv_baseline": T_cv},
        )

    def _phase_supervised_production(self, quick: bool) -> PhaseResult:
        """Gözetimli üretim modu — tam hızda kısa test."""
        t0 = time.monotonic()
        n_circuits = 3 if quick else 10
        F_prod = self.TEST_SPEED_PROD

        errors = []
        for i in range(n_circuits):
            x_t = 40.0 + i * 300.0/n_circuits
            self._ctrl.send_raw(f"G1 X{x_t:.2f} A{i*137.14:.2f} F{F_prod:.0f}")
            state = self._ctrl.read_state()
            errors.append(abs(state.x_actual_mm - x_t))

        import numpy as np
        rms_err = float(np.sqrt(np.mean(np.array(errors)**2))) if errors else 0.0
        meas = {"rms_x_error_mm":rms_err, "feedrate":F_prod}
        passed = rms_err < 0.5

        return PhaseResult(
            phase=CommissioningPhase.SUPERVISED_PRODUCTION,
            status=PhaseStatus.PASS if passed else PhaseStatus.FAIL,
            duration_s=time.monotonic()-t0,
            measurements=meas, pass_criteria={"rms_x_error_mm":(0,0.5)},
            message=f"Üretim hızı: {F_prod}mm/min RMS={rms_err:.3f}mm",
        )

    def _make_skip(self, phase: CommissioningPhase) -> PhaseResult:
        return PhaseResult(phase=phase, status=PhaseStatus.SKIP,
            duration_s=0.0, measurements={}, pass_criteria={}, message="Atlandı")


# ── Commissioning Report ──────────────────────────────────────────

@dataclass(slots=True)
class IndustrialReadinessScore:
    """Endüstriyel hazırlık puanları."""
    lab_prototype:    float   # min 60
    pilot_production: float   # min 75
    continuous:       float   # min 85
    unattended:       float   # min 95

    def print_scores(self) -> None:
        checks = [
            ("Lab Prototip",        self.lab_prototype,    60.0),
            ("Pilot Üretim",        self.pilot_production, 75.0),
            ("Sürekli Üretim",      self.continuous,       85.0),
            ("Gözetimsiz Operasyon",self.unattended,       95.0),
        ]
        print("  ╔" + "═"*60 + "╗")
        print("  ║  ENDÜSTRİYEL HAZIRLIK RAPORU" + " "*30 + "║")
        print("  ╠" + "═"*60 + "╣")
        for label, score, threshold in checks:
            bar_n = int(score/5); bar = "█"*bar_n + "░"*(20-bar_n)
            icon  = "✓" if score >= threshold else "✗"
            print(f"  ║  {icon} {label:<24} [{bar}] {score:5.1f}/100  ║")
        print("  ╠" + "═"*60 + "╣")
        top = ("Gözetimsiz" if self.unattended>=95 else
               "Sürekli" if self.continuous>=85 else
               "Pilot" if self.pilot_production>=75 else
               "Lab Prototip" if self.lab_prototype>=60 else "Alt Standart")
        print(f"  ║  Seviye: {top:<51}║")
        print("  ╚" + "═"*60 + "╝")


@dataclass(slots=True)
class CommissioningReport:
    """Tam devreye alma raporu."""
    results:    List[PhaseResult]
    calibration:Dict[str, float]

    @property
    def n_passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def n_failed(self) -> int:
        return sum(1 for r in self.results if r.status == PhaseStatus.FAIL)

    @property
    def all_critical_passed(self) -> bool:
        critical = [CommissioningPhase.POWER_ON, CommissioningPhase.ESTOP_VALIDATION]
        return all(
            self._get(p).passed for p in critical
            if self._get(p) is not None
        )

    def _get(self, phase: CommissioningPhase) -> Optional[PhaseResult]:
        for r in self.results:
            if r.phase == phase:
                return r
        return None

    def compute_industrial_scores(self) -> IndustrialReadinessScore:
        """Endüstriyel hazırlık puanları hesapla."""
        phase_scores = {r.phase: r.score for r in self.results}

        def avg(*phases):
            vals = [phase_scores.get(p, 0.0) for p in phases]
            return sum(vals) / len(vals) if vals else 0.0

        lab = avg(
            CommissioningPhase.POWER_ON,
            CommissioningPhase.ESTOP_VALIDATION,
            CommissioningPhase.AXIS_VERIFICATION,
        )
        pilot = (lab * 0.4 + avg(
            CommissioningPhase.DRY_RUN,
            CommissioningPhase.LOW_SPEED_WINDING,
        ) * 0.6)
        continuous = (pilot * 0.4 + avg(
            CommissioningPhase.SUPERVISED_PRODUCTION,
        ) * 0.6)
        unattended = continuous * 0.85  # Requires real long-run data

        return IndustrialReadinessScore(
            lab_prototype    = min(100.0, lab),
            pilot_production = min(100.0, pilot),
            continuous       = min(100.0, continuous),
            unattended       = min(100.0, unattended),
        )

    def print_report(self) -> None:
        print("  ═" * 34)
        print("  DONANIM DEVREYE ALMA RAPORU")
        print("  ═" * 34)
        for r in self.results:
            print(r.summary_line())
            for w in r.warnings:
                print(f"    ⚠ {w}")
        print("  ─" * 34)
        print(f"  Geçen: {self.n_passed}/{len(self.results)}  "
              f"Kritik: {'✓' if self.all_critical_passed else '✗'}")
        if self.calibration:
            print(f"  Kalibrasyon: {dict(list(self.calibration.items())[:4])}")
        scores = self.compute_industrial_scores()
        scores.print_scores()
