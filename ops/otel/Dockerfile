# Full upstream Contrib distribution, security rebuilt; no components removed.
# Match upstream's currently supported Go 1.26 patch line. Go 1.27 rejects
# an initialization hook in the unchanged legacy SermoDigital component.
FROM docker.io/library/golang:1.26.10-bookworm@sha256:23ce71337b726608afb1bc00986e630a05034a87a87ea1fc2cd1ae5cf33387eb AS build
ENV GOTOOLCHAIN=local GOWORK=off GOPROXY=https://goproxy.cn|https://proxy.golang.org|direct GOSUMDB="sum.golang.org https://sum.golang.org" CGO_ENABLED=0 GOMAXPROCS=2 GOFLAGS=-p=2
ADD --checksum=sha256:77907d33ff432f90b0211cacfad41426eac8f3fb1c1078115cc2c34b285121ab https://codeload.github.com/open-telemetry/opentelemetry-collector-releases/tar.gz/bf626fce5c8053ac6ae9c6c5f4c408c8c182740e /source.tar.gz
# Official generated BPF sources required by the unchanged upstream manifest.
ADD --checksum=sha256:d3cbec602e3ab7b357d2f4a57b29e502365f9098808418e8b4d96dec8fa9281f https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/releases/download/v0.15.2/obi-v0.15.2-source-generated.tar.gz /obi.tar.gz
WORKDIR /source
RUN tar -xzf /source.tar.gz --strip-components=1 && mkdir -p internal/obi-src && tar -xzf /obi.tar.gz --strip-components=1 -C internal/obi-src
RUN --mount=type=cache,id=cs-s2-collector-pkg-0164,target=/go/pkg --mount=type=cache,id=cs-s2-collector-build-0164,target=/root/.cache/go-build go install go.opentelemetry.io/collector/cmd/builder@v0.164.0
WORKDIR /source/distributions/otelcol-contrib
RUN --mount=type=cache,id=cs-s2-collector-pkg-0164,target=/go/pkg --mount=type=cache,id=cs-s2-collector-build-0164,target=/root/.cache/go-build sed -i 's/^  version: 0.164.0$/  version: 0.164.0+cs.s2/' manifest.yaml && /go/bin/builder --config manifest.yaml --skip-compilation
WORKDIR /source/distributions/otelcol-contrib/_build
RUN --mount=type=cache,id=cs-s2-collector-pkg-0164,target=/go/pkg --mount=type=cache,id=cs-s2-collector-build-0164,target=/root/.cache/go-build go get github.com/go-jose/go-jose/v4@v4.1.6 && go mod verify
RUN --mount=type=cache,id=cs-s2-collector-pkg-0164,target=/go/pkg --mount=type=cache,id=cs-s2-collector-build-0164,target=/root/.cache/go-build go build -tags grpcnotrace -trimpath -buildvcs=false -ldflags='-s -w -buildid=' -o /out/otelcol-contrib .

FROM otel/opentelemetry-collector-contrib@sha256:340c9ca6a36617084c6661bdc4f0fe92b7cda5a805d61673ec8db375fddc522b
COPY --from=build /out/otelcol-contrib /otelcol-contrib
COPY --from=build /source/distributions/otelcol-contrib/_build/go.mod /source/distributions/otelcol-contrib/_build/go.sum /source/distributions/otelcol-contrib/manifest.yaml /source/LICENSE /usr/share/customer-service-collector/
USER 10001:10001
