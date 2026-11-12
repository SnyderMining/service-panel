"""Service Panel: start/stop your local services, see what is running, check your commands."""
from __future__ import annotations

import sys
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFormLayout, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QVBoxLayout, QWidget,
)

import checker
import gpu
import theme
import config
import runner
from config import Service

COLORS = {runner.RUNNING: "#2ecc71", runner.STARTING: "#f1c40f", runner.STOPPED: "#e74c3c"}
LABELS = {runner.RUNNING: "RUNNING", runner.STARTING: "STARTING (process up, port not open yet)",
          runner.STOPPED: "STOPPED"}


def muted(text: str) -> QLabel:
    l = QLabel(text)
    l.setObjectName("muted")
    return l


def dot_icon(color: str) -> QIcon:
    pm = QPixmap(16, 16)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawEllipse(2, 2, 12, 12)
    p.end()
    return QIcon(pm)


ICONS: dict = {}


def init_icons():
    ICONS.update({k: dot_icon(v) for k, v in COLORS.items()})


class Bridge(QObject):
    """Lets worker threads hand results back to the GUI thread."""
    statuses = Signal(dict)
    message = Signal(str, str)         # service name, text
    checked = Signal(str, str, list)   # service name, which ("start"/"stop"), issues
    ports = Signal(list)


bridge = Bridge()


def in_thread(fn):
    threading.Thread(target=fn, daemon=True).start()


class CommandEditor(QWidget):
    """Code box + live syntax check result."""

    def __init__(self, title: str, svc_name: str, which: str, get_shell):
        super().__init__()
        self.svc_name, self.which, self.get_shell = svc_name, which, get_shell
        self._gen = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel(f"<b>{title}</b>"))
        row.addStretch()
        self.btn = QPushButton("Check syntax")
        self.btn.clicked.connect(self.run_check)
        row.addWidget(self.btn)
        lay.addLayout(row)
        self.edit = QPlainTextEdit()
        f = QFont("Cascadia Mono, Consolas", 10)
        self.edit.setFont(f)
        self.edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.edit.setTabStopDistance(32)
        lay.addWidget(self.edit, 1)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.result)
        self.timer = QTimer(self, singleShot=True, interval=900)
        self.timer.timeout.connect(self.run_check)
        self.edit.textChanged.connect(self.timer.start)
        bridge.checked.connect(self._show)

    def text(self) -> str:
        return self.edit.toPlainText()

    def run_check(self):
        self._gen += 1
        gen, script, shell = self._gen, self.text(), self.get_shell()
        self.result.setText("checking…")

        def work():
            issues = checker.check(script, shell)
            if gen == self._gen:
                bridge.checked.emit(f"{self.svc_name}|{self.which}|{gen}", shell, issues)
        in_thread(work)

    def _show(self, key: str, _shell: str, issues: list):
        name, which, gen = key.rsplit("|", 2)
        if name != self.svc_name or which != self.which or int(gen) != self._gen:
            return
        if not self.text().strip():
            self.result.setText("")
        elif not issues:
            self.result.setText('<span style="color:#2ecc71">✔ No problems found</span>')
        else:
            esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;")
            html = []
            for i in issues:
                col = "#e74c3c" if i.level == "error" else "#e0a800"
                html.append(f'<span style="color:{col}">{esc(str(i))}</span>')
            self.result.setText("<br>".join(html))


