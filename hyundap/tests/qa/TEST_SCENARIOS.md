# 기능 점검 시나리오 (WBS 4.1.1)

WBS 4.1.2~4.1.6 점검에서 실행하는 시나리오입니다. 모든 시나리오는 `run_qa.py`가 화면과 같은 경로(`hyundap.ui.services.BackendServices`)로 실제 백엔드(공유 Railway DB, OpenAI)를 호출해 자동으로 실행합니다. 화면 동작(로그인 탭, 안내 문구 표시, 버튼 흐름)은 사용성 테스트 `hyundap/tests/usability/TEST_PLAN.md`의 S1~S18이 다룹니다.

## 테스트 환경

| 항목 | 내용 |
|---|---|
| 기준 코드 | `develop` (실행 시점의 커밋은 결과서에 기록) |
| DB | 공유 Railway MySQL v4 (2026-10-27 15:29 초기화 이후 상태) |
| 계정 | 플랫폼 관리자 1, 회사 관리자 3(현대무벡스·현대엘리베이터·현대경제연구원), 직원 3. 비밀번호는 저장소 밖 `data/qa_accounts.json` |
| 테스트 문서 | `corpus.json`의 25건 (회사별 8~9건, PDF 5·DOCX 14·PPTX 6). 공유 드라이브 `구버전/목업 데이터`의 가상 자료와 공개 자료 |
| 정확도 질문 | 공유 드라이브 `구버전/Hit-test 질문/평가정답_100`에서 10문항 |

회사 약칭: mvx = 현대무벡스, hel = 현대엘리베이터, hri = 현대경제연구원.

## 1. 형식별 업로드·내용 추출 (WBS 4.1.2, #81)

| ID | 대상 | 기대 결과 |
|---|---|---|
| U-PDF | PDF 5건 업로드 | 업로드 성공, 수집 보고서의 섹션·청크 수가 1 이상, 출처 위치 문구가 `p.N` 형식 |
| U-DOCX | DOCX 14건 업로드 | 업로드 성공, 섹션·청크 수 1 이상, 위치 문구 `섹션 N` 형식 |
| U-PPTX | PPTX 6건 업로드 | 업로드 성공, 섹션·청크 수 1 이상, 위치 문구 `슬라이드 N` 형식 |

## 2. 필터 검색 (WBS 4.1.3, #82)

| ID | 계정 | 입력 | 기대 결과 |
|---|---|---|---|
| F1 | 플랫폼 관리자 | "신규 담당자 실습", 회사 hri | 결과 1건 이상, 모두 hri 문서 |
| F2 | 플랫폼 관리자 | "접수와 등록 매뉴얼", 회사 mvx·hel | mvx·hel 문서만, 두 회사 모두 포함 |
| F3 | 플랫폼 관리자 | "신규 담당자 실습", 유형 manual | 매뉴얼만 |
| F4 | 플랫폼 관리자 | "접수와 등록", 유형 guide | 가이드만 |
| F5 | 플랫폼 관리자 | "실습", 회사 hri + 유형 guide | hri 가이드만 |
| F6 | 플랫폼 관리자 | "접수와 등록", 유형 policy(올린 문서 없음) | 오류 없이 0건 |
| F7 | hri 회사 관리자 | "접수와 등록", 필터 없음 | hri 문서만 |
| F8 | hri 직원 | 질문 범위를 HRI-SYN-MAN-001 한 문서로 지정 | 답변 성공, 출처가 그 문서만 |

## 3. 권한 (WBS 4.1.5, #84)

