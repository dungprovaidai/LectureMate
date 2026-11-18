"""Hợp đồng dữ liệu giữa các module (xem 02-thiet-ke/04-kien-truc.md)."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field


class SessionState(str, enum.Enum):
    IDLE = "idle"
    CHECKING_DEVICE = "checking_device"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPING = "stopping"
    COMPLETED = "completed"
    RECOVERED = "recovered"  # phiên ghi dở được đóng an toàn khi khởi động lại
    ERROR = "error"


class TranslationStatus(str, enum.Enum):
    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"  # dịch bị tắt


@dataclass(frozen=True)
class TranscriptEvent:
    type: str  # "partial" | "final"
    session_id: str
    segment_id: str
    start_ms: int
    end_ms: int
    text_en: str
    version: int = 0
    sequence_number: int = -1  # chỉ có ý nghĩa với final
    boundary_ns: int = 0  # monotonic ns lúc phát hiện ranh giới câu (đo độ trễ)
    asr_done_ns: int = 0
    language: str = "und"
    speaker_id: str | None = None
    language_probability: float = 0.0

    @property
    def is_final(self) -> bool:
        return self.type == "final"


@dataclass(frozen=True)
class TranslationRequest:
    segment_id: str
    session_id: str
    text_en: str
    preceding_context: tuple[str, ...] = ()
    glossary: tuple[tuple[str, str], ...] = ()
    deadline_ms: int = 10_000
    attempt: int = 0
    source_language: str = "und"


def is_vietnamese(language: str | None) -> bool:
    return (language or "").lower().replace("_", "-").split("-")[0] == "vi"


def language_name(language: str | None) -> str:
    code = (language or "und").lower().split("-")[0]
    names = {
        "af": "Afrikaans", "ar": "Ả Rập", "az": "Azerbaijan", "be": "Belarus", "bg": "Bulgaria",
        "bn": "Bengali", "bs": "Bosnia", "ca": "Catalan", "cs": "Séc", "cy": "Wales",
        "da": "Đan Mạch", "de": "Đức", "el": "Hy Lạp", "en": "Anh", "es": "Tây Ban Nha",
        "et": "Estonia", "fa": "Ba Tư", "fi": "Phần Lan", "fr": "Pháp", "gl": "Galicia",
        "gu": "Gujarati", "ha": "Hausa", "he": "Do Thái", "hi": "Hindi", "hr": "Croatia",
        "hu": "Hungary", "hy": "Armenia", "id": "Indonesia", "is": "Iceland", "it": "Ý",
        "ja": "Nhật", "jv": "Java", "ka": "Georgia", "kk": "Kazakh", "km": "Khmer",
        "kn": "Kannada", "ko": "Hàn", "lo": "Lào", "lt": "Lithuania", "lv": "Latvia",
        "mg": "Malagasy", "mi": "Māori", "mk": "Macedonia", "ml": "Malayalam", "mn": "Mông Cổ",
        "mr": "Marathi", "ms": "Mã Lai", "mt": "Malta", "my": "Myanmar", "ne": "Nepal",
        "nl": "Hà Lan", "no": "Na Uy", "pa": "Punjabi", "pl": "Ba Lan", "ps": "Pashto",
        "pt": "Bồ Đào Nha", "ro": "Romania", "ru": "Nga", "sd": "Sindhi", "si": "Sinhala",
        "sk": "Slovak", "sl": "Slovenia", "sn": "Shona", "so": "Somali", "sq": "Albania",
        "sr": "Serbia", "su": "Sunda", "sv": "Thụy Điển", "sw": "Swahili", "ta": "Tamil",
        "te": "Telugu", "th": "Thái", "tr": "Thổ Nhĩ Kỳ", "uk": "Ukraina", "ur": "Urdu",
        "uz": "Uzbek", "vi": "Tiếng Việt", "yi": "Yiddish", "yo": "Yoruba", "zh": "Trung",
        "yue": "Quảng Đông",
    }
    return names.get(code, "Chưa rõ ngôn ngữ" if code in ("", "und") else code.upper())


@dataclass
class TranslationResult:
    segment_id: str
    text_vi: str | None
    status: TranslationStatus
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: int = 0
    first_token_ms: int | None = None
    error_code: str | None = None
    attempts: int = 1
    extra: dict = field(default_factory=dict)


def format_ts(ms: int) -> str:
    """[hh:]mm:ss — định dạng mốc thời gian hiển thị và dẫn nguồn."""
    s = max(0, int(ms)) // 1000
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def parse_ts(text: str) -> int | None:
    """'00:05:32' hoặc '05:32' -> ms. Trả None nếu không hợp lệ."""
    parts = text.strip().strip("[]").split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    nums = [int(p) for p in parts]
    if len(nums) == 2:
        nums.insert(0, 0)
    h, m, s = nums
    if m >= 60 or s >= 60:
        return None
    return ((h * 60 + m) * 60 + s) * 1000
