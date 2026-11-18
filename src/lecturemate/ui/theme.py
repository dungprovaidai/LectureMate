"""Bảng màu & QSS theo 02-thiet-ke/03-he-thong-thiet-ke.md (tối mặc định, có sáng)."""
from __future__ import annotations

PALETTES = {
    "dark": dict(bg="#0D1117", panel="#161B22", panel2="#1F2630", text="#F0F6FC", muted="#9CA3AF",
                 accent="#58A6FF", accent_text="#0D1117", error="#FF7B72", success="#3FB950",
                 warn="#D29922", border="#30363D", partial="#8B949E", focus="#58A6FF", rowhl="#1F3A5F"),
    "light": dict(bg="#F8FAFC", panel="#FFFFFF", panel2="#EEF2F7", text="#0F172A", muted="#475569",
                  accent="#2563EB", accent_text="#FFFFFF", error="#B91C1C", success="#15803D",
                  warn="#A16207", border="#CBD5E1", partial="#64748B", focus="#2563EB", rowhl="#DBEAFE"),
}


def palette(name: str) -> dict:
    return PALETTES.get(name, PALETTES["dark"])


def stylesheet(name: str, subtitle_px: int = 18) -> str:
    p = palette(name)
    return f"""
* {{ font-family: "Segoe UI", "Noto Sans", "Arial"; font-size: 14px; color: {p['text']}; }}
QMainWindow, QDialog, QWidget#page {{ background: {p['bg']}; }}
QWidget {{ background: transparent; }}
QFrame#panel, QGroupBox, QTabWidget::pane {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: 8px; }}
QGroupBox {{ margin-top: 14px; padding: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; color: {p['muted']}; }}
QLabel#h1 {{ font-size: 22px; font-weight: 600; }}
QLabel#muted, QLabel.muted {{ color: {p['muted']}; }}
QLabel#ts {{ color: {p['muted']}; font-size: 12px; font-family: "Consolas", "Cascadia Mono", monospace; }}
QLabel#en {{ font-size: {subtitle_px}px; }}
QLabel#vi {{ font-size: {subtitle_px + 4}px; font-weight: 600; }}
QLabel#preview {{ font-size: {subtitle_px + 4}px; font-weight: 600; color: {p['accent']}; }}
QLabel#original {{ font-size: {max(12, subtitle_px - 3)}px; color: {p['muted']}; }}
QLabel#partial {{ font-size: {subtitle_px}px; color: {p['partial']}; font-style: italic; }}
QLabel#pending {{ font-size: {max(12, subtitle_px - 3)}px; color: {p['muted']}; font-style: italic; }}
QLabel#failed {{ font-size: {max(12, subtitle_px - 3)}px; color: {p['error']}; }}
QLabel#badge_ok {{ color: {p['success']}; }}
QLabel#badge_warn {{ color: {p['warn']}; }}
QLabel#badge_err {{ color: {p['error']}; }}
QLabel#clock {{ font-size: 20px; font-family: "Consolas", "Cascadia Mono", monospace; }}
QFrame#row {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: 6px; }}
QFrame#row[partial="true"] {{ border-style: dashed; }}
QFrame#row[current="true"] {{ background: {p['rowhl']}; border-color: {p['accent']}; }}
QFrame#row[match="true"] {{ border: 2px solid {p['warn']}; }}
QFrame#banner_err {{ background: {p['error']}; border-radius: 6px; }}
QFrame#banner_err QLabel {{ color: {p['bg']}; font-weight: 600; }}
QFrame#banner_info {{ background: {p['panel2']}; border: 1px solid {p['warn']}; border-radius: 6px; }}
QPushButton {{ background: {p['panel2']}; border: 1px solid {p['border']}; border-radius: 6px;
               padding: 6px 14px; min-height: 26px; }}
QPushButton:hover {{ border-color: {p['accent']}; }}
QPushButton:disabled {{ color: {p['muted']}; }}
QPushButton#primary {{ background: {p['accent']}; color: {p['accent_text']}; border: none; font-weight: 600; }}
QPushButton#danger {{ background: {p['error']}; color: {p['bg']}; border: none; font-weight: 600; }}
QPushButton#primary:disabled, QPushButton#danger:disabled {{ background: {p['panel2']}; color: {p['muted']};
    border: 1px solid {p['border']}; }}
QPushButton:focus, QLineEdit:focus, QComboBox:focus, QTableWidget:focus, QTextEdit:focus,
QListWidget:focus, QCheckBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QSlider:focus {{
    outline: none; border: 2px solid {p['focus']}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit, QTextBrowser {{
    background: {p['panel2']}; border: 1px solid {p['border']}; border-radius: 6px; padding: 4px 6px; min-height: 24px; }}
QComboBox QAbstractItemView {{ background: {p['panel']}; selection-background-color: {p['accent']}; }}
QTableWidget, QListWidget {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: 6px;
    gridline-color: {p['border']}; selection-background-color: {p['rowhl']}; selection-color: {p['text']}; }}
QHeaderView::section {{ background: {p['panel2']}; border: none; padding: 6px; color: {p['muted']}; }}
QTabBar::tab {{ background: {p['panel2']}; padding: 8px 16px; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }}
QTabBar::tab:selected {{ background: {p['accent']}; color: {p['accent_text']}; }}
QProgressBar {{ background: {p['panel2']}; border: 1px solid {p['border']}; border-radius: 4px; text-align: center; max-height: 12px; }}
QProgressBar::chunk {{ background: {p['success']}; border-radius: 3px; }}
QScrollArea {{ border: none; }}
QStatusBar {{ background: {p['panel']}; color: {p['muted']}; }}
QToolTip {{ background: {p['panel']}; color: {p['text']}; border: 1px solid {p['border']}; }}
"""
