# 충남 지원사업 자동 수집기

Python 서버 + HTML/CSS/JavaScript 화면 + SQLite 저장소로 구성된 전체 소스입니다.
HTML 파일만 더블클릭하는 방식이 아니라, 실행 프로그램으로 서버를 켜서 사용합니다.

## Windows에서 실행

1. Python **3.10 이상**을 설치합니다. https://www.python.org/downloads/
2. ZIP 압축을 풀고 `start_windows.bat`을 실행합니다.
3. 첫 실행에서 필요한 패키지를 설치하고 브라우저가 열립니다.
4. 주소: **http://127.0.0.1:8000**

실행 창을 켜 둔 동안 시작 시 1회, 이후 기본 **6시간마다** 수집합니다.
‘지금 수집’ 또는 기관별 ‘다시 수집’으로 즉시 실행할 수도 있습니다.
PC가 꺼지거나 실행 창을 닫으면 수집도 멈춥니다. 별도의 ChatGPT 예약 작업은 생성하지 않았습니다.

macOS/Linux: `bash start.sh`

직접 실행:

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py
```

```sh
python run.py --interval 3 --port 8001
python run.py --once
python run.py --once --source s5 --source s8
python -m unittest discover -s tests -v
```

## 구현한 기능

- 51개 기관·서비스 경로 등록. 전국 통합 사이트 3곳은 지역 혼입 방지를 위해 기본 비활성.
- HTML 게시판 수집 및 대표 홈페이지에서 관련 게시판 탐색.
- 그누보드, 충남 통합지원시스템 HTML, 공식 CNSP API, 창조경제혁신센터 홈페이지 JSON 대응 코드.
- SQLite 누적 저장, 같은 기관·같은 URL 중복 저장 방지, 목록 내용이 바뀌면 갱신.
- 제목·목록 내용 검색, 기관 분야·지역·기간 필터, 별표, 같은 제목 묶기, CSV 내보내기.
- 첫 발견·마지막 확인·변경 시각 기록. 7일 넘게 재확인되지 않은 공고 표시.
- 기관별 성공·일부 수집·오류·접근 제한·구조 확인 필요 상태와 마지막 성공 시각 표시.
- 수집 실패가 발생해도 기존 DB 공고 유지.
- 같은 호스트에 최소 1초 요청 간격, robots.txt 확인, 동시 4개 기관 수집.
- 기존 기관 링크 HTML도 포함 (`static/directory.html`).

## 꼭 구분할 점

**51개 등록 경로 = 51개 수집 성공이 아닙니다.** 실제 검증 결과는 `VALIDATION.md`와 화면의 기관별 수집 상태를 보세요.

- 자동접근 제한·robots 정책·로그인·자바스크립트 전용 목록·페이지 개편으로 수집하지 못하는 기관이 있습니다. 제한을 우회하지 않습니다.
- 홈페이지 탐색은 최대 관련 링크 3개, 실행당 최대 추가 5페이지입니다. 과거 전체 공고를 전부 가져오는 수집기가 아닙니다.
- 게시판은 기본 최근 2페이지, CNSP HTML은 3페이지입니다. 오래된 공고를 저장하지 않았거나 최신 목록에서 밀려난 공고는 계속 재확인되지 않을 수 있습니다.
- 제목에 지원·모집·공모 등 지정 키워드가 없는 공고는 누락될 수 있습니다. 일반 공고의 지원 대상 여부를 완벽히 판별하지 않습니다.
- ‘기간 내’는 확인된 신청기간 안이라는 의미이며 예산 소진·중도 마감 여부를 보장하지 않습니다. 공고 게시기간과 신청기간을 혼동하지 않도록 일반 지자체 게시기간을 신청기간으로 자동 사용하지 않습니다.
- 기간을 알 수 없으면 ‘기간 미확인’으로 표시합니다. 첨부 PDF/HWP 속 신청기간·본문·지원금까지 추출하지 않습니다.
- 분야와 지역은 등록 기관 기준입니다. 충남 전체로 분류된 기관에도 타 지역 공고가 있을 수 있습니다.
- 같은 제목 묶기는 화면 표시 기능이며 서로 다른 기관 원문은 DB에서 별도로 유지됩니다.
- 기본 서버는 내 PC 전용입니다. 인증 없이 인터넷 공개 서비스로 배포하는 용도가 아닙니다. 여러 서버 프로세스로 동시에 실행하지 마세요.

## 충남경제진흥원 공식 API 연결

공식 문서: https://cnsp.or.kr/api/particular.do

기관에서 발급받은 키가 있으면 `.env.example`을 `.env`로 복사하고 입력합니다.

```dotenv
CNSP_API_KEY=발급받은_키
```

서버를 재시작하면 s1은 HTML 수집 대신 공식 API를 사용합니다.
API 키는 서버에서만 읽으며 브라우저 응답·소스 목록·로그에 넣지 않습니다.

- GET `https://cnsp.or.kr/api/v1/supportproject.do?format=json`
- 헤더 `cnsp-api-key`
- `datas`에서 `project_title`, `recruit_startdate`, `recruit_enddate`, `project_link`를 사용.
- `project_startdate/project_enddate`(사업기간)를 신청기간으로 쓰지 않습니다.
- API 응답 형식은 문서·테스트로 검증했으며 실제 키가 없으므로 인증 API 실호출은 미검증입니다.

