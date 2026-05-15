#!/bin/bash
HERE="$(cd "$(dirname "$0")" && pwd)"
BUNDLE="$("$HERE/linux_resolve_bundle.sh")"
exec bash "$BUNDLE/start_client.sh" "$@"
