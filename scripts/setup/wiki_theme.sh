#!/usr/bin/env bash
# lightBot look for the Wiki.js on server (owner OK 2026-10-08, "make it look better" / "yes go ahead"):
#   site title, sidebar menu (guide/out/wiki-navigation.json), CSS override (guide/wiki-theme.css) and the
#   current-page marker (guide/wiki-body.html), then copies the pages (guide/out/wiki) to ~/wiki-stack/content.
# The pages still need Admin > Storage > Local File System > Import Everything (owner's click).
# Saves the old values as SQL on server first: ~/wiki-stack/theme-rollback-<UTC time>.sql
# Usage (from the Mac, after guide/build.py):
#   scripts/setup/wiki_theme.sh                     apply
#   scripts/setup/wiki_theme.sh rollback <file>     restore a rollback file that is on server
set -euo pipefail
G="$(cd "$(dirname "$0")/../../guide" && pwd)"
PSQL='export PATH=/usr/local/bin:$PATH; docker exec -i wiki-db psql -U wikijs -d wiki -v ON_ERROR_STOP=1 -q'
RESTART='export PATH=/usr/local/bin:$PATH; cd ~/wiki-stack && docker compose restart wiki'

if [ "${1:-}" = rollback ]; then
  ssh server "$PSQL < '$2' && $RESTART"
  exit 0
fi

ts=$(date -u +%Y%m%d%H%M%S)
echo "1/4 rollback file on server: ~/wiki-stack/theme-rollback-$ts.sql"
ssh server 'bash -s' -- "$ts" <<'EOF'
export PATH=/usr/local/bin:$PATH
docker exec -i wiki-db psql -U wikijs -d wiki -At > ~/wiki-stack/theme-rollback-$1.sql <<'SQL'
select 'begin;';
select format('update settings set value = %L::json where key = %L;', value::text, key) from settings where key in ('title', 'theming');
select format('update navigation set config = %L::json where key = %L;', config::text, key) from navigation;
select 'commit;';
SQL
echo "   $(wc -l < ~/wiki-stack/theme-rollback-$1.sql) lines"
EOF

echo "2/4 title, sidebar menu, CSS, body script -> wiki-db"
python3 - "$G" <<'PY' | ssh server "$PSQL"
import json, os, sys
g = sys.argv[1]
css = open(os.path.join(g, 'wiki-theme.css')).read()
body = open(os.path.join(g, 'wiki-body.html')).read()
nav = json.dumps(json.load(open(os.path.join(g, 'out', 'wiki-navigation.json'))))
def dq(s):
    assert '$lb$' not in s
    return '$lb$' + s + '$lb$'
print('begin;')
print(f"update settings set value = {dq(json.dumps({'v': 'lightBot'}))}::json where key = 'title';")
print("update settings set value = (value::jsonb || jsonb_build_object("
      f"'injectCSS', {dq(css)}::text, 'injectBody', {dq(body)}::text))::json where key = 'theming';")
print(f"update navigation set config = {dq(nav)}::json where key = 'site';")
print('commit;')
PY

echo "3/4 changed pages -> ~/wiki-stack/content (never over a page edited in the wiki)"
"$G/wiki_sync.sh"

echo "4/4 restart the wiki container (settings are read at start)"
ssh server "$RESTART"
