"""Live GPU monitor.

NVIDIA: one long-lived `nvidia-smi -lms` process (util, VRAM, temperature, power).
Everything else (AMD / Intel): Windows' built-in GPU performance counters, read by one
long-lived PowerShell process. No amd-smi / Adrenalin needed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from collections import deque

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

CREATE_NO_WINDOW = 0x08000000
HISTORY = 120  # samples (about 2 minutes)

_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$map = @{}
Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\DirectX' | ForEach-Object {
  $p = Get-ItemProperty $_.PSPath
  if ($p.AdapterLuid -ne $null -and $p.Description) {
    $l = [uint64]$p.AdapterLuid
    $key = ('luid_0x{0:X8}_0x{1:X8}' -f [uint32]($l -shr 32), [uint32]($l -band 0xFFFFFFFF)).ToLower()
    $map[$key] = @{ name = [string]$p.Description; total = [double]$p.DedicatedVideoMemory }
  }
}
$paths = '\GPU Engine(*)\Utilization Percentage','\GPU Adapter Memory(*)\Dedicated Usage','\GPU Adapter Memory(*)\Shared Usage'
Get-Counter -Counter $paths -SampleInterval 2 -Continuous | ForEach-Object {
  $eng = @{}; $ded = @{}; $sh = @{}
  foreach ($s in $_.CounterSamples) {
    $i = $s.InstanceName.ToLower()
    if ($i -notmatch '(luid_0x[0-9a-f]+_0x[0-9a-f]+)') { continue }
    $luid = $matches[1]
    if ($s.Path -like '*utilization*') {
      $t = if ($i -match 'engtype_(.+)$') { $matches[1] } else { 'x' }
      $k = "$luid|$t"; $eng[$k] = [double]$eng[$k] + $s.CookedValue
    } elseif ($s.Path -like '*dedicated*') { $ded[$luid] = [double]$s.CookedValue }
    else { $sh[$luid] = [double]$s.CookedValue }
  }
  $out = @()
  foreach ($luid in $map.Keys) {
    $u = 0
    foreach ($k in $eng.Keys) { if ($k.StartsWith($luid + '|') -and $eng[$k] -gt $u) { $u = $eng[$k] } }
    $out += @{ id = $luid; name = $map[$luid].name; util = [math]::Min(100, [math]::Round($u, 1));
               vram_used = [math]::Round($ded[$luid] / 1MB); vram_total = [math]::Round($map[$luid].total / 1MB);
               shared_used = [math]::Round($sh[$luid] / 1MB) }
  }
  [Console]::Out.WriteLine((ConvertTo-Json -InputObject $out -Compress -Depth 3)); [Console]::Out.Flush()
}
"""


