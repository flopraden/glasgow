# Ref: Microchip MEC1618/MEC1618i Low Power 32-bit Microcontroller with Embedded Flash
# Document Number: DS00002339A

import logging
import argparse
import struct

from . import JTAGApplet
from .. import *
from ...pyrepl import *
from ...arch.jtag import *
from ...arch.arc.jtag import *
from ...database.arc import *


class JTAGARCInterface:
    def __init__(self, interface, logger):
        self.lower   = interface
        self._logger = logger
        self._level  = logging.DEBUG if self._logger.name == __name__ else logging.TRACE

    def _log(self, message, *args):
        self._logger.log(self._level, "ARC: " + message, *args)

    async def identify(self):
        await self.lower.write_ir(IR_IDCODE)
        idcode_bits = await self.lower.read_dr(32)
        idcode = DR_IDCODE.from_bitarray(idcode_bits)
        self._log("read IDCODE mfg_id=%03x part_id=%04x",
                  idcode.mfg_id, idcode.part_id)
        device = devices[idcode.mfg_id, idcode.part_id]
        return idcode, device

    async def _wait_txn(self):
        await self.lower.write_ir(IR_STATUS)
        status = DR_STATUS()
        while not status.RD:
            status_bits = await self.lower.read_dr(4)
            status = DR_STATUS.from_bitarray(status_bits)
            self._log("status %s", status.bits_repr())
            if status.FL:
                raise GlasgowAppletError("transaction failed")

    async def read(self, address, space):
        if space == "memory":
            dr_txn_command = DR_TXN_COMMAND_READ_MEMORY
        elif space == "core":
            dr_txn_command = DR_TXN_COMMAND_READ_CORE
        elif space == "aux":
            dr_txn_command = DR_TXN_COMMAND_READ_AUX
        else:
            assert False

        self._log("read space=%s address=%08x", space, address)
        dr_address = DR_ADDRESS(Address=address)
        await self.lower.write_ir(IR_ADDRESS)
        await self.lower.write_dr(dr_address.to_bitarray())
        await self.lower.write_ir(IR_TXN_COMMAND)
        await self.lower.write_dr(dr_txn_command)
        await self._wait_txn()
        await self.lower.write_ir(IR_DATA)
        dr_data_bits = await self.lower.read_dr(32)
        dr_data = DR_DATA.from_bitarray(dr_data_bits)
        self._log("read data=%08x", dr_data.Data)
        return dr_data.Data

    async def write(self, address, data, space):
        if space == "memory":
            dr_txn_command = DR_TXN_COMMAND_WRITE_MEMORY
        elif space == "core":
            dr_txn_command = DR_TXN_COMMAND_WRITE_CORE
        elif space == "aux":
            dr_txn_command = DR_TXN_COMMAND_WRITE_AUX
        else:
            assert False

        self._log("write space=%s address=%08x data=%08x", space, address, data)
        dr_address = DR_ADDRESS(Address=address)
        await self.lower.write_ir(IR_ADDRESS)
        await self.lower.write_dr(dr_address.to_bitarray())
        await self.lower.write_ir(IR_DATA)
        dr_data = DR_DATA(Data=data)
        await self.lower.write_dr(dr_data.to_bitarray())
        await self.lower.write_ir(IR_TXN_COMMAND)
        await self.lower.write_dr(dr_txn_command)
        await self._wait_txn()


class JTAGARCApplet(JTAGApplet, name="jtag-arc"):
    preview = True
    logger = logging.getLogger(__name__)
    help = "debug ARC processors via JTAG"
    description = """
    Debug ARC processors via the JTAG interface.

    The list of supported devices is:
{devices}

    There is currently no debug server implemented. This applet only allows manipulating Memory,
    Core and Aux spaces via a Python REPL.
    """.format(
        devices="\n".join(map(lambda x: "        * {.name}\n".format(x), devices.values()))
    )

    @classmethod
    def add_run_arguments(cls, parser, access):
        super().add_run_arguments(parser, access)

        parser.add_argument(
            "--tap-index", metavar="INDEX", type=int, default=0,
            help="select TAP #INDEX for communication (default: %(default)s)")

    async def run(self, device, args):
        jtag_iface = await super().run(device, args)
        await jtag_iface.test_reset()

        tap_iface = await jtag_iface.select_tap(args.tap_index)
        if not tap_iface:
            raise GlasgowAppletError("cannot select TAP #%d" % args.tap_index)

        return JTAGARCInterface(tap_iface, self.logger)

    @classmethod
    def add_interact_arguments(cls, parser):
        p_operation = parser.add_subparsers(dest="operation", metavar="OPERATION")

        p_repl = p_operation.add_parser(
            "repl", help="drop into Python shell; use `arc_iface` to communicate")

    async def interact(self, device, args, arc_iface):
        idcode, device = await arc_iface.identify()
        if device is None:
            raise GlasgowAppletError("cannot operate on unknown device IDCODE=%08x"
                                     % idcode.to_int())
        self.logger.info("IDCODE=%08x device=%s rev=%d",
                         idcode.to_int(), device.name, idcode.version)

        if args.operation == "repl":
            await AsyncInteractiveConsole(locals={"arc_iface":arc_iface}).interact()
