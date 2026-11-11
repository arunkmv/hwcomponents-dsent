from logging import Logger
import fcntl
import glob
import hashlib
import os
import subprocess
from typing import Callable, Dict
from hwcomponents import ComponentModel, action, ActionCost

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_DSENT_DIR = os.path.join(_THIS_DIR, "dsent")
_TECH_MODEL_DIR = os.path.join(_DSENT_DIR, "tech", "tech_models")

# Bulk planar models, in nm. TG11LVT is a tri-gate device and is only used if a model
# is created with include_11nm_trigate=True.
_BULK_TECH_MODELS = {22: "Bulk22LVT.model", 32: "Bulk32LVT.model", 45: "Bulk45LVT.model"}
_TRIGATE_TECH_MODELS = {11: "TG11LVT.model"}


def _clean_tmp_dir():
    temp_dir = os.path.join(_THIS_DIR, "dsent_inputs_outputs")
    os.makedirs(temp_dir, exist_ok=True)
    files = sorted(
        [f for f in glob.glob(os.path.join(temp_dir, "*")) if not f.endswith(".lock")],
        key=os.path.getctime,
        reverse=True,
    )
    if len(files) > 200:
        for file in files[200:]:
            try:
                os.remove(file)
            except OSError:
                pass
    return temp_dir


def _get_dsent_executable() -> str:
    p = os.path.join(_DSENT_DIR, "dsent")
    if os.path.isfile(p):
        return p
    raise FileNotFoundError(
        f"DSENT executable not found at {p}. Run `make build` in the "
        f"hwcomponents-dsent repository."
    )


def _format_cfg(params: Dict[str, object], evaluate: Dict[str, str]) -> str:
    """
    Formats a DSENT config file. `evaluate` maps output names to DSENT calculator
    expressions; each one is printed by the EvaluateString and returned by
    `_call_dsent`.
    """
    lines = [f"{k} = {v}" for k, v in params.items()]
    eval_lines = [f'    print "{name}" {expr};' for name, expr in evaluate.items()]
    lines.append("EvaluateString = \\\n" + " \\\n".join(eval_lines))
    return "\n".join(lines) + "\n"


def _call_dsent(
    logger: Logger, params: Dict[str, object], evaluate: Dict[str, str]
) -> Dict[str, float]:
    cfg = _format_cfg(params, evaluate)

    temp_dir = _clean_tmp_dir()
    input_name = hashlib.sha256(cfg.encode()).hexdigest()
    input_path = os.path.join(temp_dir, input_name + ".cfg")
    output_path = os.path.join(temp_dir, input_name + ".out")
    log_path = os.path.join(temp_dir, input_name + ".log")
    lock_path = os.path.join(temp_dir, input_name + ".lock")

    def read_results():
        results = {}
        with open(output_path, "r") as f:
            for line in f:
                name, value = line.rstrip("\n").split("\t")
                results[name] = float(value)
        missing = set(evaluate) - set(results)
        if missing:
            raise ValueError(f"Missing DSENT outputs {missing}")
        return results

    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            if os.path.exists(output_path):
                try:
                    return read_results()
                except Exception as e:
                    logger.warning(
                        f"Error reading DSENT output file {output_path}: {e}. "
                        f"Deleting stale cache and rerunning DSENT."
                    )
                    try:
                        os.remove(output_path)
                    except OSError:
                        pass

            with open(input_path, "w") as f:
                f.write(cfg)

            exec_list = [_get_dsent_executable(), input_path, output_path]
            logger.info(f"Calling: {' '.join(exec_list)} > {log_path} 2>&1")
            with open(log_path, "w") as out:
                result = subprocess.call(
                    exec_list, cwd=temp_dir, stdout=out, stderr=subprocess.STDOUT
                )

            if result != 0 or not os.path.exists(output_path):
                # Don't leave a partial output behind to be read as a cache hit.
                try:
                    os.remove(output_path)
                except OSError:
                    pass
                raise Exception(
                    f"DSENT failed with exit code {result}. Please check {log_path} "
                    f"for DSENT output. Run command: {' '.join(exec_list)}"
                )

            return read_results()
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _interp_tech_node(
    logger: Logger,
    tech_node: float,
    callfunc: Callable[[str], Dict[str, float]],
    include_11nm_trigate: bool = False,
) -> Dict[str, float]:
    """
    Calls `callfunc(tech_model_path)` at the supported technology nodes surrounding
    `tech_node` (in nm) and interpolates / extrapolates the results. Energy and leakage
    keys are scaled as energy; keys starting with "area" are scaled as area.

    Out-of-range scaling matches hwcomponents-cacti: below the smallest node, energy and
    leakage scale with sqrt(node) (with a 21% FinFET leakage reduction) and area
    scales linearly; above the largest node, everything scales quadratically.
    """
    models = dict(_BULK_TECH_MODELS)
    if include_11nm_trigate:
        models.update(_TRIGATE_TECH_MODELS)
    supported = sorted(models)

    def call(node):
        return callfunc(os.path.join(_TECH_MODEL_DIR, models[node]))

    lo, hi = min(supported), max(supported)
    if tech_node < lo:
        scale = tech_node / lo
        logger.info(f"Extrapolating from {lo}nm to {tech_node}nm.")
        results = call(lo)
        return {
            k: v * scale if k.startswith("area") else v * scale**0.5 * (
                0.79 if k.startswith("leak") else 1
            )
            for k, v in results.items()
        }
    if tech_node > hi:
        scale = (tech_node / hi) ** 2
        logger.info(f"Extrapolating from {hi}nm to {tech_node}nm.")
        return {k: v * scale for k, v in call(hi).items()}

    node_lo = max(s for s in supported if s <= tech_node)
    node_hi = min(s for s in supported if s >= tech_node)
    if node_lo == node_hi:
        return call(node_lo)
    logger.info(f"Interpolating tech_node between {node_lo}nm and {node_hi}nm.")
    t = (tech_node - node_lo) / (node_hi - node_lo)
    r_lo, r_hi = call(node_lo), call(node_hi)
    return {k: (1 - t) * r_lo[k] + t * r_hi[k] for k in r_lo}


