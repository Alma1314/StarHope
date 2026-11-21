"""Long-book page failures get one bounded automatic retry round."""
from main import BOOK_PAGE_RETRY_LIMIT, plan_book_page_retry


def test_no_failure_means_no_retry():
    assert plan_book_page_retry([])["status"] == "none"


def test_one_or_a_few_failed_pages_are_retried():
    plan = plan_book_page_retry([
        {"page_number": 12, "local_idx": 11, "error": "truncated"},
        {"page_number": 40, "local_idx": 39, "error": "timeout"},
    ])
    assert plan["status"] == "ready"
    assert plan["local_indices"] == [11, 39]
    assert plan["pages"] == [12, 40]


def test_systemic_failure_is_not_retried_page_by_page():
    errors = [
        {"page_number": number + 1, "local_idx": number, "error": "auth"}
        for number in range(BOOK_PAGE_RETRY_LIMIT + 1)
    ]
    plan = plan_book_page_retry(errors)
    assert plan["status"] == "skipped"
    assert plan["reason"] == "too_many_failed_pages"
    assert plan["local_indices"] == []


def test_invalid_error_rows_do_not_make_a_retry_plan():
    plan = plan_book_page_retry([{"page_number": 1, "error": "missing local"}])
    assert plan["status"] == "none"
