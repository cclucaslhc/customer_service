# Build artifact for self-managed Linux. Runtime configuration is supplied by
# the operator; this image contains no credentials and performs no migration.
FROM docker.io/library/node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Debian has no fixed libsqlite3 package for CVE-2026-119373 yet. Build the
# fixed upstream release, retaining real native provenance rather than relabeling
# the old Debian library or suppressing its finding. Pinned GCC/glibc toolchain.
FROM docker.io/library/golang:1.26.10-bookworm@sha256:23ce71337b726608afb1bc00986e630a05034a87a87ea1fc2cd1ae5cf33387eb AS sqlite-build
WORKDIR /build/sqlite
RUN curl --fail --location --retry 3 https://sqlite.org/2026/sqlite-autoconf-3540000.tar.gz -o sqlite.tar.gz \
    && echo '134ec0802dda5795816e25d25872d20b312cb3973438c49b30bc40b7705ea9ed  sqlite.tar.gz' | sha256sum -c - \
    && tar -xzf sqlite.tar.gz --strip-components=1 \
    && test "$(openssl dgst -sha3-256 sqlite3.c | awk '{print $2}')" = '8efd453d08a7cfc4c79de5923153036cdc4dcaa254d4f1e617be6a2a98828f9e' \
    && mkdir /out \
    && gcc -shared -fPIC -O2 -DSQLITE_THREADSAFE=1 -DSQLITE_ENABLE_FTS5 \
       -DSQLITE_ENABLE_FTS3 -DSQLITE_ENABLE_FTS3_PARENTHESIS -DSQLITE_ENABLE_RTREE \
       -DSQLITE_ENABLE_COLUMN_METADATA -DSQLITE_ENABLE_MATH_FUNCTIONS \
       -DSQLITE_ENABLE_DBSTAT_VTAB -DSQLITE_ENABLE_UNLOCK_NOTIFY -DSQLITE_SECURE_DELETE \
       -Wl,-soname,libsqlite3.so.0 -o /out/libsqlite3.so.0 sqlite3.c -lm -ldl -lpthread

FROM docker.io/library/golang:1.26.10-bookworm@sha256:23ce71337b726608afb1bc00986e630a05034a87a87ea1fc2cd1ae5cf33387eb AS zlib-build
WORKDIR /build/zlib
RUN curl --fail --location --retry 3 https://github.com/madler/zlib/releases/download/v1.4/zlib-1.4.tar.gz -o zlib.tar.gz \
    && echo '02dd159783677a749c04adbdd02b55b8a2bcc7e054f51b76b549199a916ad584  zlib.tar.gz' | sha256sum -c - \
    && tar -xzf zlib.tar.gz --strip-components=1 \
    && CFLAGS='-O2 -fPIC' ./configure --shared --prefix=/out \
    && make -j2 && make test && make install

FROM docker.io/library/python:3.12-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2 AS python-build
COPY --from=ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168 /uv /uvx /usr/local/bin/
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /build
COPY pyproject.toml uv.lock ./
# RapidOCR declares GUI OpenCV; this headless server installs the API-compatible
# wheel instead, avoiding OpenGL/Mesa/LLVM in the runtime image.
RUN uv sync --locked --extra production --extra production-image --no-dev --no-install-project --no-install-package opencv-python
RUN mkdir -p /build/runtime-status.d && for package in libgcc-s1 libffi8 libstdc++6; do \
    dpkg-query -s "$package" > "/build/runtime-status.d/$package"; done
COPY --from=sqlite-build /out/libsqlite3.so.0 /usr/local/lib/libsqlite3.so.0
COPY scripts/sqlite_runtime.py /build/sqlite_runtime.py
RUN python /build/sqlite_runtime.py metadata /usr/local/lib/libsqlite3.so.0 /build/native/sqlite3.cdx.json

FROM gcr.io/distroless/base-debian13:nonroot@sha256:6c591b4ac789b683ab3665327181e7cfb7a39b5834f42dcb73d7becb8d18a5d9 AS runtime-base
FROM python-build AS runtime-root
# Curate an offline copy of the exact base root. Removing the superseded zlib
# package here removes its real library AND stale Debian inventory; only the
# hash-pinned upstream replacement and its native SBOM enter the final root.
COPY --from=runtime-base / /runtime-root/
COPY --from=zlib-build /out/lib/libz.so.1.4 /build/libz.so.1.4
COPY scripts/zlib_runtime.py /build/zlib_runtime.py
RUN python /build/zlib_runtime.py prepare-root /runtime-root /build/libz.so.1.4

FROM scratch
COPY --from=runtime-root /runtime-root/ /
WORKDIR /opt/customer-service
COPY --from=python-build /usr/local/bin/python3.12 /usr/local/bin/python3.12
COPY --from=python-build /usr/local/bin/python3 /usr/local/bin/python3
COPY --from=python-build /usr/local/lib/libpython3.12.so.1.0 /usr/local/lib/libpython3.12.so.1.0
COPY --from=python-build /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=python-build /lib/x86_64-linux-gnu/libgcc_s.so.1 /lib/x86_64-linux-gnu/libgcc_s.so.1
COPY --from=python-build /lib/x86_64-linux-gnu/libffi.so.8 /lib/x86_64-linux-gnu/libffi.so.8
COPY --from=sqlite-build /out/libsqlite3.so.0 /usr/local/lib/libsqlite3.so.0
COPY --from=python-build /build/native/sqlite3.cdx.json /usr/share/customer-service/native/sqlite3.cdx.json
COPY --from=python-build /lib/x86_64-linux-gnu/libstdc++.so.6 /lib/x86_64-linux-gnu/libstdc++.so.6
COPY --from=python-build /build/runtime-status.d/ /var/lib/dpkg/status.d/
COPY --from=python-build /build/.venv ./.venv
COPY app ./app
COPY scripts ./scripts
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY --from=frontend-build /build/frontend/dist ./frontend/dist
ENV PATH=/opt/customer-service/.venv/bin:/usr/local/bin PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp LD_LIBRARY_PATH=/usr/local/lib
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
USER 65532:65532
CMD ["/opt/customer-service/.venv/bin/python", "scripts/serve_api.py"]
