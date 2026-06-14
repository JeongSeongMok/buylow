# buylow — 백테스트·라이브를 한 컨테이너에서 돌리기 위한 이미지.
#
# 왜 이렇게 묶나: buylow는 두 런타임을 함께 쓴다 — Python 3.11(오케스트레이터·전략) + .NET 10(LEAN
# 엔진). LEAN은 pythonnet으로 Python 3.11을 그대로 임베드하므로 **정확히 3.11**이 필요하다. 그래서
# python:3.11 이미지를 베이스로 잡고 .NET 10 SDK를 얹는다(반대로 dotnet SDK 이미지는 Python 버전을
# 고정하기 어렵다). 빌드 시 런처·어댑터·NuGet·AlgorithmImports·.leanpy venv를 미리 구워 첫 실행이 빠르다.
FROM python:3.11-slim-bookworm

# .NET 런타임 의존성(libicu) + 빌드 도구. git은 일부 의존성 설치에 쓰일 수 있어 포함.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git libicu-dev \
    && rm -rf /var/lib/apt/lists/*

# --- .NET 10 SDK (sudo 없이 공식 스크립트로 설치) ---
ENV DOTNET_ROOT=/usr/share/dotnet
RUN curl -fsSL https://dot.net/v1/dotnet-install.sh \
        | bash -s -- --channel 10.0 --install-dir "$DOTNET_ROOT" \
    && ln -s "$DOTNET_ROOT/dotnet" /usr/local/bin/dotnet
ENV PATH="$DOTNET_ROOT:$PATH" \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1

# --- uv (Python 환경/의존성 관리자) ---
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# pythonnet(PYTHONNET_PYDLL)이 로드할 libpython3.11.so 보장. 공식 python 이미지는
# --enable-shared로 빌드돼 libpython3.11.so.1.0를 두지만 버전 없는 심볼릭이 없을 수 있어 만들어 둔다.
RUN ln -sf /usr/local/lib/libpython3.11.so.1.0 /usr/local/lib/libpython3.11.so 2>/dev/null || true

WORKDIR /app
COPY . /app

# Windows에서 git autocrlf로 .sh가 CRLF로 체크아웃된 작업본이 빌드 컨텍스트로 들어오면 컨테이너
# (리눅스) bash가 'set: pipefail: invalid option'으로 깨진다. CR을 제거해 LF로 정규화한다
# (.gitattributes eol=lf가 근본 차단이지만, 이미 CRLF로 받은 작업본도 빌드되도록 방어).
RUN find . -name '*.sh' -exec sed -i 's/\r$//' {} +

# --- Python 의존성(오케스트레이터) ---
RUN uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev]"

# --- LEAN 런타임 미리 굽기 ---
# 런처 빌드 = NuGet 복원(+ AlgorithmImports content) → 어댑터 빌드(라이브용 KIS DLL) →
# .leanpy(전략용 pandas/numpy 3.11 venv) 생성. 이걸 이미지에 구워 첫 백테스트가 즉시 시작된다.
RUN dotnet build launcher/BuylowLauncher.csproj -c Release --nologo \
    && bash scripts/build-adapter.sh \
    && uv venv --python 3.11 .leanpy \
    && uv pip install --python .leanpy/bin/python pandas numpy

# 런타임 상태(설정·DB·토큰)를 한 디렉토리(/app/state)로 모은다. compose가 이 '디렉토리'를
# bind-mount해 영속화하므로 사전준비(cp/touch)가 필요 없다 — 개별 '파일'을 마운트하면 없을 때
# Docker가 디렉토리로 잘못 만드는 footgun이 있어, 디렉토리만 마운트하려고 경로를 여기로 옮긴다.
RUN mkdir -p /app/state
ENV BUYLOW_CONFIG_LOCAL=/app/state/config.local.yaml \
    BUYLOW_DB_PATH=/app/state/buylow.db \
    BUYLOW_KIS_TOKEN_CACHE=/app/state/.kis_token.json

# 컨테이너 안에서는 0.0.0.0에 바인딩해야 호스트 포트매핑이 닿는다. 외부 노출 차단은
# docker-compose가 호스트 쪽을 127.0.0.1로 묶어 책임진다(README/DEVELOPMENT.md 참고).
ENV BUYLOW_DASHBOARD_HOST=0.0.0.0
EXPOSE 8420

CMD [".venv/bin/python", "-m", "orchestrator.api"]