class ServiceTab(QWidget):
    changed = Signal()

    def __init__(self, svc: Service):
        super().__init__()
        self.svc = svc
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 16)
        root.setSpacing(14)

        top = QHBoxLayout()
        self.dot = QLabel()
        self.dot.setFixedSize(22, 22)
        self.state = QLabel("…")
        self.state.setObjectName("state")
        top.addWidget(self.dot)
        top.addWidget(self.state)
        top.addStretch()
        self.btn_start = QPushButton("START")
        self.btn_stop = QPushButton("STOP")
        for b, oid in ((self.btn_start, "start"), (self.btn_stop, "stop")):
            b.setObjectName(oid)
            b.setMinimumSize(120, 42)
            b.setCursor(Qt.PointingHandCursor)
            top.addWidget(b)
        root.addLayout(top)

        form = QGridLayout()
        self.name = QLineEdit(svc.name)
        self.port = QSpinBox()
        self.port.setRange(0, 65535)
        self.port.setSpecialValueText("none")
        self.port.setValue(svc.port)
        self.match = QLineEdit(svc.process_match)
        self.match.setPlaceholderText("process name or command-line text, used if no port / port not open yet")
        self.shell = QComboBox()
        self.shell.addItems(["cmd", "powershell"])
        self.shell.setCurrentText(svc.shell)
        self.cwd = QLineEdit(svc.cwd)
        self.cwd.setPlaceholderText("working directory (optional)")
        self.show_console = QCheckBox("Show console window when starting")
        self.show_console.setChecked(svc.show_console)
        self.kill = QCheckBox("After STOP, kill leftovers still on the port / matching the process")
        self.kill.setChecked(svc.kill_leftovers)
        form.addWidget(muted("Name"), 0, 0); form.addWidget(self.name, 0, 1)
        form.addWidget(muted("Port"), 0, 2); form.addWidget(self.port, 0, 3)
        form.addWidget(muted("Shell"), 0, 4); form.addWidget(self.shell, 0, 5)
        form.addWidget(muted("Process"), 1, 0); form.addWidget(self.match, 1, 1, 1, 5)
        form.addWidget(muted("Folder"), 2, 0); form.addWidget(self.cwd, 2, 1, 1, 5)
        form.addWidget(self.show_console, 3, 1, 1, 3)
        form.addWidget(self.kill, 4, 1, 1, 5)
        form.setColumnStretch(1, 1)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        root.addLayout(form)

        cmds = QHBoxLayout()
        self.start_ed = CommandEditor("START command", svc.name, "start", self.shell.currentText)
        self.stop_ed = CommandEditor("STOP command (optional)", svc.name, "stop", self.shell.currentText)
        self.start_ed.edit.setPlainText(svc.start_cmd)
        self.stop_ed.edit.setPlainText(svc.stop_cmd)
        cmds.addWidget(self.start_ed)
        cmds.addWidget(self.stop_ed)
        root.addLayout(cmds, 1)

        bottom = QHBoxLayout()
        self.btn_save = QPushButton("Save")
        self.btn_save.setObjectName("primary")
        self.btn_remove = QPushButton("Remove service")
        self.btn_remove.setObjectName("danger")
        bottom.addWidget(self.btn_save)
        bottom.addWidget(self.btn_remove)
        bottom.addStretch()
        root.addLayout(bottom)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(90)
        self.log.setFont(QFont("Cascadia Mono, Consolas", 9))
        root.addWidget(self.log)

        self.shell.currentTextChanged.connect(lambda _: (self.start_ed.run_check(), self.stop_ed.run_check()))
        self.btn_save.clicked.connect(self.save)
        self.btn_start.clicked.connect(self.do_start)
        self.btn_stop.clicked.connect(self.do_stop)
        self.set_state(runner.STOPPED)
        self.start_ed.run_check()
        self.stop_ed.run_check()

    def collect(self) -> Service:
        s = self.svc
        s.name = self.name.text().strip() or s.name
        s.port = self.port.value()
        s.process_match = self.match.text().strip()
        s.shell = self.shell.currentText()
        s.cwd = self.cwd.text().strip()
        s.show_console = self.show_console.isChecked()
        s.kill_leftovers = self.kill.isChecked()
        s.start_cmd = self.start_ed.text()
        s.stop_cmd = self.stop_ed.text()
        self.start_ed.svc_name = self.stop_ed.svc_name = s.name
        return s

    def save(self):
        self.collect()
        self.changed.emit()
        self.append(f"Saved to {config.CONFIG_PATH}")

    def append(self, text: str):
        self.log.appendPlainText(text)

    def set_state(self, st: str):
        pm = ICONS[st].pixmap(22, 22)
        self.dot.setPixmap(pm)
        self.state.setText(LABELS[st])
        self.state.setStyleSheet(f"color:{COLORS[st]}")
        self.btn_start.setEnabled(st == runner.STOPPED)
        self.btn_stop.setEnabled(st != runner.STOPPED)

    def do_start(self):
        svc = self.collect()
        self.changed.emit()
        self.append(runner.start(svc))

    def do_stop(self):
        svc = self.collect()
        self.btn_stop.setEnabled(False)
        self.append("Stopping…")
        in_thread(lambda: bridge.message.emit(svc.name, runner.stop(svc)))


