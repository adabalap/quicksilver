import glob
import time
from pathlib import Path
from .logging_setup import log

class ThermalGuard:
    INCLUDE = ("cpu", "gpu", "soc", "ddr", "nsph", "sys-therm", "tsens", "mtktscpu")
    EXCLUDE = ("trip", "bcl", "lvl", "ac", "sdr", "mmw", "ific")
    PLAUSIBLE_MIN_C = 5.0
    PLAUSIBLE_MAX_C = 110.0

    def __init__(self, cfg: dict):
        self.enabled = cfg["thermal_enabled"]
        self.hot = cfg["thermal_hot_celsius"]
        self.resume = cfg["thermal_resume_celsius"]
        self.zone_glob = cfg["thermal_zone_glob"]
        self.poll_s = cfg["thermal_poll_seconds"]
        self.max_wait_s = cfg["thermal_max_wait_seconds"]
        self._zones = self._select_zones() if self.enabled else []
        if self.enabled and not self._zones:
            log.warning("Thermal guard on but no usable compute zones — no-op.")
        elif self.enabled:
            log.info(f"Thermal guard tracking {len(self._zones)} compute zone(s).")

    def _select_zones(self) -> list[str]:
        selected = []
        for temp_path in glob.glob(self.zone_glob):
            try:
                ztype = (Path(temp_path).parent / "type").read_text().strip().lower()
            except Exception:
                continue
            if any(b in ztype for b in self.EXCLUDE):
                continue
            if not any(g in ztype for g in self.INCLUDE):
                continue
            selected.append(temp_path)
        return selected

    def read_celsius(self) -> float | None:
        if not self._zones:
            return None
        temps = []
        for z in self._zones:
            try:
                raw = int(Path(z).read_text().strip())
                c = raw / 1000.0 if abs(raw) > 1000 else float(raw)
                if self.PLAUSIBLE_MIN_C <= c <= self.PLAUSIBLE_MAX_C:
                    temps.append(c)
            except Exception:
                continue
        return max(temps) if temps else None

    def wait_until_cool(self, should_continue) -> bool:
        if not self._zones:
            return True
        temp = self.read_celsius()
        if temp is None or temp < self.hot:
            return True
        log.warning(f"Thermal hard-stop: SoC {temp:.1f}°C (>= {self.hot}°C). "
                    f"Pausing until <= {self.resume}°C.")
        waited = 0
        while should_continue() and waited < self.max_wait_s:
            time.sleep(self.poll_s)
            waited += self.poll_s
            temp = self.read_celsius()
            if temp is None or temp <= self.resume:
                log.info(f"Thermal recovered ({temp}°C). Resuming.")
                return True
            log.info(f"Cooling… {temp:.1f}°C (target <= {self.resume}), {waited}s.")
        if waited >= self.max_wait_s:
            log.warning(f"Still hot after {self.max_wait_s}s — skip batch, retry next poll.")
        return False
