from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib

from .errors import ConfigError

WAVEFORM_POINTS_ALIASES = {
    "def": "DEF",
    "default": "DEF",
    "max": "MAX",
    "maximum": "MAX",
    "dmax": "DMAX",
    "dmaximum": "DMAX",
}

SCOPE_CONNECTION_BACKENDS = {
    "lan",
    "visa",
    "pyvisa",
    "rsinstrument-socket",
    "rsinstrument",
    "rsinstrument-rsvisa",
    "rsinstrument-pyvisa-py",
}


def normalize_waveform_points(points: str) -> str:
    normalized = points.strip().lower()
    try:
        return WAVEFORM_POINTS_ALIASES[normalized]
    except KeyError as exc:
        raise ConfigError("waveform points must be one of: def, max, dmax") from exc


def vertical_scale_from_vpp(target_vpp: float, *, target_divisions: float = 5.0) -> float:
    if target_vpp <= 0:
        raise ConfigError("target Vpp must be > 0")
    if target_divisions <= 0:
        raise ConfigError("target vertical divisions must be > 0")
    return target_vpp / target_divisions


def _strict_bool(table: dict[str, object], key: str, default: bool, *, path: str) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{path}.{key} must be a boolean")
    return value

@dataclass(frozen=True)
class ConnectionConfig:
    backend: str
    resource: str
    timeout_ms: int
    opc_timeout_ms: int
    read_retry_attempts: int = 1
    read_retry_delay_ms: int = 200

@dataclass(frozen=True)
class ScopeConfig:
    driver: str
    model_hint: str | None
    default_channel: int
    reset_before_run: bool
    check_errors: bool
    options: dict[str, object] = field(default_factory=dict)

@dataclass(frozen=True)
class AutoscaleConfig:
    wait_opc: bool
    check_errors: bool

@dataclass(frozen=True)
class WaveformConfig:
    format: str
    byte_order: str
    points: str
    time_range_s: float | None = None
    expected_frequency_hz: float | None = None
    frequency_tolerance_ratio: float = 0.05
    target_cycles: float | None = None
    window_frequency_hz: float | None = None
    vertical_scale_v_per_div: float | None = None
    target_vpp: float | None = None

@dataclass(frozen=True)
class SourceConfig:
    driver: str
    resource: str | None
    default_channel: int
    check_errors: bool
    ensure_fix_mode_on_set_frequency: bool
    settle_ms_after_set_frequency: int
    options: dict[str, object] = field(default_factory=dict)

@dataclass(frozen=True)
class PowerConfig:
    driver: str
    resource: str | None
    default_channel: int
    check_errors: bool
    settle_ms_after_set: int
    settle_ms_after_output: int
    options: dict[str, object] = field(default_factory=dict)

@dataclass(frozen=True)
class DmmConfig:
    driver: str
    resource: str | None
    backend: str
    baudrate: int
    bytesize: int
    parity: str
    stopbits: float
    timeout_ms: int
    settle_ms_before_read: int = 0
    settle_ms_after_function_change: int = 500
    options: dict[str, object] = field(default_factory=dict)
    write_termination: str = "lf"
    read_termination: str = "lf"
    xonxoff: bool = False
    rtscts: bool = False
    dsrdtr: bool = False

@dataclass(frozen=True)
class OutputConfig:
    directory: Path
    package_naming: str
    save_csv: bool
    save_npy: bool
    save_json: bool
    save_commands_log: bool
    save_screenshot: bool

@dataclass(frozen=True)
class QualityConfig:
    auto_recover_attempts: int = 2
    consistency_required_captures: int = 2
    frequency_consistency_ratio: float = 0.02
    voltage_vpp_consistency_ratio: float = 0.05
    voltage_mean_consistency_v: float = 0.05
    duty_consistency: float = 0.03

@dataclass(frozen=True)
class SafetyLimitsConfig:
    max_source_vpp: float | None = None
    max_power_voltage_v: float | None = None
    max_power_current_limit_a: float | None = None

@dataclass(frozen=True)
class TuiConfig:
    log_max_lines: int = 10_000
    log_keep_lines_after_trim: int = 1_000


def _optional_positive_float(raw: dict, key: str) -> float | None:
    if key not in raw:
        return None
    value = float(raw[key])
    if value <= 0:
        raise ConfigError(f"safety_limits.{key} must be > 0")
    return value


