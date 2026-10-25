ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-debian:bookworm
FROM ${BUILD_FROM}

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        cups printer-driver-hpcups avahi-daemon avahi-utils dbus iproute2 \
    && cp -a /etc/cups /usr/share/cups-defaults \
    && rm -rf /var/lib/apt/lists/*

COPY rootfs /
RUN chmod +x /etc/s6-overlay/s6-rc.d/*/run /usr/share/usb-printer/*.sh

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD /usr/share/usb-printer/healthcheck.sh
