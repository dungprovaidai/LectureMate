import pytest

from lecturemate.ai.glossary import SAMPLE_GLOSSARY_ECON, Glossary, quality_warnings, sample_glossary
from lecturemate.ai.translator import build_messages, clean_output
from lecturemate.domain.models import TranslationRequest

GL = sample_glossary()


def test_has_20_terms():
    assert len(GL.terms) == 20


@pytest.mark.parametrize("en,vi", SAMPLE_GLOSSARY_ECON)
def test_each_term_detected_and_checked(en, vi):
    sentence = f"Today we discuss {en} in detail."
    assert any(t.en == en for t in GL.relevant(sentence))
    assert GL.check(sentence, f"Hôm nay ta bàn về {vi} một cách chi tiết.") == []
    assert en in GL.check(sentence, "Hôm nay ta bàn về một thứ khác.")


def test_plural_and_case():
    assert [t.en for t in GL.relevant("Externalities matter")] == ["externality"]
    assert [t.en for t in GL.relevant("two monopolies")] == ["monopoly"]
    assert [t.en for t in GL.relevant("INTEREST RATES rise")] == ["interest rate"]


def test_longest_match_first():
    g = Glossary.from_json([{"en": "cost", "vi": "chi phí"}, {"en": "marginal cost", "vi": "chi phí cận biên"}])
    assert [t.en for t in g.relevant("marginal cost curve")] == ["marginal cost", "cost"]


def test_csv_import_with_header_and_alternatives():
    g = Glossary.from_csv("en,vi,note\nexternality,ngoại tác/ngoại ứng,\nutility,độ thỏa dụng,kinh tế vi mô\n")
    assert len(g.terms) == 2
    assert g.check("A negative externality", "Một ngoại ứng tiêu cực") == []


def test_negation_kept():
    assert "negation" in quality_warnings("Prices did not rise.", "Giá đã tăng.")
    assert "negation" not in quality_warnings("Prices did not rise.", "Giá đã không tăng.")
    assert "negation" in quality_warnings("This isn't efficient", "Điều này hiệu quả")
    assert "negation" not in quality_warnings("We never assume that", "Chúng ta chưa bao giờ giả định vậy")


def test_quantities_kept():
    assert any(w.startswith("number") for w in quality_warnings("Inflation rose 3.5% in 2023", "Lạm phát tăng năm 2023"))
    assert quality_warnings("Inflation rose 3.5% in 2023", "Lạm phát tăng 3,5% năm 2023", GL) == []


def test_prompt_contains_only_relevant_terms_and_context():
    req = TranslationRequest("s", "S", "Inflation affects the interest rate.", ("Previous one.", "Previous two."),
                             tuple((t.en, t.vi) for t in GL.relevant("Inflation affects the interest rate.")))
    msgs = build_messages(req)
    user = msgs[1]["content"]
    assert "inflation = lạm phát" in user and "interest rate = lãi suất" in user and "monopoly" not in user
    assert "Previous two." in user and "<cau_can_dich>Inflation affects the interest rate.</cau_can_dich>" in user


@pytest.mark.parametrize("raw,clean", [
    ('"Lạm phát tăng."', "Lạm phát tăng."),
    ("Bản dịch: Lạm phát tăng.", "Lạm phát tăng."),
    ('{"vi": "Lạm phát tăng."}', "Lạm phát tăng."),
    ("```\nLạm phát tăng.\n```", "Lạm phát tăng."),
    ("<think>hmm</think>Lạm phát tăng.", "Lạm phát tăng."),
])
def test_clean_output(raw, clean):
    assert clean_output(raw) == clean
