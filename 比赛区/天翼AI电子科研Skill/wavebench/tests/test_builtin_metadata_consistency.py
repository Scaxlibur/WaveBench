from wavebench.instruments.builtin import BUILTIN_INSTRUMENTS
from wavebench.plugins.builtin import BUILTIN_PLUGINS


def test_v1_and_v2_builtin_metadata_remain_consistent():
    v1_by_id = {plugin.driver_id: plugin for plugin in BUILTIN_PLUGINS}
    v2_by_id = {
        descriptor.driver_id: descriptor.to_metadata()
        for descriptor in BUILTIN_INSTRUMENTS
    }

    assert v2_by_id.keys() == v1_by_id.keys()
    for driver_id, v1 in v1_by_id.items():
        v2 = v2_by_id[driver_id]
        assert v2.kind == v1.kind
        assert v2.display_name == v1.display_name
        assert v2.manufacturer == v1.manufacturer
        assert v2.models == v1.models
        assert v2.capabilities == v1.capabilities
        assert v2.summary == v1.summary
        assert v2.idn_patterns == v1.idn_patterns
        assert v2.config_fields == v1.config_fields
        assert v2.package == v1.package
        assert v2.origin == v1.origin