## 다른 기관 수집 규칙 바꾸기

`sources.json`을 수정하고 서버를 재시작하세요. 네트워크 주소를 추가하는 설정 파일이므로 신뢰하는 공식 주소만 입력하세요.

```json
{
  "id": "my-source",
  "name": "기관 이름",
  "url": "https://example.org/notices",
  "category": "창업·보육",
  "region": "충남 전체",
  "enabled": true,
  "mode": "board",
  "pages": 2,
  "page_param": "page",
  "link_selector": "table tbody a[href]"
}
```

- `mode: board`: 지정 게시판과 다음 페이지를 수집.
- `mode: discover`: 대표 화면과 그 안의 관련 공고 메뉴를 탐색.
- `link_selector`: 기관별 CSS 선택자. 없다면 상세보기 형태의 링크와 제목 키워드를 함께 검사.
- `js_rules`: 주소가 명시된 onclick 함수에 정규식과 URL 템플릿을 연결할 수 있습니다. 페이지 JS를 실행하지 않습니다.
- `enabled: false`: 목록은 유지하면서 자동 수집 제외.
- 페이지 규칙이 변경되면 `page_param`, 선택자 또는 `engine.py`의 기관 전용 어댑터를 수정해야 합니다.

예: `{"pattern":"goView\\('([0-9]+)'\\)","url":"/notice/view?id={0}"}`

JSON 홈페이지 피드는 사이트 robots 정책상 막히면 실행하지 않습니다. 접근 제한은 Playwright 등을 이용해 우회하지 않습니다. 허가된 API 또는 기관이 제공하는 별도 경로가 필요합니다.

## 파일과 데이터

| 파일 | 역할 |
|---|---|
| `run.py` | 웹 서버·자동 실행·수동 수집 API·CSV |
| `engine.py` | 수집·정규화·상태 관리·SQLite |
| `sources.json` | 51개 경로와 수집 설정 |
| `static/index.html`, `app.js`, `style.css` | 통합 공고 화면 |
| `data/notices.sqlite3` | 누적 공고와 수집 상태 |
| `data/collector.log` | 실행 로그 |
| `tests/test_engine.py` | 파싱·저장·갱신·실패 보존 테스트 |
| `VALIDATION.md` | 이번 환경에서의 실제 수집 결과 |

SQLite DB에는 공개 목록에서 수집한 제목·목록 요약·링크만 저장합니다. 서버를 종료한 상태에서 `data` 폴더를 복사하면 백업할 수 있습니다. 화면 별표는 브라우저 localStorage에 따로 저장됩니다.

## Docker

```sh
docker compose up --build -d
docker compose logs -f
docker compose down
```

기본 포트는 내 PC의 127.0.0.1에만 공개합니다. `data`는 호스트에 보존됩니다. Python 상주 프로세스를 지원하는 PC/VPS에서 실행하세요. 일반 정적 HTML 호스팅이나 PHP 전용 호스팅에 파일만 업로드해서는 자동 수집 서버가 실행되지 않습니다.
