from conftest import make_final
from lecturemate.ai.citations import validate_citations
from lecturemate.ai.llm_provider import LLMError, MockProvider
from lecturemate.ai.notes_chunker import chunk_segments
from lecturemate.ai.notes_generator import NotesGenerator
from lecturemate.ai.qa_service import INSUFFICIENT, QAService
from lecturemate.ai.search_service import SearchService, build_query
from lecturemate.domain.models import TranslationResult, TranslationStatus, format_ts, parse_ts
from lecturemate.export_markdown import link_timestamps, notes_markdown, safe_filename, transcript_markdown

LECTURE = [
    "Today we talk about opportunity cost.",
    "Opportunity cost is the value of the next best alternative.",
    "For example, studying instead of working part time.",
    "Next, supply and demand determine the equilibrium price.",
    "When demand rises, the equilibrium price increases.",
    "A price ceiling below equilibrium creates a shortage.",
]


def seed(repo, title="Micro 101", texts=LECTURE, course=None):
    sid = repo.create_session(title, "microphone", course_id=course)
    for i, t in enumerate(texts):
        repo.commit_final(make_final(sid, i, t, start=i * 60_000, end=i * 60_000 + 5000))
    return sid


# ------------------------------------------------------------------ chunker
def test_chunker_limits_and_traceability(repo):
    sid = seed(repo, texts=[f"Sentence {i} " + "x" * 80 for i in range(50)])
    segs = repo.list_segments(sid)
    chunks = chunk_segments(segs, max_chars=600, overlap=2)
    assert all(len(c.text) <= 600 for c in chunks)
    covered = [i for c in chunks for i in c.segment_ids]
    assert set(covered) == {s.id for s in segs}
    order = [s.id for s in segs]
    for c in chunks:
        idx = [order.index(i) for i in c.segment_ids]
        assert idx == sorted(idx)
    for a, b in zip(chunks, chunks[1:]):
        assert a.segment_ids[-2:] == b.segment_ids[:2]  # chồng lấn 2


def test_chunker_single_long_segment(repo):
    sid = seed(repo, texts=["y" * 5000])
    chunks = chunk_segments(repo.list_segments(sid), max_chars=500)
    assert len(chunks) == 1 and len(chunks[0].text) <= 500


# ---------------------------------------------------------------- citations
def test_validate_citations_removes_fake():
    text, bad = validate_citations("Ý A [00:01:00]. Ý B [00:59:59]. Ý C [01:00]", {"00:01:00"})
    assert bad == ["00:59:59"] and "[mốc?]" in text and "[00:01:00]" in text


def test_ts_roundtrip():
    assert format_ts(332_000) == "00:05:32" and parse_ts("[00:05:32]") == 332_000 and parse_ts("5:99") is None


# -------------------------------------------------------------------- notes
def test_notes_generator_no_fake_citations_and_partial_retry(repo, tmp_path):
    sid = seed(repo)
    segs = repo.list_segments(sid)
    good = "## Đề mục chính\n- Chi phí cơ hội (opportunity cost) [00:01:00]\n- Bịa [00:42:00]\n## Cần kiểm tra lại\n- Không có"
    p = MockProvider(script=[LLMError("timeout", retryable=True), good, good], responder=lambda m: good)
    gen = NotesGenerator(p, cache_dir=tmp_path / "cache", max_chars=200, overlap=0)
    r1 = gen.generate(sid, segs, "Micro 101")
    assert r1.failed_chunks == [0] and "chưa tạo được" in r1.markdown
    calls_after_first = len(p.calls)
    r2 = gen.generate(sid, segs, "Micro 101")
    assert r2.complete
    # lần 2 chỉ gọi lại chunk lỗi + bước gộp, không gọi lại các chunk đã thành công
    n_chunks = len(chunk_segments(segs, 200, 0))
    assert len(p.calls) - calls_after_first == 1 + 1
    assert n_chunks >= 2
    assert "[00:42:00]" not in r2.markdown and "[mốc?]" in r2.markdown


