#!/bin/sh
# Aegis — .deb postrm cleanup
#
# fpm (which electron-builder's DebTarget shells out to) wraps this script
# in as the package's actual postrm maintainer script, so it receives the
# standard Debian postrm argument: "remove", "purge", "upgrade", etc. — see
# https://www.debian.org/doc/debian-policy/ch-maintainerscripts.html.
# Only "purge" (dpkg --purge / apt purge, the explicit "and delete my
# config/data too" removal) deletes anything here — a plain "apt remove"
# must leave user data alone, same expectation Debian packaging conventions
# already set for /etc config files.
#
# Runs as root, so $HOME here is root's, not the desktop user's — Aegis's
# data lives under each real user's own ~/.config/Aegis (Electron's
# userData path on Linux), so every /home/* account needs checking, not
# just one. No AppImage equivalent exists for this — AppImage has no
# uninstall hook at all, so this only ever runs for the .deb target.

if [ "$1" = "purge" ]; then
  for home_dir in /root /home/*; do
    data_dir="$home_dir/.config/Aegis"
    if [ -d "$data_dir" ]; then
      rm -rf "$data_dir"
    fi
  done
fi

exit 0
