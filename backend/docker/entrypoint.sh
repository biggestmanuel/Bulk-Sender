#!/bin/sh
# Wraps gunicorn in Xvfb.
#
# The automation launches a real Chrome and needs a display. In a container
# there is none, so Chrome would fail to start — or start and render nothing,
# which breaks WhatsApp's own layout-dependent selectors.
#
# Xvfb gives it a virtual screen. This is what makes the app hostable at all;
# the free tiers of most PaaS providers offer neither a display nor a
# persistent filesystem, so they cannot run it.

set -e

DISPLAY_NUM="${DISPLAY_NUM:-99}"
SCREEN="${SCREEN:-1280x1024x24}"

if [ "$#" -eq 0 ]; then
    echo "entrypoint: nothing to run" >&2
    exit 1
fi

if command -v Xvfb >/dev/null 2>&1; then
    # -nolisten tcp: the virtual display must not be reachable from outside.
    Xvfb ":${DISPLAY_NUM}" -screen 0 "${SCREEN}" -nolisten tcp &
    XVFB_PID=$!

    # Wait for the display socket rather than sleeping blindly; on a loaded
    # machine a fixed sleep is either too short (Chrome fails to start) or
    # wasted.
    i=0
    while [ ! -e "/tmp/.X11-unix/X${DISPLAY_NUM}" ] && [ "$i" -lt 50 ]; do
        i=$((i + 1))
        sleep 0.1
    done

    if [ ! -e "/tmp/.X11-unix/X${DISPLAY_NUM}" ]; then
        echo "entrypoint: Xvfb did not come up on :${DISPLAY_NUM}" >&2
        exit 1
    fi

    export DISPLAY=":${DISPLAY_NUM}"

    # Forward termination to Xvfb so `docker stop` doesn't wait for the
    # timeout, then run the command.
    trap 'kill "$XVFB_PID" 2>/dev/null || true' TERM INT
fi

"$@"
