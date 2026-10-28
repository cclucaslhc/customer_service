# Reviewed security rebuild of upstream v0.34.1, not an official release image.
# Source commit and official prebuilt UI are immutable and checksum checked.
FROM docker.io/library/golang:1.27.3-bookworm@sha256:05237276236793ebedaf0b87dab86add9ec9adc66ebef2dbc3107c18254cde1c AS build
ENV GOTOOLCHAIN=local GOPROXY=https://goproxy.cn|https://proxy.golang.org|direct GOSUMDB=sum.golang.org CGO_ENABLED=0
ADD --checksum=sha256:5c5c72ec6b4c4746d523f2ed1994dfec4b94f7457e530ee6fb16cfec158e545e https://codeload.github.com/prometheus/alertmanager/tar.gz/73c6bfe7393929211294c1954f30d8ed78e4d0ad /source.tar.gz
ADD --checksum=sha256:881fe0f7beb9573f35be47dd233af6220e72c8db72c03bcd1780402eb8dc1307 https://github.com/prometheus/alertmanager/releases/download/v0.34.1/alertmanager-web-ui-0.34.1.tar.gz /ui.tar.gz
WORKDIR /source
RUN tar -xzf /source.tar.gz --strip-components=1 && tar -xzf /ui.tar.gz -C ui/app
# Minimal fixed dependencies for CVE-2026-78669 and CVE-2026-84445.
# Go's checksum database remains enabled; no floating @latest resolution.
RUN go get golang.org/x/net@v0.60.0 google.golang.org/grpc@v1.83.2 && go mod verify
RUN go build -trimpath -buildvcs=false -ldflags='-buildid= -X github.com/prometheus/common/version.Version=0.34.1-cs-s2 -X github.com/prometheus/common/version.Revision=73c6bfe7393929211294c1954f30d8ed78e4d0ad-security-rebuild' -o /out/alertmanager ./cmd/alertmanager && \
    go build -trimpath -buildvcs=false -ldflags='-buildid=' -o /out/amtool ./cmd/amtool && mkdir -p /out/state

FROM gcr.io/distroless/base-debian13:nonroot@sha256:6c591b4ac789b683ab3665327181e7cfb7a39b5834f42dcb73d7becb8d18a5d9
COPY --from=build /out/alertmanager /out/amtool /bin/
COPY --from=build /source/go.mod /source/go.sum /source/LICENSE /source/NOTICE /usr/share/customer-service-alertmanager/
COPY --from=build --chown=65532:65532 /out/state/ /alertmanager/
USER 65532:65532
ENTRYPOINT ["/bin/alertmanager"]
CMD ["--config.file=/etc/alertmanager/alertmanager.yml", "--storage.path=/alertmanager", "--web.listen-address=127.0.0.1:9093", "--cluster.listen-address="]
