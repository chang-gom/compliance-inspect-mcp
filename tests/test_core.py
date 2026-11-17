import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import core  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    """결과를 손으로 계산할 수 있는 작은 고정 데이터."""
    path = tmp_path / "t.db"
    c = sqlite3.connect(path)
    c.executescript(core.SCHEMA)
    c.executescript("""
        INSERT INTO meta VALUES ('snapshot_date', '2026-11-15');
        INSERT INTO categories VALUES ('FOOD', '가공식품'), ('ELEC', '생활가전');
        INSERT INTO required_documents VALUES
            ('FOOD', '품목제조보고서', '식품위생법'),
            ('FOOD', '자가품질검사성적서', '식품위생법'),
            ('ELEC', 'KC 인증서', '전기생활용품안전법');
        INSERT INTO suppliers VALUES ('S01', 'A'), ('S02', 'B');
        INSERT INTO reason_codes VALUES ('DOC_MISSING', '누락'), ('AD_CLAIM', '광고');
        INSERT INTO products VALUES
            ('P1', '누룽지', 'FOOD', 'S01', '맛있는 간식', '검수대기', '2026-11-01'),
            ('P2', '그래놀라', 'FOOD', 'S02', '면역력 강화', '검수대기', '2026-11-01'),
            ('P3', '주전자', 'ELEC', 'S01', '편리함', '검수대기', '2026-11-01'),
            ('P4', '선풍기', 'ELEC', 'S02', '편리함', '승인', '2026-11-01'),
            ('P5', '귀리우유', 'FOOD', 'S01', '고소함', '승인', '2026-09-01');
        -- P1: 서류 2종 정상 / P2: 1종 누락 + 1종 만료 / P3: 정상(유효기간 없음) / P4·P5: 승인건이라 누락 탐지에서 제외
        -- 재검수: P4는 16일 뒤 만료(EXPIRING), P5는 승인 후 11/1에 만료(EXPIRED)
        INSERT INTO product_documents VALUES
            ('P1', '품목제조보고서', 'D1', '2027-01-01'),
            ('P1', '자가품질검사성적서', 'D2', '2026-11-15'),
            ('P2', '품목제조보고서', 'D3', '2026-11-14'),
            ('P3', 'KC 인증서', 'D4', NULL),
            ('P4', 'KC 인증서', 'D5', '2026-12-01'),
            ('P5', '품목제조보고서', 'D6', '2026-11-01'),
            ('P5', '자가품질검사성적서', 'D7', '2027-06-01');
        INSERT INTO inspection_log (product_id, inspected_at, result, reason_code) VALUES
            ('P1', '2026-11-10', 'PASS', NULL),
            ('P2', '2026-11-10', 'REJECT', 'AD_CLAIM'),
            ('P2', '2026-11-12', 'REJECT', 'DOC_MISSING'),
            ('P3', '2026-11-12', 'PASS', NULL),
            ('P4', '2026-09-01', 'REJECT', 'DOC_MISSING');  -- 30일 밖
    """)
    c.commit()
    c.close()
    return path


def test_gaps_find_missing_and_expired_only(db):
    with core.connect_readonly(db) as conn:
        result = core.find_registration_gaps(conn)
    issues = {(i["product_id"], i["doc_type"], i["issue"]) for i in result["items"]}
    assert issues == {("P2", "자가품질검사성적서", "MISSING"), ("P2", "품목제조보고서", "EXPIRED")}
    assert result["products_affected"] == 1
    assert result["as_of"] == "2026-11-15"  # 만료일 당일(P1)은 유효로 본다


def test_gaps_category_filter_and_limit(db):
    with core.connect_readonly(db) as conn:
        assert core.find_registration_gaps(conn, category_code="ELEC")["total_issues"] == 0
        limited = core.find_registration_gaps(conn, limit=1)
    assert len(limited["items"]) == 1 and limited["truncated"] is True


def test_ad_copy_detects_category_terms(db):
    with core.connect_readonly(db) as conn:
        result = core.check_ad_copy(conn, "FOOD", "면역력 강화에 좋은 업계 최고 간식")
        assert result["verdict"] == "REVIEW"
        assert [h["term"] for h in result["hits"]] == ["면역력 강화", "최고"]
        assert core.check_ad_copy(conn, "ELEC", "면역력 강화")["verdict"] == "CLEAR"  # 식품 전용 규칙


def test_lowercase_category_is_normalized_not_missed(db):
    """소문자 입력이 '문제 없음'으로 보이면 안 된다 (놓침 방지)."""
    with core.connect_readonly(db) as conn:
        assert core.check_ad_copy(conn, "food", "면역력 강화")["verdict"] == "REVIEW"
        assert core.find_registration_gaps(conn, category_code="food")["total_issues"] == 2


