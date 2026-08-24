#!/bin/sh
set -eu

: "${BREWIE_HOST:?Set BREWIE_HOST, for example root@192.168.1.26}"
PACKAGE=${1:?Usage: BREWIE_HOST=root@host $0 release.tar.gz}
SSH_OPTIONS=${BREWIE_SSH_OPTIONS:-}
[ -f "$PACKAGE" ] || { echo "Package not found: $PACKAGE" >&2; exit 1; }

NAME=$(basename "$PACKAGE")
case "$NAME" in
  brewie-next-*.tar.gz|brewienext-*.tar.gz) ;;
  *) echo "Unexpected package name: $NAME" >&2; exit 1 ;;
esac

REMOTE=/tmp/$NAME
REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
LOCAL_INSTALLER="$REPO_ROOT/packaging/relinux/usr/bin/brewie-install-release"
REMOTE_INSTALLER=/tmp/brewie-install-release
[ -f "$LOCAL_INSTALLER" ] || { echo "Target installer is missing: $LOCAL_INSTALLER" >&2; exit 1; }

scp -O $SSH_OPTIONS "$PACKAGE" "$BREWIE_HOST:$REMOTE"
# Always use the installer shipped with this checkout. Older ReLinux images
# may contain an installer that switches the release but does not restart a
# newly added backend service.
scp -O $SSH_OPTIONS "$LOCAL_INSTALLER" "$BREWIE_HOST:$REMOTE_INSTALLER"
ssh $SSH_OPTIONS "$BREWIE_HOST" "chmod +x '$REMOTE_INSTALLER'"
ssh $SSH_OPTIONS "$BREWIE_HOST" "'$REMOTE_INSTALLER' '$REMOTE'"
ssh $SSH_OPTIONS "$BREWIE_HOST" "rm -f '$REMOTE_INSTALLER' '$REMOTE'"
echo "Installed $NAME on $BREWIE_HOST"