class Overview(QWidget):
    def __init__(self, panel: "Panel"):
        super().__init__()
        self.panel = panel
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(22, 18, 22, 16)
        self.lay.setSpacing(12)
        self.rows: dict[str, tuple[QLabel, QLabel]] = {}
        self.grid = QGridLayout()
        self.grid.setVerticalSpacing(10)
        self.grid.setHorizontalSpacing(12)
        self.lay.addLayout(self.grid)
        self.lay.addStretch()
        bar = QHBoxLayout()
        b_stop = QPushButton("STOP ALL")
        b_stop.setObjectName("danger")
        b_stop.clicked.connect(panel.stop_all)
        bar.addWidget(b_stop)
        b_add = QPushButton("+ Add service")
        b_add.setObjectName("primary")
        b_add.clicked.connect(panel.add_service)
        bar.addWidget(b_add)
        bar.addStretch()
        self.lay.addLayout(bar)

    def rebuild(self, tabs: list[ServiceTab]):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.rows.clear()
        for r, t in enumerate(tabs):
            dot, txt = QLabel(), QLabel(t.svc.name)
            txt.setObjectName("rowname")
            go, no = QPushButton("Start"), QPushButton("Stop")
            go.clicked.connect(t.do_start)
            no.clicked.connect(t.do_stop)
            self.grid.addWidget(dot, r, 0)
            self.grid.addWidget(txt, r, 1)
            self.grid.addWidget(go, r, 2)
            self.grid.addWidget(no, r, 3)
            self.grid.setColumnStretch(1, 1)
            self.rows[t.svc.name] = (dot, txt)

    def set_state(self, name: str, st: str):
        if name in self.rows:
            self.rows[name][0].setPixmap(ICONS[st].pixmap(22, 22))


class Listening(QWidget):
    """Everything listening on a TCP port, with 'Add as service'."""
    COLS = ["Port", "PID", "Process", "Scope", "Command"]

    def __init__(self, panel: "Panel"):
        super().__init__()
        self.panel, self.rows = panel, []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(12)
        bar = QHBoxLayout()
        self.btn_refresh = QPushButton("Refresh")
        self.btn_add = QPushButton("Add as service")
        self.btn_add.setObjectName("primary")
        self.btn_kill = QPushButton("Kill process")
        self.btn_kill.setObjectName("danger")
        self.hide_sys = QCheckBox("Hide Windows system processes")
        self.hide_sys.setChecked(True)
        for w in (self.btn_refresh, self.btn_add, self.btn_kill, self.hide_sys):
            bar.addWidget(w)
        bar.addStretch()
        lay.addLayout(bar)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table)
        self.note = muted("")
        lay.addWidget(self.note)
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_add.clicked.connect(self.add_selected)
        self.btn_kill.clicked.connect(self.kill_selected)
        self.hide_sys.toggled.connect(self.fill)
        bridge.ports.connect(self.on_ports)

    def refresh(self):
        self.note.setText("scanning…")
        in_thread(lambda: bridge.ports.emit(runner.listening_ports()))

    def on_ports(self, rows: list):
        self.rows = rows
        self.fill()

    SYSTEM = {"system", "svchost.exe", "services.exe", "lsass.exe", "wininit.exe", "spoolsv.exe"}

    def visible(self) -> list:
        if not self.hide_sys.isChecked():
            return self.rows
        return [r for r in self.rows if r["name"].lower() not in self.SYSTEM]

    def fill(self):
        rows = self.visible()
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [r["port"], r["pid"], r["name"], "local only" if r["local"] else "all interfaces",
                    r["cmd"] or r["exe"]]
            for j, v in enumerate(vals):
                self.table.setItem(i, j, QTableWidgetItem(str(v)))
        self.table.resizeColumnsToContents()
        self.note.setText(f"{len(rows)} listening ports")

    def selected(self):
        i = self.table.currentRow()
        rows = self.visible()
        return rows[i] if 0 <= i < len(rows) else None

    def add_selected(self):
        r = self.selected()
        if not r:
            return
        name, ok = QInputDialog.getText(self, "Add as service", "Name:", text=r["name"].removesuffix(".exe"))
        if ok and name.strip():
            self.panel.add_service_obj(Service(name.strip(), start_cmd=r["cmd"], port=r["port"],
                                               process_match=r["name"], cwd=r["cwd"]))

    def kill_selected(self):
        r = self.selected()
        if r and QMessageBox.question(self, "Kill", f"Kill {r['name']} (pid {r['pid']}) and its children?")                 == QMessageBox.Yes:
            runner.kill_tree(r["pid"])
            QTimer.singleShot(800, self.refresh)


