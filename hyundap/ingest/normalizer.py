"""ingest/normalizer.py — 청킹 전 텍스트의 문자·공백 정리.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

함수:
    FUN-213 normalize_text — 문자 표현·제어 문자·불필요한 공백을 정리한다.
    FUN-214 normalize_document — 각 섹션의 텍스트를 정리하고 빈 섹션을 제외한다."""


import re
import unicodedata

from hyundap.common.schemas import CommonDocument, Section

LINE_BREAK_CHARS = ["\x0b", "\x0c", "\u2028", "\u2029"]
SPECIAL_SPACES = ["\u00a0", "\u202f", "\u3000", "\t"]


def normalize_text(text):
    """글자를 청킹 전에 정리한다."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    for ch in LINE_BREAK_CHARS:
        text = text.replace(ch, "\n")
    # 제어(Cc)·서식(Cf)·짝 없는 서로게이트(Cs) 제거. Cs가 남으면 UTF-8 저장·임베딩 호출에서 예외가 난다.
    text = "".join(
        ch for ch in text
        if ch in ("\n", "\t") or unicodedata.category(ch) not in ("Cc", "Cf", "Cs")
    )

    for ch in SPECIAL_SPACES:
        text = text.replace(ch, " ")

    text = re.sub(" {2,}", " ", text)
    text = re.sub(" +\n", "\n", text)
    text = re.sub("\n{3,}", "\n\n", text)

    return text


def normalize_document(doc):
    """모든 섹션의 글자를 정규화하고 비어 버린 섹션은 뺀다."""
    new_sections = []
    for section in doc.sections:
        text = normalize_text(section.text)
        if text.strip() == "":
            continue
        new_sections.append(
            Section(
                section_no=section.section_no,
                location_label=section.location_label,
                text=text,
            )
        )
    return CommonDocument(
        header=doc.header,
        sections=new_sections,
        warnings=list(doc.warnings),
    )