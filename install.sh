#!/bin/sh
# Reality Diff installer
#
#   curl -fsSL https://raw.githubusercontent.com/kavin0x/RealityDiff/main/install.sh | sh
#
# Installs uv (if missing) into your home directory and realitydiff as an
# isolated uv tool. No sudo, and it never edits your shell rc files unless you
# say yes. Run with --help for options.

# Everything lives in main() so a partial download runs nothing, and main's
# stdin is /dev/null so no child command can read the rest of this script
# from a curl pipe. Prompts read /dev/tty directly.
main() {
  set -eu

  if [ -z "${HOME:-}" ] || [ ! -d "$HOME" ]; then
    echo "error: HOME is not set or does not exist" >&2
    exit 1
  fi

  REPO="${REALITYDIFF_REPO:-kavin0x/RealityDiff}"
  REF="main"
  SOURCE=""
  ASSUME_YES=0
  UNINSTALL=0
  PURGE=0
  SKIP_KEY=0
  INSTALL_UV=1

  CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/realitydiff"
  CONFIG_FILE="$CONFIG_DIR/env"
  LOG_FILE=""

  usage() {
    cat <<USAGE
Reality Diff installer

Usage: install.sh [options]
   or: curl -fsSL https://raw.githubusercontent.com/$REPO/main/install.sh | sh -s -- [options]

Options:
  -y, --yes            Non-interactive: accept defaults, never prompt
  --ref REF            Install a tag, branch or commit (default: main)
  --version REF        Same as --ref
  --from PATH|URL      Install from a local checkout or a package URL instead
  --no-key             Skip the XAI_API_KEY prompt
  --no-uv              Fail instead of installing uv if it is missing
  --uninstall          Remove the realitydiff tool (keeps your config and data)
  --purge              With --uninstall, also delete $CONFIG_FILE
  -h, --help           Show this help

Environment:
  REALITYDIFF_REPO     GitHub owner/name to install from (default kavin0x/RealityDiff)
  NO_COLOR             Disable colors
  REALITYDIFF_ALLOW_ROOT=1  Allow running as root (e.g. in a container)
USAGE
  }

  # ---------------------------------------------------------------- output

  if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != "dumb" ]; then
    FANCY=1
    OK_MARK='✓'; X_MARK='✗'
    ESC=$(printf '\033')
    BOLD="${ESC}[1m"; DIM="${ESC}[2m"; RED="${ESC}[31m"; GREEN="${ESC}[32m"
    YELLOW="${ESC}[33m"; CYAN="${ESC}[36m"; RESET="${ESC}[0m"
  else
    FANCY=0
    OK_MARK='[ok]'; X_MARK='[x]'
    BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; CYAN=""; RESET=""
  fi

  HAVE_GUM=0
  if [ "$FANCY" = 1 ] && command -v gum >/dev/null 2>&1; then HAVE_GUM=1; fi

  say()  { printf '%s\n' "$*"; }
  info() { printf '  %s%s%s\n' "$DIM" "$*" "$RESET"; }
  ok()   { printf '  %s%s%s %s\n' "$GREEN" "$OK_MARK" "$RESET" "$*"; }
  warn() { printf '  %s!%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
  die() {
    printf '\n  %s%s error:%s %s\n' "$RED" "$X_MARK" "$RESET" "$*" >&2
    if [ -n "$LOG_FILE" ] && [ -s "$LOG_FILE" ]; then
      printf '  %slast lines of output:%s\n' "$DIM" "$RESET" >&2
      tail -n 15 "$LOG_FILE" | sed 's/^/    /' >&2
    fi
    exit 1
  }

  banner() {
    if [ "$FANCY" = 1 ]; then
      printf '\n%s%s' "$BOLD" "$CYAN"
      cat <<'ART'
  ___          _ _ _        ___  _  __  __
 | _ \___ __ _| (_) |_ _  _|   \(_)/ _|/ _|
 |   / -_) _` | | |  _| || | |) | |  _|  _|
 |_|_\___\__,_|_|_|\__|\_, |___/|_|_| |_|
                       |__/
ART
      printf '%s  %sgit for beliefs%s\n\n' "$RESET" "$DIM" "$RESET"
    else
      say "Reality Diff installer"
      say ""
    fi
  }

  STEP=0
  STEPS=4
  step() {
    STEP=$((STEP + 1))
    printf '%s[%d/%d]%s %s%s%s\n' "$DIM" "$STEP" "$STEPS" "$RESET" "$BOLD" "$*" "$RESET"
  }

  # run "label" cmd args...  -> spinner on a TTY, plain line otherwise.
  # Command output goes to $LOG_FILE and is shown only on failure.
  run() {
    label=$1; shift
    : >"$LOG_FILE"
    # gum can only spin real executables, not shell functions
    use_gum=0
    if [ "$HAVE_GUM" = 1 ]; then
      case $(command -v "$1" 2>/dev/null) in /*) use_gum=1 ;; esac
    fi
    if [ "$use_gum" = 1 ]; then
      if gum spin --spinner dot --title "$label" -- "$@" >>"$LOG_FILE" 2>&1; then
        ok "$label"; return 0
      fi
      die "$label failed"
    fi
    if [ "$FANCY" = 0 ]; then
      "$@" >>"$LOG_FILE" 2>&1 || die "$label failed"
      ok "$label"
      return 0
    fi
    "$@" >>"$LOG_FILE" 2>&1 &
    pid=$!
    i=0
    printf '\033[?25l'
    while kill -0 "$pid" 2>/dev/null; do
      case $((i % 10)) in
        0) c='⠋';; 1) c='⠙';; 2) c='⠹';; 3) c='⠸';; 4) c='⠼';;
        5) c='⠴';; 6) c='⠦';; 7) c='⠧';; 8) c='⠇';; *) c='⠏';;
      esac
      printf '\r  %s%s%s %s' "$CYAN" "$c" "$RESET" "$label"
      i=$((i + 1))
      sleep 0.1 2>/dev/null || sleep 1
    done
    printf '\r\033[K\033[?25h'
    if wait "$pid"; then ok "$label"; else die "$label failed"; fi
  }

  # ---------------------------------------------------------------- prompts

  TTY=0

  # confirm "question" default(y|n)
  confirm() {
    q=$1; def=$2
    if [ "$ASSUME_YES" = 1 ] || [ "$TTY" = 0 ]; then [ "$def" = y ]; return; fi
    if [ "$HAVE_GUM" = 1 ]; then
      if [ "$def" = y ]; then gum confirm "$q" </dev/tty; else gum confirm --default=false "$q" </dev/tty; fi
      return
    fi
    if [ "$def" = y ]; then hint="[Y/n]"; else hint="[y/N]"; fi
    printf '  %s?%s %s %s ' "$CYAN" "$RESET" "$q" "$hint" >/dev/tty
    read -r ans </dev/tty || ans=""
    case $ans in
      [Yy]|[Yy][Ee][Ss]) return 0 ;;
      [Nn]|[Nn][Oo]) return 1 ;;
      *) [ "$def" = y ] ;;
    esac
  }

  ECHO_OFF=0
  restore_tty() {
    if [ "$ECHO_OFF" = 1 ]; then
      { stty echo </dev/tty; } 2>/dev/null || true
      ECHO_OFF=0
    fi
  }

  # read_secret "prompt" -> sets SECRET, input not echoed.
  # Returns 1 (and reads nothing) if echo cannot be turned off.
  read_secret() {
    SECRET=""
    if [ "$HAVE_GUM" = 1 ]; then
      SECRET=$(gum input --password --placeholder "$1" </dev/tty) || SECRET=""
      return
    fi
    trap 'restore_tty; exit 130' INT TERM
    if ! { stty -echo </dev/tty; } 2>/dev/null; then
      trap 'exit 130' INT TERM
      warn "can't hide typing on this terminal, so not asking for the key."
      warn "export XAI_API_KEY instead, or put XAI_API_KEY=... in $(tilde "$CONFIG_FILE")"
      return 1
    fi
    ECHO_OFF=1
    printf '  %s?%s %s ' "$CYAN" "$RESET" "$1" >/dev/tty
    IFS= read -r SECRET </dev/tty || SECRET=""
    restore_tty
    trap 'exit 130' INT TERM
    printf '\n' >/dev/tty
  }

  # ---------------------------------------------------------------- helpers

  have() { command -v "$1" >/dev/null 2>&1; }

  # Show paths under $HOME as ~/...
  tilde() {
    case $1 in
      "$HOME"/*) printf '~%s' "${1#"$HOME"}" ;;
      *) printf '%s' "$1" ;;
    esac
  }

  download() { # url dest
    if have curl; then curl -fsSL --proto '=https' --tlsv1.2 "$1" -o "$2"
    elif have wget; then wget -q --https-only -O "$2" "$1"
    else die "need curl or wget to download $1"; fi
  }

  find_uv() {
    UV=""
    if have uv; then UV=$(command -v uv); return 0; fi
    for d in "${XDG_BIN_HOME:-}" "$HOME/.local/bin" "$HOME/.cargo/bin"; do
      if [ -n "$d" ] && [ -x "$d/uv" ]; then UV="$d/uv"; return 0; fi
    done
    return 1
  }

  detect_platform() {
    os=$(uname -s 2>/dev/null || echo unknown)
    arch=$(uname -m 2>/dev/null || echo unknown)
    case $os in
      Darwin) OS=macos ;;
      Linux) OS=linux ;;
      MINGW*|MSYS*|CYGWIN*) die "Windows is not supported by this script. Use: uv tool install \"realitydiff @ https://github.com/$REPO/archive/$REF.tar.gz\"" ;;
      *) die "unsupported OS: $os" ;;
    esac
    case $arch in
      x86_64|amd64) ARCH=x86_64 ;;
      arm64|aarch64) ARCH=arm64 ;;
      *) die "unsupported CPU: $arch" ;;
    esac
  }

  # Rewrite CONFIG_FILE with KEY=VALUE, keeping other lines. Value never printed.
  set_config() {
    key=$1; value=$2
    old_umask=$(umask)
    umask 077
    mkdir -p "$CONFIG_DIR"
    chmod 700 "$CONFIG_DIR" 2>/dev/null || true
    tmp="$CONFIG_FILE.tmp.$$"
    : >"$tmp"
    chmod 600 "$tmp"
    if [ -f "$CONFIG_FILE" ]; then
      grep -Ev "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" "$CONFIG_FILE" >>"$tmp" || true
    else
      printf '# Reality Diff settings (read by the realitydiff CLI). Keep private.\n' >>"$tmp"
    fi
    printf '%s=%s\n' "$key" "$value" >>"$tmp"
    mv -f "$tmp" "$CONFIG_FILE"
    chmod 600 "$CONFIG_FILE"
    umask "$old_umask"
  }

  config_has() {
    [ -f "$CONFIG_FILE" ] && grep -Eq "^[[:space:]]*(export[[:space:]]+)?${1}[[:space:]]*=[[:space:]]*[^[:space:]]" "$CONFIG_FILE"
  }

  # ---------------------------------------------------------------- args

  while [ $# -gt 0 ]; do
    case $1 in
      -y|--yes|--non-interactive) ASSUME_YES=1 ;;
      --version|--ref)
        [ $# -ge 2 ] || { printf 'error: %s needs a value\n' "$1" >&2; exit 2; }
        REF=$2; shift ;;
      --version=*|--ref=*) REF=${1#*=} ;;
      --from)
        [ $# -ge 2 ] || { printf 'error: --from needs a value\n' >&2; exit 2; }
        SOURCE=$2; shift ;;
      --from=*) SOURCE=${1#*=} ;;
      --no-key) SKIP_KEY=1 ;;
      --no-uv) INSTALL_UV=0 ;;
      --uninstall) UNINSTALL=1 ;;
      --purge) PURGE=1 ;;
      -h|--help) usage; exit 0 ;;
      *) printf 'error: unknown option %s (try --help)\n' "$1" >&2; exit 2 ;;
    esac
    shift
  done

  # re-evaluate now that --yes is known
  TTY=0
  if [ "$ASSUME_YES" = 0 ] && (exec </dev/tty) 2>/dev/null; then TTY=1; fi

  if [ "$PURGE" = 1 ] && [ "$UNINSTALL" = 0 ]; then
    printf 'error: --purge only works with --uninstall\n' >&2
    exit 2
  fi

  case $REF in
    ''|*[!A-Za-z0-9._/-]*) printf 'error: invalid --ref %s\n' "$REF" >&2; exit 2 ;;
  esac

  [ "$(id -u)" = 0 ] && [ -z "${REALITYDIFF_ALLOW_ROOT:-}" ] && {
    printf 'error: do not run this installer as root or with sudo; it installs into your home directory.\n' >&2
    exit 1
  }

  LOG_FILE=$(mktemp "${TMPDIR:-/tmp}/realitydiff-install.XXXXXX")
  cleanup() {
    [ "$FANCY" = 1 ] && printf '\033[?25h'
    restore_tty
    rm -f "$LOG_FILE" "$CONFIG_FILE.tmp.$$" "${UV_SCRIPT:-}"
  }
  trap cleanup EXIT
  trap 'exit 130' INT TERM
  UV_SCRIPT=""

  # ---------------------------------------------------------------- uninstall

  if [ "$UNINSTALL" = 1 ]; then
    banner
    STEPS=2
    step "Removing realitydiff"
    if find_uv && "$UV" tool list 2>/dev/null | grep -q '^realitydiff '; then
      run "uv tool uninstall realitydiff" "$UV" tool uninstall realitydiff
    else
      ok "realitydiff is not installed (nothing to do)"
    fi
    step "Config"
    if [ -f "$CONFIG_FILE" ]; then
      if [ "$PURGE" = 1 ] || { [ "$ASSUME_YES" = 0 ] && confirm "Delete $(tilde "$CONFIG_FILE") (contains your API key)?" n; }; then
        rm -f "$CONFIG_FILE"; rmdir "$CONFIG_DIR" 2>/dev/null || true
        ok "deleted $(tilde "$CONFIG_FILE")"
      else
        info "kept $(tilde "$CONFIG_FILE") (use --uninstall --purge to delete)"
      fi
    else
      ok "no config file"
    fi
    data_dir="${XDG_DATA_HOME:-$HOME/.local/share}/realitydiff"
    [ -d "$data_dir" ] && info "your claims are still in $(tilde "$data_dir") (delete it by hand if you want)"
    info "uv itself was left installed"
    say ""
    say "${GREEN}${BOLD}Reality Diff removed.${RESET}"
    exit 0
  fi

  # ---------------------------------------------------------------- install

  banner
  STEPS=4

  step "Checking your system"
  detect_platform
  ok "platform: $OS $ARCH"

  step "uv (Python package manager)"
  if find_uv; then
    ok "uv found: $(tilde "$UV") ($("$UV" --version 2>/dev/null | awk '{print $2}'))"
  else
    [ "$INSTALL_UV" = 1 ] || die "uv is not installed. Get it from https://docs.astral.sh/uv/ and re-run."
    if [ "$TTY" = 0 ] && [ "$ASSUME_YES" = 0 ]; then
      die "uv is not installed and there is no terminal to ask. Re-run with --yes to let this script install uv, or install it from https://docs.astral.sh/uv/"
    fi
    confirm "uv is not installed. Install it into ~/.local/bin (no sudo)?" y || die "uv is required. Install it from https://docs.astral.sh/uv/ and re-run."
    UV_SCRIPT=$(mktemp "${TMPDIR:-/tmp}/uv-install.XXXXXX")
    run "downloading uv installer" download "https://astral.sh/uv/install.sh" "$UV_SCRIPT"
    run "installing uv" env UV_NO_MODIFY_PATH=1 INSTALLER_NO_MODIFY_PATH=1 sh "$UV_SCRIPT" --quiet
    find_uv || die "uv installed but could not be found in ~/.local/bin"
    ok "uv installed: $(tilde "$UV")"
  fi

  step "Installing realitydiff"
  if [ -n "$SOURCE" ]; then
    SPEC=$SOURCE
    case $SOURCE in
      http://*|https://*|git+*) SPEC="realitydiff @ $SOURCE" ;;
      *) [ -e "$SOURCE" ] || die "--from path not found: $SOURCE" ;;
    esac
    info "source: $SOURCE"
  else
    SPEC="realitydiff @ https://github.com/$REPO/archive/$REF.tar.gz"
    info "source: github.com/$REPO @ $REF"
  fi
  run "uv tool install (isolated env, Python >= 3.11)" \
    "$UV" tool install --force --reinstall --refresh-package realitydiff "$SPEC"

  BIN_DIR=$("$UV" tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")
  RD="$BIN_DIR/realitydiff"
  [ -x "$RD" ] || die "install finished but $RD is missing"
  if ! VERSION=$("$RD" --version 2>/dev/null); then
    "$RD" --help >/dev/null 2>&1 || die "realitydiff was installed but does not run: $RD --help"
    VERSION="realitydiff"
  fi
  ok "$VERSION -> $(tilde "$RD")"

  step "xAI API key"
  if [ "$SKIP_KEY" = 1 ]; then
    info "skipped (--no-key)"
  elif config_has XAI_API_KEY; then
    ok "already saved in $(tilde "$CONFIG_FILE")"
    if [ "$TTY" = 1 ] && confirm "Replace the saved key?" n; then
      if read_secret "New XAI_API_KEY (input hidden):"; then SAVE_KEY=$SECRET; fi
    fi
  elif [ "$TTY" = 1 ]; then
    if [ -n "${XAI_API_KEY:-}" ]; then
      info "XAI_API_KEY is set in this shell"
      if confirm "Save it to $(tilde "$CONFIG_FILE") so realitydiff always finds it?" y; then SAVE_KEY=$XAI_API_KEY; fi
    elif confirm "Save an XAI_API_KEY now? (get one at https://console.x.ai)" y; then
      if read_secret "XAI_API_KEY (input hidden):"; then SAVE_KEY=$SECRET; fi
    fi
  else
    info "not saved (non-interactive). Export XAI_API_KEY or re-run without --yes."
  fi
  if [ -n "${SAVE_KEY:-}" ]; then
    case $SAVE_KEY in
      *[!A-Za-z0-9._-]*) warn "that key has unexpected characters (spaces, quotes or newlines?); not saved" ;;
      *) set_config XAI_API_KEY "$SAVE_KEY"; ok "saved to $(tilde "$CONFIG_FILE") (permissions 600)" ;;
    esac
    SAVE_KEY=""; SECRET=""
  elif [ "$SKIP_KEY" = 0 ] && ! config_has XAI_API_KEY && [ "$TTY" = 1 ]; then
    info "skipped. You can export XAI_API_KEY later or re-run this installer."
  fi

  # PATH check
  ON_PATH=0
  case ":${PATH:-}:" in *":$BIN_DIR:"*) ON_PATH=1 ;; esac
  if [ "$ON_PATH" = 0 ]; then
    warn "$(tilde "$BIN_DIR") is not on your PATH"
    if [ "$TTY" = 1 ] && confirm "Add it to your shell config (runs 'uv tool update-shell')?" n; then
      run "updating shell config" "$UV" tool update-shell
      info "open a new terminal for it to take effect"
    else
      case $BIN_DIR in
        "$HOME"/*) rc_dir="\$HOME${BIN_DIR#"$HOME"}" ;;
        *) rc_dir=$BIN_DIR ;;
      esac
      info "add this to your shell rc:  export PATH=\"$rc_dir:\$PATH\""
    fi
  fi

  CMD=realitydiff
  [ "$ON_PATH" = 1 ] || CMD=$(tilde "$RD")
  say ""
  say "${GREEN}${BOLD}Reality Diff is installed.${RESET}"
  say ""
  say "  ${DIM}binary${RESET}   $(tilde "$RD")"
  say "  ${DIM}config${RESET}   $(tilde "$CONFIG_FILE")"
  say "  ${DIM}data${RESET}     $(tilde "${XDG_DATA_HOME:-$HOME/.local/share}/realitydiff")/"
  say ""
  say "  Next:"
  say "    ${BOLD}$CMD serve --host 127.0.0.1${RESET}"
  say "    ${DIM}then open http://127.0.0.1:8000${RESET}"
  say "    ${BOLD}$CMD open \"Apple will replace Siri with an LLM\"${RESET}"
  say ""
  say "  ${DIM}Uninstall:${RESET}"
  say "    ${DIM}curl -fsSL https://raw.githubusercontent.com/$REPO/main/install.sh | sh -s -- --uninstall${RESET}"
}

main "$@" </dev/null
