# Project-Reviewer 설계 문서

작성일: 2026-05-16

## 1. 목적

GitHub 레포 링크를 입력하면 코드를 전부 읽고 **정성 평가**하는 멀티 에이전트 시스템.
코드의 *작동 여부는 평가 대상이 아니다*. 다음 4가지 기준만 판단한다.

1. **라이브러리 의도성**: 사용된 라이브러리가 그 라이브러리의 설계 의도대로 쓰였는가.
2. **오버/언더 엔지니어링**: 문제 규모 대비 과설계 또는 부족한 설계가 있는가.
3. **데드코드**: 도달 불가/미사용 코드가 얼마나 있는가.
4. **기술 스택 활용**: 어떤 기술이 쓰였고 적절히·잘 활용되었는가.

토큰 비용 최적화가 1급 요구사항이다. 같은 레포 재검사 시 변경분만 재평가한다.

## 2. 핵심 결정 사항

| 항목 | 결정 |
|---|---|
| LLM 백엔드 | Claude (Anthropic API), `langchain-anthropic` |
| 에이전트 프레임워크 | LangChain DeepAgents (`deepagents`), 단일 deep agent + 6 subagent |
| 입력/실행 | CLI `python main.py <github-url> [--force]`, `git clone` |
| Subagent 분할 | 평가 기준별 전문 subagent + scanner + evaluator-critic |
| 캐시 단위 | git diff 기반 (변경 파일 + 역의존 파일만 재평가) |
| 저장소 | 로컬 SQLite |
| 출력 | Rich 터미널 요약 + 단일 자기완결 HTML 파일. `--serve` 시 FastAPI 이력 탐색 |
| 비용 가시화 | LangSmith 트레이싱 + langsmith SDK 비용 집계 |
| 웹 검색 | Tavily Search API (`tavily-python`) |

## 3. 아키텍처 & 데이터 흐름

```
CLI: python main.py <github-url> [--force]
  │
  ▼
[1] Repo Manager  (결정적 Python, 토큰 0)
    - clone 또는 기존 캐시 pull
    - SQLite에서 remote_url 조회
    - 신규 레포      → 전체 파일 스코프 (mode=full)
    - 기존 레포      → 최신 evaluation.commit_sha 기준 git diff
                       → 변경 파일 + 그 파일을 import하는 역의존 파일만 in-scope
                       → 미변경 파일은 직전 file_findings 행을 새 evaluation으로 복사
                       (mode=incremental)
    - --force        → mode=full 강제
  │
  ▼
[2] Orchestrator Deep Agent  (create_deep_agent)
    - DeepAgents planning tool(write_todos)로 작업 계획 수립
    - 공유 state에 project map + in-scope 파일 목록 주입
    - filesystem tool로 레포 파일 접근
    │
    ├─ subagent: scanner
    │     파일트리 / 언어 분포 / 의존성 매니페스트 / import 그래프 작성
    ├─ subagent: library-usage      ┐
    ├─ subagent: over-under-eng     │  in-scope 파일만, 병렬 dispatch
    ├─ subagent: dead-code          │  각자 구조화 findings(JSON) 반환
    ├─ subagent: tech-stack         ┘
    └─ subagent: evaluator-critic
          - 모든 findings 검증:
            · 할루시네이션 여부
            · 괜한/사소한 지적 여부
            · 기능에 대한 지적이 타당한지
            · LLM이 모르는 신기술/신규 라이브러리 → Tavily 웹검색으로
              의도된 사용법 확인 후 판정
          - 각 finding에 verified/verify_note 주석, 부적절 항목 제거
  │
  ▼
[3] Synthesizer  (결정적 Python)
    - 검증된 신규 findings + 캐시된 미변경 findings 병합
    - 기준별 점수(0~100) 산출, 종합 점수 구성
  │
  ▼
[4] Persistence + Output
    - SQLite 적재 (repos / evaluations / file_findings)
    - LangSmith trace 비용 집계 후 evaluation에 저장
    - Rich 터미널 요약 출력
    - 단일 자기완결 HTML 리포트 파일 작성
    - (선택) python -m app.server → FastAPI 이력 탐색/비교
```

스코프 결정(git diff, 캐시 복사)은 **에이전트 진입 전 결정적 Python 레이어**에서 처리해
LLM 토큰을 쓰지 않는다. 이것이 토큰 최적화의 핵심 메커니즘이다.

## 4. SQLite 스키마

```sql
repos(
  id INTEGER PK,
  remote_url TEXT UNIQUE,
  local_path TEXT,
  default_branch TEXT,
  created_at TEXT
)

evaluations(
  id INTEGER PK,
  repo_id INTEGER FK,
  commit_sha TEXT,
  parent_eval_id INTEGER NULL,        -- 직전 평가 (incremental 기준)
  mode TEXT,                          -- 'full' | 'incremental'
  created_at TEXT,
  overall_json TEXT,                  -- 기준별 점수 + 요약
  langsmith_run_url TEXT NULL,
  total_input_tokens INTEGER,
  total_output_tokens INTEGER,
  total_cost_usd REAL,
  duration_sec REAL
)

file_findings(
  id INTEGER PK,
  repo_id INTEGER FK,
  evaluation_id INTEGER FK,
  file_path TEXT,
  file_hash TEXT,                     -- 캐시 재사용 판별
  criterion TEXT,                     -- 'library' | 'eng' | 'deadcode' | 'techstack'
  findings_json TEXT,                 -- 항목 배열: severity, 위치(파일:라인), 근거
  criterion_score INTEGER,
  verified INTEGER,                   -- evaluator 검증 통과 0/1
  verify_note TEXT
)
```

