# DB 명세

책임: 전재경. 기준 계약은 [DB_contract.md](../../../docs/db-design/DB_contract.md), 물리 설계는 [DB_schema.md](../../../docs/db-design/DB_schema.md), 적용 절차는 [MIGRATION_v4.md](../../../docs/db-design/MIGRATION_v4.md).

| 명세 기능 | DB 제공 책임 | 연계 담당 |
|---|---|---|
| F39 | 원본·문서·버전 정보 저장/조회 | 김혜리·박경환 |
| F63 / FUN-206 | 회사별 해시 중복 조회·최종 중복 보호 | 김혜리 |
| FUN-247 | 활성 버전·회사 11필드 일괄 행 조회 | 이준영; DTO 변환·공개 판정은 search |
| F41 / F107 | 처리 보고서·실패 사유 영속화 | 김혜리·박영준; 관리 UI는 박경환 |
| F110~112 | 완전성 판정·DB 포인터·embedding 호출 | 박영준; Chroma 내부는 embedding |

#118은 계약 작성, #119는 구조·마이그레이션 준비다. repo 구현은 #121, 업로드/게시 서비스는 #122에서 수행한다. 현재 실행 가능한 모듈은 connection.py와 읽기 전용 schema.py다.

`cd hyundap` 후 `python -m db.schema --inspect/--check`로 구조를 확인한다. SQL 생성은 `python -m db.schema --prepare ..\docs\db-design\migrations\004_local.sql`이며 DB 쓰기를 실행하지 않는다. 실제 공유 DB는 아직 미점검이다. 일반 함수·dict/list와 기존 SQLAlchemy text 연결을 재사용한다.
