# concours-data

Concours 앱(국악 경연대회 관리)의 전국 대회 카탈로그 데이터.

- `competitions.json` — 대회 카탈로그(요강 상세 포함). 앱이 클라이언트에서 fetch.
- `version.json` — 가벼운 버전/신선도 체크용 매니페스트.

정제 파이프라인(레퍼런스 수집 → 공식홈 우선 병합 → 비전/LLM 추출)은 앱 밖에서 수행하며, 결과 JSON만 여기 호스팅한다. 차후 서버/크론으로 자동 갱신 예정.

Raw: `https://raw.githubusercontent.com/hawaii-rgb/concours-data/main/competitions.json`
