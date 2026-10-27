# ingest — 문서 수집·파싱·청킹

업로드된 문서 파일(PDF · DOCX · PPTX) 1개를 **검색할 수 있는 작은 조각(청크)** 여러 개로 바꿔 주는 폴더입니다.

- **담당**: 김혜리
- **상태**: 구현 완료 / 리뷰중
- **상세 명세**: [`docs/spec.md`](docs/spec.md)

## 이 폴더가 하는 일

```
업로드 파일 → ① 검증 → ② 글자 추출 → ③ 글자 정리 → ④ 청킹 → 청크 목록
```

| 단계 | 하는 일 | 파일 |
|---|---|---|
| ① 검증 | 형식·용량(100MiB)·암호화·손상·중복 검사 | `validator.py` |
| ② 글자 추출 | PDF는 쪽마다, DOCX는 제목 구간마다, PPTX는 슬라이드마다 글자·표를 꺼냄 | `loaders/` |
| ③ 글자 정리 | 한글 결합, 보이지 않는 글자 제거, 공백 정리 | `normalizer.py` |
| ④ 청킹 | 약 1,000자씩 자름. 쪽·슬라이드 경계는 넘지 않음 | `chunker.py` |

각 청크에는 **회사 · 파일명 · 위치(p.3, 슬라이드 5 등)** 가 붙어서, 검색 결과의 출처 표시에 그대로 쓰입니다.

이 폴더가 하지 **않는** 일: 파일 저장·DB 기록(`db/`), 임베딩·벡터 저장(`embedding/`), 화면(`ui/`), 로그인·권한(`auth/`).

## 폴더 구성

```
ingest/
├── README.md          이 문서
├── docs/spec.md       기능 명세와 구현 계획
├── models.py          IngestError(오류), IngestResult(처리 결과)
├── validator.py       업로드 파일 검증
├── loaders/
│   ├── __init__.py    확장자에 맞는 로더 선택
│   ├── pdf_loader.py
│   ├── docx_loader.py
│   ├── pptx_loader.py
│   └── base.py        표 변환, 출처 위치 문구
├── normalizer.py      글자 정리
├── chunker.py         청킹
└── pipeline.py        위 과정을 한 번에 실행
```

## 사용 방법

다른 폴더에서는 `ingest_document` 하나만 부르면 됩니다.
실행은 저장소 맨 위 폴더(Hyundap/)에서 하고, import는 hyundap. 기준으로 씁니다.

```python
from hyundap.ingest.pipeline import ingest_document

upload_info = {
    "document_id": "...", "version_id": "...",
    "company_id": "...", "company_name": "...",
    "doc_type": "...", "file_name": "...",
}
result = ingest_document("data/uploads/example.pdf", upload_info)

if result.status == "success":
    chunks = result.chunks          # → embedding 폴더로 전달
else:
    print(result.error_code, result.error_message)   # → db가 실패 원인으로 기록
```

> 위 코드는 사용 예시입니다. 함수 이름·인자는 구현하면서 바뀔 수 있고, 바뀌면 이 문서를 같이 고칩니다.

## 입력과 출력

| 구분 | 내용 |
|---|---|
| 입력 | 파일 경로, 업로드 정보 (`document_id, version_id, company_id, company_name, doc_type, file_name`) |
| 출력 | `IngestResult` — `status`, `chunks`, `report`, `error_code`, `error_message` |
| 청크 1개 | `chunk_id, document_id, version_id, company_id, company_name, doc_type, file_name, section_no, location_label, chunk_index, text` |

## 오류 코드

| 코드 | 뜻 |
|---|---|
| `UNSUPPORTED_FORMAT` | 지원하지 않는 형식 (PDF·DOCX·PPTX만 가능) |
| `FILE_TOO_LARGE` | 100MiB 초과 |
| `ENCRYPTED_FILE` | 암호가 걸린 문서 |
| `CORRUPTED_FILE` | 깨진 파일 또는 0바이트 |
| `DUPLICATE_FILE` | 같은 회사에 같은 내용의 파일이 이미 있음 |
| `EMPTY_DOCUMENT` | 글자를 꺼낼 수 없음 (스캔한 이미지 문서 등) |
| `INVALID_DOC_TYPE` | 지원하지 않는 문서 유형 |
| `FILE_UNAVAILABLE` | 파일을 찾을 수 없거나 읽을 권한이 없음 |

`LEGACY_FORMAT`은 안내 문구만 준비됐으며 구형 Office 파일 판별은 미구현입니다. DB 업로드의 사전 검사는 별도 오류를 반환할 수 있습니다.

## 알아 둘 점

- 스캔(이미지) PDF는 OCR 없이는 읽을 수 없어 `EMPTY_DOCUMENT`가 됩니다. (최소 기능만 넣을 예정이라 이미지 PDF 인식은 미구현)
- DOCX는 파일에 페이지 번호가 없어 출처가 `섹션 N (제목)` 형태입니다.
- 새 파일 형식을 추가하려면 로더 파일 1개와 `loaders/__init__.py`의 선택표 한 줄을 추가합니다.
- 청크 크기(1000자)·겹침(150자)은 기본값이며, 검색 품질을 보고 조정합니다.

## 다른 폴더와의 연결

| 폴더 | 관계 |
|---|---|
| `db/` (전재경) | 업로드 처리 흐름에서 `ingest_document`를 호출하고, 중복 검사를 위해 같은 회사의 해시 조회 함수를 제공 |
| `embedding/` (박영준) | ingest가 만든 청크를 입력으로 받아 임베딩 |
| `common/` (이성민) | `CommonDocument`, `Chunk` 공통 형식을 제공 |

## 사용 라이브러리

- `pypdf`, `langchain-community`, `PyPDFLoader`, `python-docx`, `python-pptx`, `langchain-core`, `langchain-text-splitters` 
- 버전은 루트 `requirements.txt` 참고

## 참고

- 통합 기능명세서: [`docs/spec.md`](../../docs/spec.md) 3.2절
- 기능 도식도: [`docs/diagrams/1_문서_업로드_색인.png`](../../docs/diagrams/1_문서_업로드_색인.png)
