"""컴플라이언스 검수 MCP 서버.

Claude Desktop / Claude Code 등 MCP 클라이언트에서 상품 등록 검수 데이터를
자연어로 조회할 수 있도록 읽기 전용 도구 5개를 제공합니다.

실행: python server.py   (stdio)
DB 경로: 환경변수 COMPLIANCE_DB (기본값 data/compliance.db)
"""
from __future__ import annotations

import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

import core

DB_PATH = Path(os.environ.get("COMPLIANCE_DB", Path(__file__).parent / "data" / "compliance.db"))

server = MCPServer(
    name="compliance-inspect",
    instructions=(
        "상품 등록 컴플라이언스 검수 데이터 조회용 서버입니다. 모든 도구는 읽기 전용입니다. "
        "서류 누락·만료는 find_registration_gaps, 광고 문구는 check_ad_copy, "
        "반려 패턴은 rejection_pattern_report를 먼저 사용하고, 그 외 질문만 run_readonly_sql을 쓰세요."
    ),
)


@server.tool()
def list_required_documents(category_code: str | None = None) -> list[dict]:
    """카테고리별 필수 서류와 근거 법령을 조회합니다. category_code 예: FOOD, HFOOD, COSM, ELEC, KIDS, CHEM, PETF"""
    with core.connect_readonly(DB_PATH) as conn:
        return core.list_required_documents(conn, category_code)


@server.tool()
def find_registration_gaps(
    category_code: str | None = None, status: str = "검수대기", as_of: str | None = None, limit: int = 50
) -> dict:
    """필수 서류가 없거나(MISSING) 유효기간이 지난(EXPIRED) 상품을 찾습니다. as_of 기본값은 데이터 스냅샷 날짜입니다."""
    with core.connect_readonly(DB_PATH) as conn:
        return core.find_registration_gaps(conn, category_code, status, as_of, limit)


@server.tool()
def check_ad_copy(category_code: str, text: str) -> dict:
    """광고·상세 문구에서 카테고리별 금지·주의 표현을 1차 선별합니다."""
    with core.connect_readonly(DB_PATH) as conn:
        return core.check_ad_copy(conn, category_code, text)


@server.tool()
def rejection_pattern_report(days: int = 30, group_by: str = "reason", as_of: str | None = None) -> dict:
    """최근 N일 검수 이력을 집계합니다. reason=반려 사유별 비중, category/supplier=반려율."""
    with core.connect_readonly(DB_PATH) as conn:
        return core.rejection_pattern_report(conn, days, group_by, as_of)


@server.tool()
def run_readonly_sql(sql: str, max_rows: int = 200) -> dict:
    """전용 도구로 답할 수 없는 질문에 한해 SELECT 단일 문장을 실행합니다. 쓰기·DDL은 거부됩니다."""
    with core.connect_readonly(DB_PATH) as conn:
        return core.run_readonly_sql(conn, sql, max_rows)


if __name__ == "__main__":
    server.run()
