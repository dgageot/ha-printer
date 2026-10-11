ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-debian:bookworm@sha256:68f4c1a11d10facc5442ff11509a164463f98b154c80dc1dfeedbc267dd71665
FROM ${BUILD_FROM}

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        cups printer-driver-hpcups avahi-daemon dbus \
    && cp -a /etc/cups /usr/share/cups-defaults \
    && rm -rf /var/lib/apt/lists/*

COPY rootfs /
RUN chmod +x /etc/s6-overlay/s6-rc.d/*/run /usr/share/usb-printer/*.sh

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD /usr/share/usb-printer/healthcheck.sh
