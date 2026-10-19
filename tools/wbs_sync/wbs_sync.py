"""Sync GitHub issues and milestones from the team WBS spreadsheet.

The WBS (.xlsx) is the single source of truth. Each WBS task row becomes one
issue, matched by its task ID through a hidden marker in the issue body.
The default run is a dry run that only prints the planned changes; pass
--apply to write them to GitHub.

Usage (from the repository root):
    python tools/wbs_sync/wbs_sync.py            # preview
    python tools/wbs_sync/wbs_sync.py --apply    # apply
    python tools/wbs_sync/wbs_sync.py --file "path/to/WBS.xlsx" --apply
"""
import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import openpyxl
except ImportError:
    sys.exit("openpyxl 이 필요합니다. 먼저 'pip install openpyxl' 을 실행하세요.")

TOOL_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOL_DIR.parent.parent
MARKER = "<!-- wbs-sync id={} -->"
MARKER_RE = re.compile(r"<!-- wbs-sync id=([\d.]+) -->")
TASK_ID_RE = re.compile(r"^\d+\.\d+\.\d+\.$")
STEP_ID_RE = re.compile(r"^\d+\.\d+\.0\.$")
PROC_RE = re.compile(r"^\d+\.0\.0\.")
WEEKDAY = "월화수목금토일"
LABEL_COLORS = {"type": "BFDADC", "shared": "C2E0C6", "unassigned": "EDEDED", "in_progress": "FBCA04"}


# ---------- GitHub helpers ----------

def gh_bin():
    found = shutil.which("gh")
    if found:
        return found
    default = Path(r"C:\Program Files\GitHub CLI\gh.exe")
    if default.exists():
        return str(default)
    sys.exit("GitHub CLI(gh)를 찾을 수 없습니다. 설치 후 'gh auth login' 을 실행하세요.")


def gh(args, payload=None, check=True):
    proc = subprocess.run([gh_bin()] + args, input=json.dumps(payload) if payload is not None else None,
                          capture_output=True, text=True, encoding="utf-8")
    if check and proc.returncode != 0:
        sys.exit(f"gh 명령 실패: gh {' '.join(args[:4])}\n{proc.stderr.strip()}")
    return proc


def gh_json(args):
    out = gh(args).stdout.strip()
    return json.loads(out) if out else None


def gh_list(path):
    pages = gh_json(["api", "--paginate", "--slurp", path])
    return [item for page in pages for item in page]


# ---------- WBS parsing ----------

def to_date(value):
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return None


def clean(value):
    if value is None:
        return ""
    return str(value).strip()


def read_wbs(path):
    ws = openpyxl.load_workbook(path, data_only=True).active
    rows = [list(r) for r in ws.iter_rows(values_only=True)]

    header_idx = next(i for i, r in enumerate(rows) if clean(r[0]) == "Procedures")
    header, sub = rows[header_idx], rows[header_idx + 1]
    col = {clean(v): i for i, v in enumerate(header) if clean(v)}
    id_col = col["Tasks"]
    type_cols = {clean(sub[i]): i for i in range(len(sub)) if clean(sub[i]) in ("로직", "화면", "문서")}
    c_assignee, c_start, c_status, c_note = col["담당자"], col["schedule"], col["상태"], col["산출물/비고"]

    # Day N -> date, from the row holding "Day 1" and the date row right below it.
    day_row = next(i for i, r in enumerate(rows) if any(clean(v) == "Day 1" for v in r))
    days = {}
    for c, label in enumerate(rows[day_row]):
        m = re.fullmatch(r"Day (\d+)", clean(label))
        if m and to_date(rows[day_row + 1][c]):
            days[int(m.group(1))] = to_date(rows[day_row + 1][c])

    tasks, proc, step = [], "", ""
    for r in rows[header_idx + 2:]:
        if PROC_RE.match(clean(r[0])):
            proc = clean(r[0])
        step_cell = clean(r[id_col - 2]) if id_col >= 2 else ""
        if STEP_ID_RE.match(step_cell):
            step = (step_cell, clean(r[id_col - 1]))
            continue
        task_id = clean(r[id_col])
        if not TASK_ID_RE.match(task_id):
            continue
        tasks.append({
            "id": task_id.rstrip("."),
            "name": clean(r[id_col + 1]),
            "proc": proc,
            "step_id": step[0] if step else "",
            "step_name": step[1] if step else "",
            "types": [t for t, c in type_cols.items() if clean(r[c])],
            "assignee": clean(r[c_assignee]),
            "start": to_date(r[c_start]),
            "end": to_date(r[c_start + 1]),
            "status": clean(r[c_status]),
            "note": clean(r[c_note]),
        })
    return tasks, days


