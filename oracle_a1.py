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

# Ordered from most useful to smallest; all stay inside the existing A1 ceiling.
CANDIDATES = ((OCPUS, RAM_GB), (2, 8), (1, 6), (1, 2))
FAULT_DOMAINS = ("FAULT-DOMAIN-1", "FAULT-DOMAIN-2", "FAULT-DOMAIN-3")


def select_capacity(rows):
    """Return the preferred reported configuration and optional fault domain."""
    for cpus, ram in CANDIDATES:
        matches = []
        for row in rows:
            config = getattr(row, "instance_shape_config", None)
            if getattr(row, "instance_shape", None) != SHAPE or config is None:
                continue
            if (getattr(config, "ocpus", None), getattr(config, "memory_in_gbs", None)) != (cpus, ram):
                continue
            if getattr(row, "availability_status", None) != "AVAILABLE":
                continue
            count = getattr(row, "available_count", None)
            if count is not None and count <= 0:
                continue
            fd = getattr(row, "fault_domain", None)
            if fd is not None and fd not in FAULT_DOMAINS:
                continue
            matches.append((count or 0, fd or ""))
        if matches:
            # Prefer the domain with the highest reported capacity.
            _, fd = max(matches)
            return cpus, ram, fd or None
    return None


def fallback_capacity(hour):
    """One configuration per UTC hour; no extra launch requests."""
    cpus, ram = CANDIDATES[hour % len(CANDIDATES)]
    return cpus, ram, None


def choose_capacity(compute, tenancy, domain):
    import oci
    hour = int(datetime.now(timezone.utc).timestamp() // 3600)
    models = oci.core.models
    details = models.CreateComputeCapacityReportDetails(
        compartment_id=tenancy, availability_domain=domain,
        shape_availabilities=[
            models.CreateCapacityReportShapeAvailabilityDetails(
                instance_shape=SHAPE,
                instance_shape_config=models.CapacityReportInstanceShapeConfig(
                    ocpus=cpus, memory_in_gbs=ram))
            for cpus, ram in CANDIDATES])
    try:
        rows = compute.create_compute_capacity_report(
            details, retry_strategy=oci.retry.NoneRetryStrategy()).data.shape_availabilities
    except oci.exceptions.ServiceError as error:
        # Some accounts may not be allowed to query capacity. Do not retry a
        # throttled or unexpected API failure in the same cycle.
        if error.status not in (400, 403, 404, 405, 501):
            raise
        print(f"CAPACITY: report unavailable (HTTP {error.status}); rotating configuration.")
        return fallback_capacity(hour)
    rows = rows or []
    chosen = select_capacity(rows)
    for cpus, ram in CANDIDATES:
        matching = [r for r in rows if getattr(r, "instance_shape", None) == SHAPE
                    and (getattr(getattr(r, "instance_shape_config", None), "ocpus", None),
                         getattr(getattr(r, "instance_shape_config", None), "memory_in_gbs", None))
                    == (cpus, ram)]
        available = select_capacity(matching) is not None
        print(f"CAPACITY: {cpus} OCPU / {ram} GB: "
              f"{'available' if available else 'not confirmed'}")
    if chosen is not None:
        print("CAPACITY: selecting best reported configuration.")
        return chosen
    # A capacity report is not a reservation and may not reflect the free pool.
    # Keep the existing single hourly launch, rotating the requested size.
    print("CAPACITY: no positive match; rotating configuration.")
    return fallback_capacity(hour)



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
    try:
        cpus, ram, fault_domain = choose_capacity(compute, cfg["OCI_TENANCY_OCID"], domain)
    except oci.exceptions.ServiceError as error:
        if error.status == 429:
            report("rate-limited", "Oracle HTTP 429 en informe de capacidad. Próximo ciclo programado.")
            return
        raise
    print(f"SELECTED: {cpus} OCPU / {ram} GB RAM / {fault_domain or 'automatic'}")
    if dry_run:
        report("dry-run", f"Sin crear instancia: {SHAPE} / {cpus} OCPU / {ram} GB RAM / {BOOT_GB} GB boot")
        return
    details = oci.core.models.LaunchInstanceDetails(
        compartment_id=compartment, availability_domain=domain,
        display_name=NAME, shape=SHAPE, fault_domain=fault_domain,
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=cpus, memory_in_gbs=ram),
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
    report("created", f"Solicitud aceptada: {cpus} OCPU / {ram} GB RAM. Confirmar estado y costes en OCI.")

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
