# Build artifact for self-managed Linux. Runtime configuration is supplied by
# the operator; this image contains no credentials and performs no migration.
FROM docker.io/library/node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM docker.io/library/python:3.12-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2 AS python-build
COPY --from=ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168 /uv /uvx /usr/local/bin/
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /build
COPY pyproject.toml uv.lock ./
# RapidOCR declares GUI OpenCV; this headless server installs the API-compatible
# wheel instead, avoiding OpenGL/Mesa/LLVM in the runtime image.
RUN uv sync --locked --extra production --extra production-image --no-dev --no-install-project --no-install-package opencv-python
RUN mkdir -p /build/runtime-status.d && for package in libgcc-s1 libffi8 libsqlite3-0 libstdc++6; do \
    dpkg-query -s "$package" > "/build/runtime-status.d/$package"; done

FROM gcr.io/distroless/base-debian13:nonroot@sha256:6c591b4ac789b683ab3665327181e7cfb7a39b5834f42dcb73d7becb8d18a5d9
WORKDIR /opt/customer-service
COPY --from=python-build /usr/local/bin/python3.12 /usr/local/bin/python3.12
COPY --from=python-build /usr/local/bin/python3 /usr/local/bin/python3
COPY --from=python-build /usr/local/lib/libpython3.12.so.1.0 /usr/local/lib/libpython3.12.so.1.0
COPY --from=python-build /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=python-build /lib/x86_64-linux-gnu/libgcc_s.so.1 /lib/x86_64-linux-gnu/libgcc_s.so.1
COPY --from=python-build /lib/x86_64-linux-gnu/libffi.so.8 /lib/x86_64-linux-gnu/libffi.so.8
COPY --from=python-build /lib/x86_64-linux-gnu/libsqlite3.so.0 /lib/x86_64-linux-gnu/libsqlite3.so.0
COPY --from=python-build /lib/x86_64-linux-gnu/libstdc++.so.6 /lib/x86_64-linux-gnu/libstdc++.so.6
COPY --from=python-build /build/runtime-status.d/ /var/lib/dpkg/status.d/
COPY --from=python-build /build/.venv ./.venv
COPY app ./app
COPY scripts ./scripts
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY --from=frontend-build /build/frontend/dist ./frontend/dist
ENV PATH=/opt/customer-service/.venv/bin:/usr/local/bin PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp LD_LIBRARY_PATH=/usr/local/lib
USER 65532:65532
CMD ["/opt/customer-service/.venv/bin/python", "scripts/serve_api.py"]
