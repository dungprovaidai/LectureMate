from lecturemate.metrics import markdown_report, percentile, summarize


def test_percentile():
    assert percentile([], 50) is None
    assert percentile([5], 90) == 5
    assert percentile(list(range(1, 101)), 50) == 50.5
    assert abs(percentile(list(range(1, 101)), 90) - 90.1) < 1e-9


def test_summary_and_report_warns_small_sample():
    rows = [{"asr_ms": 400 + i, "api_ms": 900 + i, "queue_ms": 5, "total_ms": 1400 + i, "asr_model": "base",
             "context_chars": 900} for i in range(10)]
    s = summarize(rows)
    assert s["total_ms"]["n"] == 10 and s["ui_ms"]["n"] == 0
    md = markdown_report(rows)
    assert "Chưa đủ 100" in md and "base" in md and "<1200" in md


def test_report_no_warning_with_100():
    rows = [{"total_ms": i, "api_ms": i} for i in range(100)]
    assert "Chưa đủ" not in markdown_report(rows)
