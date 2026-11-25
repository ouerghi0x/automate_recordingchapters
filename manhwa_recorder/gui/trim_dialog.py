"""Simple FFmpeg video trim / cut dialog."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLineEdit, QMessageBox,
                               QPushButton, QVBoxLayout, QWidget)


class TrimVideoDialog(QDialog):
    def __init__(self, ffmpeg_path: str, initial_video: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Trim / Cut Video")
        self.setMinimumWidth(460)
        self.ffmpeg_path = ffmpeg_path
        self.output_path: str = ""

        lay = QVBoxLayout(self)
        f = QFormLayout()

        self.video_edit = QLineEdit(initial_video)
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.video_edit)
        row.addWidget(btn_browse)
        w = QWidget()
        w.setLayout(row)
        f.addRow("Video file", w)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Keep selected range (trim)", "Cut out selected range (remove middle)"])
        f.addRow("Mode", self.mode_combo)

        self.start_edit = QLineEdit("00:00:00")
        self.start_edit.setPlaceholderText("HH:MM:SS or seconds (e.g. 12.5)")
        f.addRow("Start time", self.start_edit)

        self.end_edit = QLineEdit("00:00:10")
        self.end_edit.setPlaceholderText("HH:MM:SS or seconds (e.g. 25.0)")
        f.addRow("End time", self.end_edit)

        lay.addLayout(f)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Run FFmpeg")
        bb.accepted.connect(self._run_trim)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select video", "", "Videos (*.mp4 *.mkv *.webm);;All files (*.*)"
        )
        if path:
            self.video_edit.setText(path)

    def _run_trim(self) -> None:
        src = Path(self.video_edit.text().strip())
        if not src.exists():
            QMessageBox.warning(self, "Missing file", "Please select an existing video file.")
            return
        start = self.start_edit.text().strip()
        end = self.end_edit.text().strip()
        if not start or not end:
            QMessageBox.warning(self, "Invalid time", "Please enter both start and end times.")
            return

        out = src.with_name(f"{src.stem}_trimmed{src.suffix}")
        idx = 1
        while out.exists():
            out = src.with_name(f"{src.stem}_trimmed_{idx}{src.suffix}")
            idx += 1

        keep_range = self.mode_combo.currentIndex() == 0
        try:
            if keep_range:
                cmd = [
                    self.ffmpeg_path, "-y",
                    "-ss", start, "-to", end,
                    "-i", str(src),
                    "-c", "copy", str(out),
                ]
                subprocess.run(cmd, check=True, capture_output=True, text=True)
            else:
                with tempfile.TemporaryDirectory() as tmp:
                    tdir = Path(tmp)
                    p1 = tdir / f"part1{src.suffix}"
                    p2 = tdir / f"part2{src.suffix}"
                    concat_txt = tdir / "concat.txt"
                    subprocess.run(
                        [self.ffmpeg_path, "-y", "-i", str(src), "-t", start, "-c", "copy", str(p1)],
                        check=True, capture_output=True, text=True,
                    )
                    subprocess.run(
                        [self.ffmpeg_path, "-y", "-ss", end, "-i", str(src), "-c", "copy", str(p2)],
                        check=True, capture_output=True, text=True,
                    )
                    concat_txt.write_text(f"file '{p1.as_posix()}'\nfile '{p2.as_posix()}'\n", encoding="utf-8")
                    subprocess.run(
                        [self.ffmpeg_path, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt), "-c", "copy", str(out)],
                        check=True, capture_output=True, text=True,
                    )
        except subprocess.CalledProcessError as exc:
            QMessageBox.critical(self, "FFmpeg failed", exc.stderr or str(exc))
            return

        self.output_path = str(out)
        QMessageBox.information(self, "Done", f"Saved trimmed video:\n{out}")
        self.accept()