@pytest.mark.parametrize("call", [
    lambda c: core.find_registration_gaps(c, category_code="XYZ"),
    lambda c: core.list_required_documents(c, "XYZ"),
    lambda c: core.check_ad_copy(c, "XYZ", "문구"),
    lambda c: core.find_registration_gaps(c, status="대기"),
    lambda c: core.find_registration_gaps(c, as_of="2026/11/15"),
    lambda c: core.find_registration_gaps(c, as_of="2026-02-30"),
    lambda c: core.rejection_pattern_report(c, days=-5),
    lambda c: core.rejection_pattern_report(c, days=0),
])
def test_invalid_input_raises_instead_of_empty_result(db, call):
    with core.connect_readonly(db) as conn, pytest.raises(ValueError):
        call(conn)


def test_rejection_report_by_reason_and_category(db):
    with core.connect_readonly(db) as conn:
        by_reason = core.rejection_pattern_report(conn, days=30, group_by="reason")
        by_cat = core.rejection_pattern_report(conn, days=30, group_by="category")
    assert {r["key"]: (r["rejected"], r["share_of_rejections"]) for r in by_reason["rows"]} == {
        "AD_CLAIM": (1, 0.5), "DOC_MISSING": (1, 0.5)}
    food = next(r for r in by_cat["rows"] if r["key"] == "FOOD")
    assert (food["inspected"], food["rejected"], food["reject_rate"]) == (3, 2, 0.667)


def test_rejection_report_rejects_unknown_group(db):
    with core.connect_readonly(db) as conn, pytest.raises(ValueError):
        core.rejection_pattern_report(conn, group_by="product_id; DROP TABLE products")


def test_reinspection_targets_expired_after_approval_and_expiring(db):
    with core.connect_readonly(db) as conn:
        result = core.find_reinspection_targets(conn, within_days=30)
        narrow = core.find_reinspection_targets(conn, within_days=10)
    assert [(i["product_id"], i["doc_type"], i["issue"], i["days_left"]) for i in result["items"]] == [
        ("P5", "품목제조보고서", "EXPIRED", -14),
        ("P4", "KC 인증서", "EXPIRING", 16),
    ]
    assert result["counts"] == {"EXPIRED": 1, "EXPIRING": 1}
    assert [i["product_id"] for i in narrow["items"]] == ["P5"]  # 16일 남은 건은 10일 기준에서 제외


def test_reinspection_targets_validates_window(db):
    with core.connect_readonly(db) as conn, pytest.raises(ValueError):
        core.find_reinspection_targets(conn, within_days=0)


def test_readonly_sql_allows_select_and_truncates(db):
    with core.connect_readonly(db) as conn:
        result = core.run_readonly_sql(conn, "SELECT product_id FROM products ORDER BY 1", max_rows=2)
    assert result["rows"] == [["P1"], ["P2"]] and result["truncated"] is True


@pytest.mark.parametrize("sql", [
    "UPDATE products SET status = '승인'",
    "SELECT 1; DELETE FROM products",
    "DROP TABLE products",
    "WITH x AS (SELECT 1) DELETE FROM products",
])
def test_readonly_sql_blocks_writes(db, sql):
    with core.connect_readonly(db) as conn, pytest.raises((ValueError, sqlite3.DatabaseError)):
        core.run_readonly_sql(conn, sql)
    with core.connect_readonly(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 5


def test_mcp_server_exposes_tools_and_runs(db, monkeypatch):
    monkeypatch.setenv("COMPLIANCE_DB", str(db))
    sys.modules.pop("server", None)
    import server

    tools = asyncio.run(server.server.list_tools())
    assert {t.name for t in tools} == {
        "list_required_documents", "find_registration_gaps", "check_ad_copy",
        "rejection_pattern_report", "run_readonly_sql", "find_reinspection_targets",
    }
    result = asyncio.run(server.server.call_tool("find_registration_gaps", {"category_code": "FOOD"}))
    assert "P2" in str(result)


@pytest.mark.parametrize("tool, args, expected", [
    ("rejection_pattern_report", {"group_by": "brand"}, "group_by는"),
    ("find_registration_gaps", {"category_code": "XYZ"}, "사용 가능: ELEC, FOOD"),
    ("run_readonly_sql", {"sql": "DELETE FROM products"}, "SELECT 또는 WITH"),
    ("run_readonly_sql", {"sql": "WITH x AS (SELECT 1) DELETE FROM products"}, "읽기 이외의 동작"),
])
def test_mcp_errors_reach_client_with_reason(db, monkeypatch, tool, args, expected):
    """Claude가 오류 이유를 보고 입력을 고쳐 다시 호출할 수 있어야 한다."""
    from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

    monkeypatch.setenv("COMPLIANCE_DB", str(db))
    sys.modules.pop("server", None)
    import server

    with pytest.raises(ToolError, match=expected) as exc:
        asyncio.run(server.server.call_tool(tool, args))
    assert not isinstance(exc.value, UnexpectedToolError)
