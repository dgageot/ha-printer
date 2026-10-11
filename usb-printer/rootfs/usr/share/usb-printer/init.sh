#!/bin/bash
set -eu

if [ ! -d /sys/class/net/end0 ]; then
    echo "Bonjour requires the Green LAN interface end0." >&2
    exit 1
fi

mkdir -p /data /run/dbus /run/cups
if [ -L /data/cups ]; then
    if [ ! -d /data/cups ] || [ "$(readlink /data/cups)" = /etc/cups ]; then
        rm /data/cups
    else
        echo "/data/cups must be a directory, not a symlink." >&2
        exit 1
    fi
fi
if [ ! -d /data/cups ]; then
    rm -rf /data/cups.init
    cp -a /usr/share/cups-defaults /data/cups.init
    mv /data/cups.init /data/cups
else
    # Repair interrupted initialization without overwriting saved queues.
    if [ -L /data/cups/cups-files.conf ] && [ ! -e /data/cups/cups-files.conf ]; then
        rm /data/cups/cups-files.conf
    fi
    cp -an /usr/share/cups-defaults/. /data/cups/
fi
rm -rf /etc/cups
ln -s /data/cups /etc/cups
cp /usr/share/usb-printer/cupsd.conf /etc/cups/cupsd.conf.tmp
mv /etc/cups/cupsd.conf.tmp /etc/cups/cupsd.conf
cp /usr/share/usb-printer/avahi-daemon.conf /etc/avahi/avahi-daemon.conf

mkdir -p /data/spool /data/cache
chown root:lp /data/spool /data/cache
chmod 710 /data/spool
chmod 775 /data/cache
rm -rf /var/spool/cups /var/cache/cups
ln -s /data/spool /var/spool/cups
ln -s /data/cache /var/cache/cups
{
    sed -e 's|^ErrorLog .*|ErrorLog stderr|' -e '/^FatalErrors /d' \
        -e '/^SyncOnClose /d' /etc/cups/cups-files.conf
    printf 'FatalErrors config browse\nSyncOnClose Yes\n'
} > /etc/cups/cups-files.conf.tmp
chmod --reference=/etc/cups/cups-files.conf /etc/cups/cups-files.conf.tmp
mv /etc/cups/cups-files.conf.tmp /etc/cups/cups-files.conf
cupsd -t
