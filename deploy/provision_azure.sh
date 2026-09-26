#!/usr/bin/env bash
# Provision the Argus VM on Azure (docs/WEB_SPEC.md). DO NOT run automatically - operator tool.
#
#   RESOURCE_GROUP=argus LOCATION=westeurope VM_SIZE=Standard_B2s ADMIN_IP=1.2.3.4 ./provision_azure.sh
#
# Creates: resource group, public IP, NSG (22 from ADMIN_IP only + 80/443), NIC, Ubuntu 24.04 VM
# with cloud-init (docker + compose plugin + 2 GB swap). Prints the public IP; deploy/push.sh takes
# it from there. Requires: az CLI, logged in.
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-argus}"
LOCATION="${LOCATION:-westeurope}"
VM_SIZE="${VM_SIZE:-Standard_B2s}"
ADMIN_IP="${ADMIN_IP:?set ADMIN_IP to your operator IP (ssh 22 is opened to it only)}"
VM_NAME="${VM_NAME:-argus-vm}"
SSH_USER="${SSH_USER:-azureuser}"

case "${ADMIN_IP}" in
  */*) ADMIN_CIDR="${ADMIN_IP}" ;;
  *)   ADMIN_CIDR="${ADMIN_IP}/32" ;;
esac

echo "provision: ${RESOURCE_GROUP} in ${LOCATION}, ${VM_SIZE}, ssh from ${ADMIN_CIDR}"

az group create --name "${RESOURCE_GROUP}" --location "${LOCATION}" --output table

az network public-ip create --resource-group "${RESOURCE_GROUP}" --name "${VM_NAME}-ip" \
  --sku Standard --allocation-method Static --output table

az network nsg create --resource-group "${RESOURCE_GROUP}" --name "${VM_NAME}-nsg" --output table
az network nsg rule create --resource-group "${RESOURCE_GROUP}" --nsg-name "${VM_NAME}-nsg" \
  --name allow-ssh-operator --priority 1010 --direction Inbound --access Allow \
  --protocol Tcp --destination-port-ranges 22 --source-address-prefixes "${ADMIN_CIDR}" \
  --source-port-ranges '*' --destination-address-prefixes '*' --output table
az network nsg rule create --resource-group "${RESOURCE_GROUP}" --nsg-name "${VM_NAME}-nsg" \
  --name allow-http --priority 1020 --direction Inbound --access Allow \
  --protocol Tcp --destination-port-ranges 80 --source-address-prefixes Internet \
  --source-port-ranges '*' --destination-address-prefixes '*' --output table
az network nsg rule create --resource-group "${RESOURCE_GROUP}" --nsg-name "${VM_NAME}-nsg" \
  --name allow-https --priority 1030 --direction Inbound --access Allow \
  --protocol Tcp --destination-port-ranges 443 --source-address-prefixes Internet \
  --source-port-ranges '*' --destination-address-prefixes '*' --output table

az network nic create --resource-group "${RESOURCE_GROUP}" --name "${VM_NAME}-nic" \
  --subnet "${VM_NAME}-subnet" --vnet-name "${VM_NAME}-vnet" --vnet-address-prefix 10.0.0.0/16 \
  --subnet-address-prefix 10.0.0.0/24 --public-ip-address "${VM_NAME}-ip" \
  --network-security-group "${VM_NAME}-nsg" --output table

CLOUD_INIT="$(mktemp --suffix=.yml)"
trap 'rm -f "${CLOUD_INIT}"' EXIT
cat > "${CLOUD_INIT}" <<'YAML'
#cloud-config
package_update: true
packages:
  - docker.io
  - docker-compose-v2
runcmd:
  - systemctl enable --now docker
  - usermod -aG docker azureuser
  # 2 GB swap: Playwright/Chromium + FastAPI on a small VM
  - fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  - grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
YAML

az vm create --resource-group "${RESOURCE_GROUP}" --name "${VM_NAME}" \
  --image canonical:0001-com-ubuntu-server:noble:24_04-lts-gen2 \
  --size "${VM_SIZE}" --nic "${VM_NAME}-nic" --admin-username "${SSH_USER}" \
  --authentication-type ssh --ssh-key-values ~/.ssh/id_rsa.pub \
  --custom-data "${CLOUD_INIT}" --output table

PUBLIC_IP=$(az network public-ip show --resource-group "${RESOURCE_GROUP}" --name "${VM_NAME}-ip" \
  --query ipAddress --output tsv)

echo
echo "provision: done."
echo "  public IP : ${PUBLIC_IP}"
echo "  ssh       : ssh ${SSH_USER}@${PUBLIC_IP}"
echo "  console   : https://argus.${PUBLIC_IP}.sslip.io"
echo "  skyops    : https://skyops.${PUBLIC_IP}.sslip.io"
echo "  next      : VM_IP=${PUBLIC_IP} ENV_FILE=<keys.env> ./push.sh"