class GpuMonitor(QObject):
    sample = Signal(dict)   # {id, name, util, vram_used, vram_total, temp, power, source}

    def __init__(self):
        super().__init__()
        self.procs: list[subprocess.Popen] = []
        self.running = False
        self.amd_ok = False
        self.amd_names: dict = {}

    def start(self):
        if self.running:
            return
        self.running = True
        threading.Thread(target=self._boot, daemon=True).start()

    def _boot(self):
        if shutil.which("nvidia-smi"):
            self._spawn(["nvidia-smi",
                         "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                         "--format=csv,noheader,nounits", "-lms", "1000"], self._read_nvidia)
        # AMD: prefer amd-smi when it is installed and answers; else the Windows counters cover AMD too.
        self.amd_ok = False
        if shutil.which("amd-smi"):
            self.amd_names = self._amd_names()
            self.amd_ok = bool(self._amd_poll())
            if self.amd_ok:
                threading.Thread(target=self._amd_loop, daemon=True).start()
        if not self.running:
            return
        ps = shutil.which("powershell")
        if ps:
            self._spawn([ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _PS],
                        self._read_counters)

    # ----- amd-smi -----
    @staticmethod
    def _amd_json(*args) -> object:
        try:
            exe = shutil.which("amd-smi") or "amd-smi"
            r = subprocess.run([exe, *args, "--json"], capture_output=True, text=True, timeout=15,
                               creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL)
            return json.loads(r.stdout)
        except (subprocess.SubprocessError, OSError, ValueError):
            return None

    @staticmethod
    def _gpu_list(data) -> list:
        if isinstance(data, dict):
            for k in ("gpu_data", "gpus"):
                if isinstance(data.get(k), list):
                    return data[k]
            return [data]
        return data if isinstance(data, list) else []

    @staticmethod
    def _dig(d, *path):
        """Walk nested dicts; unwrap {"value": x, "unit": ...}; 'N/A' -> None."""
        for key in path:
            if not isinstance(d, dict) or key not in d:
                return None
            d = d[key]
        if isinstance(d, dict):
            d = d.get("value")
        if isinstance(d, str):
            try:
                return float(d)
            except ValueError:
                return None
        return float(d) if isinstance(d, (int, float)) else None

    def _amd_names(self) -> dict:
        names = {}
        for i, g in enumerate(self._gpu_list(self._amd_json("static"))):
            idx = g.get("gpu", i)
            n = (g.get("asic") or {}).get("market_name")
            if n and n != "N/A":
                names[idx] = n
        return names

    def _amd_poll(self) -> list[dict]:
        out = []
        for i, g in enumerate(self._gpu_list(self._amd_json("metric"))):
            if not isinstance(g, dict):
                continue
            idx = g.get("gpu", i)
            util = self._dig(g, "usage", "gfx_activity")
            used = self._dig(g, "mem_usage", "used_vram")
            total = self._dig(g, "mem_usage", "total_vram")
            temp = self._dig(g, "temperature", "edge")
            if temp is None:
                temp = self._dig(g, "temperature", "hotspot")
            power = self._dig(g, "power", "socket_power")
            if power is None:
                power = self._dig(g, "power", "average_socket_power")
            if util is None and used is None:
                continue
            out.append({"id": f"amd{idx}", "name": getattr(self, "amd_names", {}).get(idx, f"AMD GPU {idx}"),
                        "util": util, "vram_used": used, "vram_total": total, "temp": temp,
                        "power": power, "source": "amd-smi"})
        return out

    def _amd_loop(self):
        import time
        while self.running:
            for s in self._amd_poll():
                self.sample.emit(s)
            time.sleep(1.5)

    def _spawn(self, argv, reader):
        try:
            p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 stdin=subprocess.DEVNULL, text=True, creationflags=CREATE_NO_WINDOW)
        except OSError:
            return
        self.procs.append(p)
        threading.Thread(target=reader, args=(p,), daemon=True).start()

    def stop(self):
        self.running = False
        for p in self.procs:
            try:
                p.terminate()
            except OSError:
                pass
        self.procs.clear()

    @staticmethod
    def _num(s: str) -> float | None:
        try:
            return float(s)
        except ValueError:
            return None  # "[N/A]" etc.

    def _read_nvidia(self, p):
        for line in p.stdout:
            f = [x.strip() for x in line.split(",")]
            if len(f) < 7:
                continue
            self.sample.emit({"id": f"nvidia{f[0]}", "name": f[1], "util": self._num(f[2]),
                              "vram_used": self._num(f[3]), "vram_total": self._num(f[4]),
                              "temp": self._num(f[5]), "power": self._num(f[6]), "source": "nvidia-smi"})

    def _read_counters(self, p):
        for line in p.stdout:
            line = line.strip()
            if not line.startswith(("[", "{")):
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict):
                data = [data]
            for d in data:
                if "basic render" in d["name"].lower():
                    continue
                low = d["name"].lower()
                if "nvidia" in low and shutil.which("nvidia-smi"):
                    continue  # nvidia-smi is more complete
                if self.amd_ok and ("amd" in low or "radeon" in low):
                    continue  # amd-smi is more complete
                used = d["vram_used"]
                total = d["vram_total"] or None
                note = "Windows GPU counters"
                if total and total < 1024:  # integrated GPU: little dedicated memory, uses system RAM
                    used = used + d["shared_used"]
                    total = None
                    note += " (shared system memory)"
                self.sample.emit({"id": d["id"], "name": d["name"], "util": d["util"], "vram_used": used,
                                  "vram_total": total, "temp": None, "power": None, "source": note})