def _instrument_options(raw: dict, section: str) -> dict[str, object]:
    options = raw.get("options", {})
    if not isinstance(options, dict):
        raise ConfigError(f"{section}.options must be a TOML table")
    return dict(options)

@dataclass(frozen=True)
class WaveBenchConfig:
    connection: ConnectionConfig
    scope: ScopeConfig
    autoscale: AutoscaleConfig
    waveform: WaveformConfig
    output: OutputConfig
    source_path: Path
    source: SourceConfig | None = None
    power: PowerConfig | None = None
    dmm: DmmConfig | None = None
    quality: QualityConfig = QualityConfig()
    safety_limits: SafetyLimitsConfig = SafetyLimitsConfig()
    tui: TuiConfig = TuiConfig()

    def with_connection_timeout_ms(self, timeout_ms: int) -> "WaveBenchConfig":
        if timeout_ms <= 0:
            raise ConfigError("connection timeout must be > 0")
        return WaveBenchConfig(
            connection=ConnectionConfig(
                backend=self.connection.backend,
                resource=self.connection.resource,
                timeout_ms=timeout_ms,
                opc_timeout_ms=self.connection.opc_timeout_ms,
                read_retry_attempts=self.connection.read_retry_attempts,
                read_retry_delay_ms=self.connection.read_retry_delay_ms,
            ),
            scope=self.scope,
            autoscale=self.autoscale,
            waveform=self.waveform,
            output=self.output,
            source_path=self.source_path,
            source=self.source,
            power=self.power,
            dmm=self.dmm,
            quality=self.quality,
            safety_limits=self.safety_limits,
            tui=self.tui,
        )

    def with_resource(self, resource: str) -> "WaveBenchConfig":
        return WaveBenchConfig(
            connection=ConnectionConfig(
                backend=self.connection.backend,
                resource=resource,
                timeout_ms=self.connection.timeout_ms,
                opc_timeout_ms=self.connection.opc_timeout_ms,
                read_retry_attempts=self.connection.read_retry_attempts,
                read_retry_delay_ms=self.connection.read_retry_delay_ms,
            ),
            scope=self.scope,
            autoscale=self.autoscale,
            waveform=self.waveform,
            output=self.output,
            source_path=self.source_path,
            source=self.source,
            power=self.power,
            dmm=self.dmm,
            quality=self.quality,
            safety_limits=self.safety_limits,
            tui=self.tui,
        )

    def with_output_overrides(
        self,
        *,
        save_csv: bool | None = None,
        save_npy: bool | None = None,
        save_json: bool | None = None,
        save_screenshot: bool | None = None,
    ) -> "WaveBenchConfig":
        return WaveBenchConfig(
            connection=self.connection,
            scope=self.scope,
            autoscale=self.autoscale,
            waveform=self.waveform,
            output=OutputConfig(
                directory=self.output.directory,
                package_naming=self.output.package_naming,
                save_csv=self.output.save_csv if save_csv is None else save_csv,
                save_npy=self.output.save_npy if save_npy is None else save_npy,
                save_json=self.output.save_json if save_json is None else save_json,
                save_commands_log=self.output.save_commands_log,
                save_screenshot=self.output.save_screenshot if save_screenshot is None else save_screenshot,
            ),
            source_path=self.source_path,
            source=self.source,
            power=self.power,
            dmm=self.dmm,
            quality=self.quality,
            safety_limits=self.safety_limits,
            tui=self.tui,
        )

    def with_waveform_overrides(
        self,
        *,
        points: str | None = None,
        time_range_s: float | None = None,
        expected_frequency_hz: float | None = None,
        frequency_tolerance_ratio: float | None = None,
        target_cycles: float | None = None,
        window_frequency_hz: float | None = None,
        vertical_scale_v_per_div: float | None = None,
        target_vpp: float | None = None,
    ) -> "WaveBenchConfig":
        return WaveBenchConfig(
            connection=self.connection,
            scope=self.scope,
            autoscale=self.autoscale,
            waveform=WaveformConfig(
                format=self.waveform.format,
                byte_order=self.waveform.byte_order,
                points=self.waveform.points if points is None else normalize_waveform_points(points),
                time_range_s=self.waveform.time_range_s if time_range_s is None else time_range_s,
                expected_frequency_hz=(
                    self.waveform.expected_frequency_hz if expected_frequency_hz is None else expected_frequency_hz
                ),
                frequency_tolerance_ratio=(
                    self.waveform.frequency_tolerance_ratio
                    if frequency_tolerance_ratio is None
                    else frequency_tolerance_ratio
                ),
                target_cycles=self.waveform.target_cycles if target_cycles is None else target_cycles,
                window_frequency_hz=self.waveform.window_frequency_hz if window_frequency_hz is None else window_frequency_hz,
                vertical_scale_v_per_div=(
                    self.waveform.vertical_scale_v_per_div
                    if vertical_scale_v_per_div is None
                    else vertical_scale_v_per_div
                ),
                target_vpp=self.waveform.target_vpp if target_vpp is None else target_vpp,
            ),
            output=self.output,
            source_path=self.source_path,
            source=self.source,
            power=self.power,
            dmm=self.dmm,
            quality=self.quality,
            safety_limits=self.safety_limits,
            tui=self.tui,
        )

    def with_source_resource(self, resource: str) -> "WaveBenchConfig":
        source = self.source or SourceConfig(
            driver="dg4202",
            resource=None,
            default_channel=1,
            check_errors=True,
            ensure_fix_mode_on_set_frequency=True,
            settle_ms_after_set_frequency=0,
        )
        return WaveBenchConfig(
            connection=self.connection,
            scope=self.scope,
            autoscale=self.autoscale,
            waveform=self.waveform,
            output=self.output,
            source_path=self.source_path,
            source=SourceConfig(
                driver=source.driver,
                resource=resource,
                default_channel=source.default_channel,
                check_errors=source.check_errors,
                ensure_fix_mode_on_set_frequency=source.ensure_fix_mode_on_set_frequency,
                settle_ms_after_set_frequency=source.settle_ms_after_set_frequency,
                options=source.options,
            ),
            power=self.power,
            dmm=self.dmm,
            quality=self.quality,
            safety_limits=self.safety_limits,
            tui=self.tui,
        )

    def with_power_resource(self, resource: str) -> "WaveBenchConfig":
        power = self.power or PowerConfig(
            driver="dp800",
            resource=None,
            default_channel=1,
            check_errors=True,
            settle_ms_after_set=2000,
            settle_ms_after_output=1000,
        )
        return WaveBenchConfig(
            connection=self.connection,
            scope=self.scope,
            autoscale=self.autoscale,
            waveform=self.waveform,
            output=self.output,
            source_path=self.source_path,
            source=self.source,
            power=PowerConfig(
                driver=power.driver,
                resource=resource,
                default_channel=power.default_channel,
                check_errors=power.check_errors,
                settle_ms_after_set=power.settle_ms_after_set,
                settle_ms_after_output=power.settle_ms_after_output,
                options=power.options,
            ),
            dmm=self.dmm,
            quality=self.quality,
            safety_limits=self.safety_limits,
            tui=self.tui,
        )

    def with_dmm_resource(self, resource: str) -> "WaveBenchConfig":
        dmm = self.dmm or DmmConfig(
            driver="dm3058" if resource.upper().startswith("TCPIP") else "dm3000",
            resource=None,
            backend="lan" if resource.upper().startswith("TCPIP") else "serial",
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout_ms=1000,
            settle_ms_before_read=0,
            settle_ms_after_function_change=500,
        )
        is_tcpip = resource.upper().startswith("TCPIP")
        return WaveBenchConfig(
            connection=self.connection,
            scope=self.scope,
            autoscale=self.autoscale,
            waveform=self.waveform,
            output=self.output,
            source_path=self.source_path,
            source=self.source,
            power=self.power,
            dmm=DmmConfig(
                driver="dm3058" if is_tcpip else dmm.driver,
                resource=resource,
                backend="lan" if is_tcpip else dmm.backend,
                baudrate=dmm.baudrate,
                bytesize=dmm.bytesize,
                parity=dmm.parity,
                stopbits=dmm.stopbits,
                timeout_ms=dmm.timeout_ms,
                settle_ms_before_read=dmm.settle_ms_before_read,
                settle_ms_after_function_change=dmm.settle_ms_after_function_change,
                options=dmm.options,
                write_termination=dmm.write_termination,
                read_termination=dmm.read_termination,
                xonxoff=dmm.xonxoff,
                rtscts=dmm.rtscts,
                dsrdtr=dmm.dsrdtr,
            ),
            quality=self.quality,
            safety_limits=self.safety_limits,
            tui=self.tui,
        )

