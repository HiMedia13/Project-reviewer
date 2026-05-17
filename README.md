# Project-Reviewer

GitHub 레포를 클론해 코드를 전부 읽고 **정성 평가**하는 멀티 에이전트 시스템.
"작동하는가"가 아니라 **어떤 기술을 썼고, 그 기술을 의도대로 제대로 썼으며,
프로젝트 목적에 적합한가**를 평가한다. git diff 기반으로 변경분만 재평가해
토큰을 아끼고, 결과는 로컬 SQLite에 누적된다.

## 산출물

- **핵심(헤드라인): 프로젝트 레벨 기술스택 적합성 평가**
  - 프로젝트 목적 자동 추론(README·문서·디렉터리 구조·매니페스트 기반)
  - 사용 기술 인벤토리 → 기술별 *의도대로 사용 여부* + *목적 적합성* + 근거
  - 종합 verdict + stack score
- **보조: 파일별 4기준 findings**(접이식) — 라이브러리 의도성 / 오버·언더
  엔지니어링 / 데드코드 / 기술 활용. evaluator(critic)가 할루시네이션·
  괜한 지적·타당성을 검증하고, 신기술은 Tavily 웹검색으로 확인한다.

## 아키텍처

결정론적 파이프라인(클론 → git-diff 스코프 → SQLite → 리포트)이
DeepAgents 오케스트레이션을 감싼다. **하이브리드 모델**: 오케스트레이터는
Sonnet(안정적 tool-calling), 6개 서브에이전트(scanner + 4기준 + evaluator)는
Haiku(저비용). 4기준 서브에이전트는 병렬 실행되고, evaluator는 그 뒤에
모든 결과를 검증한다. 결과는 `submit_tech_assessment`/`submit_findings`
툴로 구조화 캡처하며, 미호출 시 결정론적 finalize 가드가 보강한다.

## 설치

    conda create -y -n project-reviewer python=3.13
    conda activate project-reviewer
    pip install uv && uv pip install -r requirements.txt
    cp .env.example .env   # 키 채우기 (최소 ANTHROPIC_API_KEY)

`.env`는 실행 시 자동 로드된다(`python-dotenv`).

## 설정 (.env)

| 변수 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | — | Claude API 키 |
| `TAVILY_API_KEY` | ⬜ | — | 신기술 검증용 웹검색(없으면 그 단계만 스킵) |
| `LANGSMITH_API_KEY` | ⬜ | — | 실행당 토큰 비용 집계 |
| `LANGSMITH_TRACING` | ⬜ | `true` | LangSmith 트레이싱 on/off |
| `LANGSMITH_PROJECT` | ⬜ | `project-reviewer` | LangSmith 프로젝트명 |
| `REVIEWER_MODEL` | ⬜ | `claude-haiku-4-5-20251001` | 서브에이전트 모델(저비용) |
| `REVIEWER_ORCH_MODEL` | ⬜ | `claude-sonnet-4-6` | 오케스트레이터 모델 |
| `REVIEWER_RECURSION_LIMIT` | ⬜ | `60` | 오케스트레이터 그래프 supersteps 상한(폭주 방지) |

## 사용

    python main.py https://github.com/owner/repo.git
    python main.py https://github.com/owner/repo.git --force        # 캐시 무시, 전체 재평가
    python main.py https://github.com/owner/repo.git --max-files 2   # 평가 파일 수 상한(저비용 검증)
    python main.py https://github.com/owner/repo.git --progress      # 비-TTY에서도 평문 진행 로그
    python main.py https://github.com/owner/repo.git --with-frontend  # 프론트엔드/UI 파일도 포함
    python main.py --serve                                           # 이력 조회 웹 UI

- `--workdir` 기본값 `.reviewer` (작업·캐시·DB·출력 루트)
- `--max-files 0` 은 "아무 파일도 평가하지 않음"(드라이런), 음수는 거부
- **기본은 백엔드 중심**: 프론트엔드/UI 파일(`.tsx/.jsx/.vue/.svelte/.css/...`,
  `frontend·client·web·ui·static/...` 디렉터리의 js/ts)은 자동 제외된다.
  전부 평가하려면 `--with-frontend`. 백엔드 언어 파일(`.py/.go/...`)은
  어떤 디렉터리에 있든 절대 제외되지 않는다.

### 실행 중단 (Ctrl+C)

평가 도중 `Ctrl+C` **한 번** → 우아한 중단: 진행 중인 단계 경계에서 멈추고,
그때까지 서브에이전트가 제출한 부분 findings·기술스택 평가를 DB에 저장한
뒤 부분 리포트를 출력한다(추가 LLM 호출 없음 = 추가 비용 없음).
**한 번 더** `Ctrl+C` → 즉시 강제 종료.

### 진행상황 표시(TUI)

본인 **터미널에서 직접 실행**하면(stdout이 TTY) Rich 라이브 TUI가 자동으로
켜진다 — 6단계 트래커 + 경과시간 + 파일 진행 + 토큰·비용 미터 + 활동 로그.
파이프/백그라운드/CI(비-TTY)에서는 기본 무음이며, `--progress`를 주면
평문 한 줄 로그를 출력한다.

## 결과물

- 터미널 요약: **기술스택 적합성 헤드라인** + 보조 4기준 요약
- 자체완결 HTML: `<workdir>/output/report-<id>.html`
- 진단용 원출력: `<workdir>/output/raw-<id>.txt`
- 누적 DB: `<workdir>/reviewer.sqlite3` (재실행 시 변경분만 재평가)

## 테스트

    python -m pytest
