#!/usr/bin/env bash
# Claude-Code-Hook (SessionStart, siehe .claude/settings.json): meldet, wenn die Arbeitskopie hinter
# ihrem Upstream liegt. Der Git-Status zum Sitzungsstart zeigt das nicht; ohne die Meldung baut ein
# Agent auf altem Stand. Ohne Netz oder ohne Upstream bleibt der Hook still.
set -u
cd "$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=5" git fetch --quiet 2>/dev/null || exit 0
upstream=$(git rev-parse --abbrev-ref '@{upstream}' 2>/dev/null) || exit 0
behind=$(git rev-list --count "HEAD..$upstream" 2>/dev/null) || exit 0
if [ "$behind" -gt 0 ]; then
  echo "Die Arbeitskopie liegt $behind Commit(s) hinter $upstream. Vor dem Lesen oder Ändern von Code: git pull --ff-only."
fi
exit 0