### 캐시 재사용 규칙

재실행 시 remote_url로 repo 조회 → 최신 evaluation의 commit_sha 기준 `git diff` →
변경 파일 + import 역의존 파일만 in-scope. 미변경 파일은 직전 `file_findings` 행을
새 `evaluation_id`로 **복사**(LLM 미호출). `--force`는 전체 재평가.

## 5. 출력

### 기본 (서버 불필요)

평가 종료 시:

1. **Rich 터미널 요약**: 종합 점수, 기준별 4점 게이지, 이번 실행 토큰/비용/소요시간,
   mode(full/incremental) 배지, incremental일 때 변경 하이라이트.
2. **단일 자기완결 HTML 리포트 파일**(인라인 CSS, 서버 불필요)을 출력 디렉터리에 작성.
   더블클릭으로 열람. 내용: 상단 요약 카드 → 변경 하이라이트(incremental) →
   기준별 섹션(severity 그룹, 파일:라인, 근거, evaluator 검증 배지
   ✅검증됨 / ⚠️낮은신뢰 / 🔍웹검색확인).

### 선택 (`python -m app.server`)

FastAPI + Jinja2 (HTML 리포트와 동일 템플릿 재사용)로:
레포 목록, 평가별 리포트, **동일 레포 평가 이력 비교**(기준별 점수 추이 + 비용 추이).

## 6. LangSmith 비용 집계

`LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY` + `LANGSMITH_PROJECT` 설정 시
DeepAgents/LangChain이 자동 트레이싱. 실행 종료 후 `langsmith` SDK `Client`로 해당
trace의 run tree를 조회 → prompt/completion 토큰 합계와 비용(LangSmith가 모델 단가로
산출)을 `evaluations` 레코드에 저장 → 터미널/HTML/웹에 표시.
키 미설정 시 평가는 정상 진행, 비용은 0으로 표기(선택적 기능).

## 7. 프로젝트 구조 & 스택

스택: `deepagents`, `langchain`, `langchain-anthropic`, `langsmith`, `tavily-python`,
`GitPython`, `rich`, `jinja2`, `fastapi`+`uvicorn`(선택), 표준 `sqlite3`.
conda 가상환경, 설치는 `uv pip install`, 실행은 `python`.

```
project-reviewer/
  main.py                 # CLI 진입점
  app/
    repo_manager.py       # clone/pull, git diff 스코프, import 그래프/역의존
    db.py                 # SQLite 스키마/CRUD, 캐시 행 복사
    agent/
      orchestrator.py     # create_deep_agent + planning
      subagents.py        # scanner / 4기준 / evaluator-critic 정의
      prompts.py          # 각 subagent 시스템 프롬프트
    synthesizer.py        # findings 병합 · 점수 산출
    langsmith_cost.py     # trace 비용 집계
    report.py             # Rich 터미널 요약 + HTML 렌더
    server.py             # FastAPI + Jinja2 (선택 --serve)
  templates/              # Jinja2 (HTML 리포트 / 웹 공용)
  static/
  tests/
  docs/superpowers/specs/
```

## 8. 에러 처리

- clone 실패 / 빈 레포 / 거대 바이너리·지원 외 파일 → 스킵 로깅 후 진행.
- subagent JSON 파싱 실패 → 1회 재요청, 그래도 실패 시 해당 finding "검증불가" 표기.
- LangSmith 키 없음 → 비용 0 표기, 평가는 정상 진행.
- Tavily 키 없음 → 웹검색 단계 스킵, 해당 finding은 "웹검색 미확인"으로 표기.
- git diff 기준 commit이 force-push 등으로 사라진 경우 → mode=full로 폴백.

## 9. 테스트 전략

- **단위**: repo_manager(diff 스코프·import 역의존 계산), db(캐시 행 복사 정확성·
  스키마 마이그레이션), synthesizer(점수 산출·findings 병합).
- **통합 스모크**: 소형 샘플 레포로 full→incremental 2회 실행, 미변경 파일이
  LLM 재호출 없이 캐시 복사되는지 검증(에이전트 호출 mock 또는 카운터).
- LLM/Tavily/LangSmith 호출은 테스트에서 mock.

## 10. 비범위 (YAGNI)

- 코드 실행/빌드/테스트 수행 (작동 여부 평가하지 않음).
- 다중 LLM 백엔드 추상화 (Claude 단일).
- 인증·멀티유저 웹 (로컬 단일 사용자 전제).
- 실시간 진행 TUI (터미널 요약으로 충분).