# ---------- desired state ----------

def fmt_day(d):
    return f"{d.isoformat()} ({WEEKDAY[d.weekday()]})" if d else "-"


def milestone_title(n, d):
    return f"Day {n} · {d.month}/{d.day:02d}"


def build_issue(task, cfg, days, assignable):
    lab = cfg["labels"]
    labels = {lab["type_prefix"] + t for t in task["types"]}
    area = cfg["step_area"].get(task["step_id"])
    if area:
        labels.add(area)

    assignees, shown = [], []
    names = [n.strip() for n in re.split(r"[,/]", task["assignee"]) if n.strip()]
    if not names:
        labels.add(lab["unassigned"])
        shown.append("(빈칸)")
    for name in names:
        login = cfg["members"].get(name)
        if name == cfg["shared_assignee"]:
            labels.add(lab["shared"])
            shown.append(name)
        elif login and assignable(login):
            assignees.append(login)
            shown.append(f"{name} (@{login})")
        elif login:
            labels.add(lab["unassigned"])
            shown.append(f"{name} (@{login} 초대 수락 전이라 담당자 미지정)")
        else:
            labels.add(lab["unassigned"])
            shown.append(name)

    status = task["status"]
    if status == cfg["status"]["in_progress"]:
        labels.add(lab["in_progress"])
    closed = status == cfg["status"]["done"]

    day_no = next((n for n, d in days.items() if d == task["end"]), None)
    schedule = f"{fmt_day(task['start'])} ~ {fmt_day(task['end'])}"
    if day_no:
        schedule += f" · Day {day_no}"

    table = [
        ("작업 ID", task["id"]),
        ("단계", f"{task['proc']} › {task['step_id']} {task['step_name']}".strip()),
        ("작업명", task["name"]),
        ("유형", ", ".join(task["types"]) or "-"),
        ("담당자", ", ".join(shown)),
        ("일정", schedule),
        ("상태", status or "-"),
        ("산출물/비고", task["note"] or "-"),
    ]
    body = (MARKER.format(task["id"]) + "\n"
            "> 이 이슈는 WBS 엑셀을 기준으로 자동 생성·갱신됩니다. 내용을 바꾸려면 WBS를 수정한 뒤 "
            "`tools/wbs_sync`로 동기화하세요. 이슈에서 직접 고친 제목·본문·라벨·담당자·마일스톤은 "
            "다음 동기화 때 덮어써집니다.\n\n"
            "| 항목 | 내용 |\n|---|---|\n"
            + "\n".join(f"| {k} | {str(v).replace('|', '/')} |" for k, v in table) + "\n")
    return {
        "id": task["id"],
        "title": f"[{task['id']}] {task['name']}",
        "body": body,
        "labels": labels,
        "assignees": sorted(assignees),
        "milestone": milestone_title(day_no, days[day_no]) if day_no else None,
        "closed": closed,
    }


