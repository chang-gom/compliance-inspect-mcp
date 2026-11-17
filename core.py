"""상품 등록 컴플라이언스 검수 로직.

MCP 서버(server.py)와 테스트가 함께 쓰는 순수 함수 모음입니다.
모든 조회는 SQLite 읽기 전용 연결로 수행합니다.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date
from pathlib import Path

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE categories (category_code TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE required_documents (
    category_code TEXT NOT NULL REFERENCES categories(category_code),
    doc_type      TEXT NOT NULL,
    legal_basis   TEXT NOT NULL,
    PRIMARY KEY (category_code, doc_type)
);
CREATE TABLE suppliers (supplier_id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE products (
    product_id    TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    category_code TEXT NOT NULL REFERENCES categories(category_code),
    supplier_id   TEXT NOT NULL REFERENCES suppliers(supplier_id),
    ad_copy       TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('검수대기', '승인', '반려')),
    registered_at TEXT NOT NULL
);
CREATE TABLE product_documents (
    product_id  TEXT NOT NULL REFERENCES products(product_id),
    doc_type    TEXT NOT NULL,
    doc_no      TEXT NOT NULL,
    valid_until TEXT,               -- NULL이면 유효기간 없음
    PRIMARY KEY (product_id, doc_type)
);
CREATE TABLE reason_codes (reason_code TEXT PRIMARY KEY, description TEXT NOT NULL);
CREATE TABLE inspection_log (
    log_id       INTEGER PRIMARY KEY,
    product_id   TEXT NOT NULL REFERENCES products(product_id),
    inspected_at TEXT NOT NULL,
    result       TEXT NOT NULL CHECK (result IN ('PASS', 'REJECT')),
    reason_code  TEXT REFERENCES reason_codes(reason_code)
);
"""

# 카테고리별 금지·주의 표현 (예시 규칙)
PROHIBITED_TERMS: dict[str, list[str]] = {
    "*": ["최고", "유일", "FDA 승인", "부작용 없"],
    "FOOD": ["치료", "예방", "완치", "면역력 강화", "항암", "다이어트 효과"],
    "HFOOD": ["치료", "완치", "특효", "100% 효과"],
    "COSM": ["치료", "의약품", "아토피 개선", "피부 재생"],
    "PETF": ["치료", "질병 예방"],
}

GROUP_COLUMNS = {
    "reason": "l.reason_code",
    "category": "p.category_code",
    "supplier": "p.supplier_id",
}


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def snapshot_date(conn: sqlite3.Connection) -> str:
    return conn.execute("SELECT value FROM meta WHERE key = 'snapshot_date'").fetchone()[0]


# 잘못된 입력이 빈 결과(= '문제 없음')로 보이지 않도록, 조회 전에 모두 오류로 돌려보낸다.
STATUSES = ("검수대기", "승인", "반려")


def _category(conn: sqlite3.Connection, code: str | None) -> str | None:
    if code is None:
        return None
    valid = [r[0] for r in conn.execute("SELECT category_code FROM categories ORDER BY 1")]
    normalized = code.strip().upper()
    if normalized not in valid:
        raise ValueError(f"알 수 없는 카테고리 '{code}'입니다. 사용 가능: {', '.join(valid)}")
    return normalized


def _status(status: str) -> str:
    if status not in STATUSES:
        raise ValueError(f"알 수 없는 상태 '{status}'입니다. 사용 가능: {', '.join(STATUSES)}")
    return status


def _as_of(conn: sqlite3.Connection, as_of: str | None) -> str:
    if as_of is None:
        return snapshot_date(conn)
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
            raise ValueError
        date.fromisoformat(as_of)  # 2026-02-30 같은 없는 날짜 차단
    except ValueError:
        raise ValueError(f"기준일 '{as_of}'은 YYYY-MM-DD 형식의 실제 날짜여야 합니다.") from None
    return as_of


def _days(days: int) -> int:
    if not 1 <= days <= 365:
        raise ValueError(f"기간은 1~365일이어야 합니다. 입력값: {days}")
    return days


def list_required_documents(conn: sqlite3.Connection, category_code: str | None = None) -> list[dict]:
    category_code = _category(conn, category_code)
    sql = """
        SELECT r.category_code, c.name AS category_name, r.doc_type, r.legal_basis
        FROM required_documents r JOIN categories c USING (category_code)
        WHERE (:cat IS NULL OR r.category_code = :cat)
        ORDER BY r.category_code, r.doc_type
    """
    return [dict(r) for r in conn.execute(sql, {"cat": category_code})]


