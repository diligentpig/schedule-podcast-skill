#!/usr/bin/env bash
# Deprecated name, kept so older callers keep working. Use concat_parts.sh.
exec "$(dirname "$0")/concat_parts.sh" "$@"