class Graph(QWidget):
    def __init__(self, title: str, color: str, fixed_max: float | None = 100.0, unit: str = "%"):
        super().__init__()
        self.title, self.color, self.fixed_max, self.unit = title, QColor(color), fixed_max, unit
        self.data: deque = deque(maxlen=HISTORY)
        self.cap: float | None = None
        self.setMinimumHeight(110)

    def push(self, v: float | None, cap: float | None = None):
        self.data.append(v)
        if cap:
            self.cap = cap
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        pal = self.palette()
        p.setBrush(QColor("#14161b"))
        p.setPen(QPen(pal.mid().color(), 1))
        p.drawRoundedRect(r, 8, 8)
        top = self.fixed_max or self.cap or max([v for v in self.data if v is not None] + [1]) * 1.1
        for frac in (0.25, 0.5, 0.75):
            y = r.bottom() - r.height() * frac
            p.setPen(QPen(pal.midlight().color(), 1, Qt.DotLine))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        pts = [(i, v) for i, v in enumerate(self.data) if v is not None]
        if len(pts) > 1:
            step = r.width() / (HISTORY - 1)
            x0 = r.right() - step * (len(self.data) - 1)
            path = QPainterPath()
            for n, (i, v) in enumerate(pts):
                pt = QPointF(x0 + i * step, r.bottom() - r.height() * min(v / top, 1.0))
                path.moveTo(pt) if n == 0 else path.lineTo(pt)
            area = QPainterPath(path)
            area.lineTo(QPointF(x0 + pts[-1][0] * step, r.bottom()))
            area.lineTo(QPointF(x0 + pts[0][0] * step, r.bottom()))
            fill = QColor(self.color)
            fill.setAlpha(60)
            p.fillPath(area, fill)
            p.setPen(QPen(self.color, 2))
            p.drawPath(path)
        last = next((v for v in reversed(self.data) if v is not None), None)
        txt = self.title if last is None else f"{self.title}: {last:.0f}{self.unit}"
        if self.cap and self.unit != "%":
            txt += f" / {self.cap:.0f}{self.unit}"
        p.setPen(pal.text().color())
        p.drawText(r.adjusted(6, 4, -6, -4), Qt.AlignLeft | Qt.AlignTop, txt)


class GpuCard(QWidget):
    def __init__(self, name: str, source: str):
        super().__init__()
        self.setObjectName("card")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        head = QLabel(f"<b style='font-size:12pt'>{name}</b> &nbsp;<span style='color:#8b93a7'>via {source}</span>")
        lay.addWidget(head)
        self.stats = QLabel("")
        self.stats.setObjectName("muted")
        lay.addWidget(self.stats)
        row = QHBoxLayout()
        self.util = Graph("GPU load", "#3498db")
        self.vram = Graph("VRAM", "#9b59b6", fixed_max=None, unit=" MB")
        row.addWidget(self.util)
        row.addWidget(self.vram)
        lay.addLayout(row)

    def update_sample(self, s: dict):
        self.util.push(s["util"])
        self.vram.push(s["vram_used"], s["vram_total"])
        bits = []
        if s.get("temp") is not None:
            bits.append(f"{s['temp']:.0f} °C")
        if s.get("power") is not None:
            bits.append(f"{s['power']:.0f} W")
        self.stats.setText("   ".join(bits))


class GpuTab(QWidget):
    def __init__(self):
        super().__init__()
        self.monitor = GpuMonitor()
        self.cards: dict[str, GpuCard] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(12)
        bar = QHBoxLayout()
        self.toggle = QPushButton("Pause")
        self.toggle.clicked.connect(self.on_toggle)
        bar.addWidget(self.toggle)
        bar.addWidget(QLabel("History: about 2 minutes. Monitoring only runs while this is on."))
        bar.addStretch()
        lay.addLayout(bar)
        host = QWidget()
        self.col = QVBoxLayout(host)
        self.col.setSpacing(14)
        self.col.setContentsMargins(0, 0, 8, 0)
        self.col.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(host)
        lay.addWidget(scroll)
        self.empty = QLabel("Waiting for GPU data…")
        self.col.insertWidget(0, self.empty)
        self.monitor.sample.connect(self.on_sample)

    def on_sample(self, s: dict):
        card = self.cards.get(s["id"])
        if card is None:
            self.empty.hide()
            card = GpuCard(s["name"], s["source"])
            self.cards[s["id"]] = card
            self.col.insertWidget(self.col.count() - 1, card)
        card.update_sample(s)

    def activate(self):
        if not self.monitor.running and self.toggle.text() == "Pause":
            self.monitor.start()

    def on_toggle(self):
        if self.monitor.running:
            self.monitor.stop()
            self.toggle.setText("Resume")
        else:
            self.monitor.start()
            self.toggle.setText("Pause")

    def shutdown(self):
        self.monitor.stop()
