#!/bin/sh
# Run once as root on the headless box (re-run after the box is rebuilt).
# DROP_WRITER is the ssh user the Mac pushes as; DROP_READER owns CodexBar's config.
set -eu
DROP_WRITER="${DROP_WRITER:-dann}"
DROP_READER="${DROP_READER:-box}"
d=/var/lib/aiuse-cookie-drop
mkdir -p "$d"
chown "$DROP_WRITER:$DROP_READER" "$d"
chmod 2770 "$d"
ls -ld "$d"
