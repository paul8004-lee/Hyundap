"""B07/B16~B28: 화면 이벤트·선택 상태·담당 함수 연결.

Gradio별 세션 키로 화면 상태를 분리한다. 회사/문서/로그인이 바뀌면 이전
비동기 응답을 버려 다른 선택에 답변이 뒤늦게 덮이는 일을 막는다.
실제 검색·인증·업로드 처리는 services가 받은 백엔드 함수의 책임이다.
"""
from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass, field
from html import escape
from typing import Any

import gradio as gr

from .chat_page import render_answer, render_citation, render_compact_answer, render_scope_hint
from .admin_page import render_upload_result
from .components import show_feedback, render_header, render_login_error
from .search_page import document_rows, render_document, render_evidence, render_coverage
from .services import company_choices, UIServiceError


@dataclass
class ViewState:
    user: dict | None = None
    results: list[dict] = field(default_factory=list)
    selected: dict | None = None
    citations: list[dict] = field(default_factory=list)
    modal_citation: dict | None = None
    company: str = "__all__"
    doc_type: str = "__all__"
    query: str = ""
    scope: str = "선택 문서"
    epoch: int = 0
    requests: dict[str, int] = field(default_factory=dict)
    # 문서를 선택해도 현재 검색 질문의 답변은 유지한다.
    inline_generation: int = 0
    answer_citations: list[dict] = field(default_factory=list)


class StaleResponse(Exception):
    pass