class Router(ComponentModel):
    """
    Electrical NoC input-buffered virtual-channel router modeled with DSENT.

    Clock-tree power is activity-independent, so it is reported as part of leak_power
    (clock energy per cycle * frequency) rather than as an action.

    Parameters
    ----------
        tech_node: The technology node in meters. DSENT supports 22, 32, and 45nm bulk
            directly; other nodes are interpolated or extrapolated.
        flit_width: The number of bits per flit.
        n_in_ports: The number of input ports.
        n_out_ports: The number of output ports.
        n_vnets: The number of virtual networks.
        vcs_per_vnet: The number of virtual channels per virtual network.
        buffers_per_vc: The number of flit buffers per virtual channel.
        frequency: The clock frequency in Hz. DSENT sizes gates to meet this frequency.
        pipeline_stages: The number of router pipeline stages, used for latency.
        buffer_model: DSENT input buffer model.
        crossbar_model: DSENT crossbar model.
        arbiter_model: DSENT switch allocator arbiter model.
        clock_tree_levels: The number of levels in the H-tree clock network.
        include_11nm_trigate: Also use DSENT's 11nm tri-gate model as an interpolation
            point. By default only bulk planar models are used.

    Attributes
    ----------
        component_name: set to ["Router", "router", "NoCRouter"]
        priority: set to 0.3
    """

    component_name = ["Router", "router", "NoCRouter"]
    priority = 0.3

    def __init__(
        self,
        tech_node: float,
        flit_width: int,
        n_in_ports: int = 5,
        n_out_ports: int = 5,
        n_vnets: int = 1,
        vcs_per_vnet: int = 4,
        buffers_per_vc: int = 4,
        frequency: float = 1e9,
        pipeline_stages: int = 1,
        buffer_model: str = "DFFRAM",
        crossbar_model: str = "MultiplexerCrossbar",
        arbiter_model: str = "MatrixArbiter",
        clock_tree_levels: int = 5,
        include_11nm_trigate: bool = False,
    ):
        self.tech_node = float(tech_node) * 1e9  # m -> nm
        self.flit_width = self.assert_int("flit_width", flit_width)
        self.n_in_ports = self.assert_int("n_in_ports", n_in_ports)
        self.n_out_ports = self.assert_int("n_out_ports", n_out_ports)
        self.n_vnets = self.assert_int("n_vnets", n_vnets)
        self.vcs_per_vnet = self.assert_int("vcs_per_vnet", vcs_per_vnet)
        self.buffers_per_vc = self.assert_int("buffers_per_vc", buffers_per_vc)
        self.frequency = float(frequency)
        self.pipeline_stages = self.assert_int("pipeline_stages", pipeline_stages)
        self.buffer_model = buffer_model
        self.crossbar_model = crossbar_model
        self.arbiter_model = arbiter_model
        self.clock_tree_levels = self.assert_int("clock_tree_levels", clock_tree_levels)

        r = _interp_tech_node(
            self.logger, self.tech_node, self._call_dsent, include_11nm_trigate
        )
        self.write_buffer_energy = r["write_buffer"]
        self.read_buffer_energy = r["read_buffer"]
        self.crossbar_energy = r["crossbar"]
        self.arbitrate_energy = r["arbitrate"]
        self.clock_power = r["clock"] * self.frequency
        for k, v in r.items():
            self.logger.info(f"DSENT returned {k} {v}")

        super().__init__(
            area=r["area"], leak_power=r["leakage"] + self.clock_power
        )

    def _call_dsent(self, tech_model_path: str) -> Dict[str, float]:
        vns = ", ".join([str(self.vcs_per_vnet)] * self.n_vnets)
        bufs = ", ".join([str(self.buffers_per_vc)] * self.n_vnets)
        params = {
            "ModelName": "Router",
            "ElectricalTechModelFilename": tech_model_path,
            "IsPerformTimingOptimization": "true",
            "TimingOptimization->StartNetNames": "[*]",
            "Frequency": self.frequency,
            "NumberInputPorts": self.n_in_ports,
            "NumberOutputPorts": self.n_out_ports,
            "NumberBitsPerFlit": self.flit_width,
            "NumberVirtualNetworks": self.n_vnets,
            "NumberVirtualChannelsPerVirtualNetwork": f"[{vns}]",
            "NumberBuffersPerVirtualChannel": f"[{bufs}]",
            "InputPort->BufferModel": self.buffer_model,
            "CrossbarModel": self.crossbar_model,
            "SwitchAllocator->ArbiterModel": self.arbiter_model,
            "ClockTreeModel": "BroadcastHTree",
            "ClockTree->NumberLevels": self.clock_tree_levels,
            "ClockTree->WireLayer": "Global",
            "ClockTree->WireWidthMultiplier": 1.0,
        }
        evaluate = {
            "write_buffer": "$(Energy>>Router:WriteBuffer)",
            "read_buffer": "$(Energy>>Router:ReadBuffer)",
            "crossbar": "$(Energy>>Router:TraverseCrossbar->Multicast1)",
            "arbitrate": "$(Energy>>Router:ArbitrateSwitch->ArbitrateStage1) + "
            "$(Energy>>Router:ArbitrateSwitch->ArbitrateStage2)",
            "clock": "$(Energy>>Router:DistributeClock)",
            "leakage": "$(NddPower>>Router:Leakage)",
            "area": "$(Area>>Router:Active)",
        }
        return _call_dsent(self.logger, params, evaluate)

    def _cost(self, energy: float, n_ports: int) -> ActionCost:
        return ActionCost(
            energy=energy,
            throughput=self.frequency * n_ports,
            latency=self.pipeline_stages / self.frequency,
        )

    @action(bits_per_action="flit_width")
    def route_flit(self) -> ActionCost:
        """
        Returns the energy, throughput, and latency to move one flit through the
        router: buffer write, buffer read, switch arbitration, and crossbar traversal.

        Parameters
        ----------
        bits_per_action : int
            The number of bits to route.

        Returns
        -------
            ActionCost: energy (Joules), throughput (flits/second), latency (seconds).
        """
        return self._cost(
            self.write_buffer_energy
            + self.read_buffer_energy
            + self.arbitrate_energy
            + self.crossbar_energy,
            self.n_out_ports,
        )

    @action(bits_per_action="flit_width")
    def write_buffer(self) -> ActionCost:
        """Returns the cost of writing one flit into an input buffer."""
        return self._cost(self.write_buffer_energy, self.n_in_ports)

    @action(bits_per_action="flit_width")
    def read_buffer(self) -> ActionCost:
        """Returns the cost of reading one flit from an input buffer."""
        return self._cost(self.read_buffer_energy, self.n_out_ports)

    @action(bits_per_action="flit_width")
    def arbitrate(self) -> ActionCost:
        """Returns the cost of one switch allocation for one flit."""
        return self._cost(self.arbitrate_energy, self.n_out_ports)

    @action(bits_per_action="flit_width")
    def traverse_crossbar(self) -> ActionCost:
        """Returns the cost of one flit traversing the crossbar."""
        return self._cost(self.crossbar_energy, self.n_out_ports)


