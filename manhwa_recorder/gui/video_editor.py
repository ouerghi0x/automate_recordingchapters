"""Real-time video player + visual dual-handle timeline + FFmpeg cut/trim widget."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)


def _fmt_ms(ms: int) -> str:
    s = max(0, ms // 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _fmt_ffmpeg_ms(ms: int) -> str:
    s_total = max(0.0, ms / 1000.0)
    h = int(s_total // 3600)
    m = int((s_total % 3600) // 60)
    s = s_total % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


class RangeTimelineWidget(QWidget):
    """Visual timeline with draggable Start/End cut handles and playhead."""

    positionSeeked = Signal(int)
    rangeChanged = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(34)
        self.setMaximumHeight(38)
        self._duration = 0
        self._pos = 0
        self._start = 0
        self._end = 0
        self._drag: str | None = None  # "start" | "end" | "pos"
        self._pad = 10

    def setDuration(self, dur: int) -> None:
        self._duration = max(0, dur)
        self._start = 0
        self._end = self._duration
        self._pos = min(self._pos, self._duration)
        self.rangeChanged.emit(self._start, self._end)
        self.update()

    def setPosition(self, pos: int) -> None:
        if self._drag is None:
            self._pos = max(0, min(pos, self._duration))
            self.update()

    def start_ms(self) -> int:
        return self._start

    def end_ms(self) -> int:
        return self._end

    def _ms_to_x(self, ms: int) -> int:
        w = max(1, self.width() - 2 * self._pad)
        if self._duration <= 0:
            return self._pad
        return self._pad + int((ms / self._duration) * w)

    def _x_to_ms(self, x: int) -> int:
        w = max(1, self.width() - 2 * self._pad)
        if self._duration <= 0:
            return 0
        ratio = max(0.0, min(1.0, (x - self._pad) / w))
        return int(ratio * self._duration)

    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.LeftButton or self._duration <= 0:
            return
        x = int(ev.position().x())
        sx = self._ms_to_x(self._start)
        ex = self._ms_to_x(self._end)
        if abs(x - sx) <= 10:
            self._drag = "start"
        elif abs(x - ex) <= 10:
            self._drag = "end"
        else:
            self._drag = "pos"
        self._apply_drag(x)

    def mouseMoveEvent(self, ev) -> None:
        if self._drag is not None:
            self._apply_drag(int(ev.position().x()))

    def mouseReleaseEvent(self, ev) -> None:
        self._drag = None

    def _apply_drag(self, x: int) -> None:
        ms = self._x_to_ms(x)
        if self._drag == "start":
            self._start = min(ms, self._end)
            self._pos = self._start
            self.rangeChanged.emit(self._start, self._end)
            self.positionSeeked.emit(self._start)
        elif self._drag == "end":
            self._end = max(ms, self._start)
            self._pos = self._end
            self.rangeChanged.emit(self._start, self._end)
            self.positionSeeked.emit(self._end)
        elif self._drag == "pos":
            self._pos = ms
            self.positionSeeked.emit(self._pos)
        self.update()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        h = self.height()
        cy = h // 2
        x0, x1 = self._pad, self.width() - self._pad

        # Base track
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#333642"))
        p.drawRoundedRect(x0, cy - 4, max(1, x1 - x0), 8, 4, 4)

        if self._duration > 0:
            sx = self._ms_to_x(self._start)
            ex = self._ms_to_x(self._end)
            px = self._ms_to_x(self._pos)

            # Selected range highlight
            p.setBrush(QColor("#6c5ce7"))
            p.drawRoundedRect(sx, cy - 4, max(2, ex - sx), 8, 4, 4)

            # Playhead
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.drawLine(px, 4, px, h - 4)

            # Start & End handles
            p.setPen(QPen(QColor("#ffffff"), 1.5))
            p.setBrush(QColor("#00cec9"))
            p.drawRoundedRect(sx - 5, cy - 11, 10, 22, 3, 3)
            p.setBrush(QColor("#fd79a8"))
            p.drawRoundedRect(ex - 5, cy - 11, 10, 22, 3, 3)


class VideoEditorWidget(QWidget):
    def __init__(self, get_ffmpeg: Callable[[], str], parent=None):
        super().__init__(parent)
        self._get_ffmpeg = get_ffmpeg
        self._duration_ms = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)

        # Top bar: chapter/video selector
        top = QHBoxLayout()
        top.addWidget(QLabel("Chapter / Video:"))
        self.chapter_combo = QComboBox()
        self.chapter_combo.setMinimumWidth(260)
        self.chapter_combo.currentIndexChanged.connect(self._on_combo_changed)
        top.addWidget(self.chapter_combo, 1)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_browse = QPushButton("Browse…")
        self.btn_browse.clicked.connect(self._browse)
        top.addWidget(self.btn_refresh)
        top.addWidget(self.btn_browse)
        lay.addLayout(top)

        # Video preview
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(280)
        lay.addWidget(self.video_widget, 1)

        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.video_widget)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)

        # Visual dual-handle timeline
        trow = QHBoxLayout()
        self.btn_play = QPushButton("Play")
        self.btn_play.setFixedWidth(70)
        self.btn_play.clicked.connect(self._toggle_play)
        self.timeline = RangeTimelineWidget()
        self.timeline.positionSeeked.connect(self._on_seek)
        self.timeline.rangeChanged.connect(self._on_range_changed)
        self.lbl_time = QLabel("00:00 / 00:00")
        trow.addWidget(self.btn_play)
        trow.addWidget(self.timeline, 1)
        trow.addWidget(self.lbl_time)
        lay.addLayout(trow)

        # Cut controls (no numeric inputs)
        crow = QHBoxLayout()
        self.lbl_range = QLabel("Cut range: 00:00 → 00:00")
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Keep selected range", "Cut out selected range"])
        self.btn_export = QPushButton("Export Cut Video")
        self.btn_export.clicked.connect(self._export_cut)
        crow.addWidget(self.lbl_range)
        crow.addStretch(1)
        crow.addWidget(self.mode_combo)
        crow.addWidget(self.btn_export)
        lay.addLayout(crow)

    def set_videos(self, items: list[tuple[str, str]]) -> None:
        cur = self.chapter_combo.currentData()
        self.chapter_combo.blockSignals(True)
        self.chapter_combo.clear()
        for label, path in items:
            self.chapter_combo.addItem(label, path)
        if cur:
            idx = self.chapter_combo.findData(cur)
            if idx >= 0:
                self.chapter_combo.setCurrentIndex(idx)
        self.chapter_combo.blockSignals(False)
        if self.chapter_combo.count() > 0:
            self._load_video(self.chapter_combo.currentData())

    def load_video(self, path: str) -> None:
        idx = self.chapter_combo.findData(path)
        if idx >= 0:
            self.chapter_combo.setCurrentIndex(idx)
        else:
            self.chapter_combo.blockSignals(True)
            self.chapter_combo.addItem(Path(path).name, path)
            self.chapter_combo.setCurrentIndex(self.chapter_combo.count() - 1)
            self.chapter_combo.blockSignals(False)
            self._load_video(path)

    def _on_combo_changed(self, _idx: int) -> None:
        path = self.chapter_combo.currentData()
        if path:
            self._load_video(path)

    def _load_video(self, path: str) -> None:
        if not path or not Path(path).exists():
            return
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.pause()
        self.btn_play.setText("Play")

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select video", "", "Videos (*.mp4 *.mkv *.webm);;All files (*.*)"
        )
        if path:
            self.load_video(path)

    def _toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.btn_play.setText("Play")
        else:
            self.player.play()
            self.btn_play.setText("Pause")

    def _on_duration(self, dur: int) -> None:
        self._duration_ms = dur
        self.timeline.setDuration(dur)
        self.lbl_time.setText(f"{_fmt_ms(self.player.position())} / {_fmt_ms(dur)}")

    def _on_position(self, pos: int) -> None:
        self.timeline.setPosition(pos)
        self.lbl_time.setText(f"{_fmt_ms(pos)} / {_fmt_ms(self._duration_ms)}")

    def _on_seek(self, pos: int) -> None:
        self.player.setPosition(pos)
        self.lbl_time.setText(f"{_fmt_ms(pos)} / {_fmt_ms(self._duration_ms)}")

    def _on_range_changed(self, start_ms: int, end_ms: int) -> None:
        self.lbl_range.setText(f"Cut range: {_fmt_ms(start_ms)} → {_fmt_ms(end_ms)}")

    def _export_cut(self) -> None:
        path = self.chapter_combo.currentData()
        if not path or not Path(path).exists():
            QMessageBox.warning(self, "No video", "Please select an existing video file.")
            return
        try:
            ffmpeg_bin = self._get_ffmpeg()
        except Exception as exc:
            QMessageBox.critical(self, "FFmpeg missing", str(exc))
            return

        src = Path(path)
        start = _fmt_ffmpeg_ms(self.timeline.start_ms())
        end = _fmt_ffmpeg_ms(self.timeline.end_ms())
        out = src.with_name(f"{src.stem}_trimmed{src.suffix}")
        idx = 1
        while out.exists():
            out = src.with_name(f"{src.stem}_trimmed_{idx}{src.suffix}")
            idx += 1

        keep_range = self.mode_combo.currentIndex() == 0
        try:
            if keep_range:
                subprocess.run(
                    [ffmpeg_bin, "-y", "-ss", start, "-to", end, "-i", str(src), "-c", "copy", str(out)],
                    check=True, capture_output=True, text=True,
                )
            else:
                with tempfile.TemporaryDirectory() as tmp:
                    tdir = Path(tmp)
                    p1 = tdir / f"part1{src.suffix}"
                    p2 = tdir / f"part2{src.suffix}"
                    concat_txt = tdir / "concat.txt"
                    subprocess.run(
                        [ffmpeg_bin, "-y", "-i", str(src), "-t", start, "-c", "copy", str(p1)],
                        check=True, capture_output=True, text=True,
                    )
                    subprocess.run(
                        [ffmpeg_bin, "-y", "-ss", end, "-i", str(src), "-c", "copy", str(p2)],
                        check=True, capture_output=True, text=True,
                    )
                    concat_txt.write_text(f"file '{p1.as_posix()}'\nfile '{p2.as_posix()}'\n", encoding="utf-8")
                    subprocess.run(
                        [ffmpeg_bin, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt), "-c", "copy", str(out)],
                        check=True, capture_output=True, text=True,
                    )
        except subprocess.CalledProcessError as exc:
            QMessageBox.critical(self, "FFmpeg failed", exc.stderr or str(exc))
            return

        QMessageBox.information(self, "Done", f"Saved cut video:\n{out}")
        self.load_video(str(out))
