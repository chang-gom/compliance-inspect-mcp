"""합성(더미) 검수 데이터 생성기.

실제 회사·상품·공급사 데이터가 아닙니다. 고정 시드로 매번 같은 데이터를 만듭니다.
실행: python data/seed.py  →  data/compliance.db, data/inspection_log.csv
"""
from __future__ import annotations

import csv
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core  # noqa: E402

HERE = Path(__file__).parent
SNAPSHOT = date(2026, 11, 15)
rng = random.Random(42)

CATEGORIES = {
    "FOOD": ("가공식품", [("품목제조보고서", "식품위생법"), ("자가품질검사성적서", "식품위생법")],
             ["국산 현미 누룽지", "저당 그래놀라", "냉동 닭가슴살 큐브", "유기농 귀리 우유"]),
    "HFOOD": ("건강기능식품", [("건강기능식품 품목제조신고서", "건강기능식품에 관한 법률"),
                          ("표시·광고 자율심의 결과서", "건강기능식품에 관한 법률")],
              ["프로바이오틱스 30포", "rTG 오메가3", "비타민D 1000IU", "밀크씨슬 60정"]),
    "COSM": ("화장품", [("화장품책임판매업 등록증", "화장품법")],
             ["저자극 수분크림", "약산성 클렌징폼", "무기자차 선크림", "판테놀 앰플"]),
    "ELEC": ("생활가전", [("KC 안전인증·안전확인 신고증명서", "전기용품 및 생활용품 안전관리법")],
             ["무선 전기주전자", "미니 제습기", "USB 탁상 선풍기", "전기 그릴"]),
    "KIDS": ("어린이제품", [("KC 어린이제품 공급자적합성확인서", "어린이제품 안전 특별법")],
             ["원목 블록 50pcs", "유아 실리콘 식판", "어린이 우비", "자석 칠판"]),
    "CHEM": ("생활화학제품", [("안전확인대상 생활화학제품 신고증명서", "생활화학제품 및 살생물제의 안전관리에 관한 법률")],
             ["주방세제 리필", "섬유유연제", "욕실 세정제", "얼룩 제거제"]),
    "PETF": ("반려동물 사료", [("사료 성분등록증", "사료관리법")],
             ["강아지 연어 사료", "고양이 습식 파우치", "노견용 관절 간식", "동결건조 트릿"]),
}
REASONS = {
    "DOC_MISSING": "필수 서류 누락",
    "DOC_EXPIRED": "서류 유효기간 만료",
    "LABEL_MISMATCH": "표시사항(용량·원산지 등) 불일치",
    "AD_CLAIM": "금지·과장 광고 표현",
    "CATEGORY_WRONG": "카테고리 오분류",
}
CLEAN_COPY = "매일 간편하게 즐기는 실속 구성", "꼼꼼한 품질 관리로 만든 제품", "선물용으로도 좋은 깔끔한 패키지"
RISKY_COPY = {
    "FOOD": "면역력 강화에 도움을 주는 건강 간식",
    "HFOOD": "장 건강 특효, 100% 효과 보장",
    "COSM": "아토피 개선 효과가 있는 크림",
    "ELEC": "업계 최고 성능의 제품",
    "KIDS": "유일한 무독성 인증 장난감",
    "CHEM": "부작용 없는 순한 세정력",
    "PETF": "관절 질병 예방 간식",
}
SUPPLIERS = [f"S{i:02d}" for i in range(1, 13)]
RISKY_SUPPLIER = "S07"  # 의도적으로 만료 서류가 많은 공급사 (분석 연습용 패턴)


