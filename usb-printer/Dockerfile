ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-debian:bookworm
FROM ${BUILD_FROM}

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        cups printer-driver-hpcups avahi-daemon dbus \
    && rm -rf /var/lib/apt/lists/*

COPY rootfs /
RUN chmod +x /etc/s6-overlay/s6-rc.d/*/run /usr/share/usb-printer/init.sh
