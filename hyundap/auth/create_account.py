"""auth/create_account.py — 초기 계정을 만드는 운영자 CLI (SPEC-AUTH-001 M3).

실행: 저장소 맨 위에서 python -m hyundap.auth.create_account --email <이메일> --role <역할> [--company-id <회사 ID>]
비밀번호는 기본으로 getpass로 두 번 입력받는다. 비대화형 실행에서만 비밀 저장소에서
export한 HYUNDAP_ACCOUNT_PASSWORD를 쓰며, 명령줄에 직접 적지 않는다. CLI는 읽은 즉시
이 환경변수를 지운다. 비밀번호는 명령 인자로 받지 않고 출력·로그에 남기지 않는다.
"""

import argparse
import getpass
import os
import sys

from hyundap.db.errors import DbError
from .accounts import create_account

PASSWORD_ENV = 'HYUNDAP_ACCOUNT_PASSWORD'


def build_parser():
    """이메일·역할·회사 ID만 받는 인자 파서를 반환한다. 비밀번호 옵션은 없다."""
    parser = argparse.ArgumentParser(prog='python -m hyundap.auth.create_account',
                                     description='초기 계정을 만든다. 비밀번호는 getpass로 입력한다.')
    parser.add_argument('--email', required=True)
    parser.add_argument('--role', required=True, help='platform_admin / company_admin / employee')
    parser.add_argument('--company-id', default=None, help='platform_admin 외 역할의 활성 회사 ID')
    return parser


def main(argv=None):
    """계정을 만들고 종료 코드를 반환한다. 성공 0, 등록 실패 1, 비밀번호 확인 불일치 2."""
    args = build_parser().parse_args(argv)
    # 환경변수는 읽는 즉시 지워 이후 자식 프로세스·로그에 남지 않게 한다.
    password = os.environ.pop(PASSWORD_ENV, None)
    if password is None:
        password = getpass.getpass('비밀번호: ')
        if getpass.getpass('비밀번호 확인: ') != password:
            print('비밀번호 확인이 일치하지 않습니다.', file=sys.stderr)
            return 2
    try:
        user_id = create_account(args.email, args.role, args.company_id, password)
    except DbError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f'created {user_id} {args.role}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