class Crossbar(ComponentModel):
    """
    Electrical bufferless crossbar switch modeled with DSENT, e.g. a bus.

    A multiplexer crossbar with a matrix arbiter per output, granting it to one of the
    inputs. Unlike a `Router`, it neither buffers nor pipelines words: they pass from
    an input to an output within a cycle. A shared bus is a crossbar with one output
    (an `n_inputs`:1 multiplexer); a crossbar with dedicated connections has an output
    per endpoint. Wires to and from the switch are not included; model them with a
    `NoCLink` of their length.

    Parameters
    ----------
        tech_node: The technology node in meters. DSENT supports 22, 32, and 45nm bulk
            directly; other nodes are interpolated or extrapolated.
        width: The number of bits switched in parallel.
        n_inputs: The number of input ports.
        n_outputs: The number of output ports.
        frequency: The clock frequency in Hz. DSENT sizes gates to meet this frequency.
        include_11nm_trigate: Also use DSENT's 11nm tri-gate model as an interpolation
            point. By default only bulk planar models are used.

    Attributes
    ----------
        component_name: set to ["Crossbar", "crossbar", "CrossbarSwitch"]
        priority: set to 0.3
    """

    component_name = ["Crossbar", "crossbar", "CrossbarSwitch"]
    priority = 0.3

    def __init__(
        self,
        tech_node: float,
        width: int,
        n_inputs: int,
        n_outputs: int = 1,
        frequency: float = 1e9,
        include_11nm_trigate: bool = False,
    ):
        self.tech_node = float(tech_node) * 1e9  # m -> nm
        self.width = self.assert_int("width", width)
        self.n_inputs = self.assert_int("n_inputs", n_inputs)
        self.n_outputs = self.assert_int("n_outputs", n_outputs)
        self.frequency = float(frequency)

        r = _interp_tech_node(
            self.logger, self.tech_node, self._call_dsent, include_11nm_trigate
        )
        self.traverse_energy = r["traverse"]
        self.arbitrate_energy = r["arbitrate"]
        for k, v in r.items():
            self.logger.info(f"DSENT returned {k} {v}")

        arbiters = self.n_outputs if self.n_inputs > 1 else 0
        super().__init__(
            area=r["area_crossbar"] + arbiters * r["area_arbiter"],
            leak_power=r["leak_crossbar"] + arbiters * r["leak_arbiter"],
        )

    def _call_dsent(self, tech_model_path: str) -> Dict[str, float]:
        timing = {
            "ElectricalTechModelFilename": tech_model_path,
            "IsPerformTimingOptimization": "true",
            "TimingOptimization->StartNetNames": "[*]",
            "Frequency": self.frequency,
        }
        crossbar = _call_dsent(
            self.logger,
            {
                "ModelName": "MultiplexerCrossbar",
                **timing,
                "NumberInputs": self.n_inputs,
                "NumberOutputs": self.n_outputs,
                "NumberBits": self.width,
            },
            {
                "traverse": "$(Energy>>MultiplexerCrossbar:Multicast1)",
                "leak_crossbar": "$(NddPower>>MultiplexerCrossbar:Leakage)",
                "area_crossbar": "$(Area>>MultiplexerCrossbar:Active)",
            },
        )
        # A single input needs no arbitration.
        if self.n_inputs == 1:
            none = {"arbitrate": 0.0, "leak_arbiter": 0.0, "area_arbiter": 0.0}
            return crossbar | none
        arbiter = _call_dsent(
            self.logger,
            {"ModelName": "MatrixArbiter", **timing, "NumberRequests": self.n_inputs},
            {
                "arbitrate": "$(Energy>>MatrixArbiter:Arbitrate)",
                "leak_arbiter": "$(NddPower>>MatrixArbiter:Leakage)",
                "area_arbiter": "$(Area>>MatrixArbiter:Active)",
            },
        )
        return crossbar | arbiter

    @action(bits_per_action="width")
    def traverse(self) -> ActionCost:
        """
        Returns the energy, throughput, and latency to switch one `width`-bit word from
        an input to an output.

        Parameters
        ----------
        bits_per_action : int
            The number of bits to switch.

        Returns
        -------
            ActionCost: energy (Joules), throughput (words/second), latency (seconds).
        """
        return ActionCost(
            energy=self.traverse_energy,
            throughput=self.frequency * self.n_outputs,
            latency=1 / self.frequency,
        )

    @action
    def arbitrate(self) -> ActionCost:
        """
        Returns the energy, throughput, and latency to grant an output to an input, once
        per transfer of any number of words. Zero with a single input.

        Returns
        -------
            ActionCost: energy (Joules), throughput (grants/second), latency (seconds).
        """
        return ActionCost(
            energy=self.arbitrate_energy,
            throughput=self.frequency * self.n_outputs,
            latency=1 / self.frequency,
        )


