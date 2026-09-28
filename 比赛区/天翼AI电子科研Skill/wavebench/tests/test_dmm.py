import unittest

from wavebench.drivers.dm3000 import (
    DMM_FUNCTION_COMMANDS,
    DMM_FUNCTION_QUERY_MAP,
    DMM_FUNCTION_SET_COMMANDS,
)


class DmmMappingSmokeTests(unittest.TestCase):
    def test_measure_and_set_maps_cover_same_public_functions(self):
        self.assertEqual(set(DMM_FUNCTION_COMMANDS), set(DMM_FUNCTION_SET_COMMANDS))

    def test_function_query_map_covers_supported_scpi_symbols(self):
        self.assertEqual(DMM_FUNCTION_QUERY_MAP["DCV"], "dcv")
        self.assertEqual(DMM_FUNCTION_QUERY_MAP["CAPACITANCE"], "cap")


if __name__ == "__main__":
    unittest.main()
