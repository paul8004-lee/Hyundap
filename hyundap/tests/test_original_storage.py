"""임시 파일만 사용하는 원본 복사·경로 검증. 실제 링크는 파일 시스템에서 확인한다."""

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hyundap.db import upload_service, source_files
from hyundap.db.errors import DbError


class OriginalStorageTests(unittest.TestCase):
    def test_copy_preserves_existing_file_and_cleans_partial_on_hash_conflict(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, destination = root / 'input.txt', root / 'saved.txt'
            source.write_bytes(b'original')
            digest = hashlib.sha256(b'original').hexdigest()
            upload_service.copy_original(source, destination, digest, 8)
            with self.assertRaises(FileExistsError):
                upload_service.copy_original(source, destination, digest, 8)
            self.assertEqual(destination.read_bytes(), b'original')
            with self.assertRaises(DbError) as caught:
                upload_service.copy_original(source, root / 'other.txt', 'a' * 64, 8)
            self.assertEqual(caught.exception.code, 'CONFLICT')
            self.assertFalse((root / 'other.txt').exists())
            self.assertEqual(list(root.glob('*.part')), [])

    def test_stored_paths_reject_outside_relative_and_link_escape(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'uploads'
            root.mkdir()
            outside = Path(directory) / 'outside.txt'
            outside.write_bytes(b'private')
            for source, upload_root in ((outside, root), (Path('relative'), root), (outside, Path('relative'))):
                with self.subTest(source=source), self.assertRaises(DbError) as caught:
                    upload_service.resolve_stored_source(source, upload_root)
                self.assertEqual(caught.exception.code, 'INVALID_STATE')
            link = root / 'link.txt'
            try:
                link.symlink_to(outside)
            except OSError:
                # Windows 링크 생성 권한이 없어도 resolve 후 경계 판정은 별도로 검사한다.
                original_resolve = Path.resolve
                def resolve(path, *args, **kwargs):
                    if path == link:
                        return outside
                    return original_resolve(path, *args, **kwargs)
                with patch.object(Path, 'resolve', resolve), self.assertRaises(DbError) as caught:
                    upload_service.resolve_stored_source(link, root)
            else:
                with self.assertRaises(DbError) as caught:
                    upload_service.resolve_stored_source(link, root)
            self.assertEqual(caught.exception.code, 'INVALID_STATE')

    def test_copy_permission_error_leaves_no_saved_or_partial_file(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, destination = root / 'input.txt', root / 'saved.txt'
            source.write_bytes(b'original')
            with patch.object(source_files.os, 'link', side_effect=PermissionError('synthetic')):
                with self.assertRaises(PermissionError):
                    upload_service.copy_original(source, destination, hashlib.sha256(b'original').hexdigest(), 8)
            self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob('*.part')), [])
