# compliance-inspect-mcp

상품 등록 검수 데이터를 Claude에서 자연어로 조회할 수 있게 하는 **읽기 전용 MCP 서버**입니다.
"검수 대기 중인데 서류가 빠진 상품", "최근 30일 반려가 많은 공급사", "이 광고 문구에 문제 표현이 있는지" 같은 질문을
SQL을 직접 쓰지 않고도 확인하도록 만드는 것이 목표입니다.

> **데이터 안내** — `data/seed.py`가 고정 시드로 만든 **합성 데이터**입니다. 실제 회사·상품·공급사 정보가 아니며,
> 카테고리별 필수 서류와 근거 법령 매핑, 금지 표현 목록도 설계 연습을 위한 **예시**입니다.

## 왜 만들었나

운영 자동화를 하면서 검수 업무에는 세 가지 반복이 있다고 봤습니다.

1. 필수 서류가 있는지, 유효기간이 지났는지 매번 사람이 대조한다.
2. 광고·상세 문구에서 문제 표현을 눈으로 찾는다.
3. 반려가 어디서 많이 나는지 보려면 데이터 담당자에게 SQL을 요청해야 한다.

1·2는 규칙으로 **1차 선별**하고 최종 판단은 담당자에게 남기며, 3은 검증된 집계 도구와 읽기 전용 SQL로 담당자가 직접 묻게 했습니다.

## 구조

```
core.py          검수 로직 (SQL·규칙). MCP와 분리해 테스트 가능
server.py        MCP 서버 — core 함수를 도구 5개로 노출 (stdio)
data/seed.py     합성 데이터 생성 → data/compliance.db, data/inspection_log.csv(시각화용)
tests/           고정 픽스처 기반 테스트 11개 (stdio 연동 포함)
```

| 도구 | 하는 일 | 구현 포인트 |
|---|---|---|
| `list_required_documents` | 카테고리별 필수 서류·근거 법령 | 규칙을 코드가 아닌 테이블로 관리 |
| `find_registration_gaps` | 필수 서류 누락(MISSING)·만료(EXPIRED) 상품 | CTE + LEFT JOIN, 기준일(as_of) 고정 |
| `check_ad_copy` | 카테고리 공통·전용 금지 표현 선별 | 결과는 `CLEAR / REVIEW`만, 판정은 사람 |
| `rejection_pattern_report` | 반려 사유 비중, 카테고리·공급사별 반려율 | 윈도 함수, `group_by` 화이트리스트 |
| `run_readonly_sql` | 그 외 질문용 SELECT 1문장 | 읽기 전용 연결 + SQLite authorizer |

## 안전장치

- DB는 `mode=ro`로 열고, `run_readonly_sql`은 SQLite authorizer로 읽기 이외 동작(UPDATE·DELETE·DDL·ATTACH)을 거부합니다.
  `WITH ... DELETE`처럼 SELECT로 시작하지 않는 우회도 테스트로 막았습니다.
- 여러 문장(`;`) 실행 금지, 결과 행 수 상한(`max_rows`)과 잘림 여부(`truncated`)를 함께 반환합니다.
- 집계 기준 컬럼은 사용자 입력을 SQL에 넣지 않고 허용 목록에서만 고릅니다.
- 날짜 판단은 실행 시각이 아니라 데이터 스냅샷 기준일로 고정해, 같은 질문에 같은 답이 나오게 했습니다.

## 실행

```bash
pip install -r requirements.txt
python data/seed.py          # 합성 DB 생성
python -m pytest -q          # 11 passed
```

**Claude Code에 연결**

```bash
claude mcp add compliance-inspect -e COMPLIANCE_DB=$(pwd)/data/compliance.db -- python $(pwd)/server.py
```

**Claude Desktop에 연결** (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "compliance-inspect": {
      "command": "python",
      "args": ["/절대경로/compliance-inspect-mcp/server.py"],
      "env": { "COMPLIANCE_DB": "/절대경로/compliance-inspect-mcp/data/compliance.db" }
    }
  }
}
```

## 합성 데이터 기준 실행 결과 (스냅샷 2026-11-15)

| 항목 | 값 |
|---|---|
| 상품 / 검수 이력 | 322개 / 1,113건 |
| 검수대기 상품 중 서류 문제 | 64개 중 14개 (누락 7건, 만료 7건) |
| 검수대기 상품 중 광고 문구 REVIEW | 64개 중 7개 |
| 최근 90일 반려 사유 비중 | 표시사항 불일치 27.2% · 서류 누락 26.5% · 광고 표현 20.4% · 서류 만료 19.7% · 오분류 6.1% |
| 만료 서류가 가장 많은 공급사 | S07 (6건) |

## 한계와 다음 단계

- 금지 표현은 키워드 일치라 문맥(예: "치료 목적이 아닙니다")을 구분하지 못합니다. 그래서 판정 대신 `REVIEW`로만 표시합니다.
- 실제 환경이라면 서류 원본(PDF)의 인증번호를 추출해 공공 인증 조회 결과와 대조하는 단계가 다음 과제입니다.
- 사내 시스템 연동 시에는 사용자별 권한과 조회 이력 저장이 필요합니다.

---
Claude Code를 활용해 설계·구현했으며, 검수 기준과 안전장치, 테스트 케이스는 직접 정의하고 검토했습니다.
