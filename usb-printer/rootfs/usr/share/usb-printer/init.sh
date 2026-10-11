#!/bin/bash
set -eu

mkdir -p /data /run/dbus /run/cups
if [ ! -d /data/cups ]; then
    cp -a /etc/cups /data/cups
fi
rm -rf /etc/cups
ln -s /data/cups /etc/cups
cp /usr/share/usb-printer/cupsd.conf /etc/cups/cupsd.conf
cp /usr/share/usb-printer/avahi-daemon.conf /etc/avahi/avahi-daemon.conf

mkdir -p /data/spool /data/cache
chown root:lp /data/spool /data/cache
chmod 710 /data/spool
chmod 775 /data/cache
rm -rf /var/spool/cups /var/cache/cups
ln -s /data/spool /var/spool/cups
ln -s /data/cache /var/cache/cups
sed -i -e 's|^ErrorLog .*|ErrorLog stderr|' -e '/^FatalErrors /d' /etc/cups/cups-files.conf
printf '\nFatalErrors config browse\n' >> /etc/cups/cups-files.conf

if [ ! -d /sys/class/net/end0 ]; then
    echo "Bonjour requires the Green LAN interface end0." >&2
    exit 1
fi