| ID | 계정 | 시도 | 기대 결과 |
|---|---|---|---|
| P1 | mvx 직원 | hri 문서 주제로 검색 | hri 문서 0건 |
| P2 | mvx 직원 | 회사 필터에 hri ID를 직접 지정 | 거부 또는 hri 문서 0건 |
| P3 | mvx 직원 | hri 문서 내용을 질문 | hri 출처 0건 |
| P4 | mvx 직원 | hri 문서 ID로 요약 | 접근 거부 |
| P5 | mvx 직원 | hri 문서 ID로 상세 조회 | 접근 거부 |
| P6 | mvx 직원 | hri 문서를 질문 범위로 지정 | 거부 또는 hri 출처 0건 |
| P7 | hri 직원 | 문서 업로드 | 접근 거부 |
| P8 | hri 회사 관리자 | mvx 회사로 업로드 | 접근 거부 |
| P9 | 플랫폼 관리자 | 필터 없이 검색 | 2개 회사 이상 문서 |
| P10 | hri 직원 | 로그아웃한 세션으로 검색 | `SESSION_EXPIRED` |
| P11 | hri 직원 | 세션 dict의 역할·회사를 플랫폼 관리자·mvx로 바꿔 검색 | hri 외 문서 0건 (서버가 DB 기준으로 재검증) |

## 4. 예외 상황 (WBS 4.1.6, #85)

모두 hri 회사 관리자로 hri 회사에 업로드합니다. E1·E2·E6~E10의 파일은 실행 때 `data/qa_samples/`에 새로 만듭니다.

| ID | 파일 | 기대 오류 코드 |
|---|---|---|
| E1 | 앞 2KB만 남긴 PDF | `CORRUPTED_FILE` |
| E2 | 비밀번호를 건 PDF | `ENCRYPTED_FILE` |
| E3 | 암호가 걸린 PPTX (목업) | `ENCRYPTED_FILE` |
| E4 | 손상된 PPTX (목업) | `CORRUPTED_FILE` |
| E5 | 글자가 없는 스캔 PDF (목업) | `EMPTY_DOCUMENT` |
| E6 | 0바이트 DOCX | `CORRUPTED_FILE` |
| E7 | `.txt` 파일 | `UNSUPPORTED_FORMAT` |
| E8 | 구형 .doc 시작값을 가진 `.docx` | `LEGACY_FORMAT` 또는 `UNSUPPORTED_FORMAT` (#145) |
| E9 | PDF 내용을 `.docx`로 저장 | `UNSUPPORTED_FORMAT` 또는 `CORRUPTED_FILE` |
| E10 | 본문 XML이 없는 DOCX | `CORRUPTED_FILE` |
| E11 | 이미 올린 HRI-SYN-MAN-001 | `DUPLICATE_FILE` |

## 5. 검색 정확도 (WBS 4.1.4, #83)

질문 회사의 직원 계정으로 검색해 결과 문서 상위 3개에 정답 문서가 있는지 봅니다(대표 정답 기준 문서 Hit@3). 정답은 질문과 함께 검색기에 넣지 않습니다. 같은 주제의 매뉴얼·변경 매뉴얼·교육자료·공개 제품자료가 함께 올라가 있어 서로 헷갈릴 수 있는 문서가 있습니다.

| ID | 회사 | 정답 문서 | 질문 유형 |
|---|---|---|---|
| A-Q001 | mvx | MVX-SYN-MAN-001 | 신규 등록 매뉴얼 |
| A-Q011 | mvx | MVX-SYN-MAN-002 | 변경 승인 매뉴얼 |
| A-Q031 | mvx | MVX-SYN-EDU-001 | 실습 교육자료 |
| A-Q085 | mvx | MVX-PUB-026 | 공개 제품자료(실습자료와 구분) |
| A-Q004 | hel | HEL-SYN-MAN-001 | 신규 등록 매뉴얼 |
| A-Q014 | hel | HEL-SYN-MAN-002 | 변경 승인 매뉴얼 |
| A-Q034 | hel | HEL-SYN-EDU-001 | 실습 교육자료 |
| A-Q007 | hri | HRI-SYN-MAN-001 | 신규 등록 매뉴얼 |
| A-Q017 | hri | HRI-SYN-MAN-002 | 변경 승인 매뉴얼 |
| A-Q037 | hri | HRI-SYN-EDU-001 | 실습 교육자료 |

질문 원문은 `corpus.json`의 `accuracy_questions`에 있습니다.

## 판정 기준

- **PASS**: 기대 결과와 같음
- **FAIL**: 기대 결과와 다름. 결과서에 원인과 관련 이슈를 적음
- **ERROR**: 시나리오 실행 중 예상하지 못한 예외. 테스트 도구 문제인지 제품 문제인지 결과서에서 구분함
