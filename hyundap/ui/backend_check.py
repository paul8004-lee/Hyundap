"""B24~B32: 외부 서비스를 호출하지 않는 CLI 전용 연결 준비 점검.

패키지 버전, 환경변수의 존재 여부, 공개 함수 import만 확인한다.
DB 연결·LLM 호출·Chroma 클라이언트 생성·업로드·다운로드는 실행하지 않는다.
환경변수 값과 예외 원문은 반환하지 않으며 이 결과를 업무 화면에 표시하지 않는다.
"""
from __future__ import annotations

import contextlib
import importlib
from importlib import metadata
import io
import os
from pathlib import Path
import re
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 검증용 health 함수는 실제 접속/호출을 수행할 수 있으므로 여기에 넣지 않는다.
PUBLIC_FUNCTIONS = (
    ("검색", "hyundap.search.search", "search"),
    ("질문 근거 검색", "hyundap.search.search", "search_chunks"),
    ("질문", "hyundap.chatbot.ask", "ask"),
    ("요약", "hyundap.chatbot.summary", "summarize_document"),
    ("문서 정보 조회", "hyundap.db.documents_repo", "get_documents_with_active_version"),
    ("문서 접근 재검증", "hyundap.auth.permissions", "verify_documents_access"),
    ("허용 회사 조회", "hyundap.auth.permissions", "get_allowed_company_ids"),
    ("서버 세션 재검증", "hyundap.db.sessions_repo", "current_actor"),
    ("업로드 처리", "hyundap.db.upload_service", "upload_document"),
    ("문서 수집", "hyundap.ingest.pipeline", "ingest_document"),
    ("질문 임베딩", "hyundap.embedding.embedder", "embed_query"),
    ("청크 임베딩/저장", "hyundap.embedding.embedder", "embed_and_store"),
    ("벡터 검색", "hyundap.embedding.vector_store", "query_vectors"),
    ("문서 청크 조회", "hyundap.embedding.vector_store", "get_document_chunks"),
)
REQUIRED_ENV = ("DATABASE_URL", "OPENAI_API_KEY")
OPTIONAL_ENV = (
    "OPENAI_EMBEDDING_MODEL", "OPENAI_CHAT_MODEL", "CHROMA_DB_PATH", "UPLOAD_DIR",
    "SEARCH_MIN_SCORE", "SEARCH_TOP_K_MAX", "CHUNK_SIZE", "CHUNK_OVERLAP",
)
PINNED_REQUIREMENT = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[A-Za-z0-9_,.-]+\])?==([A-Za-z0-9][A-Za-z0-9.+!-]*)"
)


def _row(name: str, status: str, message: str) -> dict[str, str]:
    return {"name": name, "status": status, "message": message}


def _package_status() -> list[dict[str, str]]:
    """루트 requirements의 정확한 고정 버전과 설치된 배포판 메타정보를 비교한다."""
    try:
        lines = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return [_row("requirements.txt", "error", "루트 설치 목록을 읽을 수 없습니다.")]
    rows = []
    for line in lines:
        requirement = line.split("#", 1)[0].strip()
        if not requirement:
            continue
        match = PINNED_REQUIREMENT.fullmatch(requirement)
        if match is None:
            rows.append(_row("requirements.txt", "error", "고정 버전 비교를 지원하지 않는 설치 항목이 있습니다."))
            continue
        package, expected = match.groups()
        try:
            installed = metadata.version(package)
        except metadata.PackageNotFoundError:
            rows.append(_row(f"package:{package}", "missing", f"미설치. 루트 요구 버전: {expected}"))
        except Exception:
            rows.append(_row(f"package:{package}", "error", "설치된 버전 정보를 확인할 수 없습니다."))
        else:
            # 배포판 메타데이터도 경로·임의 문자열을 출력하지 않도록 버전 형식만 허용한다.
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+!-]*", installed) is None:
                rows.append(_row(f"package:{package}", "error", "설치된 버전 정보의 형식을 확인해 주세요."))
            elif installed != expected:
                rows.append(_row(f"package:{package}", "mismatch", f"설치 {installed}, 루트 요구 {expected}"))
            else:
                rows.append(_row(f"package:{package}", "ok", f"루트 요구 버전 {expected}와 일치"))
    if not rows:
        rows.append(_row("requirements.txt", "error", "비교할 고정 버전 설치 항목이 없습니다."))
    return rows