def is_managed_label(name, cfg):
    lab = cfg["labels"]
    return (name.startswith(lab["type_prefix"]) or name.startswith("area:")
            or name in (lab["shared"], lab["unassigned"], lab["in_progress"]))


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description="WBS 엑셀 → GitHub 이슈 동기화")
    ap.add_argument("--file", help="WBS 엑셀 파일 경로 (기본: 레포 폴더에서 가장 최근 *WBS*.xlsx)")
    ap.add_argument("--apply", action="store_true", help="미리보기 대신 실제로 GitHub에 반영")
    args = ap.parse_args()

    cfg = json.loads((TOOL_DIR / "config.json").read_text(encoding="utf-8"))
    repo = cfg["repo"]
    if args.file:
        wbs_path = Path(args.file)
    else:
        found = sorted(REPO_ROOT.glob(cfg["wbs_glob"]), key=lambda p: p.stat().st_mtime)
        if not found:
            sys.exit(f"레포 폴더에서 '{cfg['wbs_glob']}' 파일을 찾지 못했습니다. --file 로 경로를 지정하세요.")
        wbs_path = found[-1]
    print(f"WBS 파일: {wbs_path.name}")

    tasks, days = read_wbs(wbs_path)
    ids = [t["id"] for t in tasks]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        sys.exit(f"WBS에 같은 작업 ID가 여러 번 있습니다: {', '.join(dupes)}")
    print(f"WBS 작업 {len(tasks)}개, 일정 Day {min(days)}~{max(days)} "
          f"({days[min(days)].isoformat()} ~ {days[max(days)].isoformat()})")

    cache = {}

    def assignable(login):
        if login not in cache:
            cache[login] = gh(["api", "--silent", f"repos/{repo}/assignees/{login}"], check=False).returncode == 0
        return cache[login]

    desired = {t["id"]: build_issue(t, cfg, days, assignable) for t in tasks}
    for t in tasks:
        if not desired[t["id"]]["milestone"]:
            print(f"  주의: [{t['id']}] 종료일 {t['end']} 이 WBS의 Day 날짜에 없어 마일스톤을 지정하지 않습니다.")
    pending = sorted(login for login, ok in cache.items() if not ok)
    if pending:
        print(f"  주의: 담당자로 지정할 수 없는 계정(초대 미수락 등): {', '.join(pending)}")

    plan = []  # (description, callable)

    # labels
    existing_labels = {l["name"] for l in gh_list(f"repos/{repo}/labels?per_page=100")}
    lab = cfg["labels"]
    needed = {lab["shared"]: "shared", lab["unassigned"]: "unassigned", lab["in_progress"]: "in_progress"}
    for d in desired.values():
        for name in d["labels"]:
            if name.startswith(lab["type_prefix"]):
                needed[name] = "type"
    for d in desired.values():
        for name in d["labels"]:
            if name.startswith("area:") and name not in existing_labels:
                print(f"  주의: 라벨 '{name}' 이 레포에 없습니다. config.json 의 step_area 를 확인하세요.")
    for name, kind in needed.items():
        if name not in existing_labels:
            plan.append((f"라벨 생성: {name}",
                         lambda n=name, k=kind: gh(["api", "-X", "POST", f"repos/{repo}/labels", "--input", "-"],
                                                   {"name": n, "color": LABEL_COLORS[k]})))

    # milestones
    ms_list = gh_list(f"repos/{repo}/milestones?state=all&per_page=100")
    ms_by_day = {}
    for m in ms_list:
        mm = re.match(r"Day (\d+) ·", m["title"])
        if mm:
            ms_by_day.setdefault(int(mm.group(1)), m)
    ms_number = {}
    for n, d in sorted(days.items()):
        title, due = milestone_title(n, d), f"{d.isoformat()}T14:59:59Z"
        cur = ms_by_day.get(n)
        if cur is None:
            plan.append((f"마일스톤 생성: {title}",
                         lambda t=title, du=due: gh(["api", "-X", "POST", f"repos/{repo}/milestones", "--input", "-"],
                                                    {"title": t, "due_on": du})))
        else:
            ms_number[title] = cur["number"]
            changes = []
            if cur["title"] != title:
                changes.append(f"이름 '{cur['title']}' → '{title}'")
            if (cur.get("due_on") or "")[:10] != d.isoformat():
                changes.append(f"마감일 {(cur.get('due_on') or '-')[:10]} → {d.isoformat()}")
            if changes:
                plan.append((f"마일스톤 수정 (Day {n}): " + ", ".join(changes),
                             lambda num=cur["number"], t=title, du=due: gh(
                                 ["api", "-X", "PATCH", f"repos/{repo}/milestones/{num}", "--input", "-"],
                                 {"title": t, "due_on": du})))

    def resolve_milestones():
        for m in gh_list(f"repos/{repo}/milestones?state=all&per_page=100"):
            ms_number[m["title"]] = m["number"]

    plan.append((None, resolve_milestones))

    # issues
    issues = [i for i in gh_list(f"repos/{repo}/issues?state=all&per_page=100") if "pull_request" not in i]
    by_id = {}
    for i in sorted(issues, key=lambda x: x["number"]):
        m = MARKER_RE.search(i.get("body") or "")
        if m:
            if m.group(1) in by_id:
                print(f"  주의: 작업 {m.group(1)} 에 연결된 이슈가 여러 개입니다. #{by_id[m.group(1)]['number']} 만 관리합니다.")
                continue
            by_id[m.group(1)] = i

    counts = {"생성": 0, "수정": 0, "닫기(삭제된 작업)": 0, "변경 없음": 0}
    for tid, d in desired.items():
        cur = by_id.get(tid)
        if cur is None:
            counts["생성"] += 1
            state = " (done → 닫힘)" if d["closed"] else ""
            plan.append((f"이슈 생성: {d['title']}{state}", lambda d=d: create_issue(repo, d, ms_number)))
            continue
        diff = issue_diff(cur, d, cfg)
        if diff:
            counts["수정"] += 1
            plan.append((f"이슈 수정 #{cur['number']} {d['title']}: {', '.join(diff)}",
                         lambda c=cur, d=d: update_issue(repo, c, d, cfg, ms_number)))
        else:
            counts["변경 없음"] += 1

    for tid, cur in by_id.items():
        if tid not in desired and cur["state"] == "open":
            counts["닫기(삭제된 작업)"] += 1
            plan.append((f"이슈 닫기 #{cur['number']} {cur['title']} (WBS에서 삭제된 작업)",
                         lambda c=cur: close_removed(repo, c)))

    print()
    steps = [p for p in plan if p[0]]
    if not steps:
        print("변경할 내용이 없습니다. GitHub가 WBS와 같은 상태입니다.")
        return
    print("== 반영할 변경 ==" if args.apply else "== 미리보기 (아직 아무것도 바뀌지 않았습니다) ==")
    for desc, _ in steps:
        print(" -", desc)
    print("\n요약: " + ", ".join(f"{k} {v}" for k, v in counts.items()))

    if not args.apply:
        print("\n실제로 반영하려면 --apply 를 붙여 다시 실행하세요.")
        return
    for desc, fn in plan:
        fn()
        if desc:
            print("완료:", desc)