class NoCLink(ComponentModel):
    """
    Electrical repeated-wire NoC link modeled with DSENT.

    Parameters
    ----------
        tech_node: The technology node in meters. DSENT supports 22, 32, and 45nm bulk
            directly; other nodes are interpolated or extrapolated.
        width: The number of bits sent in parallel over the link.
        length: The length of the link in meters.
        frequency: The clock frequency in Hz.
        delay: The required wire delay in seconds, used by DSENT to size repeaters.
            Defaults to one clock cycle.
        wire_layer: The metal layer, "Global" or "Intermediate".
        include_11nm_trigate: Also use DSENT's 11nm tri-gate model as an interpolation
            point. By default only bulk planar models are used.

    Attributes
    ----------
        component_name: set to ["NoCLink", "noc_link", "Link"]
        priority: set to 0.3
    """

    component_name = ["NoCLink", "noc_link", "Link"]
    priority = 0.3

    def __init__(
        self,
        tech_node: float,
        width: int,
        length: float,
        frequency: float = 1e9,
        delay: float | None = None,
        wire_layer: str = "Global",
        include_11nm_trigate: bool = False,
    ):
        self.tech_node = float(tech_node) * 1e9  # m -> nm
        self.width = self.assert_int("width", width)
        self.length = float(length)
        self.frequency = float(frequency)
        self.delay = float(delay) if delay is not None else 1 / self.frequency
        self.wire_layer = wire_layer

        r = _interp_tech_node(
            self.logger, self.tech_node, self._call_dsent, include_11nm_trigate
        )
        self.send_energy = r["send"]
        for k, v in r.items():
            self.logger.info(f"DSENT returned {k} {v}")

        super().__init__(area=r["area"], leak_power=r["leakage"])

    def _call_dsent(self, tech_model_path: str) -> Dict[str, float]:
        params = {
            "ModelName": "RepeatedLink",
            "ElectricalTechModelFilename": tech_model_path,
            "IsPerformTimingOptimization": "false",
            "Frequency": self.frequency,
            "NumberBits": self.width,
            "WireLayer": self.wire_layer,
            "WireWidthMultiplier": 1.0,
            "WireSpacingMultiplier": 1.0,
            "WireLength": self.length,
            "Delay": self.delay,
        }
        evaluate = {
            "send": "$(Energy>>RepeatedLink:Send)",
            "leakage": "$(NddPower>>RepeatedLink:Leakage)",
            "area": "$(Area>>RepeatedLink:Active)",
        }
        return _call_dsent(self.logger, params, evaluate)

    @action(bits_per_action="width")
    def send(self) -> ActionCost:
        """
        Returns the energy, throughput, and latency to send one `width`-bit transfer
        across the link.

        Parameters
        ----------
        bits_per_action : int
            The number of bits to send.

        Returns
        -------
            ActionCost: energy (Joules), throughput (transfers/second), latency
            (seconds).
        """
        return ActionCost(
            energy=self.send_energy, throughput=self.frequency, latency=self.delay
        )
