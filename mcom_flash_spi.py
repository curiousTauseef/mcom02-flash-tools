#!/usr/bin/env python

# Copyright 2015-2016 ELVEES NeoTek JSC
#
# Authors:
#     Vasiliy Zasukhin <vzasukhin@elvees.com>
#     Alexey Kiselev <akiselev@elvees.com>
#
# SPDX-License-Identifier:	GPL-2.0+

from argparse import ArgumentParser
import os
import platform
from StringIO import StringIO
import struct
import sys
import time

from intelhex import IntelHex
import serial


def wait_new_command_line(tty, timeout=10):
    s = ""
    time_stop = time.time() + timeout
    while not s.endswith("\r#"):
        char = tty.read()
        if (not char) or (time.time() > time_stop):
            return None
        s += char
    return s


def send_cmd(tty, cmd):
    tty.write(cmd + "\n")
    res = wait_new_command_line(tty)
    if res is None:
        print "Error: the device does not respond on '%s'." % cmd
        sys.exit(1)
    tty.flush()
    return res


def send_ihex(tty, ihex):
    sio = StringIO()
    ihex.write_hex_file(sio)
    sio.seek(0)
    tty.write(sio.read())
    res = wait_new_command_line(tty)
    if res is None:
        print "Error: the device does not respond on writing a file."
        sys.exit(1)
    tmp = wait_new_command_line(tty)
    if tmp is not None:
        res += tmp
    return res


def split_bin_to_ihex(file_name, base_addr, max_block_size):
    """Split a binary file to the blocks and load the blocks to IntelHex
       objects. Return the list of IntelHex objects."""
    # Block size must be aligned to 2 byte boundary (workaround for rf#2088)
    assert max_block_size % 2 == 0
    ihex_list = []
    with open(file_name, 'rb') as f:
        while True:
            block = bytearray(f.read(max_block_size))
            if not block:
                break
            # Align the last block to 2 byte boundary (workaround for rf#2088)
            if len(block) % 2 != 0:
                block.append(0xFF)
            ihex = IntelHex()
            ihex.frombytes(block, base_addr)
            ihex_list.append(ihex)
    return ihex_list


def write_bin_to_flash(tty, file_name):
    base_addr = 0x20000000
    max_block_size = 0xC000
    ihex_list = split_bin_to_ihex(file_name, base_addr, max_block_size)
    send_cmd(tty, "setflash 0")
    for i, ihex in enumerate(ihex_list):
        print "Block: {}/{}, size: {}".format(i + 1, len(ihex_list), len(ihex))
        send_ihex(tty, ihex)
        send_cmd(tty, "commitspiflash {:x} {:x}".format(base_addr, len(ihex)))


def dump2bytes(list_string):
    # TODO(vzasukhin): explain
    if not list_string[0].startswith("0x"):
        list_string = list_string[1:]

    bytes = bytearray()
    for s in list_string:
        addr, word = s.split(' : ')
        bytes += bytearray(struct.pack('<I', int(word, 0)))
    return bytes


def check_block(tty, data, offset, size):
    dump_count = int((size + 3) / 4)
    dump = send_cmd(tty, "dumpspiflash {:x} {:x}".format(offset, dump_count))
    received = dump2bytes(dump.split("\n\r")[2:][:-1])
    return received[:size] == data[offset:][:size]


def check_file(tty, file_name, count):
    max_block_size = 0x2000
    with open(file_name, 'rb') as f:
        if count is None:
            data = f.read()
        else:
            data = f.read(count)
    block_count = int((len(data) + max_block_size - 1) / max_block_size)
    for i, block_offset in enumerate(range(0, len(data), max_block_size)):
        block_size = min(max_block_size, len(data) - block_offset)
        print "Block: {}/{}, size: {}".format(i + 1, block_count, block_size)
        if not check_block(tty, data, block_offset, block_size):
            return False
    return True


if __name__ == "__main__":
    if platform.system() == 'Windows':
        default_port = 'COM3'
    else:
        default_port = '/dev/ttyUSB0'

    description = "The script to program the on-board SPI flash memory " \
                  "with a binary file via MCom Bootrom UART terminal. " \
                  "The file is written starting from the zero page " \
                  "of the SPI flash memory."
    parser = ArgumentParser(description=description)
    parser.add_argument("file_name", help="binary file for programming")
    parser.add_argument("-p", dest="port", default=default_port,
                        help="serial port the device is connected to "
                             "(default: %(default)s)")
    parser.add_argument("-c", dest="count", type=int, default=None,
                        help="count of data bytes to check after programming, "
                             "if not specified all the data is checked "
                             "(default: %(default)s)")
    parser.add_argument("--version", action='version', version='%(prog)s 2.1')
    args = parser.parse_args()

    file_name = args.file_name
    if not os.path.exists(file_name):
        print "Error: the file '%s' is not found." % file_name
        sys.exit(1)

    try:
        tty = serial.Serial(port=args.port, baudrate=115200, timeout=2)
    except serial.SerialException:
        print "Error: cannot open the device '%s'." % args.port
        sys.exit(1)

    tty.write("\n")
    if wait_new_command_line(tty) is None:
        print "Error: terminal does not respond. Set the boot mode to UART " \
              "and reset the board power (do not use warm reset)."
        sys.exit(1)

    # Disable DDR retention to avoid large current on DDRx_VDDQ (see rf#1160).
    send_cmd(tty, "set 38095024 0")

    send_cmd(tty, "autorun 0")
    send_cmd(tty, "cache 1")

    print "Writing to flash..."
    write_bin_to_flash(tty, file_name)

    print "Checking..."
    checking_succeeded = check_file(tty, file_name, args.count)

    send_cmd(tty, "cache 0")

    if checking_succeeded:
        print "Checking succeeded"
        sys.exit(0)
    else:
        print "Checking failed"
        sys.exit(1)