def issue_diff(cur, d, cfg):
    diff = []
    if cur["title"] != d["title"]:
        diff.append("제목")
    if (cur.get("body") or "").replace("\r\n", "\n") != d["body"]:
        diff.append("본문")
    cur_managed = {l["name"] for l in cur["labels"] if is_managed_label(l["name"], cfg)}
    if cur_managed != d["labels"]:
        diff.append("라벨")
    if sorted(a["login"] for a in cur["assignees"]) != d["assignees"]:
        diff.append("담당자")
    if (cur.get("milestone") or {}).get("title") != d["milestone"]:
        diff.append("마일스톤")
    if (cur["state"] == "closed") != d["closed"]:
        diff.append("닫기(done)" if d["closed"] else "다시 열기")
    return diff


def create_issue(repo, d, ms_number):
    payload = {"title": d["title"], "body": d["body"], "labels": sorted(d["labels"]), "assignees": d["assignees"]}
    if d["milestone"]:
        payload["milestone"] = ms_number[d["milestone"]]
    issue = json.loads(gh(["api", "-X", "POST", f"repos/{repo}/issues", "--input", "-"], payload).stdout)
    if d["closed"]:
        gh(["api", "-X", "PATCH", f"repos/{repo}/issues/{issue['number']}", "--input", "-"],
           {"state": "closed", "state_reason": "completed"})


def update_issue(repo, cur, d, cfg, ms_number):
    keep = [l["name"] for l in cur["labels"] if not is_managed_label(l["name"], cfg)]
    payload = {"title": d["title"], "body": d["body"], "labels": sorted(set(keep) | d["labels"]),
               "assignees": d["assignees"], "milestone": ms_number[d["milestone"]] if d["milestone"] else None}
    if d["closed"] and cur["state"] == "open":
        payload.update(state="closed", state_reason="completed")
    elif not d["closed"] and cur["state"] == "closed":
        payload.update(state="open")
    gh(["api", "-X", "PATCH", f"repos/{repo}/issues/{cur['number']}", "--input", "-"], payload)


def close_removed(repo, cur):
    gh(["api", "-X", "POST", f"repos/{repo}/issues/{cur['number']}/comments", "--input", "-"],
       {"body": "WBS에서 삭제된 작업이라 자동으로 닫았습니다. WBS에 다시 추가하면 이 이슈가 다시 열립니다."})
    gh(["api", "-X", "PATCH", f"repos/{repo}/issues/{cur['number']}", "--input", "-"],
       {"state": "closed", "state_reason": "not_planned"})


if __name__ == "__main__":
    main()
