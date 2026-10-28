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
├── ocr.py             이미지 속 글자 OCR (PaddleOCR, 지연 로딩·예열)
├── loaders/
│   ├── __init__.py    확장자에 맞는 로더 선택
│   ├── pdf_loader.py
│   ├── docx_loader.py
│   ├── pptx_loader.py
│   ├── figures.py     그림 OCR 공통(크기 기준·중복 제거·상한)
│   ├── charts.py      Office 차트 개체(DOCX·PPTX)의 제목·항목·값 읽기
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
| `LEGACY_FORMAT` | 구형 .doc/.ppt를 .docx/.pptx로 이름만 바꾼 파일 |
| `CORRUPTED_FILE` | 깨진 파일 또는 0바이트 |
| `DUPLICATE_FILE` | 같은 회사에 같은 내용의 파일이 이미 있음 |
| `EMPTY_DOCUMENT` | 글자를 꺼낼 수 없음 (OCR로도 읽지 못한 이미지 문서 등) |
| `INVALID_DOC_TYPE` | 지원하지 않는 문서 유형 |
| `FILE_UNAVAILABLE` | 파일을 찾을 수 없거나 읽을 권한이 없음 |

DB 업로드의 후보 생성 전 오류는 작업 원장 없이 반환됩니다. 수집 보고서의 `short_chunk_count`는 앞뒤 공백을 제외하고 20자 미만인 청크의 수이며 임시 진단값입니다.


## 스캔·그림·차트 읽기

글과 그림이 섞인 문서에서 그림 속 글자와 차트 값까지 검색되게 하는 부분입니다. 글자 PDF·DOCX·PPTX의 기본 동작은 그대로입니다.

| 대상 | 읽는 방법 | 넣는 위치 |
|---|---|---|
| 글자 레이어가 없는 PDF 쪽(스캔) | PaddleOCR로 쪽 전체 (200dpi) | 그 쪽의 섹션 |
| 글자 레이어가 있는 PDF 쪽 안의 그림 | 그림 영역만 잘라 PaddleOCR (300dpi) | 본문 뒤 |
| DOCX 그림 | PaddleOCR | 그림이 있는 문단 바로 뒤(표 칸 안이면 표 뒤) |
| PPTX 그림 | PaddleOCR | 읽기 순서대로 그림 자리. 그림만 있는 슬라이드도 섹션이 됨 |
| DOCX·PPTX Office 차트 개체 | 차트 XML의 제목·항목·계열·값을 읽음(OCR 아님, PaddleOCR 없이도 동작) | 차트 자리. `[차트] 제목 (종류)` 머리글이 붙은 표 |

- 로고처럼 작은 그림(60×40pt 미만)과 쪽·슬라이드 면적의 85% 이상인 그림(스캔 배경·표지)은 읽지 않습니다. OCR 신뢰도 0.5 미만 줄은 버리고, 본문과 거의 같은 줄도 뺍니다.
- 문서에 들어간 그림은 해상도가 낮은 경우가 많아 긴 변이 1000px이 되도록(최대 3배) 확대해 읽습니다. 그래도 원본 해상도가 낮으면 덜 읽힙니다(96dpi 그래프에서 정답 글자의 약 2/3).
- 차트는 값을 표로 읽으므로 선 그래프의 점 값도 얻습니다(OCR로는 불가능). 항목 60개·계열 12개·문서당 차트 200개까지 읽고, 넘으면 앞부분만 넣고 표 끝과 문서 경고에 잘렸다고 남깁니다.
- 읽을 수 없는 그림(EMF 등)과 차트는 건너뛰고 경고에 개수를 남깁니다. 텍스트 상자·도형 속 글자, Office의 새 차트 종류(폭포·깔때기 등 chartex)는 읽지 않습니다.
- 스캐너가 넣은 글자 레이어는 품질과 관계없이 그대로 씁니다(스캔본 전체를 다시 OCR하지 않음). 그림 조각에서 읽은 줄이 레이어와 오류가 달라 일부 문장이 한 번 더 붙을 수 있습니다.
- OCR한 한글은 띄어쓰기가 많이 빠지고(예: `매끄러운매생이와굴의조화`), 표는 글자만 읽고 행·열 구조는 인식하지 않습니다.
- PaddleOCR가 설치되어 있지 않으면 OCR은 모두 꺼지고 예전처럼 건너뜁니다(차트 읽기는 그대로 동작). `pypdfium2`가 없으면 PDF OCR만 꺼집니다.
- 개체 하나를 읽지 못해도 업로드 전체는 실패하지 않습니다. 이상한 차트·그림(값이 깨진 차트, 크기 정보가 없는 그림, 이미지가 아닌 그림 관계 등)은 건너뛰고 "읽을 수 없는 …" 경고로 개수를 남깁니다.
- OCR 엔진 오류(예: 인터넷이 없는 서버에서 첫 실행의 모델 내려받기 실패)가 나면 그 문서의 OCR만 멈추고 경고(`OCR 중 오류가 나 건너뜀`)를 남긴 뒤 예전처럼 건너뜁니다. 다음 업로드에서 다시 시도합니다.
- 차트는 파일에 적힌 점 번호·개수가 아니라 실제로 있는 점만 읽습니다(번호를 크게 적어 처리 시간을 늘리는 파일 방지). 행은 60개까지만 만듭니다.
- 문서 그림은 긴 변을 4000px까지 줄여 읽고, 화소가 4천만 개를 넘는 그림은 열기 전에 건너뜁니다(메모리 보호).
- 글자가 없어 건너뛴 슬라이드는 이유마다 경고 한 줄로 모읍니다(예: `슬라이드 50장(슬라이드 101, 102, ... 외): 글자가 없어 건너뜀`). 한 장이면 예전 문구 그대로입니다. DOCX의 "글자를 읽지 않음" 개수는 그림이 여러 장 든 개체도 1개로 셉니다.
- 그림 상한에 도달하면 이후 쪽은 그림을 그리지 않고 상한 경고만 남깁니다. 회전(/Rotate)된 쪽과 보이는 영역(CropBox)이 있는 쪽도 그림 영역을 맞게 잘라 읽습니다.

