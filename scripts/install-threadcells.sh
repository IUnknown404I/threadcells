#!/usr/bin/env bash
# Install a local ThreadCells source candidate without changing system services.
set -euo pipefail

die() { echo "install-threadcells: $*" >&2; exit 1; }
usage() { echo "usage: $0 [--dry-run] [--source DIR] [--prefix ABSOLUTE_DIR]" >&2; exit 2; }

dry_run=false
source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
prefix="${source_dir}/.threadcells"
while (($#)); do
  case "$1" in
    --dry-run) dry_run=true ;;
    --source) (($# >= 2)) || usage; source_dir="$2"; shift ;;
    --prefix) (($# >= 2)) || usage; prefix="$2"; shift ;;
    -h|--help) usage ;;
    *) usage ;;
  esac
  shift
done

case "$(uname -s)" in Linux) ;; *) die "ThreadCells preview supports Linux only";; esac
for command in python3 tmux uv; do command -v "$command" >/dev/null || die "missing required command: $command"; done
source_dir="$(cd "$source_dir" && pwd -P)"
[[ -f "$source_dir/candidate-manifest.json" && -f "$source_dir/LICENSE" && -f "$source_dir/uv.lock" && -f "$source_dir/SHA256SUMS" ]] || die "source is not a built ThreadCells candidate"
shopt -s nullglob
wheels=("$source_dir"/packages/threadcells-*.whl)
(( ${#wheels[@]} == 1 )) || die "candidate must contain exactly one ThreadCells wheel"
case "$prefix" in /*) ;; *) die "--prefix must be an absolute path";; esac
prefix="$(realpath -m "$prefix")"
[[ "$prefix" != "/" ]] || die "refusing to use filesystem root as a prefix"
[[ ! -e "$prefix" ]] || die "refusing to overwrite existing prefix: $prefix"
parent="$(dirname "$prefix")"
[[ -d "$parent" ]] || die "prefix parent does not exist: $parent"

(cd "$source_dir" && sha256sum -c SHA256SUMS >/dev/null) || die "candidate checksum verification failed"

echo "source: $source_dir"
echo "candidate prefix: $prefix"
echo "service address: 127.0.0.1:9889"
echo "no provider credentials, service manager, proxy, or network listener will be configured"
if "$dry_run"; then exit 0; fi

umask 077
base="$(basename "$prefix")"
stage="$(mktemp -d "$parent/.${base}.install.XXXXXX")"
cleanup() { [[ -d "$stage" ]] && rm -rf -- "$stage"; }
trap cleanup EXIT
uv venv "$stage/venv"
(cd "$source_dir" && UV_PROJECT_ENVIRONMENT="$stage/venv" uv sync --locked --no-dev --no-install-project)
uv pip install --python "$stage/venv/bin/python" --no-deps "${wheels[0]}"
mkdir -p "$stage/state" "$stage/etc"
while IFS= read -r -d '' entrypoint; do
  if grep -Iq -- "$stage" "$entrypoint"; then
    sed -i "s|$stage|$prefix|g" "$entrypoint"
  fi
done < <(find "$stage/venv/bin" -maxdepth 1 -type f -print0)
mv -- "$stage" "$prefix"
trap - EXIT
echo "Installed local candidate at $prefix"
echo "Start manually: $prefix/venv/bin/threadcells-server --host 127.0.0.1 --port 9889"
