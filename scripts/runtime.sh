#!/usr/bin/env bash

ledger_python() {
  local env_name="${LEDGER_AGENT_ENV_NAME:-financial-agent}"
  local candidate=""
  local manager=""
  local env_prefix=""

  if [[ -n "${LEDGER_AGENT_PYTHON:-}" ]]; then
    if [[ -x "$LEDGER_AGENT_PYTHON" ]]; then
      printf '%s\n' "$LEDGER_AGENT_PYTHON"
      return 0
    fi
    printf 'LEDGER_AGENT_PYTHON is not executable: %s\n' "$LEDGER_AGENT_PYTHON" >&2
    return 1
  fi

  if [[ -n "${LEDGER_AGENT_ENV_PREFIX:-}" ]]; then
    candidate="$LEDGER_AGENT_ENV_PREFIX/bin/python"
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
    printf 'No Python executable in LEDGER_AGENT_ENV_PREFIX: %s\n' "$LEDGER_AGENT_ENV_PREFIX" >&2
    return 1
  fi

  if [[ -n "${CONDA_PREFIX:-}" && "$(basename "$CONDA_PREFIX")" == "$env_name" ]]; then
    candidate="$CONDA_PREFIX/bin/python"
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  fi

  for manager in mamba micromamba conda; do
    command -v "$manager" >/dev/null 2>&1 || continue
    env_prefix="$(
      "$manager" env list 2>/dev/null |
        awk -v name="$env_name" '$1 == name { print $NF; exit }'
    )"
    if [[ -n "$env_prefix" && -x "$env_prefix/bin/python" ]]; then
      printf '%s\n' "$env_prefix/bin/python"
      return 0
    fi
  done

  for env_prefix in \
    "${MAMBA_ROOT_PREFIX:-}/envs/$env_name" \
    "$HOME/miniforge3/envs/$env_name" \
    "$HOME/mambaforge/envs/$env_name" \
    "$HOME/miniconda3/envs/$env_name" \
    "$HOME/anaconda3/envs/$env_name"; do
    [[ "$env_prefix" == /envs/* ]] && continue
    candidate="$env_prefix/bin/python"
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  printf 'Unable to find the mamba environment "%s".\n' "$env_name" >&2
  printf 'Create it with: mamba env create -f environment.yml\n' >&2
  printf 'Or set LEDGER_AGENT_PYTHON to its Python executable.\n' >&2
  return 1
}

ledger_outbound_proxy() {
  local explicit_proxy="${LEDGER_AGENT_PROXY_URL:-}"
  local proxy_port="${LEDGER_AGENT_PROXY_PORT:-7890}"
  local windows_host=""
  local candidate=""

  if [[ -n "$explicit_proxy" ]]; then
    printf '%s\n' "$explicit_proxy"
    return 0
  fi
  [[ "${LEDGER_AGENT_AUTO_PROXY:-1}" != "0" ]] || return 0
  command -v curl >/dev/null 2>&1 || return 0

  windows_host="$(ip route show default 2>/dev/null | awk '{print $3; exit}')"
  [[ -n "$windows_host" ]] || return 0
  candidate="http://${windows_host}:${proxy_port}"

  # A successful HTTP response, including Ark's expected unauthenticated 401,
  # proves the local proxy can carry the model provider's HTTPS traffic.
  if curl --noproxy '' -x "$candidate" --connect-timeout 2 --max-time 3 \
    -sS -o /dev/null https://ark.cn-beijing.volces.com/api/v3/models; then
    printf '%s\n' "$candidate"
  fi
}