def build(db_path: Path) -> None:
    db_path.unlink(missing_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(core.SCHEMA)
    conn.execute("INSERT INTO meta VALUES ('snapshot_date', ?)", (SNAPSHOT.isoformat(),))
    conn.execute("INSERT INTO meta VALUES ('notice', '합성 데이터 - 실제 상품·공급사 아님')")
    conn.executemany("INSERT INTO reason_codes VALUES (?, ?)", REASONS.items())
    conn.executemany("INSERT INTO suppliers VALUES (?, ?)", [(s, f"공급사 {s}") for s in SUPPLIERS])
    for code, (name, docs, _) in CATEGORIES.items():
        conn.execute("INSERT INTO categories VALUES (?, ?)", (code, name))
        conn.executemany("INSERT INTO required_documents VALUES (?, ?, ?)", [(code, d, law) for d, law in docs])

    product_seq = 0
    for code, (_, docs, names) in CATEGORIES.items():
        for _ in range(rng.randint(38, 52)):
            product_seq += 1
            pid = f"P{product_seq:05d}"
            supplier = rng.choice(SUPPLIERS)
            risky_copy = rng.random() < (0.22 if code == "HFOOD" else 0.08)
            copy = RISKY_COPY[code] if risky_copy else rng.choice(CLEAN_COPY)
            status = rng.choices(["검수대기", "승인", "반려"], [25, 65, 10])[0]
            registered = SNAPSHOT - timedelta(days=rng.randint(1, 120))
            conn.execute(
                "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pid, f"{rng.choice(names)} {rng.choice(['A', 'B', 'C'])}형", code, supplier, copy, status,
                 registered.isoformat()),
            )
            expire_p = 0.25 if supplier == RISKY_SUPPLIER else 0.05
            for doc_type, _ in docs:
                roll = rng.random()
                if roll < 0.08:
                    continue  # 서류 누락
                expired = roll < 0.08 + expire_p
                valid = SNAPSHOT + timedelta(days=-rng.randint(1, 200) if expired else rng.randint(30, 900))
                conn.execute(
                    "INSERT INTO product_documents VALUES (?, ?, ?, ?)",
                    (pid, doc_type, f"DOC-{rng.randint(100000, 999999)}", valid.isoformat()),
                )
            _log_inspections(conn, pid, code, supplier, risky_copy, registered)
    conn.commit()
    _export_csv(conn, HERE / "inspection_log.csv")
    conn.close()


def _log_inspections(conn, pid, code, supplier, risky_copy, registered) -> None:
    for _ in range(rng.randint(2, 5)):
        day = registered + timedelta(days=rng.randint(0, max(0, (SNAPSHOT - registered).days)))
        weights = {"DOC_MISSING": 3, "DOC_EXPIRED": 6 if supplier == RISKY_SUPPLIER else 2,
                   "LABEL_MISMATCH": 3, "AD_CLAIM": 6 if risky_copy else 1, "CATEGORY_WRONG": 1}
        reject_p = 0.10 + (0.15 if risky_copy else 0) + (0.12 if supplier == RISKY_SUPPLIER else 0)
        if rng.random() < reject_p:
            reason = rng.choices(list(weights), list(weights.values()))[0]
            conn.execute("INSERT INTO inspection_log (product_id, inspected_at, result, reason_code) VALUES (?, ?, 'REJECT', ?)",
                         (pid, day.isoformat(), reason))
        else:
            conn.execute("INSERT INTO inspection_log (product_id, inspected_at, result) VALUES (?, ?, 'PASS')",
                         (pid, day.isoformat()))


def _export_csv(conn, path: Path) -> None:
    """Tableau 등 시각화 도구용 평탄화 CSV."""
    rows = conn.execute("""
        SELECT l.log_id, l.inspected_at, l.result, COALESCE(l.reason_code, '') AS reason_code,
               p.product_id, p.category_code, c.name AS category_name, p.supplier_id
        FROM inspection_log l JOIN products p USING (product_id) JOIN categories c USING (category_code)
        ORDER BY l.inspected_at, l.log_id
    """).fetchall()
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["log_id", "inspected_at", "result", "reason_code", "product_id",
                    "category_code", "category_name", "supplier_id"])
        w.writerows(rows)


if __name__ == "__main__":
    build(HERE / "compliance.db")
    print("created data/compliance.db, data/inspection_log.csv")
