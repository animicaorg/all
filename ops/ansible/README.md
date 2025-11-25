# Ansible runbooks for Animica ops

This directory contains **optional** Ansible guidance to provision hosts and deploy the public **devnet** (or a small production-like cluster) using the Docker Compose or Kubernetes artifacts that already live under `ops/docker/` and `ops/k8s/`.

> If you already use `ops/docker/*.yml`, `ops/k8s/*`, or Terraform modules directly, you don’t need Ansible. These playbooks are for teams that prefer host automation, idempotent rollouts, and vault-backed secret handling.

---

## What this covers

- **Bootstrap hosts** (Linux) with Docker or kube tooling, time sync, log dirs, and minimal sysctls.
- **Deploy/upgrade** the node, miner, studio-services, explorer-api, and web UIs:
  - **Compose path:** pushes env/config, runs `docker compose up -d`, health-checks.
  - **Kubernetes path:** applies the rendered Kustomize/Helm manifests from `ops/k8s/` and `ops/helm/`.
- **Observability**: optional Prometheus + Grafana + Loki + Tempo stack from `ops/docker/docker-compose.observability.yml` or the `ops/k8s/observability/*` bundle.
- **Safe rollouts**: serial/percent-based updates, health gates, and quick rollback.
- **Secrets**: faucet keys and API tokens via **ansible-vault**.

This README documents layout, inventories, variables, and run commands. You can create playbooks/roles following the suggested structure below.

---

## Suggested layout

ops/ansible/
├─ inventory/
│  ├─ devnet.ini
│  └─ prod.ini
├─ group_vars/
│  ├─ all.yml
│  ├─ devnet.yml
│  └─ devnet/vault.yml        # encrypted with ansible-vault
├─ playbooks/
│  ├─ bootstrap-hosts.yml     # Docker, compose plugin, sysctls, time sync, firewall
│  ├─ deploy-compose.yml      # Node+miner+services+explorer via docker compose
│  ├─ deploy-observability.yml
│  ├─ deploy-k8s.yml          # Optional: apply k8s manifests (kustomize/helm)
│  ├─ update.yml              # Rolling update with health-gated pull+up
│  ├─ cleanup.yml             # Stop & remove stacks, optional volumes
│  └─ smoke.yml               # Health probes (RPC/WS), head advances, metrics up
├─ files/
│  ├─ docker/                 # Copies of ops/docker configs (or symlinks)
│  └─ k8s/                    # Optional rendered manifests
├─ templates/
│  ├─ services.env.j2
│  ├─ node.toml.j2
│  ├─ miner.toml.j2
│  └─ docker-compose.override.yml.j2
└─ requirements.yml           # collections: community.docker, kubernetes.core, ansible.posix, community.general

You only need this README to get started; feel free to add the above paths as your team adopts Ansible.

---

## Requirements

