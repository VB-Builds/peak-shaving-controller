"""Modbus TCP register map for a battery inverter, a simulated inverter that serves it, and a small client.

The map is this project's own (generic, not any vendor's). Addresses are 0-based protocol addresses.

Holding registers (FC3 read, FC6/FC16 write), written by the site controller:
  0  control_mode     uint16   0 = standby, 1 = follow power setpoint
  1  power_setpoint   int16    0.1 kW, + discharge / - charge
  2  peak_threshold   uint16   1 kW, informational (the threshold the controller is holding)
  3  heartbeat        uint16   the controller changes it every tick; if it stops changing for
                               WATCHDOG_TICKS ticks in mode 1 the inverter goes to 0 kW (state 4)
Input registers (FC4), written by the inverter:
  0  soc              uint16   0.01 %
  1  active_power     int16    0.1 kW, + discharge / - charge (what was actually delivered)
  2  max_discharge    uint16   0.1 kW available right now
  3  max_charge       uint16   0.1 kW available right now
  4  discharged_hi    uint32   kWh discharged since start, high word first (registers 4-5)
  5  discharged_lo
  6  state            uint16   0 standby, 1 charging, 2 discharging, 3 holding 0 kW, 4 watchdog trip
  7  capacity         uint16   1 kWh
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .battery import Battery
from .config import BatterySpec

HR_MODE, HR_SETPOINT, HR_THRESHOLD, HR_HEARTBEAT = 0, 1, 2, 3
IR_SOC, IR_POWER, IR_MAX_DIS, IR_MAX_CH, IR_DIS_HI, IR_DIS_LO, IR_STATE, IR_CAPACITY = range(8)
N_HR, N_IR = 4, 8
WATCHDOG_TICKS = 3
STANDBY, CHARGING, DISCHARGING, HOLDING, WATCHDOG = range(5)


def to_int16(v: int) -> int:
    """Signed value -> register word (two's complement). Raises outside int16."""
    v = int(v)
    if not -32768 <= v <= 32767:
        raise ValueError(f"{v} outside int16")
    return v & 0xFFFF


def from_int16(w: int) -> int:
    w = int(w) & 0xFFFF
    return w - 0x10000 if w & 0x8000 else w


def to_uint16(v: float) -> int:
    v = int(round(v))
    if not 0 <= v <= 0xFFFF:
        raise ValueError(f"{v} outside uint16")
    return v


def to_uint32(v: int) -> tuple[int, int]:
    v = int(v)
    if not 0 <= v <= 0xFFFFFFFF:
        raise ValueError(f"{v} outside uint32")
    return (v >> 16) & 0xFFFF, v & 0xFFFF


def from_uint32(hi: int, lo: int) -> int:
    return ((int(hi) & 0xFFFF) << 16) | (int(lo) & 0xFFFF)


def kw_to_reg(kw: float) -> int:
    """kW -> int16 register in 0.1 kW, saturating at the int16 range."""
    return to_int16(max(-32768, min(32767, round(kw * 10))))


def reg_to_kw(w: int) -> float:
    return from_int16(w) / 10.0


@dataclass
class InverterSim:
    """Simulated inverter: reads the holding registers, moves the battery, writes the input registers."""

    spec: BatterySpec = field(default_factory=BatterySpec)
    hr: list = field(default_factory=lambda: [0] * N_HR)
    ir: list = field(default_factory=lambda: [0] * N_IR)

    def __post_init__(self):
        self.battery = Battery(self.spec)
        self.discharged_kwh = 0.0
        self._last_hb = None
        self._stale = 0
        self._publish(0.0, STANDBY)

    def tick(self, dt_h: float = 0.25) -> float:
        mode, hb = self.hr[HR_MODE], self.hr[HR_HEARTBEAT]
        self._stale = self._stale + 1 if hb == self._last_hb else 0
        self._last_hb = hb
        if mode != 1:
            p, state = 0.0, STANDBY
        elif self._stale >= WATCHDOG_TICKS:
            p, state = 0.0, WATCHDOG
        else:
            req = reg_to_kw(self.hr[HR_SETPOINT])
            p = self.battery.step(req, dt_h)
            state = DISCHARGING if p > 1e-9 else CHARGING if p < -1e-9 else HOLDING
            self.discharged_kwh += max(p, 0.0) * dt_h
        self._publish(p, state)
        return p

    def _publish(self, p: float, state: int):
        b = self.battery
        self.ir[IR_SOC] = to_uint16(b.soc * 10000)
        self.ir[IR_POWER] = kw_to_reg(p)
        self.ir[IR_MAX_DIS] = to_uint16(b.max_discharge_kw() * 10)
        self.ir[IR_MAX_CH] = to_uint16(b.max_charge_kw() * 10)
        self.ir[IR_DIS_HI], self.ir[IR_DIS_LO] = to_uint32(round(self.discharged_kwh))
        self.ir[IR_STATE] = state
        self.ir[IR_CAPACITY] = to_uint16(self.spec.capacity_kwh)


def decode_status(ir: list) -> dict:
    return {"soc_pct": ir[IR_SOC] / 100, "power_kw": reg_to_kw(ir[IR_POWER]), "max_discharge_kw": ir[IR_MAX_DIS] / 10,
            "max_charge_kw": ir[IR_MAX_CH] / 10, "discharged_kwh": from_uint32(ir[IR_DIS_HI], ir[IR_DIS_LO]),
            "state": ir[IR_STATE], "capacity_kwh": ir[IR_CAPACITY]}


# ------------------------------------------------------------------ pymodbus server / client (optional extra)

def make_context(sim: InverterSim):
    from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext, ModbusSlaveContext

    dev = ModbusSlaveContext(hr=ModbusSequentialDataBlock(0, [0] * (N_HR + 1)),
                             ir=ModbusSequentialDataBlock(0, [0] * (N_IR + 1)))
    dev.setValues(4, 0, sim.ir)
    return ModbusServerContext(slaves=dev, single=True)


def sync_tick(ctx, sim: InverterSim, dt_h: float = 0.25) -> float:
    """Copy holding registers from the server store into the sim, step it, copy input registers back."""
    dev = ctx[0]
    sim.hr = list(dev.getValues(3, 0, N_HR))
    p = sim.tick(dt_h)
    dev.setValues(4, 0, sim.ir)
    return p


async def serve(host: str, port: int, sim: InverterSim, tick_s: float = 1.0, dt_h: float = 0.25, stop=None):
    """Run the Modbus TCP server and tick the sim every tick_s seconds (each tick = dt_h of battery time)."""
    import asyncio

    from pymodbus.server import ModbusTcpServer

    ctx = make_context(sim)
    server = ModbusTcpServer(ctx, address=(host, port))
    task = asyncio.create_task(server.serve_forever())
    try:
        while stop is None or not stop.is_set():
            await asyncio.sleep(tick_s)
            sync_tick(ctx, sim, dt_h)
    finally:
        await server.shutdown()
        task.cancel()


def run_server_forever(host: str = "127.0.0.1", port: int = 5020):
    import asyncio

    print(f"simulated battery inverter on {host}:{port} (Ctrl+C to stop); register map in peak_shaving/modbus.py")
    try:
        asyncio.run(serve(host, port, InverterSim()))
    except KeyboardInterrupt:
        pass


class InverterClient:
    """Thin synchronous client for the map above."""

    def __init__(self, host: str = "127.0.0.1", port: int = 5020, unit: int = 1):
        from pymodbus.client import ModbusTcpClient

        self.c = ModbusTcpClient(host, port=port)
        self.unit = unit
        self._hb = 0

    def connect(self) -> bool:
        return bool(self.c.connect())

    def close(self):
        self.c.close()

    def set_power(self, kw: float, threshold_kw: float = 0.0):
        self._hb = (self._hb + 1) & 0xFFFF
        regs = [1, kw_to_reg(kw), to_uint16(max(0.0, threshold_kw)), self._hb]
        r = self.c.write_registers(HR_MODE, regs, slave=self.unit)
        if r.isError():
            raise OSError(f"write failed: {r}")

    def standby(self):
        r = self.c.write_register(HR_MODE, 0, slave=self.unit)
        if r.isError():
            raise OSError(f"write failed: {r}")

    def status(self) -> dict:
        r = self.c.read_input_registers(0, count=N_IR, slave=self.unit)
        if r.isError():
            raise OSError(f"read failed: {r}")
        return decode_status(r.registers)