def find_registration_gaps(
    conn: sqlite3.Connection,
    category_code: str | None = None,
    status: str = "검수대기",
    as_of: str | None = None,
    limit: int = 50,
) -> dict:
    """필수 서류가 없거나(MISSING) 유효기간이 지난(EXPIRED) 상품을 찾는다."""
    category_code, status, as_of = _category(conn, category_code), _status(status), _as_of(conn, as_of)
    sql = """
        WITH required AS (
            SELECT p.product_id, p.name, p.category_code, p.supplier_id, r.doc_type
            FROM products p
            JOIN required_documents r ON r.category_code = p.category_code
            WHERE p.status = :status
              AND (:cat IS NULL OR p.category_code = :cat)
        )
        SELECT q.product_id, q.name, q.category_code, q.supplier_id, q.doc_type,
               d.valid_until,
               CASE WHEN d.product_id IS NULL THEN 'MISSING' ELSE 'EXPIRED' END AS issue
        FROM required q
        LEFT JOIN product_documents d
               ON d.product_id = q.product_id AND d.doc_type = q.doc_type
        WHERE d.product_id IS NULL
           OR (d.valid_until IS NOT NULL AND d.valid_until < :as_of)
        ORDER BY issue, q.category_code, q.product_id
    """
    rows = [dict(r) for r in conn.execute(sql, {"status": status, "cat": category_code, "as_of": as_of})]
    return {
        "as_of": as_of,
        "status": status,
        "total_issues": len(rows),
        "products_affected": len({r["product_id"] for r in rows}),
        "items": rows[:limit],
        "truncated": len(rows) > limit,
    }


def check_ad_copy(conn: sqlite3.Connection, category_code: str, text: str) -> dict:
    """광고 문구에서 카테고리 공통·전용 금지 표현을 찾는다. 최종 판단은 담당자가 한다."""
    category_code = _category(conn, category_code)
    terms = PROHIBITED_TERMS["*"] + PROHIBITED_TERMS.get(category_code, [])
    hits = [
        {"term": t, "position": m.start()}
        for t in terms
        for m in re.finditer(re.escape(t), text)
    ]
    hits.sort(key=lambda h: h["position"])
    return {
        "category_code": category_code,
        "verdict": "REVIEW" if hits else "CLEAR",
        "hits": hits,
        "note": "키워드 기반 1차 선별입니다. 문맥 판단은 담당자 검수가 필요합니다.",
    }


def rejection_pattern_report(
    conn: sqlite3.Connection, days: int = 30, group_by: str = "reason", as_of: str | None = None
) -> dict:
    """최근 N일 검수 이력을 묶어 집계한다. reason은 반려 비중, category/supplier는 반려율."""
    if group_by not in GROUP_COLUMNS:
        raise ValueError(f"group_by는 {sorted(GROUP_COLUMNS)} 중 하나여야 합니다.")
    days, as_of = _days(days), _as_of(conn, as_of)
    col = GROUP_COLUMNS[group_by]  # 화이트리스트 값만 SQL에 넣는다
    period = "l.inspected_at > date(:as_of, '-' || :days || ' days') AND l.inspected_at <= :as_of"
    if group_by == "reason":
        # 사유별로는 반려율이 의미가 없으므로 전체 반려 중 비중을 계산한다
        sql = f"""
            SELECT {col} AS key,
                   COUNT(*) AS rejected,
                   ROUND(1.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 3) AS share_of_rejections
            FROM inspection_log l
            WHERE l.result = 'REJECT' AND {period}
            GROUP BY {col}
            ORDER BY rejected DESC, key
        """
    else:
        sql = f"""
            SELECT {col} AS key,
                   COUNT(*) AS inspected,
                   SUM(l.result = 'REJECT') AS rejected,
                   ROUND(1.0 * SUM(l.result = 'REJECT') / COUNT(*), 3) AS reject_rate
            FROM inspection_log l JOIN products p USING (product_id)
            WHERE {period}
            GROUP BY {col}
            ORDER BY rejected DESC, key
        """
    rows = [dict(r) for r in conn.execute(sql, {"as_of": as_of, "days": days})]
    return {"as_of": as_of, "days": days, "group_by": group_by, "rows": rows}


_READ_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}


def run_readonly_sql(conn: sqlite3.Connection, sql: str, max_rows: int = 200) -> dict:
    """SELECT/WITH 단일 문장만 실행한다. 읽기 이외 동작은 authorizer가 거부한다."""
    stripped = sql.strip().rstrip(";")
    if ";" in stripped:
        raise ValueError("한 번에 하나의 SELECT 문만 실행할 수 있습니다.")
    if not re.match(r"(?is)^\s*(select|with)\b", stripped):
        raise ValueError("SELECT 또는 WITH로 시작하는 조회문만 허용합니다.")

    conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in _READ_ACTIONS else sqlite3.SQLITE_DENY)
    try:
        cur = conn.execute(stripped)
        rows = cur.fetchmany(max_rows + 1)
    except sqlite3.DatabaseError as e:
        if "not authorized" in str(e):
            raise ValueError("읽기 이외의 동작(UPDATE·DELETE·DDL·PRAGMA 등)은 허용되지 않습니다.") from e
        raise
    finally:
        conn.set_authorizer(None)
    columns = [c[0] for c in cur.description]
    return {
        "columns": columns,
        "rows": [list(r) for r in rows[:max_rows]],
        "truncated": len(rows) > max_rows,
    }
