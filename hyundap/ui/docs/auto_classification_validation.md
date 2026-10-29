# #261 자동 문서 유형 UI 연결 검수

검수일: 2026-10-29. 작업 브랜치: `featurechat-ui-v2`, 기준 커밋: `e13b546` + 이번 UI 작업 변경.

## 수정 범위

`hyundap/app.py`와 `hyundap/ui/`만 수정했다. 기존 app.py의 메뉴 간격 변경도 보존했다. DB·수집·분류·공통 설정·루트 설치 목록·팀 테스트 파일은 변경하지 않았다.

| 파일 | 변경 |
|---|---|
| `app.py` | 서비스의 자동 분류 설정을 업로드 폼으로 전달 |
| `backend_bindings.py` | 자동 분류 설정 조회 함수 등록 |
| `backend_documents.py` | 서버 설정 조회, 자동/수동 metadata 분리, 반환 분류 7개 필드 검사·전달 |
| `services.py` | 선택적 doc_type, 공통 metadata 검사, 자동 업로드 요청 연결 |
| `controller.py` | 자동 모드에서 숨긴 유형 입력을 제외하고 단일·배치 업로드 수행 |
| `admin_page.py` | 모드별 유형 선택 표시, 파일별 최종 유형·대체 사유·회사 확인·확신도·배치 확인 건수 |
| `mock_services.py` | 정상/대체/분류 실패 결과 예시, 수동 모드 주입 지원 |
| `README.md`, `docs/spec.md` | 연결 계약·설정·데모·백엔드 선행 조건 |

## 검사 결과

프로젝트 `.venv/Scripts/python.exe`를 사용했다. 테스트 실행 중 DB 전용 테스트 URL 5개를 빈 값으로 지정하여 외부 DB 검사를 건너뛰었다. 실제 DB 및 유료 AI 호출은 하지 않았다.

전체 저장소 검사 390개 + 기존 로컬 UI 회귀 29개 + 이번 자동 분류 회귀 31개:

```text
Ran 450 tests in 72.895s
OK (skipped=85)
```

365개 통과, MySQL 전용 검사 85개 건너뜀. 별도로 기존 UI 서비스·컨트롤러·문서 어댑터 44개도 통과했다.

이번 31개 검사는 설정 on/off와 구버전 설정, 실제 Gradio 앱의 선택 칸 숨김/유지, 자동 요청의 doc_type 키 생략, 수동 요청 호환, 숨긴 값 무시, 파일별 분류, 대체 건수, 정상 기타와 대체 기타 구분, 기존 유형 유지 안내, 게시 실패 안내, 회사·권한 유지, 늦은 업로드 결과 무효화, 응답 자료형·누락 검사, 빈 문자열 유지, HTML 이스케이프를 포함한다.

### 재현 및 테스트 인계

현재 수정 범위 밖인 `hyundap/tests/`에는 추가하지 않았다. 아래 회귀 검사 원본은 별도 작업 폴더에 보존되어 있으며, 테스트 담당자가 저장소로 옮길 수 있다. 이번 UI 변경만 커밋하면 아래 로컬 테스트 파일은 포함되지 않는다.

```powershell
# 저장소 최상위에서 가상환경 활성화 후
$env:PYTHONPATH = (Get-Location).Path
python -B -m unittest discover -s 'C:/Users/user/Documents/ChatGPT/프론트엔드/.ui-review-work' -p test_ui_auto_classify.py -v
```

- 회귀 원본: `C:/Users/user/Documents/ChatGPT/프론트엔드/.ui-review-work/test_ui_auto_classify.py`
- 전체 실행기: 같은 폴더의 `run_auto_upload_audit.py`
- 전체 상세 결과: 같은 폴더의 `auto-upload-audit.log`
- 테스트 담당자 인계 위치: `hyundap/tests/test_ui_auto_classify.py` (이번 작업에서는 생성하지 않음)
- 기존 로컬 `test_restored_type.py`는 `MockServices(auto_classify=False)`로 수동 모드를 명시해 기존 선택 칸 검사를 유지했다.

실제 Gradio 앱을 생성해 컴포넌트 구성을 검사했지만 브라우저 클릭/시각 검수와 실제 문서 저장 검수는 포함하지 않았다.

## 백엔드 연결 후 남은 확인

연결 목표는 [나사로프로의 #261 초안](https://github.com/paul8004-lee/Hyundap/issues/261#issuecomment-6489076920)이다. 이 UI 구현을 팀 전체 정책 확정 또는 실제 저장 완료로 해석하지 않는다.

- 공통 설정 `doc_classify`, DB의 `auto_classify=True` 입력/7개 결과 필드, 청킹 전 최종 유형 적용, 게시 시 DB 유형 갱신이 함께 반영된 환경에서 자동 모드를 사용한다.
- 현재 로컬 공통 설정에는 `doc_classify` 필드가 없어 실제 모드는 수동으로 유지된다. 데모는 자동 모드로 확인 가능하다. `.env`는 수정하지 않았다.
- 새 문서 3종을 올려 DB 유형 = 모든 청크/벡터 유형 = 반환 유형 = 화면 유형인지 확인하고 검색 필터를 검사한다.
- 기존 문서 새 버전의 유형 변경·대체 시 기존 유형 유지·게시 실패 및 복구·재시도 분류 재사용을 통합 검사한다.
- 실패 응답에 분류 필드가 있어도 UI는 `처리 중 판단 유형`으로 표시한다. 최종 DB 반영 여부가 불명확한 상태에서 저장 완료라고 안내하지 않는다.
- 꺼진 설정에서 자동 요청을 거부하는 정책과 필드 위치는 팀 확정 후 재대조한다. UI의 회사·권한 검사는 기존 백엔드 검증을 그대로 사용한다.
