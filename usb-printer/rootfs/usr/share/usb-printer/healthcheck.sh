#!/bin/bash
set -euo pipefail

timeout 3 lpstat -h localhost:631 -o >/dev/null
dbus-send --system --print-reply --reply-timeout=2000 \
    --dest=org.freedesktop.Avahi / org.freedesktop.Avahi.Server.GetState \
    | grep -q 'int32 2'
