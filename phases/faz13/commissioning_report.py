"""
commissioning_report.py — Endüstriyel Komisyonlama Raporu
==========================================================
Kategori bazlı skor sistemi: readiness / commissioning / reliability / production.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

@dataclass(slots=True)
class CategoryScore:
    name:str; score:float; max_score:float=100.0
    details:str=""; critical:bool=False
    @property
    def pct(self): return self.score/self.max_score*100
    @property
    def ok(self): return self.score>=60.0 if not self.critical else self.score>=80.0
    def bar(self,w:int=20)->str:
        n=int(self.pct/100*w); return "█"*n+"░"*(w-n)

@dataclass
class CommissioningReport:
    timestamp:       float = field(default_factory=time.time)
    categories:      Dict[str,CategoryScore] = field(default_factory=dict)
    first_wind_steps:int  = 0
    first_wind_pass: int  = 0
    n_tests_run:     int  = 0
    n_tests_pass:    int  = 0
    notes:           List[str] = field(default_factory=list)

    def add(self, name:str, score:float, details:str="", critical:bool=False):
        self.categories[name]=CategoryScore(name,score,details=details,critical=critical)

    @property
    def readiness_score(self) -> float:
        cats=["safety","timing","winding_accuracy","thermal","drift","sensors"]
        vals=[self.categories[c].score for c in cats if c in self.categories]
        return sum(vals)/len(vals) if vals else 0.0

    @property
    def commissioning_score(self) -> float:
        if self.first_wind_steps==0: return 0.0
        return self.first_wind_pass/self.first_wind_steps*100

    @property
    def reliability_score(self) -> float:
        cats=["timing","thermal","drift","recovery"]
        vals=[self.categories[c].score for c in cats if c in self.categories]
        return sum(vals)/len(vals) if vals else 0.0

    @property
    def production_score(self) -> float:
        w={"safety":0.25,"timing":0.15,"winding_accuracy":0.20,
           "thermal":0.10,"drift":0.10,"sensors":0.10,"8h":0.05,"unattended":0.05}
        s=sum(self.categories.get(k,CategoryScore(k,0)).score*v for k,v in w.items())
        return s

    @property
    def ready(self) -> bool:
        return (self.readiness_score>=70 and
                all(c.ok for c in self.categories.values() if c.critical) and
                self.first_wind_pass>=8)

    def print_full(self):
        t=time.strftime("%Y-%m-%d %H:%M:%S",time.localtime(self.timestamp))
        print(f"\n  ╔{'═'*62}╗")
        print(f"  ║  ENDÜSTRİYEL KOMİSYONLAMA RAPORU  {t}  ║")
        print(f"  ╠{'═'*62}╣")
        for name,c in sorted(self.categories.items()):
            icon="✓" if c.ok else ("⚠" if not c.critical else "✗")
            lbl=f"{'[KRİTİK] ' if c.critical else ''}{c.name}"
            print(f"  ║  {icon} {lbl:<28} [{c.bar()}] {c.score:5.1f}  ║")
        print(f"  ╠{'═'*62}╣")
        rs=self.readiness_score; cs=self.commissioning_score
        rel=self.reliability_score; ps=self.production_score
        for label,score in [("Hazırlık",rs),("Komisyonlama",cs),("Güvenilirlik",rel),("Üretim",ps)]:
            bar=self._bar(score); icon="✓" if score>=70 else "✗"
            print(f"  ║  {icon} {label:<28} [{bar}] {score:5.1f}  ║")
        print(f"  ╠{'═'*62}╣")
        print(f"  ║  İlk Sarım: {self.first_wind_pass}/{self.first_wind_steps} adım PASS  "
              f"Testler: {self.n_tests_pass}/{self.n_tests_run}  {'★' if self.ready else '✗'}  "
              f"{' '*15}║")
        if self.ready:
            print(f"  ║  ★★★  FIRST REAL WIND TEST READY  ★★★{' '*24}║")
        else:
            missing=[c.name for c in self.categories.values() if not c.ok]
            msg=f"  Eksik: {','.join(missing[:3])}"
            print(f"  ║  ✗ HAZIR DEĞİL.{msg:<46}║")
        if self.notes:
            print(f"  ╠{'═'*62}╣")
            for note in self.notes[-3:]: print(f"  ║  • {note:<59}║")
        print(f"  ╚{'═'*62}╝")

    @staticmethod
    def _bar(v:float,w:int=20)->str:
        n=int(v/100*w); return "█"*n+"░"*(w-n)
