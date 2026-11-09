#!/usr/bin/env python3
"""One OCI Ampere A1 request per execution, controlled by GitHub Actions."""
import base64
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REGION = "eu-madrid-1"
SHAPE = "VM.Standard.A1.Flex"
OCPUS = 2
RAM_GB = 12
BOOT_GB = 50
NAME = "coolify-free-a1"

def report(status, message):
    print(f"STATUS: {status}: {message}", flush=True)
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"result={status}\n")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(f"### Oracle A1: {status}\n\n{message}\n")

def configuration():
    keys = ["OCI_USER_OCID", "OCI_TENANCY_OCID", "OCI_FINGERPRINT",
            "OCI_PRIVATE_KEY_B64", "OCI_COMPARTMENT_OCID", "OCI_SUBNET_OCID",
            "OCI_SSH_PUBLIC_KEY"]
    missing = [key for key in keys if not os.getenv(key, "").strip()]
    if missing:
        raise ValueError("Faltan secretos: " + ", ".join(missing))
    cfg = {key: os.environ[key].strip() for key in keys}
    for key in ("OCI_USER_OCID", "OCI_TENANCY_OCID", "OCI_COMPARTMENT_OCID", "OCI_SUBNET_OCID"):
        if not cfg[key].startswith("ocid1."):
            raise ValueError(f"{key}: OCID inválido")
    if not cfg["OCI_SSH_PUBLIC_KEY"].startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-")):
        raise ValueError("OCI_SSH_PUBLIC_KEY no es una clave pública SSH")
    return cfg

def existing_a1(compute, compartment):
    import oci
    vms = oci.pagination.list_call_get_all_results(compute.list_instances, compartment).data
    found = [v for v in vms if v.shape == SHAPE and v.lifecycle_state != "TERMINATED"]
    for vm in found:
        print(f"VM A1 existente: {vm.display_name} ({vm.lifecycle_state})")
    return bool(found)

def select_image(compute, compartment):
    import oci
    supplied = os.getenv("OCI_IMAGE_OCID", "").strip()
    if supplied:
        if not supplied.startswith("ocid1.image."):
            raise ValueError("OCI_IMAGE_OCID inválido")
        image = compute.get_image(supplied).data
        candidates = [image]
    else:
        candidates = oci.pagination.list_call_get_all_results(
            compute.list_images, compartment, shape=SHAPE,
            operating_system="Canonical Ubuntu",
            operating_system_version="24.04").data
    candidates = [im for im in candidates
                  if im.operating_system == "Canonical Ubuntu"
                  and im.operating_system_version == "24.04"
                  and re.search("aarch64", im.display_name or "", re.IGNORECASE)
                  and getattr(im, "lifecycle_state", "AVAILABLE") == "AVAILABLE"]
    if not candidates:
        raise ValueError("No se ha encontrado una imagen Canonical Ubuntu 24.04 ARM64 compatible")
    candidates.sort(key=lambda im: im.time_created, reverse=True)
    return candidates[0].id

def select_domain(identity, tenancy):
    domains = identity.list_availability_domains(tenancy).data
    names = sorted(d.name for d in domains)
    if not names:
        raise ValueError("Oracle no devuelve availability domains")
    configured = os.getenv("OCI_AVAILABILITY_DOMAIN", "").strip()
    if configured:
        if configured not in names:
            raise ValueError("OCI_AVAILABILITY_DOMAIN no válido para esta cuenta")
        return configured
    return names[int(datetime.now(timezone.utc).timestamp() // 1800) % len(names)]

def attempt(compute, identity, cfg, dry_run):
    import oci
    compartment = cfg["OCI_COMPARTMENT_OCID"]
    if existing_a1(compute, compartment):
        report("already-exists", "A1 existente en el compartimento. No se creará otra.")
        return
    image_id = select_image(compute, compartment)
    domain = select_domain(identity, cfg["OCI_TENANCY_OCID"])
    if dry_run:
        report("dry-run", f"Sin crear recursos: {SHAPE} / {OCPUS} OCPU / {RAM_GB} GB RAM / {BOOT_GB} GB boot / {domain}")
        return
    details = oci.core.models.LaunchInstanceDetails(
        compartment_id=compartment, availability_domain=domain,
        display_name=NAME, shape=SHAPE,
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=OCPUS, memory_in_gbs=RAM_GB),
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            image_id=image_id, boot_volume_size_in_gbs=BOOT_GB),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=cfg["OCI_SUBNET_OCID"], assign_public_ip=True),
        metadata={"ssh_authorized_keys": cfg["OCI_SSH_PUBLIC_KEY"]},
        freeform_tags={"purpose": "coolify", "managed_by": "github-actions-free-tier"})
    try:
        vm = compute.launch_instance(
            details, retry_strategy=oci.retry.NoneRetryStrategy()).data
    except oci.exceptions.ServiceError as error:
        message = (getattr(error, "message", "") or "").lower()
        if error.status == 429:
            report("rate-limited", "Oracle HTTP 429. Próximo ciclo programado.")
        elif any(term in message for term in
                 ("out of host capacity", "out of capacity", "not enough host capacity")):
            report("no-capacity", "Oracle no tiene capacidad en el dominio elegido.")
        else:
            raise
        return
    report("created", f"Solicitud aceptada. Instancia: {vm.id}. Confirmar estado y costes en OCI.")

def main():
    enabled = os.getenv("OCI_ENABLE_LAUNCH", "").lower() == "true"
    dry_run = not enabled or os.getenv("OCI_DRY_RUN", "").lower() == "true"
    try:
        cfg = configuration()
        raw = base64.b64decode(cfg["OCI_PRIVATE_KEY_B64"], validate=True)
        if not raw.startswith(b"-----BEGIN "):
            raise ValueError("OCI_PRIVATE_KEY_B64 no contiene una clave privada PEM")
        import oci
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder) / "oci.pem"
            key.write_bytes(raw)
            key.chmod(0o600)
            auth = {"user": cfg["OCI_USER_OCID"],
                    "tenancy": cfg["OCI_TENANCY_OCID"],
                    "fingerprint": cfg["OCI_FINGERPRINT"],
                    "key_file": str(key),
                    "region": REGION}
            if os.getenv("OCI_PRIVATE_KEY_PASSPHRASE"):
                auth["pass_phrase"] = os.environ["OCI_PRIVATE_KEY_PASSPHRASE"]
            oci.config.validate_config(auth)
            attempt(oci.core.ComputeClient(auth), oci.identity.IdentityClient(auth),
                    cfg, dry_run)
        return 0
    except ValueError as error:
        report("configuration-error", str(error))
        return 2
    except Exception as error:
        if hasattr(error, "status"):
            report("api-error", f"Oracle HTTP {error.status}; revisar acceso y configuración.")
        else:
            report("error", f"{type(error).__name__}: revisar configuración OCI.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
