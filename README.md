# Project-Reviewer

GitHub 레포를 클론해 코드 품질을 정성 평가하는 멀티 에이전트 시스템.
작동 여부가 아니라 라이브러리 의도성·오버/언더엔지니어링·데드코드·기술
스택 활용을 평가한다. git diff 기반으로 변경분만 재평가해 토큰을 절약한다.

## 설치

    conda create -y -n project-reviewer python=3.13
    conda activate project-reviewer
    uv pip install -r requirements.txt
    cp .env.example .env   # 키 채우기

## 사용

    python main.py https://github.com/owner/repo.git
    python main.py https://github.com/owner/repo.git --force   # 전체 재평가
    python main.py --serve                                     # 이력 웹 UI

결과: 터미널 요약 + `.reviewer/output/report-<id>.html`.

## 테스트

    python -m pytest
