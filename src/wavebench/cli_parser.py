from __future__ import annotations

import argparse

from .discovery import DEFAULT_DISCOVERY_PORTS
from .mcp_http import DEFAULT_MCP_HOST, DEFAULT_MCP_PORT, DEFAULT_MCP_TOKEN_ENV


def add_runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="wavebench.toml", help="Path to wavebench TOML config")
    parser.add_argument("--resource", help="Override VISA resource, e.g. TCPIP::192.0.2.100::INSTR")


def add_scope_error_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--error-policy",
        choices=("required", "if_supported", "disabled"),
        default=None,
        help="Override the operation error-queue policy",
    )
    parser.add_argument(
        "--error-timing",
        choices=("before", "after", "before_and_after"),
        default="before_and_after",
    )
    parser.add_argument("--error-max-records", type=int, default=16)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wavebench")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit one versioned machine-readable result / 输出一个版本化机器可读结果",
    )
    subparsers = parser.add_subparsers(dest="domain", required=True)

    scope_parser = subparsers.add_parser("scope", help="Oscilloscope commands")
    source_parser = subparsers.add_parser("source", help="Signal generator commands")
    power_parser = subparsers.add_parser("power", help="Power supply commands")
    dmm_parser = subparsers.add_parser("dmm", help="Digital multimeter commands")
    sweep_parser = subparsers.add_parser("sweep", help="Source/scope sweep commands")
    run_parser = subparsers.add_parser("run", help="Multi-instrument run plan commands")
    capture_parser = subparsers.add_parser("capture", help="Offline capture package commands")
    mcp_parser = subparsers.add_parser("mcp", help="HTTP MCP server / HTTP MCP 服务")
    tui_parser = subparsers.add_parser("tui", help="Launch terminal UI / 启动终端界面")
    net_parser = subparsers.add_parser("net", help="Network discovery helpers / 网络发现工具")
    doctor_parser = subparsers.add_parser("doctor", help="Diagnose configured instruments / 诊断已配置仪器")
    plugin_parser = subparsers.add_parser("plugin", help="Plugin registry commands / 插件注册表命令")
    doctor_parser.add_argument("--config", default="wavebench.toml", help="Path to wavebench TOML config")
    doctor_parser.add_argument(
        "--timeout-ms",
        type=int,
        help="Per-instrument IDN query timeout in milliseconds / 每台仪器 IDN 查询超时毫秒数",
    )
    doctor_parser.add_argument(
        "--discover-subnet",
        help="Also scan this subnet for IDN-matching replacement resources / 同时扫描网段寻找 IDN 匹配的替代资源",
    )
    doctor_parser.add_argument(
        "--discover-ports",
        default=",".join(str(port) for port in DEFAULT_DISCOVERY_PORTS),
        help="Comma-separated TCP ports for doctor discovery / doctor 发现使用的 TCP 端口",
    )
    doctor_parser.add_argument(
        "--discover-timeout-ms",
        type=int,
        help="Per-discovery connection timeout in milliseconds / 发现阶段每次连接超时毫秒数",
    )
    doctor_parser.add_argument("--discover-workers", type=int, default=64, help="Discovery workers / 发现并发数")
    doctor_parser.add_argument(
        "--discover-max-hosts",
        type=int,
        default=256,
        help="Maximum hosts allowed in the discovery scan / 发现扫描允许的最大主机数",
    )
    doctor_parser.add_argument(
        "--no-visa",
        action="store_true",
        help="Skip PyVISA resource-manager discovery / 跳过 PyVISA 资源枚举",
    )
    capability_parser = subparsers.add_parser(
        "capability",
        help="Explain offline operation capability decisions / 离线解释操作能力",
    )
    capability_sub = capability_parser.add_subparsers(dest="command", required=True)
    capability_explain = capability_sub.add_parser(
        "explain",
        help="Explain one operation against a driver or config / 解释一个操作对驱动或配置的要求",
    )
    capability_explain.add_argument("operation", help="Registered operation name, e.g. source.output")
    capability_explain.add_argument(
        "--driver",
        default=None,
        help="Instrument driver reference, e.g. dg4202 / 仪器驱动引用",
    )
    capability_explain.add_argument(
        "--kind",
        choices=("scope", "source", "power", "dmm"),
        default=None,
        help="Instrument kind when selecting a configured driver / 仪器类型",
    )
    capability_explain.add_argument(
        "--config",
        default=None,
        help="Use driver and access settings from a WaveBench TOML config / 从配置读取驱动和访问策略",
    )
    capability_explain.add_argument(
        "--access",
        choices=("read_only", "read_write", "disabled"),
        default=None,
        help="Access policy override / 访问策略覆盖值",
    )
    capability_explain.add_argument(
        "--candidates",
        action="store_true",
        help="List local drivers that can perform the operation / 列出本地可执行该操作的驱动",
    )
    lock_parser = subparsers.add_parser(
        "lock",
        help="Inspect local resource leases / 查询本地资源租约",
    )
    lock_sub = lock_parser.add_subparsers(dest="command", required=True)
    lock_status = lock_sub.add_parser(
        "status",
        help="Read one resource lease without acquiring it / 查询资源租约但不取得锁",
    )
    lock_status.add_argument("resource", help="VISA resource or serial path")
    lock_status.add_argument("--lock-id", default="", help="Optional logical lock id")
    tui_parser.add_argument("--config", default="wavebench.toml", help="Path to wavebench TOML config")
    tui_parser.add_argument("--resource", help="Override power VISA resource / 覆盖电源 VISA 资源")
    tui_parser.add_argument(
        "--fake",
        action="store_true",
        help="Use fake power, DMM, and source snapshots / 使用模拟电源、万用表和信号源快照",
    )
    tui_parser.add_argument(
        "--refresh-interval",
        type=float,
        default=5.0,
        help="Refresh interval in seconds / 刷新间隔（秒）",
    )
    tui_parser.add_argument(
        "--log-file",
        default="data/tui/wavebench-tui.log",
        help="Persist TUI debug log to this file / TUI 调试日志文件",
    )

    plugin_sub = plugin_parser.add_subparsers(dest="command", required=True)
    plugin_list = plugin_sub.add_parser(
        "list",
        help="List available instrument plugins / 列出可用仪器插件",
    )
    plugin_list.add_argument(
        "--kind",
        choices=("scope", "source", "power", "dmm"),
        default=None,
        help="Filter plugins by instrument kind / 按仪器类型过滤",
    )
    plugin_list.add_argument(
        "--include-entry-points",
        action="store_true",
        help="Also load Python entry points from wavebench.drivers / 同时加载 wavebench.drivers 入口点",
    )
    plugin_list.add_argument(
        "--load",
        action="store_true",
        help="Load executable wavebench.instruments plugins / 加载可执行仪器插件",
    )
    plugin_info = plugin_sub.add_parser(
        "info",
        help="Show one plugin metadata record / 显示单个插件元数据",
    )
    plugin_info.add_argument("driver_id", help="Plugin driver id, e.g. rigol.dg4202")
    plugin_info_source = plugin_info.add_mutually_exclusive_group()
    plugin_info_source.add_argument(
        "--include-entry-points",
        action="store_true",
        help="Also load Python entry points from wavebench.drivers / 同时加载 wavebench.drivers 入口点",
    )
    plugin_info_source.add_argument(
        "--load",
        action="store_true",
        help="Load the selected executable instrument plugin / 加载选中的可执行仪器插件",
    )
    plugin_info_source.add_argument(
        "--installed",
        action="store_true",
        help="Read only the managed-install ledger / 仅查询受管安装账本",
    )
    plugin_package = plugin_sub.add_parser(
        "package",
        help="Inspect trusted local plugin packages / 检查受信任的本地插件包",
    )
    plugin_package_sub = plugin_package.add_subparsers(
        dest="package_command",
        required=True,
    )
    plugin_package_check = plugin_package_sub.add_parser(
        "check",
        help="Validate a source directory or wheel without installing / "
        "校验源码目录或 wheel，但不安装",
    )
    plugin_package_check.add_argument("path", help="Local source directory or wheel / 本地源码目录或 wheel")
    plugin_install = plugin_sub.add_parser(
        "install",
        help="Install a trusted local plugin in the current venv / "
        "在当前虚拟环境安装受信任的本地插件",
    )
    plugin_install.add_argument("path", help="Local source directory or wheel / 本地源码目录或 wheel")
    plugin_install.add_argument("--dry-run", action="store_true", help="Validate only / 仅校验")
    plugin_sub.add_parser(
        "installed",
        help="List managed and unmanaged instrument distributions / "
        "列出受管与未受管的仪器分发",
    )
    plugin_remove = plugin_sub.add_parser(
        "remove",
        help="Remove one healthy managed plugin / 移除一个健康的受管插件",
    )
    plugin_remove.add_argument("driver_id", help="Managed canonical driver id / 受管 canonical driver ID")
    plugin_remove.add_argument("--dry-run", action="store_true", help="Validate only / 仅校验")
    for command in ("upgrade", "downgrade"):
        lifecycle_parser = plugin_sub.add_parser(
            command,
            help=(
                "Replace a managed plugin with an explicit local wheel / "
                "用明确的本地 wheel 替换受管插件"
            ),
        )
        lifecycle_parser.add_argument("path", help="Local source directory or wheel / 本地源码目录或 wheel")
        lifecycle_parser.add_argument("--dry-run", action="store_true", help="Validate only / 仅校验")
    plugin_sub.add_parser(
        "recover",
        help="Inspect and recover a durable plugin transaction / "
        "检查并恢复持久化插件事务",
    )
    plugin_doctor = plugin_sub.add_parser(
        "doctor",
        help="Validate plugin registry metadata / 检查插件注册表元数据",
    )
    plugin_doctor.add_argument(
        "--include-entry-points",
        action="store_true",
        help="Also load Python entry points from wavebench.drivers / 同时加载 wavebench.drivers 入口点",
    )
    plugin_doctor.add_argument(
        "--load",
        action="store_true",
        help="Load and validate executable wavebench.instruments plugins / 加载并检查可执行仪器插件",
    )
    plugin_market = plugin_sub.add_parser(
        "market",
        help="Read-only plugin marketplace index / 只读插件市场索引",
    )
    plugin_market_sub = plugin_market.add_subparsers(dest="market_command", required=True)
    plugin_market_search = plugin_market_sub.add_parser(
        "search",
        help="Search a local plugin market index / 搜索本地插件市场索引",
    )
    plugin_market_search.add_argument("query", nargs="?", help="Search text / 搜索文本")
    plugin_market_search.add_argument(
        "--index",
        help="Path to a local plugin market JSON index / 本地插件市场 JSON 索引路径",
    )
    plugin_market_info = plugin_market_sub.add_parser(
        "info",
        help="Show one plugin market entry / 显示单个市场插件条目",
    )
    plugin_market_info.add_argument("plugin_id", help="Market plugin id, e.g. wavebench-rigol-dg4202")
    plugin_market_info.add_argument(
        "--index",
        help="Path to a local plugin market JSON index / 本地插件市场 JSON 索引路径",
    )
    plugin_scpi = plugin_sub.add_parser(
        "scpi",
        help="Validate local declarative SCPI plugins / 检查本地声明式 SCPI 插件",
    )
    plugin_scpi_sub = plugin_scpi.add_subparsers(dest="scpi_command", required=True)
    plugin_scpi_check = plugin_scpi_sub.add_parser(
        "check",
        help="Validate a local SCPI plugin TOML file / 检查本地 SCPI 插件 TOML",
    )
    plugin_scpi_check.add_argument("path", help="Path to a SCPI plugin TOML file / SCPI 插件 TOML 路径")
    plugin_scpi_doctor = plugin_scpi_sub.add_parser(
        "doctor",
        help="Diagnose a local SCPI plugin, optionally with IDN probe / 诊断本地 SCPI 插件，可选 IDN 探测",
    )
    plugin_scpi_doctor.add_argument("path", help="Path to a SCPI plugin TOML file / SCPI 插件 TOML 路径")
    plugin_scpi_doctor.add_argument(
        "--probe",
        action="store_true",
        help="Also run the plugin idn_query against --resource / 同时对 --resource 执行 idn_query",
    )
    plugin_scpi_doctor.add_argument("--resource", help="VISA resource to query / 要查询的 VISA 资源")
    plugin_scpi_doctor.add_argument(
        "--backend",
        choices=("pyvisa", "rsinstrument"),
        default="pyvisa",
        help="SCPI transport backend / SCPI 传输后端",
    )
    plugin_scpi_doctor.add_argument(
        "--timeout-ms",
        type=int,
        default=1000,
        help="Probe timeout in milliseconds / 探测超时毫秒数",
    )
    plugin_scpi_info = plugin_scpi_sub.add_parser(
        "info",
        help="Show a local SCPI plugin TOML file / 显示本地 SCPI 插件 TOML",
    )
    plugin_scpi_info.add_argument("path", help="Path to a SCPI plugin TOML file / SCPI 插件 TOML 路径")
    plugin_scpi_probe = plugin_scpi_sub.add_parser(
        "probe",
        help="Run the plugin idn_query against one resource / 对一个资源执行插件 idn_query",
    )
    plugin_scpi_probe.add_argument("path", help="Path to a SCPI plugin TOML file / SCPI 插件 TOML 路径")
    plugin_scpi_probe.add_argument("--resource", required=True, help="VISA resource to query / 要查询的 VISA 资源")
    plugin_scpi_probe.add_argument(
        "--backend",
        choices=("pyvisa", "rsinstrument"),
        default="pyvisa",
        help="SCPI transport backend / SCPI 传输后端",
    )
    plugin_scpi_probe.add_argument(
        "--timeout-ms",
        type=int,
        default=1000,
        help="Probe timeout in milliseconds / 探测超时毫秒数",
    )

    net_sub = net_parser.add_subparsers(dest="command", required=True)
    net_discover = net_sub.add_parser(
        "discover",
        help="Read-only scan for LAN SCPI/VISA instruments / 只读扫描局域网 SCPI/VISA 仪器",
    )
    net_discover.add_argument("--subnet", required=True, help="Subnet to scan, e.g. 192.0.2.0/24")
    net_discover.add_argument(
        "--ports",
        default=",".join(str(port) for port in DEFAULT_DISCOVERY_PORTS),
        help="Comma-separated TCP ports to probe / 要探测的 TCP 端口",
    )
    net_discover.add_argument(
        "--timeout-ms",
        type=int,
        default=300,
        help="Per-connection timeout in milliseconds / 每次连接超时毫秒数",
    )
    net_discover.add_argument("--workers", type=int, default=64, help="Concurrent probe workers / 并发探测数")
    net_discover.add_argument(
        "--max-hosts",
        type=int,
        default=256,
        help="Maximum hosts allowed in one scan / 单次扫描允许的最大主机数",
    )
    net_discover.add_argument(
        "--no-idn",
        action="store_true",
        help="Only test open ports; do not send read-only *IDN? / 只测端口，不发送只读 *IDN?",
    )
    net_discover.add_argument(
        "--idn-only",
        action="store_true",
        help="Only show devices that answered *IDN? / 只显示响应 *IDN? 的设备",
    )
    net_discover.add_argument(
        "--no-visa",
        action="store_true",
        help="Skip PyVISA resource-manager discovery / 跳过 PyVISA 资源枚举",
    )

    run_sub = run_parser.add_subparsers(dest="command", required=True)
    run_check = run_sub.add_parser(
        "check", help="Parse and validate a run plan without connecting to instruments"
    )
    run_check.add_argument("--plan", required=True, help="Path to a WaveBench run plan TOML file")
    add_runtime_options(run_check)
    run_intent = run_sub.add_parser(
        "intent",
        help="Build an offline execution intent for a run plan / 为运行计划生成离线执行意图",
    )
    run_intent.add_argument("--plan", required=True, help="Path to a WaveBench run plan TOML file")
    run_intent.add_argument("--output", default=None, help="Write the execution intent JSON to this path")
    add_runtime_options(run_intent)
    run_verify = run_sub.add_parser(
        "verify",
        help="Verify / 预检 instruments referenced by a run plan with read-only *IDN? queries",
    )
    run_verify.add_argument("--plan", required=True, help="Path to a WaveBench run plan TOML file")
    add_runtime_options(run_verify)
    run_sub.add_parser("schema", help="Print supported run plan step kinds and fields")
    run_template = run_sub.add_parser("template", help="Create or print conservative run plan templates")
    run_template.add_argument("template", nargs="?", help="Template name, e.g. source-scope-sine")
    run_template.add_argument("--list", action="store_true", help="List available run plan templates")
    run_template.add_argument("--output", help="Write template to this TOML path")
    run_template.add_argument("--print", action="store_true", dest="print_template", help="Print template to stdout")
    run_template.add_argument("--force", action="store_true", help="Overwrite --output when it already exists")
    run_template.add_argument("--frequency", type=float, default=1000.0, help="Template signal frequency in Hz")
    run_template.add_argument(
        "--frequencies",
        help="Comma-separated template sweep frequencies in Hz, e.g. 100,1000,10000",
    )
    run_template.add_argument("--vpp", type=float, default=1.0, help="Template source amplitude in Vpp")
    run_template.add_argument("--source-channel", type=int, default=None, help="Template source channel")
    run_template.add_argument("--scope-channel", type=int, default=None, help="Template scope channel")
    run_template.add_argument("--reference-channel", type=int, default=None, help="Template frequency-response reference scope channel")
    run_template.add_argument("--response-channel", type=int, default=None, help="Template frequency-response DUT-output scope channel")
    run_template.add_argument("--fit", action="store_true", dest="frequency_response_fit", help="Enable all frequency-response fit candidates in the template")
    run_template.add_argument("--power-channel", type=int, default=None, help="Template power channel")
    run_template.add_argument("--voltage", type=float, default=3.3, help="Template power voltage in V")
    run_template.add_argument("--current-limit", type=float, default=0.1, help="Template power current limit in A")
    run_plan = run_sub.add_parser("plan", help="Execute a WaveBench run plan")
    run_plan.add_argument("--plan", required=True, help="Path to a WaveBench run plan TOML file")
    run_plan.add_argument(
        "--intent",
        default=None,
        help="Verify this execution intent before opening instrument sessions / 打开仪器会话前核验执行意图",
    )
    add_runtime_options(run_plan)
    run_calibrate = run_sub.add_parser(
        "calibrate", help="Build an offline 2D frequency-response calibration LUT from an existing run"
    )
    run_calibrate.add_argument("path", help="Path to data/runs/<run_dir>")
    run_calibrate.add_argument(
        "--config", required=True, help="Path to TOML containing a [calibration] table"
    )
    run_calibrate.add_argument(
        "--response",
        default=None,
        help="Frequency-response label for a multi-response run / 多频响 run 的响应标签",
    )
    run_compare = run_sub.add_parser(
        "compare",
        help="Compare frequency-response results from existing runs offline",
    )
    run_compare.add_argument(
        "paths",
        nargs="+",
        help="Two or more data/runs/<run_dir> paths to compare / 要比较的 run 目录",
    )
    run_compare.add_argument(
        "--response",
        default=None,
        help="Frequency-response label to select in each run / 每个 run 中选择的频响标签",
    )
    run_compare.add_argument(
        "--output",
        default=None,
        help="Write the machine-readable comparison JSON to this path",
    )
    run_compare.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format; JSON is suitable for automation",
    )
    run_compare.add_argument(
        "--gain-tolerance-db",
        type=float,
        default=None,
        help="Optional absolute gain-difference limit in dB",
    )
    run_compare.add_argument(
        "--phase-tolerance-deg",
        type=float,
        default=None,
        help="Optional absolute phase-difference limit in degrees",
    )
    run_resume = run_sub.add_parser(
        "resume",
        help="Prepare an offline frequency-response remeasurement manifest",
    )
    run_resume.add_argument(
        "path",
        help="Existing run directory or frequency_response.csv path",
    )
    run_resume.add_argument("--plan", required=True, help="Current run plan TOML file")
    run_resume.add_argument("--response", default=None, help="Frequency-response label for a multi-response run")
    run_resume.add_argument("--output", default=None, help="Write the resume manifest JSON to this path")
    run_report = run_sub.add_parser("report", help="Generate an offline HTML report for a run package")
    run_report.add_argument("path", help="Path to data/runs/<run_dir>")
    run_report.add_argument("--output", default=None, help="Output HTML path; defaults to <run_dir>/report.html")
    run_report.add_argument(
        "--pdf",
        action="store_true",
        help="Also export a portable PDF with visible images embedded / 同时导出嵌入可见图片的便携 PDF 报告",
    )
    run_report.add_argument("--pdf-output", default=None, help="Output PDF path; defaults to the HTML path with a .pdf suffix")
    run_report_index = run_sub.add_parser("report-index", help="Generate manifest JSON/CSV for multiple run directories")
    run_report_index.add_argument("paths", nargs="+", help="Paths to data/runs/<run_dir>")
    run_report_index.add_argument("--output", required=True, help="Output directory for manifest.json and manifest.csv")

    capture_sub = capture_parser.add_subparsers(dest="command", required=True)
    capture_inspect = capture_sub.add_parser("inspect", help="Inspect an offline capture package")
    capture_inspect.add_argument("path", help="Path to data/raw/<capture_dir>")
    capture_inspect.add_argument("--fft", action="store_true", help="Print offline FFT spectrum summary for saved NPY waveforms")
    capture_inspect.add_argument("--harmonics", type=int, default=5, help="Highest harmonic order to report with --fft")
    capture_inspect.add_argument("--fft-expect-frequency", type=float, default=None, help="Expected FFT peak frequency in Hz")
    capture_inspect.add_argument("--fft-frequency-tolerance", type=float, default=0.05, help="Relative tolerance for --fft-expect-frequency")

    mcp_sub = mcp_parser.add_subparsers(dest="command", required=True)
    mcp_serve = mcp_sub.add_parser(
        "serve",
        help="Serve read-only HTTP MCP tools / 启动只读 HTTP MCP 工具服务",
    )
    mcp_serve.add_argument(
        "--host",
        default=DEFAULT_MCP_HOST,
        help="Bind host, defaults to 127.0.0.1 / 监听地址，默认 127.0.0.1",
    )
    mcp_serve.add_argument(
        "--port",
        type=int,
        default=DEFAULT_MCP_PORT,
        help="Bind port / 监听端口",
    )
    mcp_serve.add_argument(
        "--token",
        default=None,
        help="Bearer token for HTTP MCP auth / HTTP MCP Bearer 认证 token",
    )
    mcp_serve.add_argument(
        "--token-env",
        default=DEFAULT_MCP_TOKEN_ENV,
        help="Environment variable containing the bearer token / 保存 Bearer token 的环境变量",
    )
    mcp_serve.add_argument(
        "--config",
        default="wavebench.toml",
        help="Path to wavebench TOML config for read-only checks / 只读检查使用的 wavebench TOML 配置",
    )

    dmm_sub = dmm_parser.add_subparsers(dest="command", required=True)
    dmm_idn = dmm_sub.add_parser("idn", help="Query DMM *IDN? over configured backend")
    add_runtime_options(dmm_idn)
    dmm_read = dmm_sub.add_parser("read", help="Read one DMM measurement")
    dmm_read.add_argument("function", nargs="?", default="dcv", help="dcv/acv/dci/aci/res/fres/freq/period/continuity/diode/cap")
    add_runtime_options(dmm_read)
    dmm_profile = dmm_sub.add_parser(
        "profile", help="Query current DMM function/range profile without changing state"
    )
    add_runtime_options(dmm_profile)
    dmm_function = dmm_sub.add_parser(
        "function", help="Query or set DMM function / 查询或设置万用表功能"
    )
    dmm_function_sub = dmm_function.add_subparsers(
        dest="dmm_function_command", required=True
    )
    dmm_function_status = dmm_function_sub.add_parser(
        "status", help="Query current DMM function / 查询当前万用表功能"
    )
    add_runtime_options(dmm_function_status)
    dmm_function_set = dmm_function_sub.add_parser(
        "set", help="Set DMM function / 设置万用表功能"
    )
    dmm_function_set.add_argument(
        "function",
        help="dcv/acv/dci/aci/res/fres/freq/period/continuity/diode/cap",
    )
    add_runtime_options(dmm_function_set)
    dmm_range = dmm_sub.add_parser(
        "range", help="Set a DCV or ACV range code with readback verification"
    )
    dmm_range_sub = dmm_range.add_subparsers(dest="dmm_range_command", required=True)
    dmm_range_set = dmm_range_sub.add_parser(
        "set", help="Set the range for the already-active DCV or ACV function"
    )
    dmm_range_set.add_argument("function", choices=("dcv", "acv"))
    dmm_range_set.add_argument("range_code", type=int, choices=range(5))
    add_runtime_options(dmm_range_set)
    dmm_impedance = dmm_sub.add_parser(
        "impedance", help="Set DCV input impedance with readback verification"
    )
    dmm_impedance_sub = dmm_impedance.add_subparsers(
        dest="dmm_impedance_command", required=True
    )
    dmm_impedance_set = dmm_impedance_sub.add_parser(
        "set", help="Set impedance for the already-active DCV function"
    )
    dmm_impedance_set.add_argument("impedance", type=str.upper, choices=("10M", "10G"))
    add_runtime_options(dmm_impedance_set)
    dmm_trigger = dmm_sub.add_parser("trigger", help="Query existing DMM trigger state")
    dmm_trigger_sub = dmm_trigger.add_subparsers(dest="dmm_trigger_command", required=True)
    dmm_trigger_status = dmm_trigger_sub.add_parser(
        "status", help="Read trigger settings without changing or firing a trigger"
    )
    add_runtime_options(dmm_trigger_status)
    dmm_calculation = dmm_sub.add_parser(
        "calculation", help="Query existing DMM calculation state and statistics"
    )
    dmm_calculation_sub = dmm_calculation.add_subparsers(
        dest="dmm_calculation_command", required=True
    )
    dmm_calculation_status = dmm_calculation_sub.add_parser(
        "status", help="Read calculation mode and references without changing state"
    )
    add_runtime_options(dmm_calculation_status)
    dmm_calculation_statistics = dmm_calculation_sub.add_parser(
        "statistics", help="Read a statistic only when its matching calculation is already active"
    )
    dmm_calculation_statistics.add_argument("function", choices=("average", "min", "max"))
    dmm_calculation_statistics.add_argument(
        "--calculation-active-confirmed",
        action="store_true",
        help="Confirm the selected calculation is already active; WaveBench will not enable it",
    )
    add_runtime_options(dmm_calculation_statistics)
    dmm_system_interface = dmm_sub.add_parser(
        "system-interface", help="Query a redacted DMM system and interface status snapshot"
    )
    dmm_system_interface_sub = dmm_system_interface.add_subparsers(
        dest="dmm_system_interface_command", required=True
    )
    dmm_system_interface_status = dmm_system_interface_sub.add_parser(
        "status", help="Read non-sensitive system and interface state without changing it"
    )
    add_runtime_options(dmm_system_interface_status)

    power_sub = power_parser.add_subparsers(dest="command", required=True)
    power_idn = power_sub.add_parser("idn", help="Query power supply *IDN?")
    add_runtime_options(power_idn)
    power_status = power_sub.add_parser("status", help="Query power supply channel status")
    power_status.add_argument("--channel", type=int, default=None)
    add_runtime_options(power_status)
    power_set = power_sub.add_parser("set", help="Set power supply voltage and current limit; does not change output state")
    power_set.add_argument("--channel", type=int, default=None)
    power_set.add_argument("--voltage", type=float, required=True)
    power_set.add_argument("--current-limit", type=float, required=True)
    add_runtime_options(power_set)
    power_output = power_sub.add_parser("output", help="Turn power supply channel output on or off")
    power_output.add_argument("--channel", type=int, default=None)
    power_output.add_argument("state", choices=["on", "off"])
    add_runtime_options(power_output)
    power_protection = power_sub.add_parser("protection", help="Query or set power supply OVP/OCP protection")
    protection_sub = power_protection.add_subparsers(dest="protection_command", required=True)
    protection_status = protection_sub.add_parser("status", help="Query OVP/OCP protection status")
    protection_status.add_argument("--channel", type=int, default=None)
    add_runtime_options(protection_status)
    protection_set = protection_sub.add_parser("set", help="Set OVP/OCP thresholds or enable state")
    protection_set.add_argument("--channel", type=int, default=None)
    protection_set.add_argument("--ovp-threshold", type=float, default=None)
    protection_set.add_argument("--ovp", choices=["on", "off"], default=None)
    protection_set.add_argument("--ocp-threshold", type=float, default=None)
    protection_set.add_argument("--ocp", choices=["on", "off"], default=None)
    add_runtime_options(protection_set)

    source_sub = source_parser.add_subparsers(dest="command", required=True)

    source_idn = source_sub.add_parser("idn", help="Query source *IDN?")
    add_runtime_options(source_idn)

    source_errors = source_sub.add_parser("errors", help="Read source SYST:ERR? until empty")
    add_runtime_options(source_errors)

    source_status = source_sub.add_parser("status", help="Query source channel status")
    source_status.add_argument("--channel", type=int, default=None)
    add_runtime_options(source_status)

    source_snapshot_v2 = source_sub.add_parser(
        "snapshot-v2",
        help="Query a typed, read-only Source V2 snapshot",
    )
    add_runtime_options(source_snapshot_v2)

    source_basic_configure_v2 = source_sub.add_parser(
        "basic-configure-v2",
        help="Configure one OFF Source V2 channel with one or more basic fields",
    )
    source_basic_configure_v2.add_argument("--channel", type=int, required=True)
    source_basic_configure_v2.add_argument(
        "--waveform",
        choices=("sine", "square", "ramp", "pulse", "noise", "dc"),
        default=None,
    )
    source_basic_configure_v2.add_argument("--frequency-hz", type=float, default=None)
    source_basic_configure_v2.add_argument("--amplitude-vpp", type=float, default=None)
    source_basic_configure_v2.add_argument("--offset-v", type=float, default=None)
    source_basic_configure_v2.add_argument(
        "--square-duty-cycle-percent",
        type=float,
        default=None,
    )
    add_runtime_options(source_basic_configure_v2)

    source_output_v2 = source_sub.add_parser(
        "output-v2",
        help="Turn one Source V2 channel output on or off",
    )
    source_output_v2.add_argument("--channel", type=int, required=True)
    source_output_v2.add_argument("state", choices=("on", "off"))
    add_runtime_options(source_output_v2)

    source_harmonics_configure_v2 = source_sub.add_parser(
        "harmonics-configure-v2",
        help="Configure one OFF Source V2 channel with a declared Harmonic preset",
    )
    source_harmonics_configure_v2.add_argument("--channel", type=int, required=True)
    source_harmonics_configure_v2.add_argument("--order", type=int, required=True)
    source_harmonics_configure_v2.add_argument(
        "--preset",
        choices=("all", "even", "odd"),
        required=True,
    )
    add_runtime_options(source_harmonics_configure_v2)

    source_profile = source_sub.add_parser(
        "profile",
        help="Query the complete read-only source channel profile",
    )
    source_profile.add_argument("--channel", type=int, default=None)
    add_runtime_options(source_profile)

    source_pulse_profile = source_sub.add_parser(
        "pulse-profile",
        help="Query the complete read-only pulse-shape profile",
    )
    source_pulse_profile.add_argument("--channel", type=int, default=None)
    add_runtime_options(source_pulse_profile)

    source_burst_profile = source_sub.add_parser(
        "burst-profile",
        help="Query the complete read-only burst profile",
    )
    source_burst_profile.add_argument("--channel", type=int, default=None)
    add_runtime_options(source_burst_profile)

    source_sweep_profile = source_sub.add_parser(
        "sweep-profile",
        help="Query the complete built-in sweep profile without changing or triggering it",
    )
    source_sweep_profile.add_argument("--channel", type=int, default=None)
    add_runtime_options(source_sweep_profile)

    source_counter_profile = source_sub.add_parser(
        "counter-profile",
        help="Query the non-destructive frequency-counter profile without enabling or clearing it",
    )
    add_runtime_options(source_counter_profile)

    source_set_freq = source_sub.add_parser("set-freq", help="Set source channel frequency in Hz")
    source_set_freq.add_argument("--channel", type=int, default=None)
    source_set_freq.add_argument("value_hz", type=float)
    add_runtime_options(source_set_freq)

    source_output = source_sub.add_parser("output", help="Set source channel output on or off")
    source_output.add_argument("--channel", type=int, default=None)
    source_output.add_argument("state", choices=["on", "off", "ON", "OFF"])
    add_runtime_options(source_output)

    source_set_func = source_sub.add_parser("set-func", help="Set source channel waveform function")
    source_set_func.add_argument("--channel", type=int, default=None)
    source_set_func.add_argument("function", help="Waveform function: sin, squ, ramp/triangle, puls, nois, or dc")
    add_runtime_options(source_set_func)

    source_set_vpp = source_sub.add_parser("set-vpp", help="Set source channel amplitude in Vpp")
    source_set_vpp.add_argument("--channel", type=int, default=None)
    source_set_vpp.add_argument("value_vpp", type=float)
    add_runtime_options(source_set_vpp)

    source_set_duty = source_sub.add_parser("set-duty", help="Set square-wave duty cycle in percent")
    source_set_duty.add_argument("--channel", type=int, default=None)
    source_set_duty.add_argument("duty_percent", type=float)
    add_runtime_options(source_set_duty)

    source_arb_probe = source_sub.add_parser("arb-probe", help="Run query-only DG4202 arbitrary-waveform SCPI probes; does not upload or enable output")
    source_arb_probe.add_argument("--channel", type=int, default=None)
    source_arb_probe.add_argument("--probe-timeout-ms", type=int, default=1000, help="Per-query timeout for unsupported SCPI candidates")
    add_runtime_options(source_arb_probe)

    source_arb_load = source_sub.add_parser("arb-load", help="Load a DG4202 arbitrary waveform from .csv/.npy; dry-run can export offline payloads")
    source_arb_load.add_argument("--channel", type=int, required=True)
    source_arb_load.add_argument("--file", required=True, help="Input waveform file: .csv or .npy")
    source_arb_load.add_argument("--name", required=True, help="Instrument waveform name, e.g. EXAMPLE_ARB")
    source_arb_load.add_argument("--amplitude", type=float, required=True, help="Target output amplitude in Vpp")
    source_arb_load.add_argument("--frequency", type=float, default=None, help="Arbitrary waveform playback frequency in Hz; required when uploading")
    source_arb_load.add_argument("--offset", type=float, default=0.0, help="Target output offset in V")
    source_arb_load.add_argument("--sample-rate", type=float, default=None, help="Sample rate in Hz when the file has no time axis")
    source_arb_load.add_argument("--max-points", type=int, default=16384, help="Point-count guard; DG4000 specs list 16K arbitrary length")
    source_arb_load.add_argument("--output-on", action="store_true", help="Allow output state change after upload; ignored by dry-run")
    source_arb_load.add_argument("--export-payload", default=None, help="Write a WaveBench JSON payload artifact for manual review or future upload")
    source_arb_load.add_argument("--export-dg4000-dac-block", default=None, help="Write a DG4000 DATA:DAC VOLATILE binary SCPI command; offline artifact only")
    source_arb_load.add_argument("--dg4000-byte-order", choices=("big", "little"), default="little", help="Byte order for DG4000 uint16 DAC block; DG4202 hardware validation confirmed little-endian")
    source_arb_load.add_argument("--dry-run", action="store_true", help="Only validate/build payload summary; do not connect to the instrument")
    add_runtime_options(source_arb_load)

    sweep_sub = sweep_parser.add_subparsers(dest="command", required=True)
    sweep_discrete = sweep_sub.add_parser("discrete", help="Run a discrete source-frequency sweep and capture each point")
    sweep_discrete.add_argument("--source-channel", type=int, default=None)
    sweep_discrete.add_argument("--scope-channel", type=int, default=None)
    sweep_discrete.add_argument("--source-resource", default=None, help="Override source VISA resource")
    sweep_discrete.add_argument("--frequencies", required=True, help="Comma-separated frequency list in Hz, e.g. 1000,2000,5000")
    sweep_discrete.add_argument("--target-cycles", type=float, default=10.0)
    sweep_discrete.add_argument("--frequency-tolerance", type=float, default=None)
    sweep_discrete.add_argument("--source-func", default=None, help="Optional source function to set once before sweep")
    sweep_discrete.add_argument("--source-vpp", type=float, default=None, help="Optional source amplitude in Vpp to set once before sweep")
    sweep_discrete.add_argument("--restore-source-state", action="store_true", help="Restore basic source output/function/frequency/amplitude/duty after sweep")
    sweep_discrete.add_argument("--allow-50ohm", action="store_true", help="Explicitly allow scope input coupling that may be 50 ohm; default requires high impedance")
    sweep_discrete.add_argument("--label", default="discrete_sweep")
    sweep_discrete.add_argument("--no-csv", action="store_true", help="Do not save per-point CSV waveform output")
    sweep_discrete.add_argument("--no-npy", action="store_true", help="Do not save per-point NPY waveform output")
    add_runtime_options(sweep_discrete)

    scope_sub = scope_parser.add_subparsers(dest="command", required=True)

    idn = scope_sub.add_parser("idn", help="Query *IDN?")
    add_runtime_options(idn)

    errors = scope_sub.add_parser("errors", help="Read SYST:ERR? until empty")
    add_runtime_options(errors)

    status = scope_sub.add_parser(
        "status",
        help="Read a typed, non-mutating oscilloscope state snapshot",
    )
    status.add_argument("--channel", type=int, default=None)
    status.add_argument(
        "--strict",
        action="store_true",
        help="Require the complete scope.snapshot capability / 要求完整 scope.snapshot 能力",
    )
    add_runtime_options(status)

    acquisition_status = scope_sub.add_parser(
        "acquisition-status",
        help="Query read-only average and segmented-acquisition state",
    )
    add_runtime_options(acquisition_status)

    capture_average = scope_sub.add_parser(
        "capture-average",
        help="Run a controlled average acquisition and restore its configuration",
    )
    capture_average.add_argument(
        "--channel",
        type=int,
        action="append",
        required=True,
        help="Capture channel; repeat for multiple channels",
    )
    capture_average.add_argument(
        "--average-count",
        type=int,
        required=True,
        help="Power-of-two average count from 2 through 1024",
    )
    capture_average.add_argument(
        "--allow-50ohm",
        action="store_true",
        help="Explicitly allow scope input coupling that may be 50 ohm",
    )
    capture_average.add_argument(
        "--acquisition-stopped",
        action="store_true",
        help="Confirm acquisition is stopped before changing average settings",
    )
    add_runtime_options(capture_average)

    history_timestamps = scope_sub.add_parser(
        "history-timestamps",
        help="Query the read-only history timestamp table for one channel",
    )
    history_timestamps.add_argument("--channel", type=int, default=None)
    add_runtime_options(history_timestamps)

    digital_status = scope_sub.add_parser(
        "digital-status",
        help="Query the existing state of one MSO digital channel",
    )
    digital_status.add_argument(
        "--channel",
        type=int,
        required=True,
        help="Zero-based digital channel number (for example, 0 for D0)",
    )
    add_runtime_options(digital_status)

    digital_waveform = scope_sub.add_parser(
        "digital-waveform",
        help="Read existing MSO digital waveforms and merge Dn into uint16 bit n",
    )
    digital_waveform.add_argument(
        "--channel",
        type=int,
        action="append",
        required=True,
        help="Zero-based digital channel; repeat for multiple channels",
    )
    digital_waveform.add_argument(
        "--acquisition-stopped",
        action="store_true",
        help="Confirm acquisition is stopped so all channels refer to one stable record",
    )
    digital_waveform.add_argument(
        "--output",
        help="Optional .npy path for packed uint16 samples",
    )
    add_runtime_options(digital_waveform)

    measurement_statistics = scope_sub.add_parser(
        "measurement-statistics",
        help="Read an explicitly preconfigured automatic-measurement slot",
    )
    measurement_statistics.add_argument("--slot", type=int, required=True)
    measurement_statistics.add_argument(
        "--configured-slot",
        action="store_true",
        help="Confirm the slot was configured before this command",
    )
    measurement_statistics.add_argument(
        "--include-buffer",
        action="store_true",
        help="Include the statistics buffer (requires --acquisition-stopped)",
    )
    measurement_statistics.add_argument(
        "--acquisition-stopped",
        action="store_true",
        help="Confirm acquisition is stopped before reading the statistics buffer",
    )
    add_runtime_options(measurement_statistics)

    math_metadata = scope_sub.add_parser(
        "math-metadata",
        help="Query metadata for an existing math waveform",
    )
    math_metadata.add_argument("--index", type=int, required=True)
    add_runtime_options(math_metadata)

    fft_status = scope_sub.add_parser(
        "fft-status",
        help="Query status for an existing FFT math waveform",
    )
    fft_status.add_argument("--index", type=int, required=True)
    fft_status.add_argument(
        "--configured-fft",
        action="store_true",
        help="Confirm the math waveform is already configured as FFT",
    )
    add_runtime_options(fft_status)

    reference_metadata = scope_sub.add_parser(
        "reference-metadata",
        help="Query metadata for an existing reference waveform",
    )
    reference_metadata.add_argument("--index", type=int, required=True)
    add_runtime_options(reference_metadata)

    cursor_readout = scope_sub.add_parser(
        "cursor-readout",
        help="Read an explicitly preconfigured cursor result",
    )
    cursor_readout.add_argument("--index", type=int, default=1)
    cursor_readout.add_argument(
        "--configured-cursor",
        action="store_true",
        help="Confirm the cursor is already configured",
    )
    add_runtime_options(cursor_readout)

    screenshot = scope_sub.add_parser(
        "screenshot",
        help="Query the screenshot profile or capture a typed screenshot",
    )
    screenshot_sub = screenshot.add_subparsers(dest="screenshot_command", required=True)
    screenshot_profile = screenshot_sub.add_parser("profile", help="Query screenshot limits")
    add_runtime_options(screenshot_profile)
    screenshot_capture = screenshot_sub.add_parser(
        "capture",
        help="Capture a screenshot through the scope.screenshot_v2 contract",
    )
    screenshot_capture.add_argument("--output", required=True, help="New .png output path")
    screenshot_capture.add_argument(
        "--artifact",
        default=None,
        help="New JSON artifact path; defaults to <output>.json",
    )
    screenshot_capture.add_argument(
        "--menu-mode",
        choices=("device", "include", "exclude"),
        default="device",
    )
    screenshot_capture.add_argument(
        "--color-mode",
        choices=("device", "color", "monochrome", "inverted"),
        default="device",
    )
    add_scope_error_options(screenshot_capture)
    add_runtime_options(screenshot_capture)

    acquisition = scope_sub.add_parser(
        "acquisition",
        help="Inspect or control acquisition through the typed R1.3 contract",
    )
    acquisition_sub = acquisition.add_subparsers(dest="acquisition_command", required=True)
    acquisition_state = acquisition_sub.add_parser("status", help="Query acquisition run state")
    add_runtime_options(acquisition_state)
    acquisition_start = acquisition_sub.add_parser("start", help="Start continuous acquisition")
    acquisition_start.add_argument(
        "--trigger-mode",
        choices=("auto", "normal", "roll"),
        required=True,
    )
    add_scope_error_options(acquisition_start)
    add_runtime_options(acquisition_start)
    acquisition_single = acquisition_sub.add_parser("single", help="Acquire one proven record")
    add_scope_error_options(acquisition_single)
    add_runtime_options(acquisition_single)
    acquisition_stop = acquisition_sub.add_parser("stop", help="Stop acquisition")
    add_scope_error_options(acquisition_stop)
    add_runtime_options(acquisition_stop)

    trace = scope_sub.add_parser(
        "trace",
        help="Query trace metadata or fetch a typed trace",
    )
    trace_sub = trace.add_subparsers(dest="trace_command", required=True)

    def add_trace_reference(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--kind",
            dest="trace_kind",
            choices=("analog", "digital", "reference"),
            required=True,
        )
        reference = parser.add_mutually_exclusive_group(required=True)
        reference.add_argument("--index", dest="trace_index", type=int)
        reference.add_argument("--name", dest="trace_name")

    trace_metadata = trace_sub.add_parser("metadata", help="Query trace metadata")
    add_trace_reference(trace_metadata)
    add_runtime_options(trace_metadata)
    trace_fetch = trace_sub.add_parser("fetch", help="Fetch trace samples into a new .npy file")
    add_trace_reference(trace_fetch)
    trace_fetch.add_argument("--points", dest="trace_points", default="dmax")
    trace_fetch.add_argument("--output", required=True, help="New .npy output path")
    trace_fetch.add_argument(
        "--artifact",
        default=None,
        help="New JSON artifact path; defaults to <output>.json",
    )
    add_scope_error_options(trace_fetch)
    add_runtime_options(trace_fetch)

    auto = scope_sub.add_parser("auto", help="Run explicit AUToscale and wait for *OPC?")
    add_runtime_options(auto)

    autoscale = scope_sub.add_parser("autoscale", help="Alias of scope auto")
    add_runtime_options(autoscale)

    fetch = scope_sub.add_parser("fetch", help="Fetch waveform data without creating full package")
    fetch.add_argument("--channel", type=int, default=None)
    fetch.add_argument("--points", default=None, help="Override waveform points: def, max, or dmax")
    fetch.add_argument("--allow-50ohm", action="store_true", help="Explicitly allow scope input coupling that may be 50 ohm; default requires high impedance")
    add_runtime_options(fetch)

    capture = scope_sub.add_parser("capture", help="Capture waveform data into an acquisition package")
    capture.add_argument("--channel", type=int, action="append", default=None, help="Capture channel; repeat for multiple channels")
    capture.add_argument("--label", default="capture")
    capture.add_argument("--points", default=None, help="Override waveform points: def, max, or dmax")
    capture.add_argument(
        "--time-range",
        type=float,
        default=None,
        help="Set the total acquisition window in seconds; the driver converts it to s/div",
    )
    capture.add_argument("--expect-frequency", type=float, default=None, help="Expected signal frequency in Hz for metadata quality checks")
    capture.add_argument("--window-frequency", type=float, default=None, help="Frequency in Hz used only to compute target-cycle time range")
    capture.add_argument("--target-cycles", type=float, default=None, help="Set time range to target_cycles / window_frequency")
    capture.add_argument("--frequency-tolerance", type=float, default=None, help="Relative frequency tolerance, e.g. 0.05 for 5 percent")
    capture.add_argument("--vertical-scale", type=float, default=None, help="Set channel vertical scale in V/div before capture")
    capture.add_argument("--target-vpp", type=float, default=None, help="Set vertical scale from expected Vpp; defaults to about 5 vertical divisions")
    capture.add_argument("--no-csv", action="store_true", help="Do not save CSV waveform output")
    capture.add_argument("--no-npy", action="store_true", help="Do not save NPY waveform output")
    capture.add_argument("--screenshot", action="store_true", help="Save a PNG screenshot artifact in the capture package")
    capture.add_argument("--allow-50ohm", action="store_true", help="Explicitly allow scope input coupling that may be 50 ohm; default requires high impedance")
    add_runtime_options(capture)

    return parser
