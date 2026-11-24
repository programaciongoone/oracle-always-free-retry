"""Offline tests for adaptive selection; no OCI credentials or network."""
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import oracle_a1 as app


class ServiceError(Exception):
    def __init__(self, status):
        self.status = status


def row(cpus, ram, status="AVAILABLE", count=1, fd=None, shape=app.SHAPE):
    return NS(instance_shape=shape, instance_shape_config=NS(ocpus=cpus, memory_in_gbs=ram),
              availability_status=status, available_count=count, fault_domain=fd)


def fake_oci():
    model = lambda **kwargs: NS(**kwargs)
    return NS(core=NS(models=NS(
        CreateComputeCapacityReportDetails=model,
        CreateCapacityReportShapeAvailabilityDetails=model,
        CapacityReportInstanceShapeConfig=model,
        LaunchInstanceDetails=model,
        LaunchInstanceShapeConfigDetails=model,
        InstanceSourceViaImageDetails=model,
        CreateVnicDetails=model)),
        exceptions=NS(ServiceError=ServiceError),
        retry=NS(NoneRetryStrategy=lambda: None))


class AdaptiveTests(unittest.TestCase):
    def test_priority(self):
        self.assertEqual(app.select_capacity([row(1, 2), row(2, 12), row(2, 8)]),
                         (2, 12, None))

    def test_smaller_and_domain(self):
        self.assertEqual(app.select_capacity([row(2, 12, "OUT_OF_HOST_CAPACITY", 0),
                                              row(1, 6, fd="FAULT-DOMAIN-2")]),
                         (1, 6, "FAULT-DOMAIN-2"))

    def test_ignores_invalid_and_unavailable(self):
        self.assertIsNone(app.select_capacity([row(4, 24), row(2, 12, count=0),
            row(2, 8, "UNKNOWN_ENUM_VALUE"), row(1, 6, fd="invalid"),
            row(1, 2, shape="VM.Other")]))

    def test_prefers_highest_count(self):
        self.assertEqual(app.select_capacity([row(2, 8, count=1, fd="FAULT-DOMAIN-1"),
                                             row(2, 8, count=3, fd="FAULT-DOMAIN-3")]),
                         (2, 8, "FAULT-DOMAIN-3"))

    def test_rotation_and_ceiling(self):
        self.assertEqual([app.fallback_capacity(i)[:2] for i in range(4)], list(app.CANDIDATES))
        self.assertEqual(app.fallback_capacity(0), app.fallback_capacity(4))
        self.assertTrue(all(1 <= c <= 2 and 1 <= r <= 12 for c, r in app.CANDIDATES))

    def test_report_uses_root_and_all_candidates(self):
        c = Mock()
        c.create_compute_capacity_report.return_value = NS(data=NS(shape_availabilities=[row(2, 8)]))
        with patch.dict(sys.modules, {"oci": fake_oci()}):
            self.assertEqual(app.choose_capacity(c, "root", "AD"), (2, 8, None))
        c.create_compute_capacity_report.assert_called_once()
        request = c.create_compute_capacity_report.call_args.args[0]
        self.assertEqual(request.compartment_id, "root")
        self.assertEqual(request.availability_domain, "AD")
        self.assertEqual(len(request.shape_availabilities), len(app.CANDIDATES))
        c.launch_instance.assert_not_called()

    def test_report_permission_fallback(self):
        c = Mock()
        c.create_compute_capacity_report.side_effect = ServiceError(403)
        with patch.dict(sys.modules, {"oci": fake_oci()}):
            self.assertIn(app.choose_capacity(c, "root", "AD")[:2], app.CANDIDATES)

    def test_report_throttled_not_retried(self):
        c = Mock()
        c.create_compute_capacity_report.side_effect = ServiceError(429)
        with patch.dict(sys.modules, {"oci": fake_oci()}):
            with self.assertRaises(ServiceError):
                app.choose_capacity(c, "root", "AD")
        c.create_compute_capacity_report.assert_called_once()

    def test_no_positive_report_rotates(self):
        c = Mock()
        c.create_compute_capacity_report.return_value = NS(data=NS(shape_availabilities=[]))
        with patch.dict(sys.modules, {"oci": fake_oci()}):
            self.assertIn(app.choose_capacity(c, "root", "AD")[:2], app.CANDIDATES)

    def attempt(self, dry_run=False, exists=False, throttled=False):
        c = Mock()
        cfg = {"OCI_COMPARTMENT_OCID": "compartment", "OCI_TENANCY_OCID": "root",
               "OCI_SUBNET_OCID": "subnet", "OCI_SSH_PUBLIC_KEY": "ssh-ed25519 test"}
        chooser = Mock(return_value=(1, 6, "FAULT-DOMAIN-2"))
        if throttled:
            chooser.side_effect = ServiceError(429)
        with patch.dict(sys.modules, {"oci": fake_oci()}), \
             patch.object(app, "existing_a1", return_value=exists), \
             patch.object(app, "select_image", return_value="image"), \
             patch.object(app, "select_domain", return_value="AD"), \
             patch.object(app, "choose_capacity", chooser), patch.object(app, "report"):
            app.attempt(c, Mock(), cfg, dry_run)
        return c, chooser

    def test_dry_run_no_launch(self):
        c, chooser = self.attempt(dry_run=True)
        chooser.assert_called_once()
        c.launch_instance.assert_not_called()

    def test_existing_no_report_or_launch(self):
        c, chooser = self.attempt(exists=True)
        chooser.assert_not_called()
        c.launch_instance.assert_not_called()

    def test_throttle_no_launch(self):
        c, _ = self.attempt(throttled=True)
        c.launch_instance.assert_not_called()

    def test_one_launch_uses_selected_configuration(self):
        c, _ = self.attempt()
        c.launch_instance.assert_called_once()
        request = c.launch_instance.call_args.args[0]
        self.assertEqual((request.shape_config.ocpus, request.shape_config.memory_in_gbs), (1, 6))
        self.assertEqual(request.source_details.boot_volume_size_in_gbs, 50)
        self.assertEqual(request.fault_domain, "FAULT-DOMAIN-2")
        self.assertEqual(request.shape, app.SHAPE)


if __name__ == "__main__":
    unittest.main()
