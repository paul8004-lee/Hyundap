"""권한 있는 활성 원본을 검증된 임시 사본으로 준비한다. HTTP 전송은 UI가 담당한다."""

from pathlib import Path
from tempfile import TemporaryDirectory
from sqlalchemy import text

from hyundap.common.config import get_settings
from .documents_repo import get_document_detail
from .errors import DbError
from .runtime import transaction
from .source_files import copy_original, resolve_stored_source


def read_download_source(actor, document_id, version_id=None):
    """상세 조회 권한을 재사용하고 같은 회사 연결에서 원본 저장 정보를 읽는다."""
    detail = get_document_detail(actor, document_id, version_id=version_id)
    with transaction() as database:
        # 두 조회 사이에 활성 버전이나 권한이 바뀌면 이전 파일을 제공하지 않는다.
        get_document_detail(actor, document_id, version_id=detail['version_id'], connection=database)
        row = database.execute(text('''
            SELECT storage_path,sha256,file_size FROM document_versions
            WHERE id=:version AND document_id=:document AND company_id=:company
        '''), {'version': detail['version_id'], 'document': document_id,
               'company': detail['company_id']}).mappings().one_or_none()
        if row is None:
            raise DbError('INVALID_STATE')
        return dict(row, version_id=detail['version_id'], file_name=detail['file_name'])


def prepare_original_download(actor, document_id, version_id=None, *, upload_dir=None):
    """검증된 사본과 소유 TemporaryDirectory를 반환한다. 호출자가 반드시 cleanup한다.

    원본 경로를 공개하지 않는다. 복사 중 해시·크기를 확인하고 완료 후 권한·버전을
    다시 검사한다. 임시 사본 덕분에 전송 중 저장 원본 변경이 응답을 바꾸지 않는다.
    """
    record = read_download_source(actor, document_id, version_id)
    root = get_settings().upload_dir if upload_dir is None else Path(upload_dir)
    source = resolve_stored_source(record['storage_path'], root)
    temporary = TemporaryDirectory(prefix='hyundap-download-')
    try:
        # 고정된 파일명으로 경로·헤더 삽입을 막고 원본 이름은 HTTP 쪽에서 인용한다.
        target = Path(temporary.name) / 'original.bin'
        copy_original(source, target, record['sha256'], record['file_size'])
        current = read_download_source(actor, document_id, record['version_id'])
        if current != record:
            raise DbError('CONFLICT')
        return {'path': target, 'file_name': record['file_name'], 'temporary': temporary}
    except BaseException as error:
        temporary.cleanup()
        if isinstance(error, OSError):
            raise DbError('FILE_UNAVAILABLE') from None
        raise
