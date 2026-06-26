# azaks-bastion

Open an Azure Bastion tunnel to a private AKS API server or jump host.

## Install

```bash
pip install azaks-bastion
```

The package installs two console scripts that point at the same Typer app:

| Command         | Use when                              |
| --------------- | ------------------------------------- |
| `azaks-bastion`  | Long form, friendly for scripts       |
| `aksb` | Short alias for interactive shell use |

## Usage

```bash
aksb --help
aksb --version
```

### Discover Bastion hosts

```bash
# Table of Bastion hosts in the current subscription
aksb list

# Machine-readable, never truncated (good for scripts / piping)
aksb list --json
aksb list -s my-subscription
```

### Open a tunnel to a private AKS jump host or API server

`aksb tunnel` wraps `az network bastion tunnel`, forwarding a local port to a
target's port through a Standard-SKU Bastion host. The target may be a full ARM
resource id or a VM name (with `--target-rg`).

```bash
# SSH jump host: forward an OS-assigned local port to the VM's port 22
aksb tunnel --bastion prod-hub-bastion --bastion-rg prod-hub-neu-rg \
            --target jump-vm --target-rg prod-jump-rg

# Private API server: pin a local port and forward to 443
aksb tunnel -b prod-hub-bastion --bastion-rg prod-hub-neu-rg \
            -t /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Compute/virtualMachines/jump \
            --resource-port 443 --local-port 8443
```

The command runs in the foreground and prints the local endpoint
(`127.0.0.1:<port>`); press Ctrl-C to close the tunnel.

## Development

```bash
git clone https://github.com/NaeemH/azaks-bastion.git
cd azaks-bastion
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install

# Run the standard checks
ruff check . && ruff format --check .
mypy src
pytest -q
```

## Release

Releases are tag-driven. Bump `src/azaks_bastion/__about__.py`, commit, then:

```bash
git tag v0.1.0
git push origin v0.1.0
```

`.github/workflows/release.yml` builds the sdist + wheel and publishes to PyPI
via Trusted Publishers (OIDC) — no API tokens involved.

## License

[MIT](LICENSE)
