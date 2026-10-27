"""최종 스키마의 BIGINT 시간은 UTC Unix 초로 저장한다."""

import time


def epoch_now():
    """현재 UTC Unix 초를 정수로 반환한다. 설정으로 단위를 바꾸지 않는다."""
    return time.time_ns() // 1_000_000_000
