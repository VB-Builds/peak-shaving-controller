import asyncio
import socket
import threading
import time

import pytest

from peak_shaving import modbus as M
from peak_shaving.config import BatterySpec


def test_int16_roundtrip_and_limits():
    for v in (-32768, -1, 0, 1, 32767):
        assert M.from_int16(M.to_int16(v)) == v
    assert M.to_int16(-1) == 0xFFFF
    with pytest.raises(ValueError):
        M.to_int16(32768)


def test_uint32_word_order():
    assert M.to_uint32(0x12345678) == (0x1234, 0x5678)
    assert M.from_uint32(0x1234, 0x5678) == 0x12345678
    with pytest.raises(ValueError):
        M.to_uint32(-1)


def test_kw_scaling_saturates():
    assert M.reg_to_kw(M.kw_to_reg(-123.4)) == pytest.approx(-123.4)
    assert M.reg_to_kw(M.kw_to_reg(1e6)) == pytest.approx(3276.7)


def test_sim_follows_setpoint_and_reports():
    sim = M.InverterSim(BatterySpec.sized(100))
    sim.hr[:] = [1, M.kw_to_reg(40.0), 900, 1]
    p = sim.tick(0.25)
    st = M.decode_status(sim.ir)
    assert p == pytest.approx(40.0)
    assert st["power_kw"] == pytest.approx(40.0)
    assert st["state"] == M.DISCHARGING
    assert st["soc_pct"] < 90.0
    assert st["capacity_kwh"] == 100


def test_sim_clips_to_battery_power():
    sim = M.InverterSim(BatterySpec.sized(100))  # 50 kW
    sim.hr[:] = [1, M.kw_to_reg(500.0), 0, 1]
    assert sim.tick() == pytest.approx(50.0)


def test_sim_standby_mode_holds_zero():
    sim = M.InverterSim(BatterySpec.sized(100))
    sim.hr[:] = [0, M.kw_to_reg(40.0), 0, 1]
    assert sim.tick() == 0.0
    assert sim.ir[M.IR_STATE] == M.STANDBY


def test_watchdog_trips_when_heartbeat_stops():
    sim = M.InverterSim(BatterySpec.sized(100))
    sim.hr[:] = [1, M.kw_to_reg(-20.0), 0, 7]
    sim.battery.soc_kwh = 50.0
    powers = [sim.tick() for _ in range(M.WATCHDOG_TICKS + 2)]
    assert powers[0] < 0
    assert powers[-1] == 0.0
    assert sim.ir[M.IR_STATE] == M.WATCHDOG
    sim.hr[M.HR_HEARTBEAT] = 8  # controller alive again
    assert sim.tick() < 0


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_modbus_tcp_end_to_end():
    pytest.importorskip("pymodbus")
    port = _free_port()
    sim = M.InverterSim(BatterySpec.sized(200))
    stop = threading.Event()
    th = threading.Thread(target=lambda: asyncio.run(M.serve("127.0.0.1", port, sim, tick_s=0.1, stop=stop)), daemon=True)
    th.start()
    cli = M.InverterClient("127.0.0.1", port)
    try:
        for _ in range(50):
            if cli.connect():
                break
            time.sleep(0.1)
        s0 = cli.status()
        assert s0["capacity_kwh"] == 200
        assert s0["soc_pct"] == pytest.approx(90.0)
        for _ in range(6):
            cli.set_power(80.0, threshold_kw=1200)
            time.sleep(0.1)
        s1 = cli.status()
        assert s1["power_kw"] == pytest.approx(80.0)
        assert s1["state"] == M.DISCHARGING
        assert s1["soc_pct"] < s0["soc_pct"]
        cli.standby()
        time.sleep(0.2)
        assert cli.status()["state"] == M.STANDBY
    finally:
        cli.close()
        stop.set()
        th.join(timeout=5)
