#!/usr/bin/env bash
# Extract a Texas + New Mexico PMTiles slice from the Protomaps daily build.
#
# Uses the `pmtiles` CLI (go-pmtiles) to do an HTTP-range extract directly
# against the daily build URL — no need to download the ~107 GB planet.
# Output: ./permian.pmtiles  (gitignored)
#
# Requires: bash, curl, tar/unzip. Auto-downloads the pmtiles binary into
# .bin/ on first run.
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
OUT_FILE="${SCRIPT_DIR}/permian.pmtiles"
BIN_DIR="${SCRIPT_DIR}/.bin"
PMTILES_BIN="${BIN_DIR}/pmtiles"

# Texas + New Mexico bounding box (W,S,E,N). Loose enough to cover the
# entire Permian + surrounding context for navigation.
BBOX_W=-109.10
BBOX_S=25.80
BBOX_E=-93.50
BBOX_N=37.05
MAX_ZOOM=12

detect_platform() {
    local os arch
    os="$(uname -s)"
    arch="$(uname -m)"
    case "${os}" in
        Linux)  os="Linux" ;;
        Darwin) os="Darwin" ;;
        MINGW*|MSYS*|CYGWIN*) os="Windows" ;;
        *) echo "Unsupported OS: ${os}" >&2; exit 1 ;;
    esac
    case "${arch}" in
        x86_64|amd64) arch="x86_64" ;;
        arm64|aarch64) arch="arm64" ;;
        *) echo "Unsupported arch: ${arch}" >&2; exit 1 ;;
    esac
    echo "${os}_${arch}"
}

# Look up the latest go-pmtiles release dynamically and resolve the matching
# asset URL. Avoids stale-version drift and asset-naming guesswork.
install_pmtiles() {
    local platform asset_url asset_name
    platform="$(detect_platform)"
    mkdir -p "${BIN_DIR}"

    echo "Looking up latest go-pmtiles release..."
    asset_url="$(curl -fsSL "https://api.github.com/repos/protomaps/go-pmtiles/releases/latest" \
        | grep '"browser_download_url"' \
        | grep -E "${platform}\\.(zip|tar\\.gz)" \
        | head -1 \
        | sed -E 's/.*"(https:[^"]+)".*/\1/')"
    if [[ -z "${asset_url}" ]]; then
        echo "No go-pmtiles asset matching ${platform}" >&2
        exit 1
    fi
    asset_name="$(basename "${asset_url}")"

    echo "Downloading ${asset_name}"
    curl -fsSL -o "${BIN_DIR}/${asset_name}" "${asset_url}"

    if [[ "${platform}" == Windows_* ]]; then
        ( cd "${BIN_DIR}" && unzip -o "${asset_name}" pmtiles.exe >/dev/null )
        PMTILES_BIN="${BIN_DIR}/pmtiles.exe"
    else
        tar -xzf "${BIN_DIR}/${asset_name}" -C "${BIN_DIR}" pmtiles
        chmod +x "${PMTILES_BIN}"
    fi
    rm -f "${BIN_DIR}/${asset_name}"
}

# Resolve the most recent published daily build by walking back day-by-day
# until we find one that exists. The Protomaps team publishes most days but
# occasionally skips one.
find_latest_build() {
    local d url
    for offset in 1 2 3 4 5 6 7; do
        if date -u -d "${offset} days ago" >/dev/null 2>&1; then
            d="$(date -u -d "${offset} days ago" +%Y%m%d)"
        else
            # BSD date (macOS)
            d="$(date -u -v-${offset}d +%Y%m%d)"
        fi
        url="https://build.protomaps.com/${d}.pmtiles"
        if curl -fsSI "${url}" -o /dev/null; then
            echo "${url}"
            return 0
        fi
    done
    echo "Could not locate a recent Protomaps daily build" >&2
    return 1
}

main() {
    if [[ -f "${OUT_FILE}" ]] && [[ "${1:-}" != "--force" ]]; then
        echo "PMTiles file already present at ${OUT_FILE} — pass --force to re-fetch."
        exit 0
    fi

    if [[ ! -x "${PMTILES_BIN}" ]] && [[ ! -x "${PMTILES_BIN}.exe" ]]; then
        install_pmtiles
    fi
    [[ -x "${PMTILES_BIN}.exe" ]] && PMTILES_BIN="${PMTILES_BIN}.exe"

    echo "Resolving latest Protomaps daily build..."
    SOURCE_URL="$(find_latest_build)"
    echo "Source: ${SOURCE_URL}"
    echo "BBox:   ${BBOX_W},${BBOX_S},${BBOX_E},${BBOX_N}  (maxzoom ${MAX_ZOOM})"
    echo "Output: ${OUT_FILE}"
    echo

    "${PMTILES_BIN}" extract "${SOURCE_URL}" "${OUT_FILE}" \
        --bbox="${BBOX_W},${BBOX_S},${BBOX_E},${BBOX_N}" \
        --maxzoom="${MAX_ZOOM}"

    echo
    echo "Done. Wrote $(du -h "${OUT_FILE}" | cut -f1) to ${OUT_FILE}"
}

main "$@"