def test_export_markdown_links_valid(repo, tmp_path):
    sid = seed(repo)
    segs = repo.list_segments(sid)
    md = notes_markdown("- ý [00:01:00] và [00:33:33]", repo.get_session(sid), segs)
    assert "[00:01:00](#t-000100)" in md and '<a id="t-000100"></a>' in md
    assert "[00:33:33](" not in md
    p = tmp_path / "out.md"
    p.write_text(md, encoding="utf-8")
    assert p.read_text(encoding="utf-8").startswith("- ý")
    assert link_timestamps("[00:01:00](#x)", {"00:01:00"}) == "[00:01:00](#x)"


def test_transcript_markdown_states(repo):
    sid = seed(repo, texts=["a", "b"])
    segs = repo.list_segments(sid)
    repo.save_translation(TranslationResult(segs[0].id, "xin chào", TranslationStatus.DONE))
    md = transcript_markdown(repo.get_session(sid), repo.list_segments(sid))
    assert "xin chào" in md and "(chưa dịch)" in md


def test_safe_filename():
    assert safe_filename('a/b:c*?"<>|') == "a_b_c______"


# ------------------------------------------------------------------- search
def test_search_finds_phrase_and_respects_session(repo):
    a = seed(repo, "A")
    b = seed(repo, "B", texts=["Opportunity cost appears here too."])
    s = SearchService(repo)
    hits = s.search("opportunity cost", session_id=a)
    assert hits and all(h.session_id == a for h in hits)
    assert "opportunity cost" in hits[0].en_text.lower()
    assert {h.session_id for h in s.search("opportunity")} == {a, b}
    assert s.search("photosynthesis", session_id=a) == []


def test_search_vietnamese_without_diacritics(repo):
    sid = seed(repo, texts=["Inflation"])
    seg = repo.list_segments(sid)[0]
    repo.save_translation(TranslationResult(seg.id, "Lạm phát tăng", TranslationStatus.DONE))
    assert SearchService(repo).search("lam phat", session_id=sid)


def test_search_filters_course(repo):
    c1 = repo.upsert_course("Econ")
    c2 = repo.upsert_course("CS")
    seed(repo, "A", course=c1)
    seed(repo, "B", course=c2, texts=["supply chain in software"])
    hits = SearchService(repo).search("supply", course_id=c2)
    assert len(hits) == 1 and "software" in hits[0].en_text


def test_build_query_escapes():
    q = build_query('price" OR 1=1 --')
    assert q == '"price"' and '--' not in q
    assert build_query("???") is None


# ----------------------------------------------------------------------- QA
def test_qa_insufficient_without_api_call(repo):
    sid = seed(repo)
    p = MockProvider()
    a = QAService(repo, p).ask("What is photosynthesis?", sid)
    assert a.insufficient and a.text == INSUFFICIENT and p.calls == []


def test_qa_answer_with_valid_citations(repo):
    sid = seed(repo)
    p = MockProvider(lambda m: "Chi phí cơ hội là giá trị của lựa chọn tốt nhất kế tiếp [00:01:00]. Sai [00:09:09].")
    a = QAService(repo, p).ask("What is opportunity cost?", sid)
    assert not a.insufficient
    assert a.invalid_citations == ["00:09:09"]
    assert a.citations and a.citations[0][1] == 60_000  # click để tua tới 00:01:00
    sent = p.calls[0][1]["content"]
    assert "[00:01:00] Opportunity cost is the value" in sent


def test_qa_model_says_insufficient(repo):
    sid = seed(repo)
    a = QAService(repo, MockProvider(lambda m: "KHÔNG ĐỦ DỮ KIỆN")).ask("Who invented opportunity cost?", sid)
    assert a.insufficient


def test_qa_api_error_returns_evidence(repo):
    sid = seed(repo)
    a = QAService(repo, MockProvider(script=[LLMError("offline", retryable=True)])).ask("equilibrium price", sid)
    assert a.error == "offline" and a.evidence
