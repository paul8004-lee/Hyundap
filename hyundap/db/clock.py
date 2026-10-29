"""최종 스키마의 BIGINT 시간은 UTC Unix 초로 저장한다."""

import time


def epoch_now():
    """현재 UTC Unix 초를 정수로 반환한다. 설정으로 단위를 바꾸지 않는다."""
    # 나노초를 10억으로 나누어 스키마에서 사용하는 정수 초로 바꾼다.
    return time.time_ns() // 1_000_000_000