def load_config(path: str | Path = "wavebench.toml") -> WaveBenchConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(
            f"config file not found: {config_path}. Copy wavebench.example.toml to wavebench.toml first."
        )
    try:
        raw = tomllib.loads(config_path.read_bytes().decode("utf-8-sig"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {config_path}: {exc}") from exc

    try:
        c = raw["connection"]
        s = raw["scope"]
        a = raw.get("autoscale", {})
        w = raw.get("waveform", {})
        o = raw.get("output", {})
        q = raw.get("quality", {})
        sl = raw.get("safety_limits", {})
        tui_raw = raw.get("tui", {})
        src = raw.get("source")
        source = None
        if src is not None:
            source = SourceConfig(
                driver=str(src.get("driver", "dg4202")),
                resource=str(src["resource"]) if "resource" in src else None,
                default_channel=int(src.get("default_channel", 1)),
                check_errors=bool(src.get("check_errors", True)),
                ensure_fix_mode_on_set_frequency=bool(src.get("ensure_fix_mode_on_set_frequency", True)),
                settle_ms_after_set_frequency=int(src.get("settle_ms_after_set_frequency", 0)),
                options=_instrument_options(src, "source"),
            )
        pwr = raw.get("power")
        power = None
        if pwr is not None:
            power = PowerConfig(
                driver=str(pwr.get("driver", "dp800")),
                resource=str(pwr["resource"]) if "resource" in pwr else None,
                default_channel=int(pwr.get("default_channel", 1)),
                check_errors=bool(pwr.get("check_errors", True)),
                settle_ms_after_set=int(pwr.get("settle_ms_after_set", 2000)),
                settle_ms_after_output=int(pwr.get("settle_ms_after_output", 1000)),
                options=_instrument_options(pwr, "power"),
            )
        dmm_raw = raw.get("dmm")
        dmm = None
        if dmm_raw is not None:
            dmm = DmmConfig(
                driver=str(dmm_raw.get("driver", "dm3000")),
                resource=str(dmm_raw["resource"]) if "resource" in dmm_raw else None,
                backend=str(dmm_raw.get("backend", "serial")),
                baudrate=int(dmm_raw.get("baudrate", 9600)),
                bytesize=int(dmm_raw.get("bytesize", 8)),
                parity=str(dmm_raw.get("parity", "N")),
                stopbits=float(dmm_raw.get("stopbits", 1)),
                timeout_ms=int(dmm_raw.get("timeout_ms", 1000)),
                settle_ms_before_read=int(dmm_raw.get("settle_ms_before_read", 0)),
                settle_ms_after_function_change=int(
                    dmm_raw.get("settle_ms_after_function_change", 500)
                ),
                options=_instrument_options(dmm_raw, "dmm"),
                write_termination=str(dmm_raw.get("write_termination", "lf")),
                read_termination=str(dmm_raw.get("read_termination", "lf")),
                xonxoff=_strict_bool(dmm_raw, "xonxoff", False, path="dmm"),
                rtscts=_strict_bool(dmm_raw, "rtscts", False, path="dmm"),
                dsrdtr=_strict_bool(dmm_raw, "dsrdtr", False, path="dmm"),
            )
        config = WaveBenchConfig(
            connection=ConnectionConfig(
                backend=str(c.get("backend", "lan")),
                resource=str(c["resource"]),
                timeout_ms=int(c.get("timeout_ms", 10000)),
                opc_timeout_ms=int(c.get("opc_timeout_ms", 30000)),
                read_retry_attempts=int(c.get("read_retry_attempts", 1)),
                read_retry_delay_ms=int(c.get("read_retry_delay_ms", 200)),
            ),
            scope=ScopeConfig(
                driver=str(s.get("driver", "rtm2032")),
                model_hint=s.get("model_hint"),
                default_channel=int(s.get("default_channel", 1)),
                reset_before_run=bool(s.get("reset_before_run", False)),
                check_errors=bool(s.get("check_errors", True)),
                options=_instrument_options(s, "scope"),
            ),
            autoscale=AutoscaleConfig(
                wait_opc=bool(a.get("wait_opc", True)),
                check_errors=bool(a.get("check_errors", True)),
            ),
            waveform=WaveformConfig(
                format=str(w.get("format", "real")),
                byte_order=str(w.get("byte_order", "lsbf")),
                points=normalize_waveform_points(str(w.get("points", "dmax"))),
                time_range_s=float(w["time_range_s"]) if "time_range_s" in w else None,
                expected_frequency_hz=float(w["expected_frequency_hz"]) if "expected_frequency_hz" in w else None,
                frequency_tolerance_ratio=float(w.get("frequency_tolerance_ratio", 0.05)),
                target_cycles=float(w["target_cycles"]) if "target_cycles" in w else None,
                window_frequency_hz=float(w["window_frequency_hz"]) if "window_frequency_hz" in w else None,
                vertical_scale_v_per_div=(
                    float(w["vertical_scale_v_per_div"])
                    if "vertical_scale_v_per_div" in w
                    else None
                ),
                target_vpp=float(w["target_vpp"]) if "target_vpp" in w else None,
            ),
            output=OutputConfig(
                directory=Path(str(o.get("directory", "data/raw"))),
                package_naming=str(o.get("package_naming", "timestamp_label")),
                save_csv=bool(o.get("save_csv", True)),
                save_npy=bool(o.get("save_npy", True)),
                save_json=bool(o.get("save_json", True)),
                save_commands_log=bool(o.get("save_commands_log", True)),
                save_screenshot=bool(o.get("save_screenshot", False)),
            ),
            source_path=config_path,
            source=source,
            power=power,
            dmm=dmm,
            quality=QualityConfig(
                auto_recover_attempts=int(q.get("auto_recover_attempts", 2)),
                consistency_required_captures=int(q.get("consistency_required_captures", 2)),
                frequency_consistency_ratio=float(q.get("frequency_consistency_ratio", 0.02)),
                voltage_vpp_consistency_ratio=float(q.get("voltage_vpp_consistency_ratio", 0.05)),
                voltage_mean_consistency_v=float(q.get("voltage_mean_consistency_v", 0.05)),
                duty_consistency=float(q.get("duty_consistency", 0.03)),
            ),
            safety_limits=SafetyLimitsConfig(
                max_source_vpp=_optional_positive_float(sl, "max_source_vpp"),
                max_power_voltage_v=_optional_positive_float(sl, "max_power_voltage_v"),
                max_power_current_limit_a=_optional_positive_float(sl, "max_power_current_limit_a"),
            ),
            tui=TuiConfig(
                log_max_lines=int(tui_raw.get("log_max_lines", 10_000)),
                log_keep_lines_after_trim=int(tui_raw.get("log_keep_lines_after_trim", 1_000)),
            ),
        )
    except KeyError as exc:
        raise ConfigError(f"missing required config key: {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"invalid config value in {config_path}: {exc}") from exc

    if config.connection.backend.lower() not in SCOPE_CONNECTION_BACKENDS:
        choices = ", ".join(sorted(SCOPE_CONNECTION_BACKENDS))
        raise ConfigError(f"connection.backend must be one of: {choices}")
    if config.connection.read_retry_attempts < 0:
        raise ConfigError("connection.read_retry_attempts must be >= 0")
    if config.connection.read_retry_delay_ms < 0:
        raise ConfigError("connection.read_retry_delay_ms must be >= 0")
    from wavebench.instruments.registry import validate_instrument_reference

    validate_instrument_reference(config.scope.driver, expected_kind="scope")
    if config.scope.default_channel < 1:
        raise ConfigError("scope.default_channel must be >= 1")
    if config.waveform.time_range_s is not None and config.waveform.time_range_s <= 0:
        raise ConfigError("waveform.time_range_s must be > 0")
    if config.waveform.expected_frequency_hz is not None and config.waveform.expected_frequency_hz <= 0:
        raise ConfigError("waveform.expected_frequency_hz must be > 0")
    if config.waveform.frequency_tolerance_ratio <= 0:
        raise ConfigError("waveform.frequency_tolerance_ratio must be > 0")
    if config.waveform.target_cycles is not None and config.waveform.target_cycles <= 0:
        raise ConfigError("waveform.target_cycles must be > 0")
    if config.waveform.window_frequency_hz is not None and config.waveform.window_frequency_hz <= 0:
        raise ConfigError("waveform.window_frequency_hz must be > 0")
    if config.waveform.vertical_scale_v_per_div is not None and config.waveform.vertical_scale_v_per_div <= 0:
        raise ConfigError("waveform.vertical_scale_v_per_div must be > 0")
    if config.waveform.target_vpp is not None and config.waveform.target_vpp <= 0:
        raise ConfigError("waveform.target_vpp must be > 0")
    if config.quality.auto_recover_attempts < 0:
        raise ConfigError("quality.auto_recover_attempts must be >= 0")
    if config.quality.consistency_required_captures < 2:
        raise ConfigError("quality.consistency_required_captures must be >= 2")
    if config.quality.frequency_consistency_ratio <= 0:
        raise ConfigError("quality.frequency_consistency_ratio must be > 0")
    if config.quality.voltage_vpp_consistency_ratio <= 0:
        raise ConfigError("quality.voltage_vpp_consistency_ratio must be > 0")
    if config.quality.voltage_mean_consistency_v < 0:
        raise ConfigError("quality.voltage_mean_consistency_v must be >= 0")
    if config.quality.duty_consistency <= 0:
        raise ConfigError("quality.duty_consistency must be > 0")
    if config.tui.log_max_lines <= 0:
        raise ConfigError("tui.log_max_lines must be > 0")
    if config.tui.log_keep_lines_after_trim < 0:
        raise ConfigError("tui.log_keep_lines_after_trim must be >= 0")
    if config.tui.log_keep_lines_after_trim > config.tui.log_max_lines:
        raise ConfigError("tui.log_keep_lines_after_trim must be <= tui.log_max_lines")
    if config.source is not None:
        validate_instrument_reference(config.source.driver, expected_kind="source")
        if config.source.default_channel < 1:
            raise ConfigError("source.default_channel must be >= 1")
        if config.source.settle_ms_after_set_frequency < 0:
            raise ConfigError("source.settle_ms_after_set_frequency must be >= 0")
    if config.dmm is not None:
        validate_instrument_reference(config.dmm.driver, expected_kind="dmm")
        if config.dmm.backend.lower() not in {"serial", "lan", "visa", "pyvisa"}:
            raise ConfigError("dmm.backend must be one of: serial, lan, visa, pyvisa")
        if config.dmm.baudrate <= 0:
            raise ConfigError("dmm.baudrate must be > 0")
        if config.dmm.bytesize not in {7, 8}:
            raise ConfigError("dmm.bytesize must be 7 or 8")
        if config.dmm.parity.upper() not in {"N", "O", "E", "NONE", "ODD", "EVEN"}:
            raise ConfigError("dmm.parity must be N, O, E, NONE, ODD, or EVEN")
        if config.dmm.stopbits not in {1.0, 1.5, 2.0}:
            raise ConfigError("dmm.stopbits must be 1, 1.5, or 2")
        if config.dmm.write_termination.strip().lower() not in {"lf", "crlf"}:
            raise ConfigError("dmm.write_termination must be lf or crlf")
        if config.dmm.read_termination.strip().lower() not in {"lf", "crlf"}:
            raise ConfigError("dmm.read_termination must be lf or crlf")
        if config.dmm.timeout_ms <= 0:
            raise ConfigError("dmm.timeout_ms must be > 0")
        if config.dmm.settle_ms_before_read < 0:
            raise ConfigError("dmm.settle_ms_before_read must be >= 0")
        if config.dmm.settle_ms_after_function_change < 0:
            raise ConfigError("dmm.settle_ms_after_function_change must be >= 0")
    if config.power is not None:
        validate_instrument_reference(config.power.driver, expected_kind="power")
        if config.power.default_channel < 1:
            raise ConfigError("power.default_channel must be >= 1")
        if config.power.settle_ms_after_set < 0:
            raise ConfigError("power.settle_ms_after_set must be >= 0")
        if config.power.settle_ms_after_output < 0:
            raise ConfigError("power.settle_ms_after_output must be >= 0")
    return config