class UIController:
    """각 브라우저의 상태와 화면 변경을 관리. 비밀값은 HTML로 반환하지 않는다."""

    def __init__(self, services, login, search, chat, admin, shell):
        self.service = services
        self.login, self.search, self.chat, self.admin, self.shell = login, search, chat, admin, shell
        self.states: dict[str, ViewState] = {}

    def state(self, key: str) -> ViewState:
        return self.states.setdefault(key, ViewState())

    def discard(self, key: str):
        ctx = self.states.pop(key, None)
        if ctx is not None:
            ctx.epoch += 1
            self.invalidate_inline(ctx)

    @staticmethod
    def optional_ref(refs, name):
        try:
            return refs[name]
        except KeyError:
            return None

    def chat_patch(self, **updates):
        """이전 질문 화면을 주입한 호출자만 해당 컴포넌트를 갱신한다."""
        patch = {}
        for name, value in updates.items():
            component = self.optional_ref(self.chat, name)
            if component is not None:
                patch[component] = value
        return patch

    def inline_patch(self, value="", *, visible=False):
        answer = self.optional_ref(self.search, "answer")
        return {answer: gr.update(value=value, visible=visible)} if answer is not None else {}

    @staticmethod
    def invalidate_inline(ctx):
        ctx.inline_generation += 1
        ctx.answer_citations = []

    @staticmethod
    def inline_current(ctx, generation):
        return ctx.inline_generation == generation and ctx.user is not None

    @staticmethod
    def answer_source(ctx, doc):
        """답변 출처를 현재 재검증한 문서·버전에만 연결한다."""
        document_id, version_id = doc.get("document_id"), doc.get("version_id")
        if document_id is None or version_id is None:
            return None
        return next((source for source in ctx.answer_citations
                     if isinstance(source, dict)
                     and source.get("document_id") == document_id
                     and source.get("version_id") == version_id), None)

    def selected_evidence(self, ctx, doc):
        source = self.answer_source(ctx, doc)
        if source is None:
            return render_evidence(doc)
        return render_evidence({**doc, "location_label": source.get("location_label"), "excerpt": source.get("excerpt")})

    @staticmethod
    def current(ctx, ticket):
        epoch, action, number = ticket
        return ctx.epoch == epoch and ctx.requests.get(action) == number

    async def wait(self, ctx, ticket, awaitable):
        result = await awaitable
        if not self.current(ctx, ticket):
            raise StaleResponse()
        return result

    def clear_chat(self):
        return {**self.chat_patch(answer="", question="", feedback="", citation_choice=gr.update(choices=[], value=None, visible=False), citation_detail=gr.update(value="", visible=False), open_source=gr.update(visible=False), submit=gr.update(interactive=True)), self.shell["modal"]: gr.update(visible=False)}

    def clear_document(self):
        s = self.search
        return {s["detail"]: render_document(None), s["evidence"]: "", s["summary"]: "선택한 문서를 요약해 주요 내용을 확인하세요.", s["coverage"]: "", s["detail_feedback"]: "", s["summarize"]: gr.update(interactive=False, value="요약하기"), s["source"]: gr.update(interactive=False), s["download"]: gr.update(interactive=False), s["download_file"]: gr.update(value=None, visible=False)}

    def clear_search(self):
        return {**self.clear_chat(), **self.clear_document(), **self.inline_patch(), **self.chat_patch(container=gr.update(visible=False)), self.search["table"]: [], self.search["results_area"]: gr.update(visible=False), self.search["empty"]: gr.update(visible=False), self.search["caption"]: "", self.search["feedback"]: "", self.search["intro"]: gr.update(visible=True), self.search["quick"]: gr.update(visible=True), self.search["submit"]: gr.update(interactive=True)}

    def signed_out(self, message=""):
        return {**self.clear_search(), self.login["container"]: gr.update(visible=True), self.login["password"]: "", self.login["feedback"]: show_feedback(message, "warning") if message else "", self.login["submit"]: gr.update(interactive=True), self.shell["container"]: gr.update(visible=False), self.shell["account"]: "", self.admin["file"]: None, self.admin["result"]: "", self.admin["feedback"]: "", self.admin["submit"]: gr.update(interactive=True), self.shell["modal_body"]: ""}

    def document_patch(self, ctx, doc):
        ctx.selected = deepcopy(doc)
        ctx.citations = []
        ctx.modal_citation = None
        s = self.search
        return {**self.clear_chat(), **self.clear_document(), **self.chat_patch(scope_hint=render_scope_hint(ctx.scope, doc.get("file_name")), container=gr.update(visible=True)), s["table"]: document_rows(ctx.results, doc.get("document_id")), s["detail"]: render_document(doc), s["evidence"]: self.selected_evidence(ctx, doc), s["summarize"]: gr.update(interactive=True), s["source"]: gr.update(interactive=True), s["download"]: gr.update(interactive=True)}

    def feedback_target(self, action):
        if action in {"login", "logout"}:
            return self.login["feedback"]
        if action == "upload":
            return self.admin["feedback"]
        if action in {"ask", "scope", "citation", "open_citation"}:
            target = self.optional_ref(self.chat, "feedback")
            return target if target is not None else self.search["feedback"]
        if action in {"select", "summarize", "download", "source"}:
            return self.search["detail_feedback"]
        return self.search["feedback"]

    def busy_patch(self, action, active):
        controls = {"login": self.login["submit"], "search": self.search["submit"], "ask": self.optional_ref(self.chat, "submit"), "summarize": self.search["summarize"], "upload": self.admin["submit"], "download": self.search["download"]}
        button = controls.get(action)
        patch = {button: gr.update(interactive=not active)} if button is not None else {}
        if active:
            labels = {"login": "로그인 정보를 확인하고 있어요.", "search": "문서를 검색하고 있어요.", "select": "문서 정보를 확인하고 있어요.", "ask": "문서에서 답변의 근거를 찾고 있어요.", "summarize": "선택한 문서를 요약하고 있어요.", "upload": "파일 정보를 확인하고 있어요.", "download": "파일을 준비하고 있어요."}
            if action in labels:
                patch[self.feedback_target(action)] = show_feedback(labels[action], "loading")
        return patch

    async def run(self, action: str, key: str, *args):
        """B07/B18/B20~B23: 중복 제출·늦은 응답·세션 만료를 공통 처리."""
        ctx = self.state(key)
        if action in {"login", "logout", "search", "filter", "reset", "nav", "open_citation"}:
            self.invalidate_inline(ctx)
        inline_generation = ctx.inline_generation
        if action in {"login", "logout", "search", "select", "filter", "reset", "nav", "open_citation"}:
            ctx.epoch += 1
        ctx.requests[action] = ctx.requests.get(action, 0) + 1
        ticket = ctx.epoch, action, ctx.requests[action]
        initial = self.busy_patch(action, True)
        if action in {"search", "filter", "reset"}:
            ctx.results, ctx.selected, ctx.citations, ctx.modal_citation = [], None, [], None
            initial = {**self.clear_search(), **initial}
        elif action in {"select", "open_citation"}:
            ctx.selected = None
            initial = {**self.clear_chat(), **self.clear_document(), **initial}
        elif action == "ask":
            ctx.citations = []
            initial = {**self.clear_chat(), **initial}
            # 질문 입력값은 실패했을 때에도 유지한다.
            initial.pop(self.chat["question"], None)
        elif action == "upload":
            initial[self.admin["result"]] = ""
        elif action == "download":
            initial[self.search["download_file"]] = gr.update(value=None, visible=False)
        elif action == "summarize":
            initial[self.search["summary"]] = ""
            initial[self.search["coverage"]] = ""
        if initial:
            yield initial
        try:
            result = await getattr(self, f"do_{action}")(ctx, ticket, *args)
            if self.current(ctx, ticket):
                yield {**self.busy_patch(action, False), **result}
                # 검색 결과를 먼저 보여 주고 별도의 AI 요청을 이어 간다.
                # 행 선택은 이 검색 질문의 답변을 취소하지 않는다.
                if action == "search":
                    async for patch in self.run_inline_answer(ctx, inline_generation):
                        yield patch
        except StaleResponse:
            return
        except Exception as error:
            if not self.current(ctx, ticket):
                return
            if not isinstance(error, UIServiceError):
                logging.getLogger(__name__).exception("UI action failed: %s", action)
                error = UIServiceError("UI_ERROR", "화면 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.")
            if error.code in {"SESSION_EXPIRED", "UNAUTHENTICATED", "AUTH_REQUIRED"} or error.status == "session_expired":
                ctx.epoch += 1
                self.invalidate_inline(ctx)
                ctx.user, ctx.results, ctx.selected, ctx.citations = None, [], None, []
                yield self.signed_out("로그인 시간이 만료되었습니다. 다시 로그인해 주세요.")
            else:
                feedback = render_login_error(error.message, error.code) if action == "login" else show_feedback(error.message, "error")
                yield {**self.busy_patch(action, False), self.feedback_target(action): feedback}

    async def run_inline_answer(self, ctx, generation):
        """검색한 문서 안에서 답변을 만들고 기존 상세 영역에 근거를 연결한다."""
        if not self.inline_current(ctx, generation) or not ctx.results or not ctx.selected:
            return
        if self.optional_ref(self.search, "answer") is None:
            return
        companies, types = self.filters(ctx)
        document_ids = list(dict.fromkeys(doc["document_id"] for doc in ctx.results if doc.get("document_id") is not None))
        if not document_ids:
            return
        user, query = deepcopy(ctx.user), ctx.query
        yield self.inline_patch(show_feedback("짧은 답변을 준비하고 있어요.", "loading"), visible=True)
        try:
            response = await self.service.ask(user, query, companies, types, document_ids)
            if not self.inline_current(ctx, generation):
                return
            citations = response.get("citations") or []
            ctx.answer_citations = deepcopy(citations) if isinstance(citations, list) else []
            content = render_compact_answer(response) or show_feedback("답변할 근거를 찾지 못했어요.", "info")
            patch = self.inline_patch(content, visible=True)
            if ctx.selected:
                patch[self.search["evidence"]] = self.selected_evidence(ctx, ctx.selected)
            yield patch
        except Exception as error:
            if not self.inline_current(ctx, generation):
                return
            ctx.answer_citations = []
            if not isinstance(error, UIServiceError):
                logging.getLogger(__name__).exception("Inline UI answer failed")
                error = UIServiceError("UI_ERROR", "답변을 준비하지 못했어요. 잠시 후 다시 검색해 주세요.")
            if error.code in {"SESSION_EXPIRED", "UNAUTHENTICATED", "AUTH_REQUIRED"} or error.status == "session_expired":
                ctx.epoch += 1
                self.invalidate_inline(ctx)
                ctx.user, ctx.results, ctx.selected, ctx.citations, ctx.modal_citation = None, [], None, [], None
                yield self.signed_out("로그인 시간이 만료되었습니다. 다시 로그인해 주세요.")
            else:
                yield self.inline_patch(show_feedback(error.message, "error"), visible=True)

    async def checked(self, ctx, ticket):
        checked = await self.wait(ctx, ticket, self.service.validate_session(ctx.user))
        ctx.user = checked
        return checked

    async def do_login(self, ctx, ticket, username, password, role):
        user = await self.wait(ctx, ticket, self.service.login(username, password))
        is_admin = user.get("role") in {"company_admin", "platform_admin"}
        if (role == "관리자") != is_admin:
            await self.service.logout(user)
            raise UIServiceError("LOGIN_ROLE", "계정의 사용자 유형에 맞는 로그인 탭을 선택해 주세요.", "input_error")
        if user.get("must_change_password"):
            await self.service.logout(user)
            raise UIServiceError("PASSWORD_CHANGE_REQUIRED", "비밀번호 변경이 필요한 계정입니다. 인증 담당에게 변경 절차를 요청해 주세요.")
        ctx.user = user
        ctx.results, ctx.selected, ctx.citations = [], None, []
        ctx.query, ctx.doc_type, ctx.scope = "", "__all__", "선택 문서"
        companies = company_choices(user, demo=self.service.demo or getattr(self.service, "temporary_auth", False))
        ctx.company = "__all__" if user.get("role") == "platform_admin" else user.get("company_id")
        upload_company = user.get("company_id") or (companies[0][1] if companies else None)
        choices = [("전체 회사", "__all__"), *companies] if user.get("role") == "platform_admin" else companies
        return {**self.clear_search(), **self.chat_patch(scope="선택 문서"), self.login["container"]: gr.update(visible=False), self.login["password"]: "", self.login["feedback"]: "", self.shell["container"]: gr.update(visible=True), self.shell["account"]: render_header(user), self.shell["upload_nav"]: gr.update(visible=is_admin), self.search["container"]: gr.update(visible=True), self.admin["container"]: gr.update(visible=False), self.search["keyword"]: "", self.search["company"]: gr.update(choices=choices, value=ctx.company, interactive=user.get("role") == "platform_admin"), self.search["doc_type"]: "__all__", self.admin["company"]: gr.update(choices=companies, value=upload_company, interactive=user.get("role") == "platform_admin"), self.admin["file"]: None, self.admin["result"]: "", self.admin["feedback"]: "", self.shell["nav_search"]: gr.update(variant="primary"), self.shell["upload_nav"]: gr.update(visible=is_admin, variant="secondary")}

    async def do_logout(self, ctx, ticket):
        previous = ctx.user
        ctx.user, ctx.results, ctx.selected, ctx.citations, ctx.modal_citation = None, [], None, [], None
        message = ""
        if previous:
            try:
                await self.wait(ctx, ticket, self.service.logout(previous))
            except UIServiceError:
                message = "이 화면에서는 로그아웃했습니다. 서버 세션 종료 여부는 인증 담당에게 확인해 주세요."
        return self.signed_out(message)

    @staticmethod
    def filters(ctx):
        companies = None if ctx.company == "__all__" else [ctx.company]
        types = None if ctx.doc_type == "__all__" else [ctx.doc_type]
        return companies, types

    async def do_filter(self, ctx, ticket, company, doc_type):
        await self.checked(ctx, ticket)
        ctx.company, ctx.doc_type = company, doc_type
        ctx.query = ""
        return {self.search["feedback"]: show_feedback("검색 범위가 바뀌었어요. 검색 버튼을 눌러 다시 조회해 주세요.", "info"), **self.chat_patch(scope_hint=render_scope_hint(ctx.scope))}

    async def do_reset(self, ctx, ticket):
        user = await self.checked(ctx, ticket)
        ctx.company = "__all__" if user.get("role") == "platform_admin" else user.get("company_id")
        ctx.doc_type, ctx.query = "__all__", ""
        return {self.search["company"]: ctx.company, self.search["doc_type"]: "__all__", self.search["keyword"]: ""}

    async def do_search(self, ctx, ticket, query, company, doc_type):
        ctx.company, ctx.doc_type, ctx.query = company, doc_type, (query or "").strip()
        companies, types = self.filters(ctx)
        response = await self.wait(ctx, ticket, self.service.search(ctx.user, ctx.query, companies, types))
        docs = response.get("results") or []
        ctx.results = deepcopy(docs)
        patch = {**self.chat_patch(container=gr.update(visible=True), scope_hint=render_scope_hint(ctx.scope)), self.search["intro"]: gr.update(visible=False), self.search["quick"]: gr.update(visible=False), self.search["table"]: document_rows(docs), self.search["feedback"]: "" if docs else show_feedback(f'‘{ctx.query}’에 해당하는 문서가 없어요. 검색어나 필터를 바꿔 주세요.', "info"), self.search["results_area"]: gr.update(visible=bool(docs)), self.search["empty"]: gr.update(visible=not docs, value='<h3>검색 결과가 없습니다</h3><p>검색어를 짧게 입력하거나 문서 유형을 바꿔 보세요.</p>'), self.search["caption"]: f'받은 문서 {len(docs)}건 · 목록에서 문서를 선택하세요.'}
        if docs:
            try:
                doc = await self.wait(ctx, ticket, self.service.detail(ctx.user, docs[0]["document_id"], docs[0].get("version_id")))
                patch.update(self.document_patch(ctx, doc))
            except UIServiceError as error:
                if error.status == "session_expired" or error.code in {"SESSION_EXPIRED", "UNAUTHENTICATED", "AUTH_REQUIRED"}:
                    raise
                patch[self.search["detail_feedback"]] = show_feedback(error.message, "error")
        return patch

    async def do_select(self, ctx, ticket, index):
        if not isinstance(index, int) or not 0 <= index < len(ctx.results):
            raise UIServiceError("SELECTION_REQUIRED", "검색 결과에서 문서를 선택해 주세요.", "input_error")
        entry = ctx.results[index]
        doc = await self.wait(ctx, ticket, self.service.detail(ctx.user, entry["document_id"], entry.get("version_id")))
        return {**self.document_patch(ctx, doc), self.search["feedback"]: ""}

    async def do_summarize(self, ctx, ticket):
        if not ctx.selected:
            raise UIServiceError("SELECTION_REQUIRED", "먼저 문서를 선택해 주세요.", "input_error")
        doc = deepcopy(ctx.selected)
        response = await self.wait(ctx, ticket, self.service.summarize(ctx.user, doc["document_id"], doc.get("version_id")))
        return {self.search["summary"]: str(response.get("summary") or "요약 내용을 제공받지 못했습니다."), self.search["coverage"]: render_coverage(response), self.search["detail_feedback"]: "", self.search["summarize"]: gr.update(value="다시 요약", interactive=True)}

    async def do_scope(self, ctx, ticket, scope):
        # 진행 중인 질문만 무효화. 문서 요약에는 영향을 주지 않는다.
        ctx.requests["ask"] = ctx.requests.get("ask", 0) + 1
        ctx.scope = scope
        ctx.citations = []
        return {**self.clear_chat(), self.chat["scope_hint"]: render_scope_hint(scope, (ctx.selected or {}).get("file_name"))}

    async def do_ask(self, ctx, ticket, question, scope):
        ctx.scope = scope
        companies, types = self.filters(ctx)
        if scope == "선택 문서" and not ctx.selected:
            raise UIServiceError("SELECTION_REQUIRED", "검색 결과에서 문서를 선택하거나 질문 범위를 바꿔 주세요.", "input_error")
        doc_ids = [ctx.selected["document_id"]] if scope == "선택 문서" else None
        response = await self.wait(ctx, ticket, self.service.ask(ctx.user, question, companies, types, doc_ids))
        ctx.citations = deepcopy(response.get("citations") or [])
        choices = [(f'[{source.get("number", index + 1)}] {source.get("file_name", "문서")} · {source.get("location_label", "")}', str(index)) for index, source in enumerate(ctx.citations)]
        return {self.chat["answer"]: render_answer(response), self.chat["feedback"]: show_feedback(response.get("message") or "답변의 출처를 확인해 주세요.", "info") if ctx.citations else show_feedback(response.get("message") or "답변을 뒷받침하는 근거가 없습니다.", "warning"), self.chat["citation_choice"]: gr.update(choices=choices, value="0" if choices else None, visible=bool(choices)), self.chat["citation_detail"]: gr.update(value=render_citation(ctx.citations[0]) if choices else "", visible=bool(choices)), self.chat["open_source"]: gr.update(visible=bool(choices))}

    def citation_at(self, ctx, index):
        try:
            return ctx.citations[int(index)] if int(index) >= 0 else None
        except (TypeError, ValueError, IndexError):
            return None

    async def do_citation(self, ctx, ticket, index):
        citation = self.citation_at(ctx, index)
        return {self.chat["citation_detail"]: gr.update(value=render_citation(citation), visible=bool(citation)), self.chat["open_source"]: gr.update(visible=bool(citation))}

    async def do_source(self, ctx, ticket):
        if not ctx.selected:
            raise UIServiceError("SELECTION_REQUIRED", "먼저 문서를 선택해 주세요.", "input_error")
        doc = await self.wait(ctx, ticket, self.service.detail(ctx.user, ctx.selected["document_id"], ctx.selected.get("version_id")))
        citation = self.answer_source(ctx, doc) or (doc.get("citations") or [doc])[0]
        ctx.modal_citation = deepcopy(citation)
        return {self.shell["modal"]: gr.update(visible=True), self.shell["modal_body"]: render_citation(citation), self.search["evidence"]: self.selected_evidence(ctx, doc), self.search["detail_feedback"]: ""}

    async def do_open_citation(self, ctx, ticket, index):
        source = self.citation_at(ctx, index)
        if not source:
            raise UIServiceError("SOURCE_REQUIRED", "확인할 출처를 선택해 주세요.", "input_error")
        doc = await self.wait(ctx, ticket, self.service.detail(ctx.user, source["document_id"], source.get("version_id")))
        # 검색 범위 밖의 출처를 열 때 화면의 필터도 해당 문서로 맞춘다.
        ctx.company, ctx.doc_type = doc["company_id"], doc.get("doc_type", "__all__")
        ctx.results, ctx.query = [doc], ""
        patch = self.document_patch(ctx, doc)
        return {**patch, self.search["results_area"]: gr.update(visible=True), self.search["company"]: ctx.company, self.search["doc_type"]: ctx.doc_type, self.search["keyword"]: "", self.search["caption"]: "출처에서 연 문서 1건", self.search["feedback"]: show_feedback("답변에 사용된 출처의 문서·버전을 확인했어요.", "info"), self.search["intro"]: gr.update(visible=False), self.search["quick"]: gr.update(visible=False), self.search["empty"]: gr.update(visible=False)}

    async def do_download(self, ctx, ticket):
        if not ctx.selected:
            raise UIServiceError("SELECTION_REQUIRED", "먼저 문서를 선택해 주세요.", "input_error")
        path = await self.wait(ctx, ticket, self.service.download(ctx.user, ctx.selected["document_id"], ctx.selected.get("version_id")))
        return {self.search["download_file"]: gr.update(value=path, visible=True), self.search["detail_feedback"]: show_feedback("예시 텍스트 파일입니다. 실제 원문 다운로드는 담당 함수 연결 후 사용할 수 있어요." if self.service.demo else "파일이 준비되었습니다. 아래 파일을 눌러 저장하세요.", "info")}

    async def do_nav(self, ctx, ticket, destination):
        user = await self.checked(ctx, ticket)
        upload = destination == "upload"
        if upload and user.get("role") not in {"company_admin", "platform_admin"}:
            raise UIServiceError("FORBIDDEN", "관리자만 파일을 업로드할 수 있습니다.", "denied")
        return {**self.clear_chat(), **self.inline_patch(), **self.chat_patch(container=gr.update(visible=not upload and bool(ctx.results))), self.search["container"]: gr.update(visible=not upload), self.admin["container"]: gr.update(visible=upload), self.shell["nav_search"]: gr.update(variant="secondary" if upload else "primary"), self.shell["upload_nav"]: gr.update(variant="primary" if upload else "secondary"), self.search["submit"]: gr.update(interactive=True), self.search["summarize"]: gr.update(interactive=bool(ctx.selected)), self.search["download"]: gr.update(interactive=bool(ctx.selected)), self.admin["submit"]: gr.update(interactive=True)}

    async def do_upload(self, ctx, ticket, file_path, company, doc_type):
        result = await self.wait(ctx, ticket, self.service.upload(ctx.user, file_path, company, doc_type))
        return {self.admin["result"]: render_upload_result({**result, "demo": self.service.demo}), self.admin["feedback"]: show_feedback("입력 확인이 끝났어요. 예시 모드에서는 파일을 저장하거나 색인하지 않습니다." if self.service.demo else result.get("message") or "업로드 요청 결과를 확인해 주세요.", "info")}

    async def do_close(self, ctx, ticket):
        ctx.modal_citation = None
        return {self.shell["modal"]: gr.update(visible=False), self.shell["modal_body"]: ""}
