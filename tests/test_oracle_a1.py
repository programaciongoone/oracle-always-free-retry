"""Safety regression tests that do not need real Oracle credentials."""
import os
import unittest
from unittest.mock import patch
import oracle_a1 as app

class SafetyTests(unittest.TestCase):
    def test_resource_limits(self):
        self.assertEqual(app.SHAPE, "VM.Standard.A1.Flex")
        self.assertEqual((app.OCPUS, app.RAM_GB, app.BOOT_GB), (2, 12, 50))

    def test_missing_secrets_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(app.main(), 2)

    def test_public_ssh_key_required(self):
        values = {
            "OCI_USER_OCID": "ocid1.user.example",
            "OCI_TENANCY_OCID": "ocid1.tenancy.example",
            "OCI_FINGERPRINT": "aa:bb",
            "OCI_PRIVATE_KEY_B64": "ZmFrZQ==",
            "OCI_COMPARTMENT_OCID": "ocid1.compartment.example",
            "OCI_SUBNET_OCID": "ocid1.subnet.example",
            "OCI_SSH_PUBLIC_KEY": "a-private-key"
        }
        with patch.dict(os.environ, values, clear=True):
            with self.assertRaises(ValueError):
                app.configuration()

    def test_existing_a1_detected_without_launch(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        import sys
        fake_oci = SimpleNamespace(pagination=SimpleNamespace(
            list_call_get_all_results=lambda method, *args: method(*args)))
        client = Mock()
        client.list_instances.return_value = SimpleNamespace(
            data=[SimpleNamespace(shape=app.SHAPE,
                                  lifecycle_state="STOPPED",
                                  display_name="existing")])
        with patch.dict(sys.modules, {"oci": fake_oci}):
            self.assertTrue(app.existing_a1(client, "ocid1.compartment.example"))
        client.launch_instance.assert_not_called()

if __name__ == "__main__":
    unittest.main()