### 설정 (`.env`, 코드는 `get_settings()`가 읽음)

| 이름 | 기본값 | 뜻 |
|---|---|---|
| `OCR_MAX_PAGES` | 50 | 문서 하나에서 OCR로 읽는 쪽 수 상한. 0이면 스캔 쪽 OCR을 끔 |
| `OCR_MAX_FIGURES` | 100 | 문서 하나에서 OCR로 읽는 그림 수 상한. 0이면 그림 OCR을 끔 |
| `OCR_THREADS` | 코어 3개 이상이면 코어 수 − 1, 2개 이하면 코어 수 | OCR이 쓰는 CPU 스레드 수. 같은 서버의 다른 요청과 경쟁을 줄이려고 하나를 남김 |
| `OCR_WARMUP` | 켜짐 | 서버 시작 때 OCR 엔진을 백그라운드에서 미리 불러옴. `0`·`false`면 끔 |

- 기본 상한은 처리 시간으로 정했습니다. 4코어 CPU 실측으로 스캔 쪽은 쪽당 2~5초라 50쪽이면 최악 2~4분, 그림은 장당 약 0.5초라 100개여도 1분 안팎입니다. 업로드는 한 요청 안에서 끝날 때까지 이어지므로(`db.upload_service`) 더 길게 잡지 않았습니다. 필요하면 `.env`에서 올립니다. 상한을 넘은 쪽은 OCR하지 않고 경고 한 줄(예: `15쪽(p.6~p.20)은 글자가 없어 건너뜀 (OCR은 문서당 5쪽까지)`)로 남깁니다.
- 스레드 수별 실측(쪽당): 4개 2.6초, 3개 3.1초(+18%), 2개 4.0초, 1개 6.5초.
- 예열은 엔진 생성(약 2.5초)과 첫 실행 지연을 서버 시작 시점으로 옮깁니다. 예열 중에 업로드가 들어오면 예열이 끝날 때까지 기다립니다.

### 왜 로컬 PaddleOCR인가 (도구 선택 이유)

선택 기준은 **무료 · 속도 · 한글 정확도**였고, 수업의 "모델 선택 4기준"(정확도 vs 비용 / 속도 / 컨텍스트 크기 / 데이터 보안) 중 **데이터 보안(내부 데이터면 로컬 모델)** 과 **비용(무료 조건)** 을 함께 고려했습니다. 사내 문서가 외부 서버로 나가지 않고, 호출당 비용이 없고, CPU만으로 쓸 만한 속도가 나오는 도구로 PaddleOCR(PP-OCRv5 모바일 검출 + 한국어 인식)를 골랐습니다. 후보 비교와 실측은 [`docs/spec.md`](docs/spec.md)의 "OCR 도구 선택 기록"에 있습니다. 외부 OCR API(Naver Clova 등)는 문서 내용이 외부로 나가고 무료 한도가 작아 제외했습니다.

## 알아 둘 점

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
- OCR: `pypdfium2`(PDF 쪽 렌더링, `requirements.txt`; 없으면 PDF OCR만 꺼지고 DOCX·PPTX 그림 OCR은 영향 없음), `paddleocr`·`paddlepaddle`(3.2.0 고정, CPU, 선택 설치 `requirements-ocr.txt`). 설치: `pip install -r requirements.txt -r requirements-ocr.txt`. 설치하지 않으면 OCR만 꺼집니다.
- 버전은 루트 `requirements.txt` 참고

## 참고

- 통합 기능명세서: [`docs/spec.md`](../../docs/spec.md) 3.2절
- 기능 도식도: [`docs/diagrams/1_문서_업로드_색인.png`](../../docs/diagrams/1_문서_업로드_색인.png)
