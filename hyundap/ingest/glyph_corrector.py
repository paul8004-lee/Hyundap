"""ingest/glyph_corrector.py — 로드한 문서의 깨진 글자를 사전(glyph_dictionary)으로 바로잡는다.

담당: 김혜리

함수:
    correct_text — 글자 하나를 사전대로 고치고, 고친 것과 사전에 없는 깨짐을 세어 돌려준다.
    correct_document — 각 섹션에 correct_text를 적용하고 결과를 문서 경고에 남긴다.

흐름: 사전에 있으면 교정 → 그래도 남은 깨짐(사전에 없음)은 고치지 않고 경고(unknown issue)로 기록.
정규화(normalizer)가 제어 문자를 지우기 전에 실행해야 깨진 글자가 원형 그대로 남아 있다."""

from collections import Counter

from hyundap.common.schemas import CommonDocument, Section

from .glyph_dictionary import CHAR_MAP, REGEX_RULES, UNKNOWN_PATTERNS

MAX_LISTED_TOKENS = 5     # 경고 문구에 풀어 쓰는 깨짐 종류 수
MAX_LISTED_LOCATIONS = 5  # 경고 문구에 풀어 쓰는 위치 수


def correct_text(text):
    """사전으로 글자를 고친다. (고친 글자, 교정 횟수 Counter, 사전에 없는 깨짐 Counter)를 돌려준다."""
    corrected = Counter()

    chars = []
    for ch in text:
        replacement = CHAR_MAP.get(ch)
        if replacement is None:
            chars.append(ch)
        else:
            chars.append(replacement)
            corrected[f"{_show(ch)}→{replacement}"] += 1
    text = "".join(chars)

    for name, pattern, replacement in REGEX_RULES:
        text, count = pattern.subn(replacement, text)
        if count:
            corrected[name] += count

    unknown = Counter()
    for _, pattern in UNKNOWN_PATTERNS:
        for match in pattern.finditer(text):
            unknown[_show(match.group())] += 1
    return text, corrected, unknown


def correct_document(doc):
    """모든 섹션의 깨진 글자를 사전으로 고친다. 교정·사전 밖 깨짐 건수는 문서 경고에 남긴다."""
    new_sections = []
    corrected_total = Counter()
    unknown_total = Counter()
    unknown_locations = []

    for section in doc.sections:
        text, corrected, unknown = correct_text(section.text)
        corrected_total.update(corrected)
        if unknown:
            unknown_total.update(unknown)
            unknown_locations.append(section.location_label)
        new_sections.append(
            Section(section_no=section.section_no, location_label=section.location_label, text=text)
        )

    warnings = list(doc.warnings)
    if corrected_total:
        warnings.append(f"깨진 글자 {sum(corrected_total.values())}건을 사전으로 교정함")
    if unknown_total:
        listed = ", ".join(f"{token} ×{n}" for token, n in unknown_total.most_common(MAX_LISTED_TOKENS))
        more = len(unknown_total) - MAX_LISTED_TOKENS
        if more > 0:
            listed += f" 외 {more}종"
        places = ", ".join(unknown_locations[:MAX_LISTED_LOCATIONS])
        if len(unknown_locations) > MAX_LISTED_LOCATIONS:
            places += f" 외 {len(unknown_locations) - MAX_LISTED_LOCATIONS}곳"
        warnings.append(
            f"사전에 없는 깨진 글자 {sum(unknown_total.values())}건 (unknown issue: {listed}) — {places}"
        )

    return CommonDocument(header=doc.header, sections=new_sections, warnings=warnings)


def _show(token):
    """경고 문구에 쓸 표기. 보이지 않거나 깨진 글자는 U+XXXX로 적는다."""
    if len(token) == 1 and (not token.isprintable() or ord(token) >= 0xE000):
        return f"U+{ord(token):04X}"
    return token
