"""Điểm vào: ``python -m lecturemate`` (hoặc LectureMate.exe sau khi đóng gói)."""
from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler


def setup_logging() -> None:
    from .ai.secrets import RedactingFilter
    from .config import logs_dir

    handler = RotatingFileHandler(logs_dir() / "lecturemate.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # httpx ghi URL ở mức INFO; giữ ở WARNING để log không chứa chi tiết request
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lecturemate")
    ap.add_argument("--version", action="store_true")
    ap.add_argument("--smoke", action="store_true", help="mở cửa sổ rồi tự đóng (kiểm tra bản đóng gói)")
    args = ap.parse_args(argv)
    from . import __version__

    if args.version:
        print(f"LectureMate {__version__}")
        return 0
    setup_logging()
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from .config import AppConfig, audio_dir, db_path
    from .persistence.repository import SessionRepository
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("LectureMate")
    cfg = AppConfig.load()
    repo = SessionRepository(db_path(), audio_root=audio_dir())
    win = MainWindow(cfg, repo)
    if args.smoke:
        cfg.privacy_accepted = True
        win.show()
        QTimer.singleShot(1500, win.close)
        rc = app.exec()
        repo.close()
        print("SMOKE OK")
        return rc
    if not win.first_run():
        repo.close()
        return 0
    win.show()
    rc = app.exec()
    repo.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