class Panel(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Service Panel")
        self.resize(1000, 760)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.overview = Overview(self)
        self.tabs.addTab(self.overview, "Overview")
        self.stab: list[ServiceTab] = []
        for s in config.load():
            self._add_tab(s)
        self.overview.rebuild(self.stab)
        self.listening = Listening(self)
        self.tabs.addTab(self.listening, "What's running")
        self.gpu = gpu.GpuTab()
        self.tabs.addTab(self.gpu, "GPU")
        QApplication.instance().aboutToQuit.connect(self.gpu.shutdown)
        self.tabs.currentChanged.connect(self.on_tab_changed)
        bridge.statuses.connect(self.on_statuses)
        bridge.message.connect(self.on_message)
        self._polling = False
        self.poll = QTimer(self, interval=2500)
        self.poll.timeout.connect(self.refresh)
        self.poll.start()
        self.refresh()

    def on_tab_changed(self, i: int):
        w = self.tabs.widget(i)
        if w is self.listening:
            self.listening.refresh()
        elif w is self.gpu:
            self.gpu.activate()

    def _add_tab(self, svc: Service) -> ServiceTab:
        t = ServiceTab(svc)
        t.changed.connect(self.persist)
        t.btn_remove.clicked.connect(lambda: self.remove_service(t))
        self.stab.append(t)
        self.tabs.insertTab(len(self.stab), t, ICONS[runner.STOPPED], svc.name)
        return t

    def persist(self):
        config.save([t.svc for t in self.stab])
        for i, t in enumerate(self.stab, 1):
            self.tabs.setTabText(i, t.svc.name)
        self.overview.rebuild(self.stab)

    def add_service(self):
        name, ok = QInputDialog.getText(self, "Add service", "Name:")
        if ok and name.strip():
            self.add_service_obj(Service(name.strip()))

    def add_service_obj(self, svc: Service):
        t = self._add_tab(svc)
        self.persist()
        self.tabs.setCurrentWidget(t)

    def remove_service(self, t: ServiceTab):
        if QMessageBox.question(self, "Remove", f"Remove '{t.svc.name}' from the panel?\n"
                                "(Nothing is deleted from disk.)") != QMessageBox.Yes:
            return
        self.stab.remove(t)
        self.tabs.removeTab(self.tabs.indexOf(t))
        t.deleteLater()
        self.persist()

    def stop_all(self):
        for t in self.stab:
            if t.btn_stop.isEnabled():
                t.do_stop()

    def refresh(self):
        if self._polling:
            return
        self._polling = True
        svcs = [t.svc for t in self.stab]

        def work():
            procs = runner.snapshot_processes()
            bridge.statuses.emit({s.name: runner.status(s, procs) for s in svcs})
        in_thread(work)

    def on_statuses(self, d: dict):
        self._polling = False
        for i, t in enumerate(self.stab, 1):
            st = d.get(t.svc.name)
            if st:
                t.set_state(st)
                self.tabs.setTabIcon(i, ICONS[st])
                self.overview.set_state(t.svc.name, st)

    def on_message(self, name: str, text: str):
        for t in self.stab:
            if t.svc.name == name:
                t.append(text)


def main():
    app = QApplication(sys.argv)
    theme.apply(app)
    init_icons()
    w = Panel()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
