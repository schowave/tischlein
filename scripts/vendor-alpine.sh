#!/usr/bin/env bash
# Legt Alpine.js als feste Datei unter src/tischlein/static/vendor/ ab.
# Version und npm-Prüfsumme kommen aus mise.toml (Task vendor:alpine), die Datei wird nur
# übernommen, wenn die Prüfsumme des npm-Pakets stimmt.
set -euo pipefail
version="${ALPINE_VERSION:?ALPINE_VERSION fehlt}"
integrity="${ALPINE_INTEGRITY:?ALPINE_INTEGRITY fehlt}"
target="src/tischlein/static/vendor"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

curl -fsSL -o "$work/alpine.tgz" "https://registry.npmjs.org/alpinejs/-/alpinejs-${version}.tgz"
actual="sha512-$(openssl dgst -sha512 -binary "$work/alpine.tgz" | openssl base64 -A)"
if [ "$actual" != "$integrity" ]; then
  echo "Prüfsumme stimmt nicht: erwartet $integrity, bekommen $actual" >&2
  exit 1
fi
mkdir -p "$work/pkg"
tar -xzf "$work/alpine.tgz" -C "$work/pkg" package/dist/cdn.min.js
mkdir -p "$target"
rm -f "$target"/alpine-*.min.js
cp "$work/pkg/package/dist/cdn.min.js" "$target/alpine-${version}.min.js"
echo "Alpine.js ${version} -> $target/alpine-${version}.min.js"