def _environment_status() -> list[dict[str, str]]:
    """프로세스 우선으로 .env를 읽되 환경을 변경하거나 비밀값을 반환하지 않는다."""
    values = {}
    rows = []
    env_path = PROJECT_ROOT / ".env"
    if env_path.is_file():
        try:
            from dotenv import dotenv_values

            # dotenv 파싱 경고에도 원문이 섞이지 않도록 CLI 결과만 돌려준다.
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                values = dotenv_values(env_path, interpolate=False)
        except Exception:
            rows.append(_row(".env", "error", ".env를 읽을 수 없습니다. 프로세스 환경변수만 확인합니다."))
    for key in (*REQUIRED_ENV, *OPTIONAL_ENV):
        value = os.environ[key] if key in os.environ else values.get(key)
        present = isinstance(value, str) and bool(value.strip())
        if present:
            rows.append(_row(key, "ok", "설정값 있음. 값의 유효성·접속 가능 여부는 미검증."))
        elif key in REQUIRED_ENV:
            rows.append(_row(key, "missing", "실제 업무 함수 실행에 필요한 설정값이 없습니다."))
        else:
            rows.append(_row(key, "default", "설정값 없음. 공통 모듈의 기본값을 사용합니다."))
    return rows


def _function_status() -> list[dict[str, str]]:
    """지연 import와 callable 확인만 수행하며 어떤 업무 함수도 호출하지 않는다."""
    rows = []
    prior_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        for label, module_name, function_name in PUBLIC_FUNCTIONS:
            target = f"{module_name}.{function_name}"
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    module = importlib.import_module(module_name)
                    function = getattr(module, function_name, None)
            except ImportError:
                rows.append(_row(label, "missing", f"{target}: 모듈 또는 의존 패키지를 가져올 수 없습니다."))
            except Exception:
                rows.append(_row(label, "error", f"{target}: import 검사에 실패했습니다. 예외 원문은 출력하지 않습니다."))
            else:
                if callable(function):
                    rows.append(_row(label, "ok", f"{target}: 함수 import 가능. 실제 실행은 미검증."))
                else:
                    rows.append(_row(label, "missing", f"{target}: 호출 가능한 함수가 없습니다."))
    finally:
        sys.dont_write_bytecode = prior_bytecode
    return rows


def collect_backend_status() -> list[dict[str, str]]:
    """B24~B32: 로컬 준비 상태만 반환. ok는 실제 연결·인증·업무 성공을 뜻하지 않는다."""
    rows = [
        _row("인증", "pending", "임시 체험 인증을 사용합니다. 실제 로그인·세션 발급/폐기 함수가 필요합니다."),
        _row("보호된 업무 요청", "pending", "실제 서버 세션이 없으면 AUTH_BACKEND_PENDING으로 요청을 차단합니다."),
        _row("원본 다운로드", "pending", "권한을 재검증해 원본을 제공하는 공개 다운로드 함수가 아직 없습니다."),
    ]
    rows.extend(_package_status())
    rows.extend(_environment_status())
    rows.extend(_function_status())
    return rows


def print_backend_status() -> int:
    """CLI에 안전한 상태만 출력. 준비되지 않은 항목이 있으면 종료 코드 1을 반환한다."""
    rows = collect_backend_status()
    print("Hyundap 백엔드 연결 준비 점검")
    print("외부 접속·업무 함수 실행·DB 변경·파일 저장은 수행하지 않습니다.")
    for row in rows:
        print(f"[{row['status'].upper()}] {row['name']}: {row['message']}")
    print("이 결과는 import/설정 점검이며 실제 DB·인증·LLM 통합 검수가 아닙니다.")
    return int(any(row["status"] in {"pending", "missing", "mismatch", "error"} for row in rows))