- **ansible-core ≥ 2.15**
- Python on control host with `pipx` (recommended) or `pip`.
- Collections (example `requirements.yml`):
  ```yaml
  collections:
    - name: community.docker
    - name: ansible.posix
    - name: community.general
    - name: kubernetes.core

Install: ansible-galaxy collection install -r requirements.yml
	•	Target hosts:
	•	Ubuntu 20.04+ or Debian 11+ (RHEL/CentOS works with minor changes).
	•	SSH access with a user that can become: true.

⸻

Inventory examples

inventory/devnet.ini

[nodes]
node1 ansible_host=10.0.0.10

[miners]
miner1 ansible_host=10.0.0.11

[services]
svc1 ansible_host=10.0.0.12

[explorer]
expl1 ansible_host=10.0.0.13

[all:vars]
ansible_user=ubuntu
ansible_ssh_common_args='-o StrictHostKeyChecking=no'

For small devnet, all groups can target a single host.

⸻

Variables

group_vars/all.yml (defaults)

# Common
animica_chain_id: 1
animica_env: devnet

# Image tags (can be overridden per env)
node_image: "ghcr.io/animica/node:latest"
miner_image: "ghcr.io/animica/miner:latest"
explorer_image: "ghcr.io/animica/explorer:latest"
services_image: "ghcr.io/animica/studio-services:latest"
studio_web_image: "ghcr.io/animica/studio-web:latest"
explorer_web_image: "ghcr.io/animica/explorer-web:latest"

# Paths on targets
animica_root_dir: "/opt/animica"
compose_dir: "{{ animica_root_dir }}/compose"
data_dir: "{{ animica_root_dir }}/data"
logs_dir: "{{ animica_root_dir }}/logs"

# Ports (keep in sync with ops/docker/* compose files)
rpc_http_port: 8545
rpc_ws_port: 8546
p2p_tcp_port: 37000
p2p_quic_port: 37001
services_http_port: 8080
explorer_http_port: 8081

# Health checks
rpc_health_path: "/healthz"
services_health_path: "/healthz"
explorer_health_path: "/healthz"

# Compose project names
compose_project_node: "animica-node"
compose_project_observability: "animica-observability"

# Toggle stacks
deploy_observability: true

group_vars/devnet.yml (env overrides)

animica_chain_id: 2
node_image: "ghcr.io/animica/node:v0.1.0"
miner_image: "ghcr.io/animica/miner:v0.1.0"
services_image: "ghcr.io/animica/studio-services:v0.1.0"
studio_web_image: "ghcr.io/animica/studio-web:v0.1.0"
explorer_image: "ghcr.io/animica/explorer:v0.1.0"
explorer_web_image: "ghcr.io/animica/explorer-web:v0.1.0"

Vaulted secrets — group_vars/devnet/vault.yml (encrypt with ansible-vault create):

faucet_private_key: "0x<hex>"
services_api_keys:
  - "dev-12345"


⸻

Playbook sketches (copy/paste friendly)

Below are outlines you can paste into playbooks/ if you want a quick start.

playbooks/bootstrap-hosts.yml

- name: Bootstrap Animica hosts
  hosts: all
  become: true
  gather_facts: true
  vars:
    docker_packages:
      - ca-certificates
      - curl
      - gnupg
      - lsb-release
  tasks:
    - name: Ensure basic packages
      ansible.builtin.package:
        name: [git, jq, curl, apt-transport-https]
        state: present

    - name: Install Docker engine & compose plugin
      vars:
        docker_install_compose_plugin: true
      ansible.builtin.include_role:
        name: community.docker.docker_install

    - name: Add animica user to docker group
      ansible.builtin.user:
        name: "{{ ansible_user }}"
        groups: docker
        append: true

    - name: Tune sysctls (network/file limits)
      ansible.posix.sysctl:
        name: "{{ item.name }}"
        value: "{{ item.value }}"
        state: present
        sysctl_set: true
        reload: true
      loop:
        - { name: "net.core.somaxconn", value: "1024" }
        - { name: "fs.file-max", value: "1048576" }

    - name: Ensure dirs
      ansible.builtin.file:
        path: "{{ item }}"
        state: directory
        mode: "0755"
      loop:
        - "{{ animica_root_dir }}"
        - "{{ compose_dir }}"
        - "{{ data_dir }}"
        - "{{ logs_dir }}"

playbooks/deploy-compose.yml

- name: Deploy Animica stacks (compose)
  hosts: all
  become: true
  vars_files:
    - "../group_vars/{{ animica_env }}.yml"
    - "../group_vars/{{ animica_env }}/vault.yml"
  tasks:
    - name: Sync compose assets
      ansible.builtin.copy:
        src: "../files/docker/"
        dest: "{{ compose_dir }}/"
        mode: "0644"

    - name: Render node config
      ansible.builtin.template:
        src: "../templates/node.toml.j2"
        dest: "{{ compose_dir }}/node.toml"

    - name: Render miner config
      ansible.builtin.template:
        src: "../templates/miner.toml.j2"
        dest: "{{ compose_dir }}/miner.toml"

    - name: Render services env
      ansible.builtin.template:
        src: "../templates/services.env.j2"
        dest: "{{ compose_dir }}/services.env"
        mode: "0600"

    - name: Pull images
      community.docker.docker_compose_v2:
        project_src: "{{ compose_dir }}"
        files:
          - "docker-compose.devnet.yml"
        pull: always

    - name: Up node+miner+services+explorer
      community.docker.docker_compose_v2:
        project_src: "{{ compose_dir }}"
        files:
          - "docker-compose.devnet.yml"
        state: present
        build: false

    - name: Health — RPC responds
      ansible.builtin.uri:
        url: "http://localhost:{{ rpc_http_port }}{{ rpc_health_path }}"
        method: GET
        status_code: 200
        validate_certs: false
        return_content: false
        timeout: 10
      register: rpc_health
      retries: 30
      delay: 5
      until: rpc_health.status == 200

    - name: Optionally deploy observability
      when: deploy_observability | bool
      community.docker.docker_compose_v2:
        project_src: "{{ compose_dir }}"
        files:
          - "docker-compose.observability.yml"
        state: present

playbooks/update.yml

- name: Rolling update
  hosts: all
  become: true
  serial: 1
  tasks:
    - name: Pull latest images
      community.docker.docker_compose_v2:
        project_src: "{{ compose_dir }}"
        files: ["docker-compose.devnet.yml"]
        pull: always
    - name: Recreate containers with zero-downtime where possible
      community.docker.docker_compose_v2:
        project_src: "{{ compose_dir }}"
        files: ["docker-compose.devnet.yml"]
        state: present
        recreate: "smart"
    - name: Post-update health
      ansible.builtin.uri:
        url: "http://localhost:{{ rpc_http_port }}{{ rpc_health_path }}"
        status_code: 200
      register: health
      retries: 20
      delay: 5
      until: health.status == 200

Kubernetes path (optional): create playbooks/deploy-k8s.yml that uses kubernetes.core.k8s to apply the manifests from ops/k8s/ or the Helm chart from ops/helm/animica-devnet/.

⸻

Running it

# 1) Install collections
ansible-galaxy collection install -r ops/ansible/requirements.yml

# 2) Bootstrap target hosts (Docker + sysctls + dirs)
ansible-playbook -i ops/ansible/inventory/devnet.ini ops/ansible/playbooks/bootstrap-hosts.yml

# 3) Deploy stacks via Compose
ansible-playbook -i ops/ansible/inventory/devnet.ini ops/ansible/playbooks/deploy-compose.yml

# 4) Update to new images later
ansible-playbook -i ops/ansible/inventory/devnet.ini ops/ansible/playbooks/update.yml

# 5) Optional: deploy observability only
ansible-playbook -i ops/ansible/inventory/devnet.ini ops/ansible/playbooks/deploy-observability.yml --tags obs

Overrides at runtime

ansible-playbook -i ops/ansible/inventory/devnet.ini \
  ops/ansible/playbooks/deploy-compose.yml \
  -e node_image=ghcr.io/animica/node:v0.2.0 \
  -e miner_image=ghcr.io/animica/miner:v0.2.0

Vault usage

# Create/edit encrypted vars
ansible-vault create ops/ansible/group_vars/devnet/vault.yml
ansible-vault edit   ops/ansible/group_vars/devnet/vault.yml
# Run with vault
ansible-playbook -i ops/ansible/inventory/devnet.ini ops/ansible/playbooks/deploy-compose.yml --ask-vault-pass


⸻

Health & smoke checks

You can also run the repository’s shell probes as ad-hoc commands:

ansible all -i ops/ansible/inventory/devnet.ini -m shell -a \
 'bash -lc "ops/scripts/wait_for.sh http://127.0.0.1:8545/healthz 120"'

Or use the smoke.yml playbook to:
	•	verify RPC/WS endpoints respond,
	•	query chain.getHead via curl,
	•	ensure Prometheus /metrics is reachable when observability is enabled.

⸻

Ports & firewall

If you use ufw or firewalld, open:
	•	RPC HTTP: {{ rpc_http_port }} (default 8545)
	•	RPC WS: {{ rpc_ws_port }} (default 8546)
	•	P2P TCP: {{ p2p_tcp_port }} (default 37000)
	•	P2P QUIC/UDP: {{ p2p_quic_port }} (default 37001)
	•	Services API: {{ services_http_port }} (default 8080)
	•	Explorer API/UI: {{ explorer_http_port }} (default 8081)

Add tasks in bootstrap-hosts.yml to configure your firewall accordingly.

⸻

Rollback
	•	Compose: pin prior image tags in group_vars/* and re-run update.yml, or:

docker compose -p animica-node -f docker-compose.devnet.yml down
docker compose -p animica-node -f docker-compose.devnet.yml up -d


	•	Kubernetes: kubectl rollout undo deploy/<name> or re-apply prior chart version.

⸻

CI & linting
	•	Add ansible-lint to your CI for playbooks/roles you create here.
	•	Use --check --diff during PRs for safe dry-runs:

ansible-playbook -i inventory/devnet.ini playbooks/deploy-compose.yml --check --diff



⸻

FAQ

Q: Compose or Kubernetes?
Start with Compose for a single host devnet. Move to Kubernetes when you need HA, rolling upgrades, HPA for miners, and managed ingress/TLS (see ops/k8s/ & ops/helm/).

Q: Where do configs come from?
Templates in ops/docker/config/* and ops/k8s/configmaps/*. Ansible renders them with your vars and secrets.

Q: How do I keep it idempotent?
Use community.docker.docker_compose_v2 and explicit health checks. Avoid raw shell for core steps.

Q: Can I mix-and-match?
Yes. Many teams use Ansible only for host bootstrap (Docker, users, sysctls) and call the repository’s native make or docker compose commands afterward.

⸻

Happy shipping! If you standardize playbooks later, drop them into ops/ansible/playbooks/ following the sketches above and commit requirements.yml with the exact collection versions you’ve validated.
