#!/usr/bin/env bash
# Baut das Tischlein-Image und pusht es optional (mise run docker:build / docker:publish).
#   VERSION  Version im Image (Release-Tag ohne "v"), Standard: dev
#   IMAGE    Ziel-Repository, Standard: tischlein (lokal) – für Releases ghcr.io/schowave/tischlein
#   PUSH=1   Image nach dem Bauen pushen (docker login vorher nötig)
# Die mise-Version im Image ist min_version aus mise.toml, nicht die zufällig lokal installierte.
set -euo pipefail
version="${VERSION:-dev}"
image="${IMAGE:-tischlein}"
mise_version="$(sed -n 's/^min_version = "\([^"]*\)".*/\1/p' mise.toml)"
revision="$(git rev-parse --short HEAD)"
test -n "$mise_version"

tags=(-t "$image:$version" -t "$image:$revision")
if [[ "$version" =~ ^([0-9]+)\.([0-9]+)\.[0-9]+$ ]]; then
  # Release: zusätzlich Major.Minor und latest – darauf hört das Auto-Update im LXC
  tags+=(-t "$image:${BASH_REMATCH[1]}.${BASH_REMATCH[2]}" -t "$image:latest")
elif [ "$version" = "dev" ]; then
  tags+=(-t "$image:latest")
fi

docker build \
  --build-arg MISE_VERSION="$mise_version" \
  --build-arg VERSION="$version" \
  --build-arg REVISION="$revision" \
  "${tags[@]}" .

if [ "${PUSH:-0}" = "1" ]; then
  for ((i = 1; i < ${#tags[@]}; i += 2)); do docker push "${tags[$i]}"; done
fi
